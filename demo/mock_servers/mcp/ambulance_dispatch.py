"""
救护车调度 MCP Server

工具：
- list_ambulances: 查询救护车列表（可按状态过滤）
- dispatch_ambulance: 派遣救护车（更新 DB）
- recall_ambulance: 召回救护车（状态→待命）
- get_hospital_capacity: 查询医院 ICU/急诊可用容量
"""
from __future__ import annotations

from pathlib import Path
import aiosqlite
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("ambulance_dispatch", host="0.0.0.0", port=8102)

DB = Path(__file__).parent.parent.parent / "city_state.db"


@mcp.tool()
async def list_ambulances(status: str | None = None) -> list[dict]:
    """查询救护车列表。

    Args:
        status: 可选过滤状态，"待命" 或 "出车"；None 返回全部

    Returns:
        救护车列表，每项包含 id/status/lat/lng/hospital_id/dest_lat/dest_lng/patient_type
    """
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        if status:
            cursor = await db.execute(
                "SELECT * FROM ambulances WHERE status = ?", (status,)
            )
        else:
            cursor = await db.execute("SELECT * FROM ambulances")
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


@mcp.tool()
async def dispatch_ambulance(
    ambulance_id: str,
    dest_lat: float,
    dest_lng: float,
    patient_type: str,
) -> dict:
    """⚠️ 写操作：派遣救护车前往目的地，会修改系统状态，需经 HITL 审批后执行。

    Args:
        ambulance_id: 救护车编号（如 "A3"）
        dest_lat: 目的地纬度
        dest_lng: 目的地经度
        patient_type: 伤员类型（如 "车祸外伤" "烧伤" "心梗" 等）

    Returns:
        更新后的调度记录
    """
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        # 检查救护车是否存在且处于待命状态
        cur = await db.execute(
            "SELECT * FROM ambulances WHERE id = ?", (ambulance_id,)
        )
        row = await cur.fetchone()
        if not row:
            raise ValueError(f"救护车 {ambulance_id} 不存在")
        ambulance = dict(row)
        if ambulance["status"] == "出车":
            raise ValueError(f"救护车 {ambulance_id} 当前已在执行任务，无法派遣")

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

    return {
        **updated,
        "from_lat": from_lat,
        "from_lng": from_lng,
        "message": f"已派遣 {ambulance_id} 前往 ({dest_lat}, {dest_lng})，接送 {patient_type} 伤员",
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
    async with aiosqlite.connect(DB) as db:
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
async def get_hospital_capacity() -> list[dict]:
    """查询各医院 ICU 和急诊可用容量。

    Returns:
        医院列表，每项包含 id/name/lat/lng/icu_available/emergency_available
    """
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM hospitals ORDER BY icu_available DESC"
        )
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
