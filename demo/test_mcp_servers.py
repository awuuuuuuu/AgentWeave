"""
MCP Server HTTP 快速验证脚本

测试所有 6 个 MCP Server 的工具列表和部分工具调用（HTTP transport）。
运行前先启动各 MCP Server（见 README 或计划文档）。

用法：
    uv run python demo/test_mcp_servers.py
"""
from __future__ import annotations

import asyncio
import sys

# 世纪大道×陆家嘴环路（火灾事故点）
_INCIDENT_LAT = 31.2380
_INCIDENT_LNG = 121.4970

SERVERS = [
    {
        "name": "ambulance_dispatch",
        "url": "http://localhost:8102/mcp",
        "tests": [
            {"tool": "list_ambulances", "args": {"status": "待命"}},
            {"tool": "list_ambulances", "args": {}},                        # 全量列表
            {"tool": "get_hospital_capacity", "args": {}},
            # list_ambulances 结果验证：应有可用救护车
            {"tool": "list_ambulances", "args": {"status": "待命"}, "assert": {
                "type": "list_nonempty",
                "desc": "应有至少 1 辆待命救护车",
            }},
            # 验证医院数量（seed_shanghai_real_data.py 导入后应 ≥ 30 家；未 seed 时软警告）
            {"tool": "get_hospital_capacity", "args": {}, "assert": {
                "type": "list_min_length",
                "min_count": 30,
                "soft": True,
                "desc": "医院总数应 ≥ 30（运行 seed_shanghai_real_data.py 后满足）",
            }},
        ],
    },
    {
        "name": "signal_control",
        "url": "http://localhost:8103/mcp",
        "tests": [
            {"tool": "list_intersections", "args": {}},
            # 验证路口数据含坐标
            {"tool": "list_intersections", "args": {}, "assert": {
                "type": "items_have_keys",
                "keys": ["id", "name", "lat", "lng", "mode"],
                "desc": "路口列表每项必须含 id/name/lat/lng/mode",
            }},
            # 周边路口查询（事故坐标附近 3km）
            {"tool": "get_nearby_intersections", "args": {
                "lat": _INCIDENT_LAT, "lng": _INCIDENT_LNG, "radius_km": 3.0,
            }},
            # 验证周边路口含距离字段
            {"tool": "get_nearby_intersections", "args": {
                "lat": _INCIDENT_LAT, "lng": _INCIDENT_LNG, "radius_km": 3.0,
            }, "assert": {
                "type": "items_have_keys",
                "keys": ["id", "name", "lat", "lng", "mode", "distance_km"],
                "desc": "周边路口列表每项必须含 distance_km（按距离排序依据）",
            }},
        ],
    },
    {
        "name": "warehouse",
        "url": "http://localhost:8104/mcp",
        "tests": [
            {"tool": "check_alerts", "args": {}},
            {"tool": "get_inventory", "args": {"category": "个人防护"}},
            {"tool": "get_inventory", "args": {}},                           # 全量库存（WH1）
            # 验证仓库信息含坐标（来自 DB，非硬编码）
            {"tool": "get_inventory", "args": {}, "assert": {
                "type": "dict_has_key",
                "key": "warehouse",
                "desc": "get_inventory 应返回 warehouse 字段（含仓库坐标）",
            }},
            # 验证仓库信息含 map_marker（供地图标注仓库位置）
            {"tool": "get_inventory", "args": {}, "assert": {
                "type": "dict_key_nested_key",
                "outer_key": "warehouse",
                "inner_key": "map_marker",
                "desc": "get_inventory.warehouse 应包含 map_marker（供地图标注仓库位置）",
            }},
            # 按 warehouse_id 过滤（WH2：徐汇仓库）
            {"tool": "get_inventory", "args": {"warehouse_id": "WH2"}},
            {"tool": "get_inventory", "args": {"warehouse_id": "WH2"}, "assert": {
                "type": "dict_has_key",
                "key": "warehouse",
                "desc": "get_inventory(warehouse_id=WH2) 应返回 warehouse 字段",
            }},
            # 全仓库列表（按距离排序）
            {"tool": "list_warehouses", "args": {"lat": _INCIDENT_LAT, "lng": _INCIDENT_LNG}},
            {"tool": "list_warehouses", "args": {"lat": _INCIDENT_LAT, "lng": _INCIDENT_LNG}, "assert": {
                "type": "items_have_keys",
                "keys": ["id", "name", "lat", "lng", "distance_km", "map_marker"],
                "desc": "仓库列表每项必须含 id/name/lat/lng/distance_km/map_marker",
            }},
        ],
    },
    {
        "name": "environment_sensor",
        "url": "http://localhost:8105/mcp",
        "tests": [
            {"tool": "get_critical_alarms", "args": {}},
            {"tool": "get_sensor_readings", "args": {"sensor_type": "烟雾"}},
            {"tool": "get_sensor_readings", "args": {"sensor_type": "PM2.5"}},
            {"tool": "get_sensor_readings", "args": {"sensor_type": "一氧化碳"}},
            # 验证传感器数据含坐标
            {"tool": "get_sensor_readings", "args": {"sensor_type": "烟雾"}, "assert": {
                "type": "items_have_keys",
                "keys": ["id", "lat", "lng", "current_value"],
                "desc": "传感器读数必须含 lat/lng 坐标",
            }},
            # 验证告警传感器含 map_marker
            {"tool": "get_critical_alarms", "args": {}, "assert": {
                "type": "items_have_keys",
                "keys": ["id", "lat", "lng", "map_marker"],
                "desc": "告警传感器列表每项必须含 map_marker（供地图标注告警位置）",
            }},
        ],
    },
    {
        "name": "amap",
        "url": "http://localhost:8106/mcp",
        # 工具调用需要 AMAP_API_KEY；此处仅验证工具列表可获取且包含必要工具
        "tests": [],
        "tool_list_assert": {
            "required_tools": ["plan_driving_route", "text_search", "nearby_search"],
            "desc": "amap MCP 应暴露 plan_driving_route / text_search / nearby_search 工具",
        },
    },
    {
        "name": "fire_station",
        "url": "http://localhost:8107/mcp",
        "tests": [
            {"tool": "get_fire_stations", "args": {"lat": _INCIDENT_LAT, "lng": _INCIDENT_LNG}},
            {"tool": "get_fire_stations", "args": {"lat": _INCIDENT_LAT, "lng": _INCIDENT_LNG, "radius_km": 20}},
            # 验证全市消防站总数（无坐标过滤 = 返回全部）
            {"tool": "get_fire_stations", "args": {}, "assert": {
                "type": "dict_key_min_items",
                "dict_key": "stations",
                "min_count": 30,
                "soft": True,
                "desc": "消防站总数应 ≥ 30（运行 seed_shanghai_real_data.py 后满足）",
            }},
            {"tool": "get_water_supplies", "args": {"lat": _INCIDENT_LAT, "lng": _INCIDENT_LNG}},
            {"tool": "get_water_supplies", "args": {"lat": _INCIDENT_LAT, "lng": _INCIDENT_LNG, "radius_km": 10}},
            {"tool": "set_fire_perimeter", "args": {
                "center_lat": _INCIDENT_LAT, "center_lng": _INCIDENT_LNG, "radius_m": 300,
            }},
            # 验证消防站含坐标（来自 DB）
            {"tool": "get_fire_stations", "args": {"lat": _INCIDENT_LAT, "lng": _INCIDENT_LNG}, "assert": {
                "type": "dict_key_items_have_keys",
                "dict_key": "stations",
                "keys": ["id", "name", "lat", "lng", "available_trucks"],
                "desc": "消防站列表每项必须含 id/name/lat/lng/available_trucks",
            }},
            # 验证消防水源含坐标（来自 DB）
            {"tool": "get_water_supplies", "args": {"lat": _INCIDENT_LAT, "lng": _INCIDENT_LNG, "radius_km": 10}, "assert": {
                "type": "dict_key_items_have_keys",
                "dict_key": "supplies",
                "keys": ["id", "name", "lat", "lng", "capacity_tons"],
                "desc": "消防水源列表每项必须含 id/name/lat/lng/capacity_tons",
            }},
        ],
    },
]


def _check_assert(result: object, spec: dict) -> tuple[bool, str]:
    """执行测试断言，返回 (passed, message)。
    soft=True：断言失败时返回警告而非失败（用于需要 seed 脚本的数量断言）。
    """
    atype = spec.get("type", "")
    desc = spec.get("desc", "")
    soft = spec.get("soft", False)

    if atype == "list_nonempty":
        ok = isinstance(result, list) and len(result) > 0
        return ok, f"{'✓' if ok else '✗'} {desc}"

    if atype == "items_have_keys":
        keys = spec.get("keys", [])
        if not isinstance(result, list):
            return False, f"✗ {desc}（结果不是列表，实际：{type(result).__name__}）"
        if not result:
            return True, f"  (空列表，跳过 key 验证)"
        missing = [k for item in result for k in keys if k not in (item if isinstance(item, dict) else {})]
        ok = len(missing) == 0
        return ok, f"{'✓' if ok else '✗'} {desc}" + (f"（缺少字段：{list(set(missing))}）" if not ok else "")

    if atype == "dict_has_key":
        key = spec.get("key", "")
        ok = isinstance(result, dict) and key in result
        return ok, f"{'✓' if ok else '✗'} {desc}" + (f"（缺少键 '{key}'）" if not ok else "")

    if atype == "dict_key_items_have_keys":
        dict_key = spec.get("dict_key", "")
        keys = spec.get("keys", [])
        if not isinstance(result, dict):
            return False, f"✗ {desc}（结果不是 dict）"
        items = result.get(dict_key, [])
        if not items:
            return True, f"  ({dict_key} 为空，跳过 key 验证)"
        missing = [k for item in items for k in keys if k not in (item if isinstance(item, dict) else {})]
        ok = len(missing) == 0
        return ok, f"{'✓' if ok else '✗'} {desc}" + (f"（缺少字段：{list(set(missing))}）" if not ok else "")

    if atype == "dict_key_nested_key":
        outer_key = spec.get("outer_key", "")
        inner_key = spec.get("inner_key", "")
        if not isinstance(result, dict):
            return False, f"✗ {desc}（结果不是 dict）"
        outer = result.get(outer_key)
        if outer is None or not isinstance(outer, dict):
            return False, f"✗ {desc}（键 '{outer_key}' 不存在或不是 dict）"
        ok = inner_key in outer
        return ok, f"{'✓' if ok else '✗'} {desc}" + (f"（{outer_key}.{inner_key} 不存在）" if not ok else "")

    if atype == "list_min_length":
        min_count = spec.get("min_count", 1)
        if not isinstance(result, list):
            return False, f"✗ {desc}（结果不是列表）"
        ok = len(result) >= min_count
        msg = f"{'✓' if ok else ('⚠' if soft else '✗')} {desc}（实际 {len(result)} 条）"
        return (True if soft else ok), msg

    if atype == "dict_key_min_items":
        dict_key = spec.get("dict_key", "")
        min_count = spec.get("min_count", 1)
        if not isinstance(result, dict):
            return False, f"✗ {desc}（结果不是 dict）"
        items = result.get(dict_key, [])
        ok = len(items) >= min_count
        msg = f"{'✓' if ok else ('⚠ [soft]' if soft else '✗')} {desc}（实际 {len(items)} 条）"
        return (True if soft else ok), msg

    return True, f"  (未知断言类型 {atype!r}，跳过)"


def _parse_mcp_result(raw: object) -> object:
    """
    langchain_mcp_adapters 将 MCP 响应包装为 list[{'type':'text','text':'...JSON...'}]。
    将每个 text block 解析为 Python 对象：
    - 多个 block → list（每 block 一个元素）
    - 单个 block 且解析结果为 dict → 直接返回该 dict
    """
    import json
    if not isinstance(raw, list):
        return raw
    parsed = []
    for item in raw:
        if isinstance(item, dict) and item.get("type") == "text":
            try:
                parsed.append(json.loads(item["text"]))
            except (json.JSONDecodeError, KeyError):
                parsed.append(item)
        else:
            parsed.append(item)
    if len(parsed) == 1 and isinstance(parsed[0], (dict, list)):
        return parsed[0]
    return parsed


async def test_server(name: str, url: str, tests: list[dict], tool_list_assert: dict | None = None) -> bool:
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError:
        print("  [ERROR] langchain-mcp-adapters 未安装")
        return False

    try:
        client = MultiServerMCPClient(
            {name: {"url": url, "transport": "streamable_http"}}
        )
        tools = await client.get_tools()
        tool_names = [t.name for t in tools]
        print(f"  [OK] 工具列表（{len(tool_names)} 个）：{', '.join(tool_names)}")

        # 验证工具列表中必须包含的工具名
        if tool_list_assert:
            required = tool_list_assert.get("required_tools", [])
            desc = tool_list_assert.get("desc", "")
            missing_tools = [t for t in required if t not in tool_names]
            if missing_tools:
                print(f"  [FAIL] {desc}（缺少工具：{missing_tools}）")
                return False
            else:
                print(f"  [OK]  {desc}")

        # 去重：同一 tool+args 组合只调用一次，但所有 assert 都执行
        seen: dict[str, object] = {}  # key: f"{tool}:{args}" → result
        assert_failures = 0

        for test in tests:
            tool_name = test["tool"]
            tool = next((t for t in tools if t.name == tool_name), None)
            if tool is None:
                print(f"  [WARN] 工具 {tool_name} 不存在")
                continue

            cache_key = f"{tool_name}:{test['args']}"
            if cache_key not in seen:
                try:
                    raw = await tool.ainvoke(test["args"])
                    result = _parse_mcp_result(raw)
                    seen[cache_key] = result
                    preview = str(result)[:200] + ("..." if len(str(result)) > 200 else "")
                    print(f"  [OK] {tool_name}() -> {preview}")
                except Exception as e:
                    print(f"  [WARN] {tool_name} 调用失败：{e}")
                    seen[cache_key] = None

            if "assert" in test and seen.get(cache_key) is not None:
                passed, msg = _check_assert(seen[cache_key], test["assert"])
                print(f"       {msg}")
                if not passed:
                    assert_failures += 1

        return assert_failures == 0
    except BaseException as e:
        # 展开 Python 3.11+ ExceptionGroup，暴露真实子异常
        if hasattr(e, "exceptions"):
            for sub in e.exceptions:  # type: ignore[attr-defined]
                print(f"  [FAIL] 真实错误：{type(sub).__name__}: {sub}")
        else:
            print(f"  [FAIL] 连接失败（是否已启动？）：{e}")
        return False


async def main() -> None:
    print("\n=== MCP Server HTTP 验证 ===\n")

    passed, failed = 0, 0
    for s in SERVERS:
        print(f"[{s['name']}]  {s['url']}")
        ok = await test_server(s["name"], s["url"], s["tests"], s.get("tool_list_assert"))
        if ok:
            passed += 1
        else:
            failed += 1
        print()

    print(f"结果：{passed} 通过  {failed} 失败\n")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
