# demo/mock_servers/mcp/environment_sensor.py
"""
通用环境传感器 MCP Server（只读）

工具：
- get_sensor_readings: 查询传感器数据（可按类型过滤）
- get_critical_alarms: 查询当前报警传感器

适用场景：火灾、危化品泄漏、洪涝等各类事故的环境监测数据。
数据来源：demo/city_state.db sensor_readings 表
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

mcp = FastMCP("environment_sensor", host="0.0.0.0", port=8105)

DB = Path(__file__).parent.parent.parent / "city_state.db"


@mcp.tool()
async def get_sensor_readings(sensor_type: str | None = None) -> list[dict]:
    """查询环境传感器实时读数。

    Args:
        sensor_type: 可选，过滤类型。可选值：风速 / 风向 / 温度 / 烟雾 / 一氧化碳 / 噪声 / PM2.5

    Returns:
        list[dict]: 传感器读数列表，每条包含 id/sensor_type/location/lat/lng/
                    current_value/unit/threshold/is_alarm
    """
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        if sensor_type:
            rows = await db.execute_fetchall(
                "SELECT * FROM sensor_readings WHERE sensor_type = ?", (sensor_type,)
            )
        else:
            rows = await db.execute_fetchall("SELECT * FROM sensor_readings")
    return [dict(r) for r in rows]


def _mock_alarms_around(lat: float, lng: float, k: int) -> list[dict]:
    """在事故点周围生成模拟告警传感器（1°≈111km，小偏移量=百米级）。"""
    # 固定偏移量（度），对应约 50-300m 范围内的不同方位
    _OFFSETS = [
        (0.0005,  0.0002, "HP_SM1",  "烟雾",    68.0, "%obs", 30.0),
        (-0.0003, 0.0006, "HP_CO1",  "一氧化碳", 45.0, "ppm",  25.0),
        (0.0008, -0.0004, "HP_TM1",  "温度",    420.0, "°C",  100.0),
        (-0.0006,-0.0003, "HP_SM2",  "烟雾",    52.0, "%obs", 30.0),
        (0.0002,  0.0009, "HP_CO2",  "一氧化碳", 38.0, "ppm",  25.0),
        (-0.0010, 0.0005, "HP_WS1",  "风速",     8.5, "m/s",  15.0),
        (0.0012, -0.0008, "HP_PM1",  "PM2.5",  320.0, "μg/m³",150.0),
        (-0.0004, 0.0011, "HP_TM2",  "温度",    185.0, "°C",  100.0),
    ]
    result = []
    for i, (dlat, dlng, sid, stype, val, unit, threshold) in enumerate(_OFFSETS[:k]):
        slat, slng = round(lat + dlat, 6), round(lng + dlng, 6)
        dist = round(_dist_km(lat, lng, slat, slng), 3)
        d = {
            "id": sid,
            "sensor_type": stype,
            "location": f"事故点周边{int(dist*1000)}m",
            "lat": slat,
            "lng": slng,
            "current_value": val,
            "unit": unit,
            "threshold": threshold,
            "is_alarm": 1,
            "distance_km": dist,
            "map_marker": {
                "icon": "⚠️",
                "position": [slng, slat],
                "label": f"{stype}告警：{val}{unit}（超阈值{round(val/threshold, 1)}×）",
            },
        }
        result.append(d)
    result.sort(key=lambda x: x["distance_km"])
    return result


@mcp.tool()
async def get_critical_alarms(
    lat: float | None = None,
    lng: float | None = None,
    k: int = 8,
) -> list[dict]:
    """查询当前超阈值报警传感器，按距事故点距离排序返回最近 k 个。

    Args:
        lat: 事故点纬度（用于按距离排序；提供时在事故点周围生成动态告警数据）
        lng: 事故点经度
        k: 返回最近的 k 个报警传感器，默认 8

    Returns:
        list[dict]: 告警传感器列表（is_alarm=1），含 map_marker 供地图标注告警位置
    """
    if lat is not None and lng is not None:
        return _mock_alarms_around(lat, lng, k)

    # 无坐标时回退到 DB 全局告警（兼容旧调用）
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT * FROM sensor_readings WHERE is_alarm = 1"
        )
    result = []
    for r in rows:
        d = dict(r)
        d["map_marker"] = {
            "icon": "⚠️",
            "position": [d["lng"], d["lat"]],
            "label": f"{d['sensor_type']}告警：{d['current_value']}{d['unit']}",
        }
        result.append(d)
    return result[:k]


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
