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

from pathlib import Path
import aiosqlite
from mcp.server.fastmcp import FastMCP

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


@mcp.tool()
async def get_critical_alarms() -> list[dict]:
    """查询当前超阈值报警传感器。

    Returns:
        list[dict]: 告警传感器列表（is_alarm=1），含 map_marker 供地图标注告警位置
    """
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
    return result


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
