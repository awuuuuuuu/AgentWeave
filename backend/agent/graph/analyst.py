"""
Analyst 节点：实时工具调用与数据分析

通过部门 MCP 工具查询实时数据（传感器、调度、库存等），并对结果进行推理分析。
MCP client 在整个节点执行期间保持存活，避免连接被 GC 提前回收。
"""
from __future__ import annotations

import logging
from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

from .map_extract import extract_map_update
from .prompts import ANALYST_SYSTEM
from .state import AgentState

logger = logging.getLogger(__name__)

AGENT_CARD = {
    "name": "analyst",
    "description": "实时工具调用与数据分析：通过部门 MCP 工具直接查询实时数据（传感器报警、救护车状态、库存、信号灯、路线规划），并对结果进行推理和结构化输出",
    "routing_hint": "纯实时状态查询（传感器读数、救护车位置、库存数量等）→ 直接路由；researcher 已检索到预案/规程内容后需要执行 MCP 工具 → 路由到 analyst；已有数据需进一步计算或分析 → 路由到 analyst",
    "tools": ["mcp_tools"],
    "icon": "📊",
    "color": "orange",
}

ANALYST_CONTEXT_WINDOW = 8
_MAX_TOOL_ROUNDS = 5   # 防止 ReAct 无限循环


def build_analyst(llm_model: str = "gpt-4o") -> object:
    """构建 Analyst 节点函数（运行时按 org MCP 连接动态注入工具）"""
    llm = ChatOpenAI(model=llm_model, temperature=0)

    async def analyst_node(state: AgentState) -> dict:
        task = state.get("task", "")
        dept_code = state.get("dept_code", "")
        messages = state.get("messages", [])
        recent = messages[-ANALYST_CONTEXT_WINDOW:]
        mcp_connections = state.get("org_mcp_connections") or []

        # 执行模式检测（三重来源，优先级从高到低）：
        # 1. state["task"] —— supervisor 在 HITL 批准后写入（单部门直接调用场景）
        # 2. HumanMessage  —— Weave Orchestrator 通过 A2A 发送的任务消息
        # 3. hitl AIMessage —— 本轮（最后一条 HumanMessage 之后）已完成 HITL，说明已获授权
        #    注意：只检查本轮范围，避免历史会话的 hitl 消息污染后续轮次的执行模式判断
        last_human_idx = max(
            (i for i, m in enumerate(messages) if isinstance(m, HumanMessage)),
            default=-1,
        )
        msgs_this_turn = messages[last_human_idx + 1:] if last_human_idx >= 0 else []
        _hitl_done = any(
            isinstance(m, AIMessage) and getattr(m, "name", "") == "hitl"
            for m in msgs_this_turn
        )
        is_execution_task = (
            "【执行阶段】" in (state.get("task") or "")
            or any("【执行阶段】" in str(m.content) for m in messages if isinstance(m, HumanMessage))
            or _hitl_done
        )
        if is_execution_task:
            task = f"【执行阶段】已授权：{task}"

        # ── MCP 工具加载（client 保持在当前作用域，生命周期覆盖整个 node）──────
        mcp_client = None
        mcp_tools: list[BaseTool] = []
        _write_op_tool_names: set[str] = set()  # 执行阶段：记录 HITL 写操作工具名，用于安全网检测

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

        # MCP 连接已配置但工具加载为空：直接报错，不降级为无工具模式。
        # 降级会让 LLM 输出"我要做什么"的计划文本，看起来像执行但什么都没发生，误导用户。
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

        # ── bind_tools：执行阶段在 schema 层面移除 HITL 限制 ────────────────────
        # model_copy / setattr 对 MCP adapter 工具类不可靠；
        # 直接修改传给 bind_tools 的 schema dict，LLM 看到的就是授权后的描述。
        # 工具执行仍走 mcp_tool_map（原始对象），两者解耦。
        mcp_tool_map: dict[str, BaseTool] = {t.name: t for t in mcp_tools}
        if mcp_tools:
            if is_execution_task:
                from langchain_core.utils.function_calling import convert_to_openai_function
                patched_schemas: list = []
                for t in mcp_tools:
                    try:
                        fn_def = convert_to_openai_function(t)
                        desc = fn_def.get("description", "") or ""
                        if "需经 HITL" in desc:
                            _write_op_tool_names.add(t.name)
                            fn_def = dict(fn_def)
                            fn_def["description"] = desc.replace(
                                "需经 HITL 审批后执行", "已获上层指挥中心授权，可直接调用"
                            )
                        patched_schemas.append({"type": "function", "function": fn_def})
                    except Exception:
                        patched_schemas.append(t)  # 降级：直接传原始工具
                logger.info("Analyst: 执行阶段写操作工具（schema 已脱敏）: %s", list(_write_op_tool_names))
                llm_with_tools = llm.bind_tools(patched_schemas)
            else:
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

        # ── 构建 Analyst system prompt（通用 + 部门专属补充）──────────────────
        analyst_prompt = ANALYST_SYSTEM
        org_analyst_context = state.get("org_analyst_context", "")
        if org_analyst_context:
            analyst_prompt += f"\n\n[本部门调用说明] {org_analyst_context}"

        # 执行阶段：在 system prompt 追加强制执行规则，防止 LLM 在数据查询后直接输出建议
        if is_execution_task:
            analyst_prompt += (
                "\n\n**执行阶段强制规则**："
                "本次任务含「【执行阶段】已授权」，HITL 批准已完成，所有标注 ⚠️ 的写操作工具均已解锁，可直接调用。"
                "完成查询/评估后，必须紧接调用对应写操作工具执行实际操作，不得仅输出建议或评估结果。"
                "若任务未明确参数，根据当前上下文合理推断后直接调用。"
                "即使资源部分不足，也必须调用写操作工具尝试执行（系统会返回实际履行结果），"
                "不得因「资源不足」直接输出无法执行的结论而跳过调用。"
            )
        else:
            if _write_op_tool_names:
                analyst_prompt += (
                    "\n\n**研判/评估模式约束**（写操作工具在执行时将被系统阻止）：\n"
                    "完成只读数据查询后输出评估结果。\n"
                    "仅当用户任务本身明确要求**立即执行**写操作时（如含「办理调拨出库」「批量设置路口」「派遣救护车」「切换信号」等执行动词），"
                    "才在输出的最后一行（另起一行）写：\n"
                    "【HITL_REQUIRED】待执行：<操作名称和关键参数>（需 HITL 审批授权）\n"
                    "若任务仅要求查询、评估或建议（如「给出建议」「分析是否满足」「查询状态」），不得添加此标记。"
                )

        # ── 显式注入 Researcher 检索结论（避免 Analyst 忽略已有 KB 知识）────────
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
        if is_execution_task:
            task_prompt += (
                "\n\n[执行授权] HITL 已批准，写操作工具已解锁。"
                "查询完资源/状态后必须立即调用写操作工具完成实际执行，不得仅输出建议。"
            )

        # ── ReAct 多轮循环：支持工具间串行依赖（如先查传感器再算扩散半径）────
        trajectory: list = [
            SystemMessage(content=analyst_prompt),
            *recent,
            HumanMessage(content=f"当前分析任务：{task_prompt}"),
        ]

        answer = ""
        _map_updates: list[dict] = []
        _mcp_sources: list[dict] = []   # 写入 state，A2A/chat 都可直接读取
        _tool_call_idx = 0  # 跨所有 ReAct 轮次的全局工具调用序号（M1、M2…）

        for _ in range(_MAX_TOOL_ROUNDS):
            resp: AIMessage = await llm_with_tools.ainvoke(trajectory)
            trajectory.append(resp)

            if not resp.tool_calls:
                # LLM 不再调用工具，输出最终答案
                answer = resp.content.strip()
                break

            # 执行本轮所有工具调用
            tool_messages: list[ToolMessage] = []
            for tc in resp.tool_calls:
                tool_name = tc["name"]
                # 工具调用前推送状态（文案由前端 ANALYST_TOOL_TEXT 维护）
                await adispatch_custom_event(
                    "analyst_tool_status",
                    {"step": "tool_call", "tool_name": tool_name},
                )
                if tool_name in mcp_tool_map:
                    if not is_execution_task and tool_name in _write_op_tool_names:
                        content = (
                            f"【系统阻止】{tool_name} 是写操作工具，研判模式下不可执行。"
                            f"若原始任务明确要求执行此操作，请在输出末尾写：\n"
                            f"【HITL_REQUIRED】待执行：{tool_name}（需 HITL 审批授权）"
                        )
                        logger.info("Analyst: 写操作工具 %r 在研判模式下被阻止", tool_name)
                    else:
                        try:
                            result = await mcp_tool_map[tool_name].ainvoke(tc["args"])
                            content = str(result)
                            logger.info("Analyst: MCP 工具 %r 返回 %d 字符", tool_name, len(content))
                            extract_map_update(tool_name, result, content, _map_updates, dept_code)
                        except Exception as exc:
                            content = f"工具 {tool_name} 调用失败: {exc}"
                            logger.warning("Analyst: MCP 工具 %r 调用失败: %s", tool_name, exc)
                else:
                    content = f"工具 {tool_name} 不可用"
                tool_messages.append(ToolMessage(content=content, tool_call_id=tc["id"]))

                # 调用完成后记录结果（写入 state + 推送 SSE 自定义事件）
                _tool_call_idx += 1
                key_result = content[:400] + ("…" if len(content) > 400 else "")
                _mcp_sources.append({"idx": _tool_call_idx, "tool_name": tool_name, "key_result": key_result})
                await adispatch_custom_event(
                    "analyst_tool_result",
                    {"idx": _tool_call_idx, "tool_name": tool_name, "key_result": key_result},
                )

            trajectory.extend(tool_messages)
        else:
            # 达到最大轮次，强制用最后一轮内容作为答案
            answer = resp.content.strip() if resp.content else "（已达最大工具调用轮次）"
            logger.warning("Analyst: 达到最大工具调用轮次 %d，强制输出", _MAX_TOOL_ROUNDS)

        # ── 执行阶段安全网：若 ReAct 结束后写操作工具一个都没调用，追加一轮 ────
        if is_execution_task and _write_op_tool_names:
            called = {s["tool_name"] for s in _mcp_sources}
            if not (called & _write_op_tool_names):
                logger.warning(
                    "Analyst: 执行阶段未调用写操作工具（期望 %s），追加强制执行轮次",
                    _write_op_tool_names,
                )
                trajectory.append(HumanMessage(content=(
                    "你尚未调用任何写操作工具。根据以上查询结果，"
                    "请立即调用相应的写操作工具完成实际执行（已获授权，可直接调用）。"
                )))
                resp_sn: AIMessage = await llm_with_tools.ainvoke(trajectory)
                trajectory.append(resp_sn)
                sn_msgs: list[ToolMessage] = []
                for tc in resp_sn.tool_calls:
                    tool_name = tc["name"]
                    await adispatch_custom_event(
                        "analyst_tool_status",
                        {"step": "tool_call", "tool_name": tool_name},
                    )
                    if tool_name in mcp_tool_map:
                        try:
                            result = await mcp_tool_map[tool_name].ainvoke(tc["args"])
                            content = str(result)
                            extract_map_update(tool_name, result, content, _map_updates, dept_code)
                        except Exception as exc:
                            content = f"工具 {tool_name} 调用失败: {exc}"
                    else:
                        content = f"工具 {tool_name} 不可用"
                    sn_msgs.append(ToolMessage(content=content, tool_call_id=tc["id"]))
                    _tool_call_idx += 1
                    key_result = content[:400] + ("…" if len(content) > 400 else "")
                    _mcp_sources.append({"idx": _tool_call_idx, "tool_name": tool_name, "key_result": key_result})
                    await adispatch_custom_event(
                        "analyst_tool_result",
                        {"idx": _tool_call_idx, "tool_name": tool_name, "key_result": key_result},
                    )
                if sn_msgs:
                    trajectory.extend(sn_msgs)
                    resp_final: AIMessage = await llm_with_tools.ainvoke(trajectory)
                    if resp_final.content:
                        answer = resp_final.content.strip()
                elif resp_sn.content:
                    answer = resp_sn.content.strip()

        # mcp_client 在此处出作用域，连接自然关闭（工具调用已全部完成）
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
