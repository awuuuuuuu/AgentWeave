"""
Agent 对话路由

POST /agent/stream   — 启动 Agent 对话，SSE 流式返回各节点事件
POST /agent/resume   — HITL 审批后恢复图执行
GET  /agent/state    — 查询当前 session 的图状态（调试用）

SSE 事件格式（chat session）
----------------------------
    {"type": "node_start",  "node": "supervisor", "data": {}}
    {"type": "node_end",    "node": "researcher",  "data": {"citations": [...], "answer_text": "..."}}
    {"type": "token",       "node": "researcher",  "data": {"content": "..."}}
    {"type": "interrupt",   "node": "hitl",        "data": {...}}
    {"type": "map_update",  "data": {...}}
    {"type": "final_answer","data": {"content": "...", "citations": [...]}}
    {"type": "done",        "data": {"citations": [...]}}
    {"type": "error",       "data": {"message": "..."}}

SSE 事件格式（weave session，来自 Weave Supervisor 自定义事件）
-------------------------------------------------------------
    {"type": "dept_report",   "data": {"dept_code": ..., "status": ..., "summary": ..., "key_facts": [...]}}
    {"type": "dispatch_plan", "data": {"steps": [...]}}
    {"type": "plan_step",     "data": {"step_id": ..., "status": ..., "summary": ...}}
    {"type": "map_update",    "data": {...}}
    {"type": "hitl_required", "data": {"step_id": ..., "title": ..., "timeout_sec": ...}}
    {"type": "final_answer",  "data": {"content": ...}}
    {"type": "interrupt",     "data": {"type": "plan_review"|"step_review", ...}}
    {"type": "done",          "data": {}}
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.graph.agent_graph import get_agent_cards
from agent.graph.memory_nodes import run_on_session_end
from auth.dependencies import get_current_user
from db.models import ConversationSession, Organization, User
from db.session import get_session

router = APIRouter(prefix="/agent", tags=["agent"])
logger = logging.getLogger(__name__)

_MAX_QUERY_CHARS = 4000
_KNOWN_NODES = frozenset(
    {"memory_inject", "supervisor", "researcher", "analyst", "reporter"}
)

# Researcher 子图内部步骤名（文案由前端 RESEARCHER_STEP_TEXT 维护）
_RESEARCHER_STEPS = frozenset({"retrieve", "grade", "rewrite", "generate"})


# ── 请求/响应 Schema ──────────────────────────────────────────────────────────

class AgentChatRequest(BaseModel):
    query: str
    session_id: str
    kb_ids: list[str] = []
    selected_dept_codes: list[str] = []  # weave session only


class AgentResumeRequest(BaseModel):
    session_id: str
    decision: Any  # HITL: "approve" | "reject" | list[PlanStep]（HITL-1 修改计划）


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def _get_graph_by_type(request: Request, session_type: str):
    if session_type == "weave":
        return request.app.state.weave_graph
    return request.app.state.agent_graph


async def _get_session_type(session_id: str, user_id: str, db: AsyncSession) -> str:
    """从 DB 查询会话类型，未找到时默认返回 "chat"。"""
    row = await db.scalar(
        select(ConversationSession).where(
            ConversationSession.id == session_id,
            ConversationSession.user_id == user_id,
        )
    )
    if not row:
        return "chat"
    stype = row.session_type or "chat"
    if stype not in ("chat", "weave"):
        logger.warning("未知会话类型 session=%s type=%r，回退为 chat", session_id, stype)
        return "chat"
    return stype


def _make_config(session_id: str, user_id: str) -> dict:
    return {
        "configurable": {"thread_id": f"{user_id}:{session_id}"},
        "recursion_limit": 30,
    }


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _infer_node(event: dict) -> str:
    """从事件 metadata 推断当前所属节点名"""
    node = event.get("metadata", {}).get("langgraph_node", "")
    if not node:
        for tag in event.get("tags", []):
            if tag in _KNOWN_NODES:
                return tag
    return node or "agent"


# ── 共享 SSE 事件解析器（DRY：stream 和 resume 共用） ────────────────────────

async def _process_events(
    event_source: AsyncGenerator[dict, None],
    request: Request,
    session_id: str,
    graph,
    config: dict,
) -> AsyncGenerator[str, None]:
    """
    遍历 graph.astream_events() 迭代器，将 LangGraph 内部事件转换为前端 SSE 格式。

    设计原则：
    - 客户端断开时 return（不 raise），由调用方 finally 输出 [DONE]
    - CancelledError 向上透传，让 ASGI 层正常取消
    - 所有业务异常在此捕获并转为 error 事件
    """
    try:
        # 追踪最新答案文本和引用，用于 final_answer 事件
        # 预填充：HITL resume 时 Researcher 不会重跑，需从 checkpoint 恢复
        _last_answer_text: str = ""
        _last_citations: list = []
        _final_answer_sent: bool = False

        initial_state = await graph.aget_state(config)
        if initial_state and initial_state.values:
            sv_init = initial_state.values
            for m in reversed(sv_init.get("messages", [])):
                if isinstance(m, AIMessage) and m.content:
                    _last_answer_text = m.content
                    break
                elif isinstance(m, dict) and m.get("type") == "ai" and m.get("content"):
                    _last_answer_text = m["content"]
                    break
            _last_citations = sv_init.get("citations", [])

        async for event in event_source:
            if await request.is_disconnected():
                logger.info("Agent SSE: 客户端断开 session=%s", session_id)
                return

            ev_type = event.get("event", "")
            ev_name = event.get("name", "")
            ev_data = event.get("data", {})

            # 节点开始
            if ev_type == "on_chain_start" and ev_name in _KNOWN_NODES:
                yield _sse({"type": "node_start", "node": ev_name, "data": {}})

            # Researcher 子图步骤提示（retrieve / grade / rewrite / generate）
            elif ev_type == "on_chain_start" and ev_name in _RESEARCHER_STEPS:
                yield _sse({
                    "type": "status",
                    "node": "researcher",
                    "data": {"step": ev_name},
                })

            # Analyst 工具调用状态推送：调用前（tool_status）+ 调用后结果（tool_result）
            elif ev_type == "on_custom_event" and ev_name == "analyst_tool_status":
                node = _infer_node(event) or "analyst"
                yield _sse({"type": "status", "node": node, "data": ev_data})

            elif ev_type == "on_custom_event" and ev_name == "analyst_tool_result":
                node = _infer_node(event) or "analyst"
                yield _sse({"type": "tool_result", "node": node, "data": ev_data})

            # LLM token 逐字（supervisor 使用 structured_output，跳过原始 JSON token）
            elif ev_type == "on_chat_model_stream":
                node = _infer_node(event)
                if node == "supervisor":
                    continue
                chunk = ev_data.get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    yield _sse({
                        "type": "token",
                        "node": node,
                        "data": {"content": chunk.content},
                    })

            # 节点结束
            elif ev_type == "on_chain_end" and ev_name in _KNOWN_NODES:
                output = ev_data.get("output", {}) or {}
                data: dict = {}
                if citations := output.get("citations", []):
                    data["citations"] = citations
                # researcher: 无 token 流（ainvoke），从 node_end output 提取答案文本
                # analyst: 有 token 流，只更新 _last_answer_text 供 final_answer 使用，不发 answer_text（避免覆盖已流式渲染的内容）
                if ev_name == "researcher":
                    msgs = output.get("messages", [])
                    for m in reversed(msgs):
                        if isinstance(m, AIMessage) and m.content:
                            data["answer_text"] = m.content
                            _last_answer_text = m.content
                            break
                        elif isinstance(m, dict) and m.get("type") == "ai" and m.get("content"):
                            data["answer_text"] = m["content"]
                            _last_answer_text = m["content"]
                            break
                    _last_citations = output.get("citations", [])
                elif ev_name == "analyst":
                    # 只更新追踪变量，不写 data["answer_text"]
                    msgs = output.get("messages", [])
                    for m in reversed(msgs):
                        if isinstance(m, AIMessage) and m.content:
                            _last_answer_text = m.content
                            break
                        elif isinstance(m, dict) and m.get("type") == "ai" and m.get("content"):
                            _last_answer_text = m["content"]
                            break
                    # 直接从 node output 读取 mcp_sources（analyst 写入 state）
                    if mcp_sources := output.get("mcp_sources", []):
                        data["mcp_sources"] = mcp_sources
                    # amap 工具调用结果 → 前端地图气泡
                    for mu in output.get("map_updates", []):
                        yield _sse({"type": "map_update", "data": mu})
                # supervisor 携带路由意图说明；超纲降级（无 workers）时以 message_to_user 作最终答案
                if ev_name == "supervisor":
                    if msg := output.get("message_to_user", ""):
                        data["message_to_user"] = msg
                    # 超纲降级：supervisor 直接 __end__ 且本轮无 worker 参与
                    if (
                        output.get("next_agent") == "__end__"
                        and not _last_answer_text
                        and msg
                        and not _final_answer_sent
                    ):
                        yield _sse({"type": "node_end", "node": ev_name, "data": data})
                        yield _sse({
                            "type": "final_answer",
                            "data": {"content": msg, "citations": []},
                        })
                        _final_answer_sent = True
                        continue
                # reporter 整合完毕 → 更新追踪变量并发出 final_answer（Reporter 是质量链的终点）
                if ev_name == "reporter":
                    msgs = output.get("messages", [])
                    for m in reversed(msgs):
                        if isinstance(m, AIMessage) and m.content:
                            _last_answer_text = m.content
                            break
                        elif isinstance(m, dict) and m.get("type") == "ai" and m.get("content"):
                            _last_answer_text = m["content"]
                            break
                    if _last_answer_text and not _final_answer_sent:
                        yield _sse({"type": "node_end", "node": ev_name, "data": data})
                        yield _sse({
                            "type": "final_answer",
                            "data": {
                                "content": _last_answer_text,
                                "citations": _last_citations,
                            },
                        })
                        _final_answer_sent = True
                        continue
                yield _sse({"type": "node_end", "node": ev_name, "data": data})

            # interrupt（HITL 高风险操作审批）
            elif ev_type == "on_chain_stream":
                chunk_val = ev_data.get("chunk")
                if isinstance(chunk_val, dict) and chunk_val.get("__interrupt__"):
                    payload = chunk_val["__interrupt__"][0].value
                    yield _sse({"type": "interrupt", "node": "hitl", "data": payload})

        # 图执行完毕
        final_state = await graph.aget_state(config)
        sv = final_state.values if final_state and final_state.values else {}
        final_citations = sv.get("citations", [])

        if not _final_answer_sent:
            answer = _last_answer_text
            cits = _last_citations or final_citations

            if not answer:
                # HITL resume 场景：本次流中未经过 researcher，从图状态消息中找最后一条 AI 回答
                for m in reversed(sv.get("messages", [])):
                    if isinstance(m, AIMessage) and m.content:
                        answer = m.content
                        break
                    elif isinstance(m, dict) and m.get("type") == "ai" and m.get("content"):
                        answer = m["content"]
                        break

            if answer:
                yield _sse({
                    "type": "final_answer",
                    "data": {"content": answer, "citations": cits},
                })
        yield _sse({"type": "done", "data": {"citations": final_citations}})

    except asyncio.CancelledError:
        # ASGI 层取消响应时正常退出，不产生 error 事件
        logger.info("Agent SSE: 任务取消 session=%s", session_id)
        raise
    except Exception as exc:
        logger.exception("Agent SSE: 错误 session=%s", session_id)
        # 网络类错误给用户友好提示，避免暴露原始异常字符串
        exc_qualname = f"{type(exc).__module__}.{type(exc).__name__}"
        _NETWORK_ERRORS = ("ConnectionError", "ConnectError", "Timeout", "APIConnectionError")
        if any(k in exc_qualname for k in _NETWORK_ERRORS):
            user_msg = "AI 服务暂时无法连接，请稍后重试"
        else:
            user_msg = str(exc) or "Agent 执行出错"
        yield _sse({"type": "error", "data": {"message": user_msg}})


# ── Weave SSE 事件处理器 ───────────────────────────────────────────────────────

async def _process_weave_events(
    event_source: AsyncGenerator[dict, None],
    request: Request,
    session_id: str,
) -> AsyncGenerator[str, None]:
    """
    遍历 weave_graph.astream_events()，将 em_event 自定义事件和 HITL interrupt 转为前端 SSE。
    事件数据格式：{type: str, data: dict}，与 weave_supervisor.py 中的 adispatch_custom_event 对齐。

    设计要点：
    - interrupt 检测到后继续排水（不 return/aclose），让 event_source 自然耗尽。
      原因：aclose() 会取消 LangGraph 尚未完成的 checkpoint 写入 → resume 409。
      自然耗尽（StopAsyncIteration）不触发 aclose()，checkpoint 安全落盘。
    - 每次节点调用只含一个 interrupt()（单步审批循环），无多次 interrupt 问题。
    """
    try:
        interrupt_sent = False

        async for event in event_source:
            # interrupt 发出后只排水（不处理），让 event_source 自然结束
            if interrupt_sent:
                continue

            if await request.is_disconnected():
                logger.info("Weave SSE: 客户端断开 session=%s", session_id)
                return

            ev_type = event.get("event", "")
            ev_name = event.get("name", "")
            ev_data = event.get("data", {})

            # Weave 自定义事件（dept_report / dispatch_plan / plan_step / map_update / hitl_required / final_answer）
            if ev_type == "on_custom_event" and ev_name == "em_event":
                yield _sse(ev_data)  # ev_data 已是 {type, data} 结构

            # HITL 中断（plan_review 或 step_review）
            elif ev_type == "on_chain_stream":
                chunk_val = ev_data.get("chunk")
                if isinstance(chunk_val, dict) and chunk_val.get("__interrupt__"):
                    payload = chunk_val["__interrupt__"][0].value
                    yield _sse({"type": "interrupt", "data": payload})
                    interrupt_sent = True  # 继续排水直到 event_source 自然结束

        if not interrupt_sent:
            yield _sse({"type": "done", "data": {}})

    except asyncio.CancelledError:
        logger.info("Weave SSE: 任务取消 session=%s", session_id)
        raise
    except Exception as exc:
        logger.exception("Weave SSE: 错误 session=%s", session_id)
        yield _sse({"type": "error", "data": {"message": str(exc) or "Weave 执行出错"}})


# ── 路由 ──────────────────────────────────────────────────────────────────────

@router.get("/members")
async def list_members(
    _: User = Depends(get_current_user),
) -> list[dict]:
    """返回群组成员 card（AgentRegistry 侧边栏用）"""
    return get_agent_cards()


async def _get_org_mcp_connections(user: User, db: AsyncSession) -> list[dict]:
    """读取用户所在机构的 MCP 连接配置；无机构或无连接时返回空列表。"""
    if not user.org_id:
        return []
    org = await db.scalar(select(Organization).where(Organization.id == user.org_id))
    if org is None:
        return []
    return org.mcp_connections or []


async def _get_org_dept_prompts(user: User, db: AsyncSession) -> tuple[str, str]:
    """读取用户所在机构的部门专属 prompt；返回 (supervisor_hints, analyst_context)。"""
    if not user.org_id:
        return "", ""
    org = await db.scalar(select(Organization).where(Organization.id == user.org_id))
    if org is None or not org.dept_prompts:
        return "", ""
    prompts = org.dept_prompts
    return prompts.get("supervisor_hints", ""), prompts.get("analyst_context", "")


def _build_mcp_summary(mcp_connections: list[dict]) -> str:
    """把 mcp_connections 列表转为 Supervisor prompt 里的一行描述。"""
    if not mcp_connections:
        return ""
    parts = [
        f"{c['name']}（{c.get('description', '')}）"
        for c in mcp_connections
        if c.get("name")
    ]
    return "Analyst 可调用以下 MCP 工具：" + "、".join(parts)


@router.post("/stream")
async def agent_stream(
    req: AgentChatRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """启动 Agent 对话，SSE 流式返回节点事件"""
    query = req.query.strip()[:_MAX_QUERY_CHARS]
    if not query:
        raise HTTPException(status_code=400, detail="query 不能为空")

    session_type = await _get_session_type(req.session_id, current_user.id, db)

    # ── Weave 会话（应急指挥台） ────────────────────────────────────────────────
    if session_type == "weave":
        graph = _get_graph_by_type(request, "weave")
        config = _make_config(req.session_id, current_user.id)

        existing = await graph.aget_state(config)
        has_thread = bool(existing and existing.values)

        if has_thread:
            # 已有线程：追加用户消息（续命令 / 追加指令）
            graph_input: Any = {"messages": [HumanMessage(content=query)]}
        else:
            # 首轮：query 即事故描述，初始化完整 WeaveState
            graph_input = {
                "session_id": req.session_id,
                "user_id": current_user.id,
                "incident": query,
                "selected_dept_codes": req.selected_dept_codes,
                "dept_reports": {},
                "dispatch_plan": [],
                "current_step": 0,
                "map_events": [],
                "phase": "dispatch",
                "messages": [HumanMessage(content=query)],
            }

        logger.info("weave_stream: session=%s dept_codes=%s", req.session_id, req.selected_dept_codes)

        async def weave_event_gen():
            try:
                event_source = graph.astream_events(graph_input, config=config, version="v2")
                async for chunk in _process_weave_events(event_source, request, req.session_id):
                    yield chunk
            except asyncio.CancelledError:
                logger.info("Weave stream 生成器取消 session=%s", req.session_id)
                raise
            finally:
                yield "data: [DONE]\n\n"

        return StreamingResponse(weave_event_gen(), media_type="text/event-stream")

    # ── Chat 会话（普通 Agent 对话） ──────────────────────────────────────────
    # 读取当前用户 org 的 MCP 连接配置和部门专属 prompt
    org_mcp_connections, (org_supervisor_hints, org_analyst_context) = await asyncio.gather(
        _get_org_mcp_connections(current_user, db),
        _get_org_dept_prompts(current_user, db),
    )
    logger.info(
        "agent_stream: session=%s kb_ids=%s mcp_conns=%d",
        req.session_id, req.kb_ids, len(org_mcp_connections),
    )

    graph = _get_graph_by_type(request, "chat")
    config = _make_config(req.session_id, current_user.id)

    # 检查线程是否已存在：
    # - 新线程：传入完整初始状态（含 user_id / session_id）
    # - 已有线程：只追加新消息 + 重置本轮控制字段，保留 checkpoint 中的历史数据
    existing = await graph.aget_state(config)
    has_thread = bool(existing and existing.values)

    per_turn_reset = {
        "messages": [HumanMessage(content=query)],
        "kb_ids": req.kb_ids,
        "org_mcp_connections": org_mcp_connections,
        "org_mcp_summary": _build_mcp_summary(org_mcp_connections),
        "org_supervisor_hints": org_supervisor_hints,
        "org_analyst_context": org_analyst_context,
        "next_agent": "",
        "task": "",
        "message_to_user": "",
        "supervisor_count": 0,
        "researcher_count": 0,
        "analyst_count": 0,
        "pending_approval": None,
        "citations": [],
        # memory_context / memory_injected 不在此处列出：由 LangGraph checkpoint 跨轮持久化
        # memory_injected 首轮由 memory_inject 节点置 True，后续轮次跳过注入步骤
    }

    graph_input = per_turn_reset if has_thread else {
        **per_turn_reset,
        "user_id": current_user.id,
        "session_id": req.session_id,
    }

    async def event_gen():
        try:
            event_source = graph.astream_events(graph_input, config=config, version="v2")
            async for chunk in _process_events(event_source, request, req.session_id, graph, config):
                yield chunk
        except asyncio.CancelledError:
            logger.info("Agent stream 生成器取消 session=%s", req.session_id)
            raise
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@router.post("/resume")
async def agent_resume(
    req: AgentResumeRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """HITL 审批后恢复图执行（approve / reject / list[PlanStep]）"""
    session_type = await _get_session_type(req.session_id, current_user.id, db)
    graph = _get_graph_by_type(request, session_type)
    config = _make_config(req.session_id, current_user.id)

    state = await graph.aget_state(config)
    logger.info(
        "resume: session=%s state.next=%s state.tasks=%s",
        req.session_id,
        getattr(state, "next", None),
        [getattr(t, "id", t) for t in (getattr(state, "tasks", None) or [])],
    )
    if state is None or not state.next:
        raise HTTPException(status_code=409, detail="当前会话没有待审批的操作")

    resume_command = Command(resume=req.decision)

    if session_type == "weave":
        async def weave_resume_gen():
            try:
                event_source = graph.astream_events(resume_command, config=config, version="v2")
                async for chunk in _process_weave_events(event_source, request, req.session_id):
                    yield chunk
            except asyncio.CancelledError:
                logger.info("Weave resume 生成器取消 session=%s", req.session_id)
                raise
            finally:
                yield "data: [DONE]\n\n"

        return StreamingResponse(weave_resume_gen(), media_type="text/event-stream")

    async def event_gen():
        try:
            event_source = graph.astream_events(resume_command, config=config, version="v2")
            async for chunk in _process_events(event_source, request, req.session_id, graph, config):
                yield chunk
        except asyncio.CancelledError:
            logger.info("Agent resume 生成器取消 session=%s", req.session_id)
            raise
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@router.post("/sessions/{session_id}/close")
async def close_agent_session(
    session_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    关闭会话：触发 memory_save（on_session_end）。
    fire-and-forget，立即返回；前端在切换会话或离开页面时调用。
    """
    memory_manager = getattr(request.app.state, "memory_manager", None)
    if memory_manager:
        task = asyncio.create_task(
            run_on_session_end(memory_manager, session_id, current_user.id)
        )
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        logger.info("close_session: memory_save 已调度 session=%s", session_id)
    return {"status": "ok"}


@router.get("/state/{session_id}")
async def get_agent_state(
    session_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> dict:
    """查询指定会话的当前图状态（调试用）"""
    session_type = await _get_session_type(session_id, current_user.id, db)
    graph = _get_graph_by_type(request, session_type)
    config = _make_config(session_id, current_user.id)
    state = await graph.aget_state(config)
    if state is None:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")

    sv = state.values or {}
    base = {
        "session_id": session_id,
        "session_type": session_type,
        "next": list(state.next),
        "interrupted": bool(state.next),
        "message_count": len(sv.get("messages", [])),
    }
    if session_type == "weave":
        base.update({
            "phase": sv.get("phase", ""),
            "current_step": sv.get("current_step", 0),
            "plan_length": len(sv.get("dispatch_plan", [])),
        })
    else:
        base["citations"] = sv.get("citations", [])
    return base
