"""
高德地图 MCP Server

工具：
- plan_driving_route: 驾车路线规划（调用高德 REST API v3）
- geocode: 地理编码（地址 → 坐标）

需要环境变量 AMAP_API_KEY 或 AMAP_SERVICE_KEY（两者均可）。
优先读取进程环境，其次自动加载 backend/.env（dotenv）。
"""
from __future__ import annotations

import os
from pathlib import Path
import httpx
from mcp.server.fastmcp import FastMCP

# 自动加载 backend/.env（仅当环境变量未设置时补充）
_env_file = Path(__file__).parent.parent.parent.parent / "backend" / ".env"
if _env_file.exists() and not (os.environ.get("AMAP_API_KEY") or os.environ.get("AMAP_SERVICE_KEY")):
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_file, override=False)
    except ImportError:
        pass  # dotenv 未安装时跳过

mcp = FastMCP("amap", host="0.0.0.0", port=8106)

_AMAP_BASE = "https://restapi.amap.com/v3"


def _get_key() -> str:
    key = os.environ.get("AMAP_API_KEY", "") or os.environ.get("AMAP_SERVICE_KEY", "")
    if not key:
        raise RuntimeError("环境变量 AMAP_API_KEY 或 AMAP_SERVICE_KEY 未设置")
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


@mcp.tool()
async def text_search(keywords: str, city: str = "上海", page_size: int = 8) -> dict:
    """POI 文本搜索，用于事故地点消歧。

    Args:
        keywords: 搜索关键词（如"陆家嘴"、"世纪公园"）
        city: 城市名称（默认"上海"）
        page_size: 最多返回条数（默认8，最大25）

    Returns:
        dict: {"candidates": [{"name", "address", "lat", "lng", "type"}], "query", "city"}
    """
    key = _get_key()
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"{_AMAP_BASE}/place/text",
            params={
                "keywords": keywords,
                "city": city,
                "output": "json",
                "offset": min(page_size, 25),
                "key": key,
                "extensions": "base",
            },
        )
        r.raise_for_status()
        data = r.json()

    candidates: list[dict] = []
    for p in data.get("pois", []) or []:
        loc = p.get("location", "")
        if "," not in loc:
            continue
        try:
            lng_str, lat_str = loc.split(",", 1)
            candidates.append({
                "name":    p.get("name", ""),
                "address": p.get("address", "") or p.get("name", ""),
                "lat":     float(lat_str),
                "lng":     float(lng_str),
                "type":    p.get("type", ""),
            })
        except ValueError:
            continue

    return {"candidates": candidates, "query": keywords, "city": city}


@mcp.tool()
async def nearby_search(
    keywords: str,
    lat: float,
    lng: float,
    radius: int = 3000,
    page_size: int = 10,
) -> dict:
    """周边 POI 搜索（高德 /v3/place/around）。供 Agent 发现事故点附近的地点。

    Args:
        keywords: 搜索类型，如 "医院"/"消防局"/"学校"/"加油站"
        lat: 中心点纬度
        lng: 中心点经度
        radius: 搜索半径（米），最大 50000，默认 3000
        page_size: 最多返回条数，默认 10，最大 25

    Returns:
        {"pois": [{"name", "address", "lat", "lng", "type", "distance_m"}], "query", "center"}
    """
    key = _get_key()
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"{_AMAP_BASE}/place/around",
            params={
                "key": key,
                "location": f"{lng},{lat}",
                "keywords": keywords,
                "radius": min(radius, 50000),
                "output": "json",
                "offset": min(page_size, 25),
                "extensions": "base",
                "sortrule": "distance",
            },
        )
        r.raise_for_status()
        data = r.json()

    pois: list[dict] = []
    for p in data.get("pois", []) or []:
        loc = p.get("location", "")
        if "," not in loc:
            continue
        try:
            lng_str, lat_str = loc.split(",", 1)
            pois.append({
                "name":       p.get("name", ""),
                "address":    p.get("address", "") or p.get("name", ""),
                "lat":        float(lat_str),
                "lng":        float(lng_str),
                "type":       p.get("type", ""),
                "distance_m": int(p.get("distance", 0) or 0),
            })
        except (ValueError, TypeError):
            continue

    return {
        "pois": pois,
        "query": keywords,
        "center": {"lat": lat, "lng": lng},
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
