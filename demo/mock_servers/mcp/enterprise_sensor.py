"""
企业传感器 MCP Server（只读）

工具：
- get_sensor_readings: 查询传感器数据（可按类型过滤）
- get_critical_alarms: 查询当前报警传感器
- get_incident_timeline: 返回事故快报时间线

数据来源：demo/city_state.db sensor_readings 表
时间线数据：基于 demo/enterprise-safety/XX合成氨有限公司设备安全数据与事故快报.md
"""
from __future__ import annotations

from pathlib import Path
import aiosqlite
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("enterprise_sensor", host="0.0.0.0", port=8105)

DB = Path(__file__).parent.parent.parent / "city_state.db"

# 事故快报时间线（2026-05-16 港城大道388号 XX合成氨有限公司）
_INCIDENT_TIMELINE = [
    {
        "time": "13:38",
        "event": "DCS 系统检测到合成塔出口温度异常升高（超限 +12℃）",
        "level": "预警",
        "operator": "班长李某",
    },
    {
        "time": "13:42",
        "event": "P1 主管道压力传感器报警（2.8 MPa，超限 1.5 MPa），安全阀启跳",
        "level": "一级告警",
        "operator": "自动系统",
    },
    {
        "time": "13:45",
        "event": "现场操作员确认液氨输送管道法兰处泄漏，能见度明显下降",
        "level": "确认事故",
        "operator": "操作员王某",
    },
    {
        "time": "13:47",
        "event": "企业启动内部应急预案，紧急关停合成系统，拨打119/120",
        "level": "应急启动",
        "operator": "安全总监赵某",
    },
    {
        "time": "13:50",
        "event": "消防支队接警出动，N1 氨气浓度传感器达 890 ppm（ERPG-3 区域）",
        "level": "消防响应",
        "operator": "消防指挥中心",
    },
    {
        "time": "13:52",
        "event": "企业启动员工疏散，周边 500m 范围内人员撤离",
        "level": "疏散",
        "operator": "应急指挥部",
    },
    {
        "time": "13:55",
        "event": "天津市滨海新区应急管理局接报，现场指挥部成立",
        "level": "政府响应",
        "operator": "滨海新区应急管理局",
    },
    {
        "time": "14:02",
        "event": "第一批消防队员穿戴重型防化服进场，确认 8 名中毒伤员",
        "level": "救援",
        "operator": "消防指挥员",
    },
    {
        "time": "14:08",
        "event": "泰达医院急救医生到场，启动大规模伤亡事件（MCI）响应流程",
        "level": "医疗响应",
        "operator": "泰达医院急诊科",
    },
    {
        "time": "14:15",
        "event": "当前状态：泄漏持续，现场设立三区（热区/温区/冷区），救援进行中",
        "level": "持续",
        "operator": "现场指挥部",
    },
]


@mcp.tool()
async def get_sensor_readings(sensor_type: str | None = None) -> list[dict]:
    """查询传感器读数。

    Args:
        sensor_type: 可选类型过滤，如"氨气浓度"/"压力"/"温度"/"风速"/"风向"
                     "一氧化碳"/"可燃气体"/"噪声"/"烟雾"；None 返回全部

    Returns:
        传感器列表，每项包含 id/sensor_type/location/current_value/unit/threshold/is_alarm
    """
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        if sensor_type:
            cur = await db.execute(
                "SELECT * FROM sensor_readings WHERE sensor_type = ? ORDER BY id",
                (sensor_type,),
            )
        else:
            cur = await db.execute(
                "SELECT * FROM sensor_readings ORDER BY sensor_type, id"
            )
        rows = await cur.fetchall()
    result = [dict(r) for r in rows]
    # 将 is_alarm 从 0/1 转为 bool
    for r in result:
        r["is_alarm"] = bool(r["is_alarm"])
    return result


@mcp.tool()
async def get_critical_alarms() -> list[dict]:
    """查询当前超阈值（报警）传感器。

    Returns:
        报警传感器列表，每项包含 id/sensor_type/location/current_value/unit/threshold/超出倍数
    """
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM sensor_readings
               WHERE is_alarm = 1
               ORDER BY (current_value * 1.0 / threshold) DESC"""
        )
        rows = await cur.fetchall()

    result = []
    for r in rows:
        d = dict(r)
        d["is_alarm"] = True
        if d["threshold"]:
            d["exceed_ratio"] = round(d["current_value"] / d["threshold"], 1)
        result.append(d)
    return result


@mcp.tool()
async def get_incident_timeline() -> list[dict]:
    """返回事故快报时间线（2026-05-16 港城大道388号氨气泄漏事故）。

    Returns:
        时间线事件列表，每项包含 time/event/level/operator
    """
    return _INCIDENT_TIMELINE


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
