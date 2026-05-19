"""
交通信号控制 MCP Server

工具：
- list_intersections: 查询 8 个路口当前状态
- set_mode: 设置单个路口信号模式
- apply_evacuation_plan: 按应急预案等级批量设置路口模式

预案对应关系（天津滨海新区应急疏散路线方案）：
  Ⅳ级：S1/S2 全红封闭，其余正常
  Ⅲ级：S1-S4 全红封闭，S5-S8 应急绿波
  Ⅱ级：S1-S4 全红封闭，S5-S8 单向清空
"""
from __future__ import annotations

from pathlib import Path
import aiosqlite
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("signal_control", host="0.0.0.0", port=8103)

DB = Path(__file__).parent.parent.parent / "city_state.db"

VALID_MODES = {"正常", "全红封闭", "应急绿波", "单向清空", "消防应急"}

# 预案批量配置
_EVACUATION_PLANS: dict[str, dict[str, str]] = {
    "Ⅳ": {
        "S1": "全红封闭", "S2": "全红封闭",
        "S3": "正常", "S4": "正常",
        "S5": "正常", "S6": "正常",
        "S7": "正常", "S8": "正常",
    },
    "Ⅲ": {
        "S1": "全红封闭", "S2": "全红封闭",
        "S3": "全红封闭", "S4": "全红封闭",
        "S5": "应急绿波", "S6": "应急绿波",
        "S7": "应急绿波", "S8": "应急绿波",
    },
    "Ⅱ": {
        "S1": "全红封闭", "S2": "全红封闭",
        "S3": "全红封闭", "S4": "全红封闭",
        "S5": "单向清空", "S6": "单向清空",
        "S7": "单向清空", "S8": "单向清空",
    },
}


@mcp.tool()
async def list_intersections() -> list[dict]:
    """查询所有路口当前信号模式和坐标。

    Returns:
        路口列表，每项包含 id/name/lat/lng/mode/mode_expires_at
    """
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM intersections ORDER BY id")
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@mcp.tool()
async def set_mode(
    intersection_id: str,
    mode: str,
    duration_min: int,
) -> dict:
    """⚠️ 写操作：设置单个路口信号模式，会修改系统状态，需经 HITL 审批后执行。

    Args:
        intersection_id: 路口编号（S1-S8）
        mode: 信号模式（"正常"/"全红封闭"/"应急绿波"/"单向清空"/"消防应急"）
        duration_min: 持续时间（分钟，0 表示永久）

    Returns:
        更新后的路口信息
    """
    if mode not in VALID_MODES:
        raise ValueError(f"无效模式 {mode!r}，可选：{', '.join(sorted(VALID_MODES))}")

    async with aiosqlite.connect(DB) as db:
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
    Ⅳ级：S1/S2 封闭；Ⅲ级：S1-S4 封闭+S5-S8 绿波；Ⅱ级：S1-S4 封闭+S5-S8 单向清空。

    Args:
        level: 预案等级，"Ⅳ"/"Ⅲ"/"Ⅱ"（响应程度递增）

    Returns:
        包含 level/updated_count/intersections 的汇总
    """
    if level not in _EVACUATION_PLANS:
        raise ValueError(f"预案等级 {level!r} 无效，可选：Ⅳ、Ⅲ、Ⅱ")

    plan = _EVACUATION_PLANS[level]
    updated = []

    async with aiosqlite.connect(DB) as db:
        for sid, mode in plan.items():
            await db.execute(
                "UPDATE intersections SET mode=?, mode_expires_at=NULL WHERE id=?",
                (mode, sid),
            )
            updated.append({"id": sid, "mode": mode})
        await db.commit()

    return {
        "level": level,
        "updated_count": len(updated),
        "intersections": updated,
        "message": f"已启动 {level} 级应急疏散预案，共更新 {len(updated)} 个路口信号",
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
