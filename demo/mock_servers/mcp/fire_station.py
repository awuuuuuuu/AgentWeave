# demo/mock_servers/mcp/fire_station.py
"""
消防救援 MCP Server

工具：
- get_fire_stations: 查询消防站列表（按距离过滤）
- dispatch_fire_trucks: ⚠️ 调派消防车（写操作，需经 HITL 审批后执行）
- recall_fire_trucks: ⚠️ 回撤消防车（写操作，需经 HITL 审批后执行）
- set_fire_perimeter: 设置火场隔离警戒圈
- get_water_supplies: 查询附近消防水源

数据来源：demo/city_state.db fire_stations 表
"""
from __future__ import annotations

import math
from pathlib import Path

import aiosqlite
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("fire_station", host="0.0.0.0", port=8107)

DB = Path(__file__).parent.parent.parent / "city_state.db"


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """简化欧氏距离（上海纬度下，1°≈111km）。"""
    return math.sqrt((lat1 - lat2) ** 2 + (lng1 - lng2) ** 2) * 111


@mcp.tool()
async def get_fire_stations(
    lat: float | None = None,
    lng: float | None = None,
    radius_km: float = 15.0,
) -> dict:
    """查询消防救援站列表。

    Args:
        lat: 事故点纬度（可选，用于按距离排序和过滤）
        lng: 事故点经度
        radius_km: 搜索半径（公里，默认15）

    Returns:
        dict: {"stations": [...], "total": int}
              每条包含 id/name/address/lat/lng/total_trucks/available_trucks/personnel/distance_km
    """
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall("SELECT * FROM fire_stations")

    stations = []
    for row in rows:
        d = dict(row)
        if lat is not None and lng is not None:
            dist = _haversine_km(lat, lng, d["lat"], d["lng"])
            d["distance_km"] = round(dist, 2)
            if dist > radius_km:
                continue
        stations.append(d)

    if lat is not None:
        stations.sort(key=lambda s: s.get("distance_km", 9999))

    return {"stations": stations, "total": len(stations)}


@mcp.tool()
async def dispatch_fire_trucks(
    station_id: str,
    truck_count: int,
    dest_lat: float,
    dest_lng: float,
    incident_type: str = "火灾",
) -> dict:
    """⚠️ 写操作：调派消防车前往事故地点，会修改系统状态，需经 HITL 审批后执行。

    Args:
        station_id: 消防站 ID（如 FS1）
        truck_count: 调派车辆数（不超过 available_trucks）
        dest_lat: 目的地纬度
        dest_lng: 目的地经度
        incident_type: 事故类型，影响预计处置时间

    Returns:
        dict: 调派结果，含 from_lat/from_lng 可供路线规划
    """
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM fire_stations WHERE id = ?", (station_id,)
        )).fetchone()
        if not row:
            return {"status": "failed", "message": f"未找到消防站 {station_id}"}
        s = dict(row)
        actual = min(truck_count, s["available_trucks"])
        if actual == 0:
            return {"status": "failed", "message": f"{s['name']} 无可用消防车"}
        await db.execute(
            "UPDATE fire_stations SET available_trucks = available_trucks - ? WHERE id = ?",
            (actual, station_id),
        )
        await db.commit()

    dist_km = _haversine_km(s["lat"], s["lng"], dest_lat, dest_lng)
    eta_min = round(dist_km / 40 * 60 + 5, 1)  # 40km/h + 5min 出动准备

    return {
        "status": "dispatched",
        "station_name": s["name"],
        "from_lat": s["lat"],
        "from_lng": s["lng"],
        "to_lat": dest_lat,
        "to_lng": dest_lng,
        "truck_count": actual,
        "incident_type": incident_type,
        "eta_minutes": eta_min,
        "message": (
            f"已从{s['name']}调派{actual}辆消防车，预计{eta_min}分钟到达。"
            f"剩余可用车辆：{s['available_trucks'] - actual}辆"
        ),
        "map_marker": {
            "icon": "🚒",
            "position": [s["lng"], s["lat"]],
            "label": s["name"],
        },
    }


@mcp.tool()
async def recall_fire_trucks(station_id: str, truck_count: int) -> dict:
    """⚠️ 写操作：回撤消防车归还至消防站，会修改系统状态，需经 HITL 审批后执行。

    Args:
        station_id: 消防站 ID（如 FS1）
        truck_count: 回撤车辆数

    Returns:
        dict: 回撤结果
    """
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM fire_stations WHERE id = ?", (station_id,)
        )).fetchone()
        if not row:
            return {"status": "failed", "message": f"未找到消防站 {station_id}"}
        s = dict(row)
        new_available = min(s["total_trucks"], s["available_trucks"] + truck_count)
        actual_recalled = new_available - s["available_trucks"]
        await db.execute(
            "UPDATE fire_stations SET available_trucks = ? WHERE id = ?",
            (new_available, station_id),
        )
        await db.commit()

    return {
        "status": "recalled",
        "station_name": s["name"],
        "trucks_recalled": actual_recalled,
        "available_trucks": new_available,
        "message": f"已回撤{actual_recalled}辆消防车至{s['name']}，当前可用：{new_available}辆",
    }


@mcp.tool()
async def set_fire_perimeter(
    center_lat: float,
    center_lng: float,
    radius_m: int = 200,
) -> dict:
    """设置火场隔离警戒圈。

    Args:
        center_lat: 圆心纬度
        center_lng: 圆心经度
        radius_m: 警戒半径（米，建议：普通火灾100-200m，危化品300-500m）

    Returns:
        dict: 警戒圈数据，含地图渲染字段 map_circle
    """
    return {
        "status": "set",
        "center_lat": center_lat,
        "center_lng": center_lng,
        "radius_m": radius_m,
        "message": f"已在 ({center_lat:.4f}, {center_lng:.4f}) 设置 {radius_m}m 火场警戒圈",
        "map_circle": {
            "center": [center_lng, center_lat],
            "radius": radius_m,
            "color": "#ef4444",
            "label": f"🔴 火场警戒线 {radius_m}m",
        },
    }


@mcp.tool()
async def get_water_supplies(
    lat: float,
    lng: float,
    radius_km: float = 3.0,
) -> dict:
    """查询附近消防水源（消防水池/消火栓）。

    Args:
        lat: 中心纬度
        lng: 中心经度
        radius_km: 搜索半径（公里，默认3）

    Returns:
        dict: {"supplies": [...], "total": int}
    """
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall("SELECT * FROM water_supplies")

    results = []
    for row in rows:
        w = dict(row)
        dist = _haversine_km(lat, lng, w["lat"], w["lng"])
        if dist <= radius_km:
            results.append({**w, "distance_km": round(dist, 2)})
    results.sort(key=lambda x: x["distance_km"])
    return {"supplies": results, "total": len(results)}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
