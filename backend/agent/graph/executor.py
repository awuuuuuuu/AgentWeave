"""
Executor 节点：确定性 MCP 写操作执行

两种触发方式（统一路径）：
  1. 普通会话：analyst 输出 HITL_REQUIRED + EXECUTION_INTENT，HITL 批准后 supervisor 路由此节点
  2. Weave 编排：execute_all_parallel 通过 A2A context.execution_intent 触发

与 Analyst 的区别：
  Analyst  — ReAct 探索，纯只读，适合研判阶段（路径未知）
  Executor — 直接执行，适合执行阶段（操作已确定，参数可能需要查询补全）
"""
from __future__ import annotations

import logging
from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool

from .map_extract import extract_map_update
from .state import AgentState

logger = logging.getLogger(__name__)

_TOOL_RESULT_LIMIT = 8_000

AGENT_CARD = {
    "name": "executor",
    "description": "确定性写操作执行：接收结构化执行意图，直接调用 MCP 写操作工具，无 LLM 介入",
    "tools": ["mcp_tools"],
    "icon": "⚡",
    "color": "red",
}


def build_executor() -> object:
    """构建 Executor 节点函数"""

    async def executor_node(state: AgentState) -> dict:
        dept_code = state.get("dept_code", "")
        mcp_connections = state.get("org_mcp_connections") or []
        execution_intent: dict | None = state.get("execution_intent")

        if not execution_intent:
            logger.warning("Executor [%s]: state 中无 execution_intent，跳过执行", dept_code)
            return {
                "messages": [AIMessage(
                    content="⚠️ 未收到结构化执行意图，跳过执行。",
                    name="executor",
                )],
                "executor_count": state.get("executor_count", 0) + 1,
            }

        tool_name = execution_intent.get("tool_name")
        params = dict(execution_intent.get("params") or {})

        if not tool_name:
            return {
                "messages": [AIMessage(
                    content="⚠️ execution_intent.tool_name 为空，跳过执行。",
                    name="executor",
                )],
                "executor_count": state.get("executor_count", 0) + 1,
            }

        # ── 加载 MCP 工具 ─────────────────────────────────────────────────────
        mcp_tools: list[BaseTool] = []
        mcp_client = None
        if mcp_connections:
            try:
                from langchain_mcp_adapters.client import MultiServerMCPClient
                servers = {
                    conn["name"]: {"url": conn["url"], "transport": "streamable_http"}
                    for conn in mcp_connections
                    if conn.get("name") and conn.get("url")
                }
                if servers:
                    mcp_client = MultiServerMCPClient(servers)
                    mcp_tools = await mcp_client.get_tools()
            except Exception as exc:
                logger.warning("Executor [%s]: MCP 工具加载失败: %s", dept_code, exc)

        mcp_tool_map: dict[str, BaseTool] = {t.name: t for t in mcp_tools}
        tool = mcp_tool_map.get(tool_name)

        if tool is None:
            logger.error("Executor [%s]: 工具 %r 不存在，可用: %s", dept_code, tool_name, list(mcp_tool_map))
            return {
                "messages": [AIMessage(
                    content=f"⚠️ 工具 {tool_name!r} 在 MCP Server 中不存在，执行失败。",
                    name="executor",
                )],
                "executor_count": state.get("executor_count", 0) + 1,
            }

        # ── 参数格式规范化：移除无效字段，补全缺失必填项 ──────────────────────
        params = _normalize_params(tool_name, params)

        # ── "auto" 占位符解析：运行时查询补全参数 ────────────────────────────
        if "auto" in params.values():
            params = await _resolve_auto_params(tool_name, params, mcp_tool_map)

        # ── 直接调用写操作工具（无 LLM） ─────────────────────────────────────
        _map_updates: list[dict] = []
        _mcp_sources: list[dict] = []

        await adispatch_custom_event(
            "analyst_tool_status",
            {"step": "tool_call", "tool_name": tool_name},
        )

        try:
            result = await tool.ainvoke(params)
            content = str(result)
            if len(content) > _TOOL_RESULT_LIMIT:
                content = content[:_TOOL_RESULT_LIMIT] + "…（结果过长已截断）"
            extract_map_update(tool_name, result, content, _map_updates, dept_code)
            logger.info("Executor [%s]: %r 调用成功，%d 字符", dept_code, tool_name, len(content))
        except Exception as exc:
            content = f"工具 {tool_name} 调用失败: {exc}"
            logger.warning("Executor [%s]: %r 调用失败: %s", dept_code, tool_name, exc)

        _mcp_sources.append({"idx": 1, "tool_name": tool_name, "key_result": content[:400]})
        await adispatch_custom_event(
            "analyst_tool_result",
            {"idx": 1, "tool_name": tool_name, "key_result": content[:400]},
        )

        return {
            "messages": [AIMessage(content=content, name="executor")],
            "map_updates": _map_updates,
            "mcp_sources": _mcp_sources,
            "executor_count": state.get("executor_count", 0) + 1,
            "execution_intent": None,
        }

    return executor_node


def _normalize_params(tool_name: str, params: dict) -> dict:
    """清理 params 中格式错误的字段，确保符合目标工具的 schema。

    LLM 生成 execution_params 时偶尔使用错误字段名（如 destination 字符串代替
    dest_lat/dest_lng），此函数在调用工具前做规范化处理。
    """
    p = dict(params)

    if tool_name == "dispatch_ambulance":
        # destination/priority 不是合法字段，始终移除
        p.pop("destination", None)
        p.pop("priority", None)
        # 确保缺失的必填浮点坐标标记为 "auto"（由 _resolve_auto_params 处理）
        if "dest_lat" not in p:
            p["dest_lat"] = "auto"
        if "dest_lng" not in p:
            p["dest_lng"] = "auto"
        if "patient_type" not in p:
            p["patient_type"] = "外伤"

    elif tool_name == "dispatch_fire_trucks":
        p.pop("destination", None)
        if "dest_lat" not in p:
            p["dest_lat"] = "auto"
        if "dest_lng" not in p:
            p["dest_lng"] = "auto"

    return p


async def _resolve_auto_params(
    tool_name: str,
    params: dict,
    mcp_tool_map: dict,
) -> dict:
    """将 params 中值为 "auto" 的字段通过只读查询补全为真实值（规则驱动，非 LLM）。"""
    resolved = dict(params)

    if tool_name == "dispatch_fire_trucks":
        if resolved.get("station_id") == "auto":
            query = mcp_tool_map.get("get_fire_stations")
            if query:
                try:
                    data = await query.ainvoke({})
                    stations = data if isinstance(data, list) else (data.get("stations") or [])
                    for s in stations:
                        if s.get("available_trucks", 0) > 0:
                            resolved["station_id"] = s["id"]
                            break
                except Exception:
                    pass
        if resolved.get("dest_lat") == "auto":
            resolved["dest_lat"] = 31.2380
        if resolved.get("dest_lng") == "auto":
            resolved["dest_lng"] = 121.4970

    elif tool_name == "dispatch_ambulance":
        # Resolve ambulance_id
        if resolved.get("ambulance_id") == "auto":
            query = mcp_tool_map.get("list_ambulances")
            if query:
                try:
                    data = await query.ainvoke({})
                    ambulances = data if isinstance(data, list) else (data.get("ambulances") or [])
                    for a in ambulances:
                        if a.get("status") == "待命":
                            resolved["ambulance_id"] = a["id"]
                            break
                except Exception:
                    pass
        # Resolve dest_lat/dest_lng from ambulance dest coords (last known incident location)
        if resolved.get("dest_lat") == "auto" or resolved.get("dest_lng") == "auto":
            query = mcp_tool_map.get("list_ambulances")
            if query:
                try:
                    data = await query.ainvoke({"status": "出车"})
                    ambulances = data if isinstance(data, list) else (data.get("ambulances") or [])
                    for a in ambulances:
                        if a.get("dest_lat") and a.get("dest_lng"):
                            resolved["dest_lat"] = float(a["dest_lat"])
                            resolved["dest_lng"] = float(a["dest_lng"])
                            break
                except Exception:
                    pass
            # Last resort: use Shanghai Pudong default coords
            if resolved.get("dest_lat") == "auto":
                resolved["dest_lat"] = 31.2380
            if resolved.get("dest_lng") == "auto":
                resolved["dest_lng"] = 121.4970

    elif tool_name == "set_mode" and resolved.get("intersection_id") == "auto":
        query = mcp_tool_map.get("list_intersections")
        if query:
            try:
                data = await query.ainvoke({})
                intersections = data if isinstance(data, list) else (data.get("intersections") or [])
                if intersections:
                    resolved["intersection_id"] = intersections[0]["id"]
            except Exception:
                pass

    elif tool_name == "allocate_standard_pack" and resolved.get("level") == "auto":
        resolved["level"] = "Ⅲ"  # 默认启用三级应急标准包

    return resolved
