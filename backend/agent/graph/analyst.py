"""
Analyst 节点：实时工具调用与数据分析

通过部门 MCP 工具查询实时数据（传感器、调度、库存等），并对结果进行推理分析。
MCP client 在整个节点执行期间保持存活，避免连接被 GC 提前回收。
"""
from __future__ import annotations

import json as _json
import logging
from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

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
        messages = state.get("messages", [])
        recent = messages[-ANALYST_CONTEXT_WINDOW:]
        mcp_connections = state.get("org_mcp_connections") or []

        # ── MCP 工具加载（client 保持在当前作用域，生命周期覆盖整个 node）──────
        mcp_client = None
        mcp_tools: list[BaseTool] = []

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
                logger.warning("Analyst: MCP 工具加载失败，降级为无工具模式: %s", exc)
                mcp_tools = []

        if not mcp_tools and mcp_connections:
            logger.warning(
                "Analyst: MCP 连接配置存在 %d 条但工具加载为空，LLM 将在无工具模式下运行",
                len(mcp_connections),
            )

        llm_with_tools = llm.bind_tools(mcp_tools) if mcp_tools else llm
        mcp_tool_map: dict[str, BaseTool] = {t.name: t for t in mcp_tools}

        # ── 构建 Analyst system prompt（通用 + 部门专属补充）──────────────────
        analyst_prompt = ANALYST_SYSTEM
        org_analyst_context = state.get("org_analyst_context", "")
        if org_analyst_context:
            analyst_prompt += f"\n\n[本部门调用说明] {org_analyst_context}"

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
                # 工具调用前推送状态
                await adispatch_custom_event(
                    "analyst_tool_status",
                    {"step": "tool_call", "text": _tool_status(tool_name)},
                )
                if tool_name in mcp_tool_map:
                    try:
                        result = await mcp_tool_map[tool_name].ainvoke(tc["args"])
                        content = str(result)
                        logger.info("Analyst: MCP 工具 %r 返回 %d 字符", tool_name, len(content))
                        _try_extract_map_update(tool_name, result, content, _map_updates)
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


# ── 工具状态文字映射 ──────────────────────────────────────────────────────────

_TOOL_STATUS_MAP: dict[str, str] = {
    "geocode":              "正在解析地址坐标…",
    "plan_driving_route":   "正在规划驾车路线…",
    "get_hospital_capacity":"正在查询医院 ICU 容量…",
    "list_ambulances":      "正在查询救护车状态…",
    "dispatch_ambulance":   "正在调度救护车…",
    "calculate_plume":      "正在计算气体扩散范围…",
    "get_sensor_readings":  "正在读取传感器数据…",
    "get_critical_alarms":  "正在获取高风险传感器告警…",
    "get_incident_timeline":"正在检索事故时间线…",
    "list_intersections":   "正在查询路口信号状态…",
    "set_intersection_mode":"正在设置路口信号模式…",
    "batch_set_intersections":"正在批量设置路口信号…",
    "get_inventory":        "正在查询应急物资库存…",
    "check_alerts":         "正在检查库存告警…",
    "dispatch_materials":   "正在调拨应急物资…",
    "get_equipment_status": "正在查询设备状态…",
}


def _tool_status(tool_name: str) -> str:
    """返回工具调用时的用户友好状态文字。"""
    for key, text in _TOOL_STATUS_MAP.items():
        if key in tool_name:
            return text
    return f"正在调用工具 {tool_name}…"


# ── 地图数据提取 ───────────────────────────────────────────────────────────────

def _try_extract_map_update(
    tool_name: str,
    result: object,
    content: str,
    out: list[dict],
) -> None:
    """从 amap 工具调用结果中提取地图数据，追加到 out 列表。

    工具名匹配用 in 而非 ==，兼容 MultiServerMCPClient 可能添加的服务名前缀
    （如 amap_plan_driving_route）。
    """
    is_route   = "plan_driving_route" in tool_name
    is_geocode = "geocode" in tool_name and not is_route
    if not (is_route or is_geocode):
        return

    # result 可能是：dict、str（JSON）、list[TextContent]（MCP adapter 格式）
    parsed: dict | None = None
    if isinstance(result, dict):
        parsed = result
    elif isinstance(result, list) and result:
        # langchain-mcp-adapters 返回 [TextContent(type='text', text='...')]
        first = result[0]
        text = (
            getattr(first, "text", None)
            or (first.get("text") if isinstance(first, dict) else None)
            or str(first)
        )
        try:
            parsed = _json.loads(text)
        except Exception:
            pass
    if parsed is None:
        try:
            parsed = _json.loads(content)
        except Exception:
            return

    if not isinstance(parsed, dict):
        return

    if tool_name == "plan_driving_route" and "polyline" in parsed:
        frm = parsed.get("from", {})
        to  = parsed.get("to", {})
        out.append({
            "title": "路线规划",
            "center": [
                (frm.get("lng", 0) + to.get("lng", 0)) / 2,
                (frm.get("lat", 0) + to.get("lat", 0)) / 2,
            ],
            "zoom": 13,
            "markers": [
                {"position": [frm.get("lng", 0), frm.get("lat", 0)], "label": "出发点", "icon": "🔵"},
                {"position": [to.get("lng", 0),  to.get("lat", 0)],  "label": "目的地", "icon": "📍"},
            ],
            "route": {
                "from": frm,
                "to":   to,
                "polyline":         parsed["polyline"],
                "distance_m":       parsed.get("distance_m", 0),
                "duration_seconds": parsed.get("duration_seconds", 0),
            },
        })

    # geocode 仅作为中间步骤，不单独渲染地图气泡（路线地图已包含起终点 marker）
