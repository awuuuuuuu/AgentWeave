"""
高德地图 MCP Server

工具：
- plan_driving_route: 驾车路线规划（调用高德 REST API v3）
- geocode: 地理编码（地址 → 坐标）

需要环境变量 AMAP_API_KEY。
"""
from __future__ import annotations

import os
import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("amap", host="0.0.0.0", port=8106)

_AMAP_BASE = "https://restapi.amap.com/v3"


def _get_key() -> str:
    key = os.environ.get("AMAP_API_KEY", "")
    if not key:
        raise RuntimeError("环境变量 AMAP_API_KEY 未设置")
    return key


def _parse_polyline(polyline_str: str) -> list[list[float]]:
    """高德 polyline 字符串（'lng,lat;lng,lat;...'）→ [[lng,lat], ...]"""
    points = []
    for pair in polyline_str.split(";"):
        pair = pair.strip()
        if not pair:
            continue
        parts = pair.split(",")
        if len(parts) == 2:
            try:
                points.append([float(parts[0]), float(parts[1])])
            except ValueError:
                pass
    return points


@mcp.tool()
async def plan_driving_route(
    from_lat: float,
    from_lng: float,
    to_lat: float,
    to_lng: float,
) -> dict:
    """驾车路线规划。

    Args:
        from_lat: 出发地纬度
        from_lng: 出发地经度
        to_lat: 目的地纬度
        to_lng: 目的地经度

    Returns:
        dict 包含：duration_seconds, distance_m, polyline([[lng,lat], ...])
    """
    key = _get_key()
    origin = f"{from_lng},{from_lat}"
    destination = f"{to_lng},{to_lat}"

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{_AMAP_BASE}/direction/driving",
            params={
                "key": key,
                "origin": origin,
                "destination": destination,
                "output": "json",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    if data.get("status") != "1":
        raise RuntimeError(f"高德路线规划失败：{data.get('info', '未知错误')}")

    route = data["route"]
    path = route["paths"][0]
    duration = int(float(path["duration"]))
    distance = int(float(path["distance"]))

    # 拼合所有步骤的 polyline
    all_points: list[list[float]] = []
    for step in path.get("steps", []):
        polyline_str = step.get("polyline", "")
        all_points.extend(_parse_polyline(polyline_str))

    return {
        "from": {"lat": from_lat, "lng": from_lng},
        "to": {"lat": to_lat, "lng": to_lng},
        "duration_seconds": duration,
        "distance_m": distance,
        "polyline": all_points,
    }


@mcp.tool()
async def geocode(address: str) -> dict:
    """地理编码：将地址转换为坐标。

    Args:
        address: 地址字符串（如"天津市滨海新区港城大道388号"）

    Returns:
        dict 包含：lat, lng, formatted_address
    """
    key = _get_key()

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{_AMAP_BASE}/geocode/geo",
            params={
                "key": key,
                "address": address,
                "output": "json",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    if data.get("status") != "1" or not data.get("geocodes"):
        raise RuntimeError(f"地理编码失败：{data.get('info', '未知错误')}")

    geo = data["geocodes"][0]
    location = geo["location"]  # "lng,lat"
    lng_str, lat_str = location.split(",")

    return {
        "lat": float(lat_str),
        "lng": float(lng_str),
        "formatted_address": geo.get("formatted_address", address),
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
