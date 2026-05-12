"""
Agent 对话路由

POST /agent/stream   — 启动 Agent 对话，SSE 流式返回各节点事件
POST /agent/resume   — HITL 审批后恢复图执行
GET  /agent/state    — 查询当前 session 的图状态（调试用）

SSE 事件格式
------------
每条事件均为 `data: <JSON>\n\n`，JSON 结构：

    {"type": "node_start",  "node": "supervisor", "data": {}}
    {"type": "node_end",    "node": "researcher",  "data": {"citations": [...]}}
    {"type": "token",       "node": "researcher",  "data": {"content": "..."}}
    {"type": "interrupt",   "node": "hitl",        "data": {"tool_name": "...", "description": "...", "message": "..."}}
    {"type": "done",        "data": {"citations": [...]}}
    {"type": "error",       "data": {"message": "..."}}
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from langgraph.types import Command
from pydantic import BaseModel

from agent.graph.agent_graph import get_agent_cards
from auth.dependencies import get_current_user
from db.models import User

router = APIRouter(prefix="/agent", tags=["agent"])
logger = logging.getLogger(__name__)

_MAX_QUERY_CHARS = 4000


# ── 请求/响应 Schema ──────────────────────────────────────────────────────────

class AgentChatRequest(BaseModel):
    query: str
    session_id: str                  # LangGraph thread_id
    kb_ids: list[str] = []          # 允许检索的知识库列表（空 = 不检索文档）


class AgentResumeRequest(BaseModel):
    session_id: str
    decision: str                    # "approve" 或 "reject"


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def _get_graph(request: Request):
    return request.app.state.agent_graph


def _make_config(session_id: str, user_id: str) -> dict:
    """LangGraph 线程配置（thread_id 隔离会话状态）"""
    return {
        "configurable": {
            "thread_id": f"{user_id}:{session_id}",
        },
        "recursion_limit": 30,
    }


# ── 路由 ──────────────────────────────────────────────────────────────────────

@router.get("/members")
async def list_members(
    current_user: User = Depends(get_current_user),
) -> list[dict]:
    """返回当前群组的所有 Agent 成员 card（供 AgentRegistry 侧边栏展示）"""
    return get_agent_cards()


@router.post("/stream")
async def agent_stream(
    req: AgentChatRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """
    启动 Agent 对话，SSE 流式返回节点事件。

    每个节点（supervisor / researcher / analyst / critic / hitl）开始和结束
    时各发送一条事件；LLM token 逐字推送；HITL interrupt 时发送 interrupt 事件。
    """
    query = req.query.strip()[:_MAX_QUERY_CHARS]
    if not query:
        raise HTTPException(status_code=400, detail="query 不能为空")

    graph = _get_graph(request)
    config = _make_config(req.session_id, current_user.id)

    initial_input = {
        "messages": [HumanMessage(content=query)],
        "user_id": current_user.id,
        "session_id": req.session_id,
        "kb_ids": req.kb_ids,
        "next_agent": "",
        "task": "",
        "critic_count": 0,
        "supervisor_count": 0,
        "pending_approval": None,
        "citations": [],
    }

    async def event_gen():
        try:
            async for event in graph.astream_events(
                initial_input, config=config, version="v2"
            ):
                if await request.is_disconnected():
                    logger.warning("Agent SSE: 客户端断开 session=%s", req.session_id)
                    break

                ev_type = event.get("event", "")
                ev_name = event.get("name", "")
                ev_data = event.get("data", {})
                # namespace 标识事件来源（主图节点名 or 子图）
                ns: tuple = event.get("metadata", {}).get("langgraph_node", ev_name)

                # ── 节点开始 ────────────────────────────────────────────────
                if ev_type == "on_chain_start" and ev_name in (
                    "supervisor", "researcher", "analyst", "critic", "hitl"
                ):
                    yield _sse({"type": "node_start", "node": ev_name, "data": {}})

                # ── LLM token（逐字） ────────────────────────────────────────
                elif ev_type == "on_chat_model_stream":
                    chunk = ev_data.get("chunk")
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        # 找出所属节点（从 tags 或 metadata 推断）
                        node = _infer_node(event)
                        yield _sse({
                            "type": "token",
                            "node": node,
                            "data": {"content": chunk.content},
                        })

                # ── 节点结束 ────────────────────────────────────────────────
                elif ev_type == "on_chain_end" and ev_name in (
                    "supervisor", "researcher", "analyst", "critic", "hitl"
                ):
                    output = ev_data.get("output", {}) or {}
                    citations = output.get("citations", [])
                    yield _sse({
                        "type": "node_end",
                        "node": ev_name,
                        "data": {"citations": citations} if citations else {},
                    })

                # ── HITL interrupt ───────────────────────────────────────────
                elif ev_type == "on_chain_stream":
                    # interrupt() 触发时 LangGraph 发出特殊 chunk
                    chunk_val = ev_data.get("chunk")
                    if (
                        isinstance(chunk_val, dict)
                        and chunk_val.get("__interrupt__")
                    ):
                        interrupt_payload = chunk_val["__interrupt__"][0].value
                        yield _sse({
                            "type": "interrupt",
                            "node": "hitl",
                            "data": interrupt_payload,
                        })

            # ── 图执行完毕：取最终状态发送 done 事件 ──────────────────────
            final_state = graph.get_state(config)
            citations = []
            if final_state and final_state.values:
                citations = final_state.values.get("citations", [])
            yield _sse({"type": "done", "data": {"citations": citations}})

        except asyncio.CancelledError:
            logger.info("Agent SSE: 请求取消 session=%s", req.session_id)
            raise
        except Exception as exc:
            logger.exception("Agent SSE: 错误 session=%s", req.session_id)
            yield _sse({"type": "error", "data": {"message": str(exc)}})
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@router.post("/resume")
async def agent_resume(
    req: AgentResumeRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """
    HITL 审批后恢复图执行。

    decision: "approve" 继续执行，"reject" 取消操作。
    返回与 /agent/stream 相同格式的 SSE 流。
    """
    graph = _get_graph(request)
    config = _make_config(req.session_id, current_user.id)

    # 验证当前图状态确实处于 interrupt
    state = graph.get_state(config)
    if state is None or not state.next:
        raise HTTPException(
            status_code=409, detail="当前会话没有待审批的操作"
        )

    resume_command = Command(resume=req.decision)

    async def event_gen():
        try:
            async for event in graph.astream_events(
                resume_command, config=config, version="v2"
            ):
                if await request.is_disconnected():
                    break

                ev_type = event.get("event", "")
                ev_name = event.get("name", "")
                ev_data = event.get("data", {})

                if ev_type == "on_chain_start" and ev_name in (
                    "supervisor", "researcher", "analyst", "critic", "hitl"
                ):
                    yield _sse({"type": "node_start", "node": ev_name, "data": {}})

                elif ev_type == "on_chat_model_stream":
                    chunk = ev_data.get("chunk")
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        node = _infer_node(event)
                        yield _sse({
                            "type": "token",
                            "node": node,
                            "data": {"content": chunk.content},
                        })

                elif ev_type == "on_chain_end" and ev_name in (
                    "supervisor", "researcher", "analyst", "critic", "hitl"
                ):
                    output = ev_data.get("output", {}) or {}
                    citations = output.get("citations", [])
                    yield _sse({
                        "type": "node_end",
                        "node": ev_name,
                        "data": {"citations": citations} if citations else {},
                    })

            final_state = graph.get_state(config)
            citations = []
            if final_state and final_state.values:
                citations = final_state.values.get("citations", [])
            yield _sse({"type": "done", "data": {"citations": citations}})

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Agent resume 错误 session=%s", req.session_id)
            yield _sse({"type": "error", "data": {"message": str(exc)}})
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
    state = graph.get_state(config)
    if state is None:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")
    return {
        "session_id": session_id,
        "next": list(state.next),
        "interrupted": bool(state.next),  # next 非空表示图被 interrupt 暂停
        "message_count": len(state.values.get("messages", [])),
        "citations": state.values.get("citations", []),
    }


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _infer_node(event: dict) -> str:
    """从事件 metadata 推断当前所属节点名"""
    metadata = event.get("metadata", {})
    # LangGraph v2 事件在 metadata.langgraph_node 中标注节点名
    node = metadata.get("langgraph_node", "")
    if not node:
        # 回退：从 tags 中找已知节点名
        tags = event.get("tags", [])
        known = {"supervisor", "researcher", "analyst", "critic", "hitl"}
        for tag in tags:
            if tag in known:
                return tag
    return node or "agent"
