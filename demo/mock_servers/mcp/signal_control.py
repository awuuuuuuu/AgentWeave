"""
交通信号控制 MCP Server

工具：
- list_intersections: 查询 8 个路口当前状态（含坐标）
- set_mode: 设置单个路口信号模式
- apply_evacuation_plan: 按应急预案等级批量设置路口模式

预案对应关系（上海浦东新区应急疏散路线方案）：
  Ⅳ级：INT-01/INT-02 全红封闭，其余正常
  Ⅲ级：INT-01~INT-04 全红封闭，INT-05~INT-08 应急绿波
  Ⅱ级：INT-01~INT-04 全红封闭，INT-05~INT-08 单向清空
"""
from __future__ import annotations

from pathlib import Path
import aiosqlite
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("signal_control", host="0.0.0.0", port=8103)

DB = Path(__file__).parent.parent.parent / "city_state.db"

VALID_MODES = {"正常", "全红封闭", "应急绿波", "单向清空", "消防应急"}

# LLM 常见英文/简写别名 → 规范中文模式名
_MODE_ALIASES: dict[str, str] = {
    "emergency":       "消防应急",
    "fire_emergency":  "消防应急",
    "fire emergency":  "消防应急",
    "消防":            "消防应急",
    "all_red":         "全红封闭",
    "all red":         "全红封闭",
    "red":             "全红封闭",
    "全红":            "全红封闭",
    "green_wave":      "应急绿波",
    "green wave":      "应急绿波",
    "greenwave":       "应急绿波",
    "绿波":            "应急绿波",
    "one_way":         "单向清空",
    "one way":         "单向清空",
    "single":          "单向清空",
    "单向":            "单向清空",
    "normal":          "正常",
    "restore":         "正常",
}


def _normalize_mode(mode: str) -> str:
    """将 LLM 输出的模式字符串规范化为 VALID_MODES 中的值。"""
    if mode in VALID_MODES:
        return mode
    return _MODE_ALIASES.get(mode.lower().strip(), mode)

# 预案批量配置（路口编号与 RAG 文档及 city_state.db 保持一致，使用 INT-XX）
_EVACUATION_PLANS: dict[str, dict[str, str]] = {
    "Ⅳ": {
        "INT-01": "全红封闭", "INT-02": "全红封闭",
        "INT-03": "正常",    "INT-04": "正常",
        "INT-05": "正常",    "INT-06": "正常",
        "INT-07": "正常",    "INT-08": "正常",
    },
    "Ⅲ": {
        "INT-01": "全红封闭", "INT-02": "全红封闭",
        "INT-03": "全红封闭", "INT-04": "全红封闭",
        "INT-05": "应急绿波", "INT-06": "应急绿波",
        "INT-07": "应急绿波", "INT-08": "应急绿波",
    },
    "Ⅱ": {
        "INT-01": "全红封闭", "INT-02": "全红封闭",
        "INT-03": "全红封闭", "INT-04": "全红封闭",
        "INT-05": "单向清空", "INT-06": "单向清空",
        "INT-07": "单向清空", "INT-08": "单向清空",
    },
}


@mcp.tool()
async def list_intersections() -> list[dict]:
    """查询所有路口当前信号模式和坐标。

    Returns:
        路口列表，每项包含 id/name/lat/lng/mode/mode_expires_at
    """
    async with aiosqlite.connect(DB, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM intersections ORDER BY id")
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@mcp.tool()
async def set_mode(
    intersection_id: str,
    mode: str,
    duration_min: int = 120,
) -> dict:
    """⚠️ 写操作：设置单个路口信号模式，会修改系统状态，需经 HITL 审批后执行。

    Args:
        intersection_id: 路口编号（INT-01 ~ INT-08）
        mode: 信号模式（"正常"/"全红封闭"/"应急绿波"/"单向清空"/"消防应急"）
        duration_min: 持续时间（分钟，0 表示永久）

    Returns:
        更新后的路口信息
    """
    mode = _normalize_mode(mode)
    if mode not in VALID_MODES:
        raise ValueError(f"无效模式 {mode!r}，可选：{', '.join(sorted(VALID_MODES))}")

    async with aiosqlite.connect(DB, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM intersections WHERE id = ?", (intersection_id,)
        )
        row = await cur.fetchone()
        if not row:
            raise ValueError(f"路口 {intersection_id} 不存在")

        expires = None
        if duration_min > 0:
            import datetime
            expires = (
                datetime.datetime.now()
                + datetime.timedelta(minutes=duration_min)
            ).isoformat(timespec="seconds")

        await db.execute(
            "UPDATE intersections SET mode=?, mode_expires_at=? WHERE id=?",
            (mode, expires, intersection_id),
        )
        await db.commit()

        cur2 = await db.execute(
            "SELECT * FROM intersections WHERE id = ?", (intersection_id,)
        )
        updated = dict(await cur2.fetchone())

    updated["message"] = (
        f"路口 {intersection_id}（{updated['name']}）已设置为【{mode}】"
        + (f"，持续 {duration_min} 分钟" if duration_min > 0 else "，永久生效")
    )
    return updated


@mcp.tool()
async def apply_evacuation_plan(level: str) -> dict:
    """⚠️ 写操作：按应急预案等级批量设置路口信号模式，需经 HITL 审批后执行。
    Ⅳ级：INT-01/INT-02 封闭；Ⅲ级：INT-01~INT-04 封闭+INT-05~INT-08 绿波；Ⅱ级：INT-01~INT-04 封闭+INT-05~INT-08 单向清空。

    Args:
        level: 预案等级，"Ⅳ"/"Ⅲ"/"Ⅱ"（响应程度递增）

    Returns:
        包含 level/updated_count/intersections 的汇总
    """
    if level not in _EVACUATION_PLANS:
        raise ValueError(f"预案等级 {level!r} 无效，可选：Ⅳ、Ⅲ、Ⅱ")

    plan = _EVACUATION_PLANS[level]
    updated = []

    async with aiosqlite.connect(DB, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        for sid, mode in plan.items():
            await db.execute(
                "UPDATE intersections SET mode=?, mode_expires_at=NULL WHERE id=?",
                (mode, sid),
            )
        await db.commit()

        # 回查，带上 lat/lng 供 map_extract 直接渲染，无需外部坐标表
        rows = await (await db.execute(
            f"SELECT id, name, lat, lng, mode FROM intersections WHERE id IN ({','.join('?'*len(plan))})",
            list(plan.keys()),
        )).fetchall()
        updated = [dict(r) for r in rows]

    return {
        "level": level,
        "updated_count": len(updated),
        "intersections": updated,
        "message": f"已启动 {level} 级应急疏散预案，共更新 {len(updated)} 个路口信号",
    }


@mcp.tool()
async def get_nearby_intersections(
    lat: float,
    lng: float,
    radius_km: float = 3.0,
    limit: int = 10,
) -> list[dict]:
    """按事故坐标查询周边路口（按距离升序）。

    Args:
        lat: 事故地点纬度
        lng: 事故地点经度
        radius_km: 搜索半径（公里），默认 3.0
        limit: 最多返回条数，默认 10

    Returns:
        路口列表，每项包含 id/name/lat/lng/mode/district/road_grade/distance_km
    """
    import math

    def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        R = 6371.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lng2 - lng1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        return R * 2 * math.asin(math.sqrt(a))

    async with aiosqlite.connect(DB, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM intersections")
        rows = await cur.fetchall()

    results = []
    for row in rows:
        r = dict(row)
        dist = _haversine(lat, lng, r["lat"], r["lng"])
        if dist <= radius_km:
            r["distance_km"] = round(dist, 3)
            results.append(r)

    results.sort(key=lambda x: x["distance_km"])
    return results[:limit]


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
