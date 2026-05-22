"""
大气扩散 MCP Server（纯计算，无 DB）

工具：
- calculate_plume: 高斯烟羽扩散，返回 ERPG-1/2/3 半径和最大浓度
- get_evacuation_direction: 根据风向返回疏散方向和优先路口

Pasquill-Gifford 稳定度分类（σ 参数化）：
  A: σy=0.22x(1+0.0001x)^-0.5, σz=0.20x
  B: σy=0.16x(1+0.0001x)^-0.5, σz=0.12x
  C: σy=0.11x(1+0.0001x)^-0.5, σz=0.08x(1+0.0002x)^-0.5
  D: σy=0.08x(1+0.0001x)^-0.5, σz=0.06x(1+0.0015x)^-0.5
  E: σy=0.06x(1+0.0001x)^-0.5, σz=0.03x(1+0.0003x)^-1
  F: σy=0.04x(1+0.0001x)^-0.5, σz=0.016x(1+0.0003x)^-1

氨气 ERPG 浓度标准（AIHA 2024）：
  ERPG-1: 25 ppm（感知阈，需疏散）
  ERPG-2: 150 ppm（不可逆伤害）
  ERPG-3: 750 ppm（危及生命）

氨气分子量 MW=17.03 g/mol，25℃ 下 1 ppm = 0.703 mg/m³
"""
from __future__ import annotations

import math
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("atmospheric_dispersion", host="0.0.0.0", port=8101)

# ERPG 浓度阈值（mg/m³）
_ERPG_MG = {
    "ERPG1": 25 * 0.703,    # 17.575 mg/m³
    "ERPG2": 150 * 0.703,   # 105.45 mg/m³
    "ERPG3": 750 * 0.703,   # 527.25 mg/m³
}

# Pasquill-Gifford σ 参数（a, b for σy；c, d for σz）
# σy = a * x * (1 + 0.0001*x)^b
# σz = c * x * (1 + 0.0015*x)^d  (D 类示例)
_PG_PARAMS: dict[str, dict] = {
    "A": dict(ay=0.22, by=-0.5, az=0.20, bz=0.0),
    "B": dict(ay=0.16, by=-0.5, az=0.12, bz=0.0),
    "C": dict(ay=0.11, by=-0.5, az=0.08, bz=-0.5),
    "D": dict(ay=0.08, by=-0.5, az=0.06, bz=-0.5),
    "E": dict(ay=0.06, by=-0.5, az=0.03, bz=-1.0),
    "F": dict(ay=0.04, by=-0.5, az=0.016, bz=-1.0),
}

# 路口 ID 按距事故点（港城大道388号）距离排序
_INTERSECTION_ORDER = ["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"]


def _sigma(x: float, a: float, b: float, factor: float = 0.0001) -> float:
    """σ = a * x * (1 + factor*x)^b，x 单位米"""
    return a * x * math.pow(1.0 + factor * x, b)


def _plume_concentration(x: float, y: float, u: float, Q: float,
                         sigma_y: float, sigma_z: float) -> float:
    """高斯烟羽地面中心线浓度（mg/m³），假设地面完全反射"""
    if u <= 0 or sigma_y <= 0 or sigma_z <= 0:
        return 0.0
    c = (Q / (math.pi * sigma_y * sigma_z * u)) * math.exp(-0.5 * (y / sigma_y) ** 2)
    return c


def _find_radius(erpg_mg: float, u: float, Q: float,
                 ay: float, by: float, az: float, bz: float) -> float:
    """二分法求中心线上达到 ERPG 浓度的距离（米）"""
    lo, hi = 1.0, 50000.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        sy = _sigma(mid, ay, by)
        sz = _sigma(mid, az, bz, factor=0.0015)
        c = _plume_concentration(mid, 0.0, u, Q, sy, sz)
        if c > erpg_mg:
            lo = mid
        else:
            hi = mid
    return round(hi, 1)


@mcp.tool()
def calculate_plume(
    lat: float,
    lng: float,
    wind_speed_ms: float,
    wind_dir_deg: float,
    release_rate_gs: float,
    stability_class: str = "D",
) -> dict:
    """高斯烟羽扩散计算，输出 ERPG-1/2/3 疏散半径。
    风速/风向参数请先调用 get_sensor_readings(sensor_type="风速"/"风向") 获取实测值。

    Args:
        lat: 泄漏点纬度
        lng: 泄漏点经度
        wind_speed_ms: 风速（m/s），来自 sensor_type="风速" 的 current_value
        wind_dir_deg: 风向（°，气象方向，0=北风即风从北吹向南），来自 sensor_type="风向" 的 current_value
        release_rate_gs: 泄漏速率（g/s），可由泄漏量估算或从事故快报获取
        stability_class: Pasquill-Gifford 稳定度（A-F，默认 D；白天晴天用 B/C，夜间用 E/F）

    Returns:
        dict 包含：erpg1_radius_m, erpg2_radius_m, erpg3_radius_m,
                   plume_axis_deg（烟羽轴向，即风的下风向）,
                   max_concentration_ppm（泄漏点下风10m处）,
                   center（事故点坐标）
    """
    sc = stability_class.upper()
    if sc not in _PG_PARAMS:
        sc = "D"
    p = _PG_PARAMS[sc]
    u = max(wind_speed_ms, 0.5)  # 避免除零

    # 烟羽轴向 = 风来向的反方向（下风向）
    plume_axis = (wind_dir_deg + 180) % 360

    # 求各 ERPG 半径
    r1 = _find_radius(_ERPG_MG["ERPG1"], u, release_rate_gs, **p)
    r2 = _find_radius(_ERPG_MG["ERPG2"], u, release_rate_gs, **p)
    r3 = _find_radius(_ERPG_MG["ERPG3"], u, release_rate_gs, **p)

    # 泄漏点下风10m处最大浓度
    sy10 = _sigma(10.0, p["ay"], p["by"])
    sz10 = _sigma(10.0, p["az"], p["bz"], factor=0.0015)
    c10_mg = _plume_concentration(10.0, 0.0, u, release_rate_gs, sy10, sz10)
    c10_ppm = round(c10_mg / 0.703, 1)

    return {
        "center": {"lat": lat, "lng": lng},
        "erpg1_radius_m": r1,
        "erpg2_radius_m": r2,
        "erpg3_radius_m": r3,
        "plume_axis_deg": plume_axis,
        "max_concentration_ppm": c10_ppm,
        "stability_class": sc,
        "wind_speed_ms": u,
        "wind_dir_deg": wind_dir_deg,
    }


@mcp.tool()
def estimate_release_rate(
    concentration_ppm: float,
    distance_m: float,
    wind_speed_ms: float,
    stability_class: str = "D",
) -> dict:
    """用中心线高斯反算法，由已知点浓度估算泄漏速率。
    适用场景：已知某传感器读数（ppm）及其距泄漏点距离，即可反推 release_rate_gs，
    再传入 calculate_plume 计算 ERPG 疏散半径。

    Args:
        concentration_ppm: 传感器氨气浓度读数（ppm）
        distance_m: 传感器距泄漏点距离（米），从 get_sensor_readings 的 location 字段解析
        wind_speed_ms: 风速（m/s），来自 get_sensor_readings(sensor_type='风速')
        stability_class: Pasquill-Gifford 稳定度（A-F，默认 D）

    Returns:
        dict 包含：release_rate_gs（估算泄漏速率 g/s），concentration_mg_m3，
                   distance_m，note（计算说明）
    """
    sc = stability_class.upper()
    if sc not in _PG_PARAMS:
        sc = "D"
    p = _PG_PARAMS[sc]
    u = max(wind_speed_ms, 0.5)

    c_mg = concentration_ppm * 0.703  # ppm → mg/m³（氨气 25℃）
    x = max(distance_m, 1.0)
    sy = _sigma(x, p["ay"], p["by"])
    sz = _sigma(x, p["az"], p["bz"], factor=0.0015)

    # 中心线 y=0：c = Q / (π·σy·σz·u)  →  Q = c·π·σy·σz·u
    if sy <= 0 or sz <= 0:
        q_gs = 0.0
    else:
        q_gs = c_mg * math.pi * sy * sz * u

    return {
        "release_rate_gs": round(q_gs, 2),
        "concentration_ppm": concentration_ppm,
        "concentration_mg_m3": round(c_mg, 2),
        "distance_m": x,
        "wind_speed_ms": u,
        "stability_class": sc,
        "note": (
            f"基于 {distance_m}m 处 {concentration_ppm} ppm 实测浓度，"
            f"高斯反算估计泄漏速率 ≈ {round(q_gs, 1)} g/s。"
            "请将此值作为 calculate_plume 的 release_rate_gs 参数。"
        ),
    }


@mcp.tool()
def get_evacuation_direction(wind_dir_deg: float) -> dict:
    """根据风向确定疏散方向和优先控制路口。

    Args:
        wind_dir_deg: 风向（°，气象方向）

    Returns:
        dict 包含：evacuate_toward（疏散朝向描述）,
                   evacuate_bearing（疏散方位角）,
                   priority_intersections（优先管控路口列表）
    """
    # 下风向（烟羽扩散方向）
    downwind = (wind_dir_deg + 180) % 360
    # 疏散方向 = 上风向（垂直于下风向的两侧）
    evac_bearing_1 = (wind_dir_deg + 90) % 360
    evac_bearing_2 = (wind_dir_deg + 270) % 360

    def bearing_to_name(b: float) -> str:
        dirs = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]
        idx = round(b / 45) % 8
        return dirs[idx]

    downwind_name = bearing_to_name(downwind)
    evac_name_1 = bearing_to_name(evac_bearing_1)
    evac_name_2 = bearing_to_name(evac_bearing_2)

    # 根据风向选择需要优先封控的路口（靠近下风向的路口）
    # 风向 202° (南偏西) → 下风向 22°（东北）→ S4、S1 在下风区
    # 简化逻辑：优先管控下风向象限内的路口
    if 135 <= downwind < 225:   # 下风向偏南
        priority = ["S3", "S6", "S7"]
    elif 225 <= downwind < 315:  # 下风向偏西
        priority = ["S5", "S6", "S8"]
    elif 315 <= downwind or downwind < 45:   # 下风向偏北
        priority = ["S1", "S4", "S2"]
    else:                        # 下风向偏东
        priority = ["S2", "S3", "S5"]

    return {
        "wind_dir_deg": wind_dir_deg,
        "downwind_bearing": downwind,
        "downwind_direction": downwind_name,
        "evacuate_toward": f"{evac_name_1}或{evac_name_2}方向（垂直于下风向）",
        "evacuate_bearing_options": [evac_bearing_1, evac_bearing_2],
        "priority_intersections": priority,
        "note": f"禁止向{downwind_name}方向疏散（下风向），应急车辆从上风向（{bearing_to_name(wind_dir_deg)}方向）进入",
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
