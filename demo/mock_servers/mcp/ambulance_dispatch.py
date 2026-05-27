"""
救护车调度 MCP Server

工具：
- list_ambulances: 查询救护车列表（可按状态过滤）
- dispatch_ambulance: 派遣救护车（更新 DB）
- recall_ambulance: 召回救护车（状态→待命）
- get_hospital_capacity: 查询医院 ICU/急诊可用容量
"""
from __future__ import annotations

import math
from pathlib import Path
import aiosqlite
from mcp.server.fastmcp import FastMCP


def _dist_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    return R * 2 * math.asin(min(1.0, math.sqrt(a)))

mcp = FastMCP("ambulance_dispatch", host="0.0.0.0", port=8102)

DB = Path(__file__).parent.parent.parent / "city_state.db"


@mcp.tool()
async def list_ambulances(
    status: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    k: int = 5,
) -> list[dict]:
    """查询救护车列表，按距事故点距离排序返回最近 k 辆。

    Args:
        status: 可选过滤状态，"待命" 或 "出车"；None 返回全部
        lat: 事故点纬度（用于按距离排序）
        lng: 事故点经度
        k: 返回最近的 k 辆，默认 5；lat/lng 缺失时忽略此参数返回全部

    Returns:
        救护车列表，每项包含 id/status/lat/lng/hospital_id/dest_lat/dest_lng/patient_type/distance_km
    """
    async with aiosqlite.connect(DB, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        if status:
            cursor = await db.execute(
                "SELECT * FROM ambulances WHERE status = ?", (status,)
            )
        else:
            cursor = await db.execute("SELECT * FROM ambulances")
        rows = await cursor.fetchall()

    result = [dict(r) for r in rows]
    if lat is not None and lng is not None:
        for r in result:
            r["distance_km"] = round(_dist_km(lat, lng, r["lat"], r["lng"]), 2)
        result.sort(key=lambda r: r["distance_km"])
        # 8km 软上限：优先返回近处；8km 内无车辆时移除半径限制
        _SOFT_RADIUS = 8.0
        within = [r for r in result if r["distance_km"] <= _SOFT_RADIUS]
        result = (within if within else result)[:k]
    return result


@mcp.tool()
async def dispatch_ambulance(
    ambulance_id: str,
    patient_type: str,
    dest_lat: float | None = None,
    dest_lng: float | None = None,
) -> dict:
    """⚠️ 写操作：派遣救护车前往目的地，会修改系统状态，需经 HITL 审批后执行。

    Args:
        ambulance_id: 救护车编号（如 "A3"）
        patient_type: 伤员类型（如 "车祸外伤" "烧伤" "心梗" 等）
        dest_lat: 目的地纬度（可选，有坐标时记录目的地；缺省时仅更新出车状态）
        dest_lng: 目的地经度

    Returns:
        更新后的调度记录
    """
    async with aiosqlite.connect(DB, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        # 检查救护车是否存在且处于待命状态
        cur = await db.execute(
            "SELECT * FROM ambulances WHERE id = ?", (ambulance_id,)
        )
        row = await cur.fetchone()
        if not row:
            raise ValueError(f"救护车 {ambulance_id} 不存在")
        ambulance = dict(row)

        # 保存出发坐标（派遣前的当前位置，供路线规划使用）
        from_lat = ambulance["lat"]
        from_lng = ambulance["lng"]

        await db.execute(
            """UPDATE ambulances
               SET status='出车', dest_lat=?, dest_lng=?, patient_type=?
               WHERE id=?""",
            (dest_lat, dest_lng, patient_type, ambulance_id),
        )
        await db.commit()

        cur2 = await db.execute(
            "SELECT * FROM ambulances WHERE id = ?", (ambulance_id,)
        )
        updated = dict(await cur2.fetchone())

    dest_text = f"({dest_lat}, {dest_lng})" if dest_lat is not None else "事故现场"
    return {
        **updated,
        "from_lat": from_lat,
        "from_lng": from_lng,
        "message": f"已派遣 {ambulance_id} 前往 {dest_text}，接送 {patient_type} 伤员",
        "map_marker": {
            "icon": "🚑",
            "position": [from_lng, from_lat],
            "label": ambulance_id,
        },
    }


@mcp.tool()
async def recall_ambulance(ambulance_id: str) -> dict:
    """⚠️ 写操作：召回救护车，状态更新为待命，需经 HITL 审批后执行。

    Args:
        ambulance_id: 救护车编号

    Returns:
        更新后的记录
    """
    async with aiosqlite.connect(DB, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM ambulances WHERE id = ?", (ambulance_id,)
        )
        row = await cur.fetchone()
        if not row:
            raise ValueError(f"救护车 {ambulance_id} 不存在")

        await db.execute(
            """UPDATE ambulances
               SET status='待命', dest_lat=NULL, dest_lng=NULL, patient_type=NULL
               WHERE id=?""",
            (ambulance_id,),
        )
        await db.commit()

        cur2 = await db.execute(
            "SELECT * FROM ambulances WHERE id = ?", (ambulance_id,)
        )
        updated = dict(await cur2.fetchone())

    return {**updated, "message": f"救护车 {ambulance_id} 已召回，状态：待命"}


@mcp.tool()
async def get_hospital_capacity(
    lat: float | None = None,
    lng: float | None = None,
    k: int = 5,
) -> list[dict]:
    """查询各医院 ICU 和急诊可用容量，按距事故点距离排序返回最近 k 家。

    Args:
        lat: 事故点纬度（用于按距离排序）
        lng: 事故点经度
        k: 返回最近的 k 家医院，默认 5；lat/lng 缺失时按 ICU 容量降序返回全部

    Returns:
        医院列表，每项包含 id/name/lat/lng/icu_available/emergency_available/distance_km
    """
    async with aiosqlite.connect(DB, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM hospitals ORDER BY icu_available DESC"
        )
        rows = await cur.fetchall()
        # 每家医院待命救护车数量（按 hospital_id GROUP BY）
        amb_cur = await db.execute(
            "SELECT hospital_id, COUNT(*) AS cnt FROM ambulances WHERE status='待命' GROUP BY hospital_id"
        )
        amb_counts: dict[str, int] = {r["hospital_id"]: r["cnt"] for r in await amb_cur.fetchall()}

    result = [dict(r) for r in rows]
    for r in result:
        r["standby_ambulance_count"] = amb_counts.get(r["id"], 0)
    if lat is not None and lng is not None:
        for r in result:
            r["distance_km"] = round(_dist_km(lat, lng, r["lat"], r["lng"]), 2)
        result.sort(key=lambda r: r["distance_km"])
        result = result[:k]
    return result


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
