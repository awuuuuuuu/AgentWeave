"""
Analyst 节点：纯只读研判节点

通过部门 MCP 工具查询实时数据（传感器、调度、库存等），并对结果进行推理分析。
写操作由 executor 节点负责；analyst 仅负责研判。
"""
from __future__ import annotations

import json
import logging
import re as _re
from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

from .map_extract import extract_map_update
from .prompts import ANALYST_HITL_CONSTRAINT, ANALYST_SYSTEM
from .state import AgentState

logger = logging.getLogger(__name__)

_TOOL_RESULT_LIMIT = 8_000
ANALYST_CONTEXT_WINDOW = 8
_MAX_TOOL_ROUNDS = 5

AGENT_CARD = {
    "name": "analyst",
    "description": "实时工具调用与数据分析：通过部门 MCP 工具直接查询实时数据（传感器报警、救护车状态、库存、信号灯、路线规划），并对结果进行推理和结构化输出",
    "routing_hint": "纯实时状态查询（传感器读数、救护车位置、库存数量等）→ 直接路由；researcher 已检索到预案/规程内容后需要执行 MCP 工具 → 路由到 analyst；已有数据需进一步计算或分析 → 路由到 analyst",
    "tools": ["mcp_tools"],
    "icon": "📊",
    "color": "orange",
}


def build_analyst(llm_model: str = "gpt-4o") -> object:
    """构建 Analyst 节点函数（运行时按 org MCP 连接动态注入工具）"""
    llm = ChatOpenAI(model=llm_model, temperature=0)

    async def analyst_node(state: AgentState) -> dict:
        task = state.get("task", "")
        dept_code = state.get("dept_code", "")
        messages = state.get("messages", [])
        recent = messages[-ANALYST_CONTEXT_WINDOW:]
        mcp_connections = state.get("org_mcp_connections") or []

        # ── MCP 工具加载 ──────────────────────────────────────────────────────
        mcp_client = None
        mcp_tools: list[BaseTool] = []
        _write_op_tool_names: set[str] = set()

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
                    logger.info(
                        "Analyst: 已加载 %d 个 MCP 工具: %s",
                        len(mcp_tools), [t.name for t in mcp_tools],
                    )
            except Exception as exc:
                logger.warning("Analyst: MCP 工具加载失败: %s", exc)
                mcp_tools = []

        if not mcp_tools and mcp_connections:
            logger.warning(
                "Analyst: MCP 连接配置存在 %d 条但工具加载为空，返回错误提示",
                len(mcp_connections),
            )
            return {
                "messages": [AIMessage(
                    content="⚠️ 无法连接 MCP 工具服务，实时数据查询失败。请确认 MCP 服务已启动后重试。",
                    name="analyst",
                )],
                "analyst_count": state.get("analyst_count", 0) + 1,
            }

        # ── 始终以研判模式绑定工具（识别写操作工具名，用于 HITL 注入）──────────
        mcp_tool_map: dict[str, BaseTool] = {t.name: t for t in mcp_tools}
        if mcp_tools:
            from langchain_core.utils.function_calling import convert_to_openai_function
            for t in mcp_tools:
                try:
                    fn_def = convert_to_openai_function(t)
                    desc = fn_def.get("description", "") or ""
                    if "需经 HITL" in desc:
                        _write_op_tool_names.add(t.name)
                except Exception:
                    pass
            if _write_op_tool_names:
                logger.info("Analyst: 研判模式，写操作工具将在调用时被阻止: %s", _write_op_tool_names)
            llm_with_tools = llm.bind_tools(mcp_tools)
        else:
            llm_with_tools = llm

        # ── 构建 Analyst system prompt ────────────────────────────────────────
        analyst_prompt = ANALYST_SYSTEM
        org_analyst_context = state.get("org_analyst_context", "")
        if org_analyst_context:
            analyst_prompt += f"\n\n[本部门调用说明] {org_analyst_context}"

        if _write_op_tool_names:
            analyst_prompt += ANALYST_HITL_CONSTRAINT

        # ── 显式注入 Researcher 检索结论 ───────────────────────────────────────
        researcher_msgs = [
            m.content for m in messages
            if isinstance(m, AIMessage) and m.content and getattr(m, "name", "") == "researcher"
        ]
        task_prompt = task
        if researcher_msgs:
            task_prompt = (
                f"[知识库检索结论（请结合以下内容完成分析）]\n{researcher_msgs[-1]}\n\n"
                f"[当前分析任务]\n{task}"
            )

        # ── ReAct 多轮循环（纯只读） ───────────────────────────────────────────
        trajectory: list = [
            SystemMessage(content=analyst_prompt),
            *recent,
            HumanMessage(content=f"当前分析任务：{task_prompt}"),
        ]

        answer = ""
        _map_updates: list[dict] = []
        _mcp_sources: list[dict] = []
        _tool_call_idx = 0

        for _ in range(_MAX_TOOL_ROUNDS):
            resp: AIMessage = await llm_with_tools.ainvoke(trajectory)
            trajectory.append(resp)

            if not resp.tool_calls:
                answer = resp.content.strip()
                break

            tool_messages: list[ToolMessage] = []
            for tc in resp.tool_calls:
                tool_name = tc["name"]
                await adispatch_custom_event(
                    "analyst_tool_status",
                    {"step": "tool_call", "tool_name": tool_name},
                )
                if tool_name in mcp_tool_map:
                    if tool_name in _write_op_tool_names:
                        content = (
                            f"【系统阻止】{tool_name} 是写操作工具，研判模式下不可执行。"
                            f"若原始任务明确要求执行此操作，请在输出末尾写：\n"
                            f"【HITL_REQUIRED】待执行：{tool_name}（需 HITL 审批授权）\n"
                            f"【EXECUTION_INTENT】{{\"tool_name\": \"{tool_name}\", \"params\": {{}}}}"
                        )
                        logger.info("Analyst: 写操作工具 %r 在研判模式下被阻止", tool_name)
                    else:
                        try:
                            result = await mcp_tool_map[tool_name].ainvoke(tc["args"])
                            content = str(result)
                            logger.info("Analyst: MCP 工具 %r 返回 %d 字符", tool_name, len(content))
                            if len(content) > _TOOL_RESULT_LIMIT:
                                content = content[:_TOOL_RESULT_LIMIT] + f"\n…（结果过长已截断，原始 {len(content)} 字符）"
                            extract_map_update(tool_name, result, content, _map_updates, dept_code)
                        except Exception as exc:
                            content = f"工具 {tool_name} 调用失败: {exc}"
                            logger.warning("Analyst: MCP 工具 %r 调用失败: %s", tool_name, exc)
                else:
                    content = f"工具 {tool_name} 不可用"
                tool_messages.append(ToolMessage(content=content, tool_call_id=tc["id"]))

                _tool_call_idx += 1
                key_result = content[:400] + ("…" if len(content) > 400 else "")
                _mcp_sources.append({"idx": _tool_call_idx, "tool_name": tool_name, "key_result": key_result})
                await adispatch_custom_event(
                    "analyst_tool_result",
                    {"idx": _tool_call_idx, "tool_name": tool_name, "key_result": key_result},
                )

            trajectory.extend(tool_messages)
        else:
            answer = resp.content.strip() if resp.content else "（已达最大工具调用轮次）"
            logger.warning("Analyst: 达到最大工具调用轮次 %d，强制输出", _MAX_TOOL_ROUNDS)

        # ── 安全网：写操作被阻止但 HITL_REQUIRED 未注入，自动补全结构化意图 ────
        if "HITL_REQUIRED" not in answer and _write_op_tool_names:
            blocked_tools = [
                s for s in _mcp_sources if s["tool_name"] in _write_op_tool_names
            ]
            if blocked_tools:
                primary = blocked_tools[0]
                t_name = primary["tool_name"]
                intent_params = _extract_intent_params(primary.get("key_result", ""), t_name)
                intent_json = json.dumps({"tool_name": t_name, "params": intent_params}, ensure_ascii=False)
                answer += (
                    f"\n【HITL_REQUIRED】待执行：{t_name}"
                    f"\n【EXECUTION_INTENT】{intent_json}"
                )
                logger.info("Analyst: 安全网注入结构化 HITL_REQUIRED: %s", t_name)

        # ── 安全网：剥除无写操作支撑的幻觉 HITL_REQUIRED ──────────────────────
        if "HITL_REQUIRED" in answer:
            _called_write = {s["tool_name"] for s in _mcp_sources if s["tool_name"] in _write_op_tool_names}
            if not _called_write:
                answer = _re.sub(r'\n?【HITL_REQUIRED】[^\n]*', '', answer)
                answer = _re.sub(r'\n?【EXECUTION_INTENT】[^\n]*', '', answer)
                answer = answer.strip()
                logger.info("Analyst: 剥除幻觉 HITL_REQUIRED 标记（无写操作工具实际调用/阻止）")

        analyst_count = state.get("analyst_count", 0) + 1
        logger.info(
            "Analyst [%d]: 生成分析结果 %d 字，地图更新 %d 条，MCP 调用 %d 次",
            analyst_count, len(answer), len(_map_updates), len(_mcp_sources),
        )
        return {
            "messages": [AIMessage(content=answer, name="analyst")],
            "analyst_count": analyst_count,
            "map_updates": _map_updates,
            "mcp_sources": _mcp_sources,
        }

    return analyst_node


def _extract_intent_params(key_result: str, tool_name: str) -> dict:
    """从工具被阻止时的 key_result 文本尝试提取参数，无法提取时返回占位符。"""
    import re
    params: dict = {}
    for kv in re.findall(r'(\w+)[=:]\s*(["\w一-鿿]+)', key_result):
        params[kv[0]] = kv[1].strip('"\'')
    required: dict[str, list[str]] = {
        "dispatch_fire_trucks":  ["station_id", "truck_count"],
        "dispatch_ambulance":    ["ambulance_id", "dest_lat", "dest_lng"],
        "set_mode":              ["intersection_id", "mode"],
        "apply_evacuation_plan": ["level"],
        "allocate_standard_pack": ["level"],
        "allocate_custom":       ["items"],
        "recall_fire_trucks":    ["station_id", "truck_count"],
        "recall_ambulance":      ["ambulance_id"],
    }
    for field in required.get(tool_name, []):
        params.setdefault(field, "auto")
    return params
