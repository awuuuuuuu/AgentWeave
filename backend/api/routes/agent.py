"""
Agent 对话路由

POST /agent/stream   — 启动 Agent 对话，SSE 流式返回各节点事件
POST /agent/resume   — HITL 审批后恢复图执行
GET  /agent/state    — 查询当前 session 的图状态（调试用）

SSE 事件格式
------------
每条事件均为 `data: <JSON>\n\n`，JSON 结构：

    {"type": "node_start",  "node": "supervisor", "data": {}}
    {"type": "node_end",    "node": "researcher",  "data": {"citations": [...], "answer_text": "..."}}
    {"type": "node_end",    "node": "supervisor",  "data": {"message_to_user": "..."}}
    {"type": "token",       "node": "researcher",  "data": {"content": "..."}}
    {"type": "interrupt",   "node": "hitl",        "data": {"tool_name": "...", "description": "...", "message": "..."}}
    {"type": "final_answer","data": {"content": "...", "citations": [...]}}
    {"type": "done",        "data": {"citations": [...]}}
    {"type": "error",       "data": {"message": "..."}}
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command
from pydantic import BaseModel

from agent.graph.agent_graph import get_agent_cards
from auth.dependencies import get_current_user
from db.models import User

router = APIRouter(prefix="/agent", tags=["agent"])
logger = logging.getLogger(__name__)

_MAX_QUERY_CHARS = 4000
_KNOWN_NODES = frozenset(
    {"memory_inject", "supervisor", "researcher", "analyst", "reporter", "memory_save"}
)


# ── 请求/响应 Schema ──────────────────────────────────────────────────────────

class AgentChatRequest(BaseModel):
    query: str
    session_id: str
    kb_ids: list[str] = []


class AgentResumeRequest(BaseModel):
    session_id: str
    decision: str  # HITL: "approve" | "reject"


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def _get_graph(request: Request):
    return request.app.state.agent_graph


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
        yield _sse({"type": "error", "data": {"message": str(exc)}})


# ── 路由 ──────────────────────────────────────────────────────────────────────

@router.get("/members")
async def list_members(
    _: User = Depends(get_current_user),
) -> list[dict]:
    """返回群组成员 card（AgentRegistry 侧边栏用）"""
    return get_agent_cards()


@router.post("/stream")
async def agent_stream(
    req: AgentChatRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """启动 Agent 对话，SSE 流式返回节点事件"""
    query = req.query.strip()[:_MAX_QUERY_CHARS]
    if not query:
        raise HTTPException(status_code=400, detail="query 不能为空")
    logger.info("agent_stream: session=%s kb_ids=%s", req.session_id, req.kb_ids)

    graph = _get_graph(request)
    config = _make_config(req.session_id, current_user.id)

    # 检查线程是否已存在：
    # - 新线程：传入完整初始状态（含 user_id / session_id）
    # - 已有线程：只追加新消息 + 重置本轮控制字段，保留 checkpoint 中的历史数据
    existing = await graph.aget_state(config)
    has_thread = bool(existing and existing.values)

    per_turn_reset = {
        "messages": [HumanMessage(content=query)],
        "kb_ids": req.kb_ids,
        "next_agent": "",
        "task": "",
        "memory_context": "",
        "message_to_user": "",
        "supervisor_count": 0,
        "researcher_count": 0,
        "analyst_count": 0,
        "pending_approval": None,
        "citations": [],
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
) -> StreamingResponse:
    """HITL 审批后恢复图执行（approve / reject）"""
    graph = _get_graph(request)
    config = _make_config(req.session_id, current_user.id)

    state = await graph.aget_state(config)
    if state is None or not state.next:
        raise HTTPException(status_code=409, detail="当前会话没有待审批的操作")

    resume_command = Command(resume=req.decision)

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


@router.get("/state/{session_id}")
async def get_agent_state(
    session_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> dict:
    """查询指定会话的当前图状态（调试用）"""
    graph = _get_graph(request)
    config = _make_config(session_id, current_user.id)
    state = await graph.aget_state(config)
    if state is None:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")
    return {
        "session_id": session_id,
        "next": list(state.next),
        "interrupted": bool(state.next),
        "message_count": len(state.values.get("messages", [])),
        "citations": state.values.get("citations", []),
    }
