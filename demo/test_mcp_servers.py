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

SERVERS = [
    {
        "name": "atmospheric_dispersion",
        "url": "http://localhost:8101/mcp",
        "tests": [
            {
                "tool": "calculate_plume",
                "args": {
                    "lat": 39.0205, "lng": 117.7432,
                    "wind_speed_ms": 3.0, "wind_dir_deg": 202,
                    "release_rate_gs": 100, "stability_class": "D",
                },
            },
            {
                "tool": "get_evacuation_direction",
                "args": {"wind_dir_deg": 202},
            },
        ],
    },
    {
        "name": "ambulance_dispatch",
        "url": "http://localhost:8102/mcp",
        "tests": [
            {"tool": "list_ambulances", "args": {"status": "待命"}},
            {"tool": "get_hospital_capacity", "args": {}},
        ],
    },
    {
        "name": "signal_control",
        "url": "http://localhost:8103/mcp",
        "tests": [
            {"tool": "list_intersections", "args": {}},
        ],
    },
    {
        "name": "warehouse",
        "url": "http://localhost:8104/mcp",
        "tests": [
            {"tool": "check_alerts", "args": {}},
            {"tool": "get_inventory", "args": {"category": "个人防护"}},
        ],
    },
    {
        "name": "enterprise_sensor",
        "url": "http://localhost:8105/mcp",
        "tests": [
            {"tool": "get_critical_alarms", "args": {}},
            {"tool": "get_incident_timeline", "args": {}},
        ],
    },
    {
        "name": "amap",
        "url": "http://localhost:8106/mcp",
        "tests": [],  # 需要 AMAP_API_KEY，跳过工具调用
    },
]


async def test_server(name: str, url: str, tests: list[dict]) -> bool:
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

        for test in tests:
            tool_name = test["tool"]
            tool = next((t for t in tools if t.name == tool_name), None)
            if tool is None:
                print(f"  [WARN] 工具 {tool_name} 不存在")
                continue
            try:
                result = await tool.ainvoke(test["args"])
                preview = str(result)[:200] + ("..." if len(str(result)) > 200 else "")
                print(f"  [OK] {tool_name}() -> {preview}")
            except Exception as e:
                print(f"  [WARN] {tool_name} 调用失败：{e}")

        return True
    except Exception as e:
        print(f"  [FAIL] 连接失败（是否已启动？）：{e}")
        return False


async def main() -> None:
    print("\n=== MCP Server HTTP 验证 ===\n")

    passed, failed = 0, 0
    for s in SERVERS:
        print(f"[{s['name']}]  {s['url']}")
        ok = await test_server(s["name"], s["url"], s["tests"])
        if ok:
            passed += 1
        else:
            failed += 1
        print()

    print(f"结果：{passed} 通过  {failed} 失败\n")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
