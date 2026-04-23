from __future__ import annotations

import asyncio
import json
import logging
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from api.schemas.chat import ChatRequest, ChatResponse, Citation

router = APIRouter(prefix="/chat", tags=["chat"])
logger = logging.getLogger(__name__)

_INJECTION_RE = re.compile(
    r"ignore\s+previous\s+instructions?|<\|system\|>|<\|im_start\|>",
    re.IGNORECASE,
)
_MAX_QUERY_CHARS = 2000


def _get_chain(request: Request):
    return request.app.state.rag_chain

def _sanitize(query: str) -> str:
    query = query.strip()
    if len(query) > _MAX_QUERY_CHARS:
        query = query[:_MAX_QUERY_CHARS]
    if _INJECTION_RE.search(query):
        raise HTTPException(status_code=400, detail="query 包含不允许的内容")
    return query


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request) -> ChatResponse:
    """同步 RAG 问答，返回完整答案和引用列表"""
    query = _sanitize(req.query)
    try:
        result = await _get_chain(request).ainvoke(query, req.knowledge_base_id, req.top_k)
    except Exception as exc:
        logger.exception("chat error: query=%r", query[:80])
        raise HTTPException(status_code=500, detail="问答服务暂时不可用，请稍后重试") from exc
    return ChatResponse(
        answer=result["answer"],
        citations=[Citation(**c) for c in result["citations"]],
    )

@router.post("/stream")
async def chat_stream(req: ChatRequest, request: Request) -> StreamingResponse:
    """
    SSE 流式问答。

    事件格式（text/event-stream）：
        data: {"type": "token", "content": "..."}\\n\\n
        data: {"type": "citations", "data": [...]}\\n\\n
        data: [DONE]\\n\\n
    """
    query = _sanitize(req.query)
    chain = _get_chain(request)

    async def event_gen():
        try:
            async for kind, data in chain.astream_full(
                query, req.knowledge_base_id, req.top_k
            ):
                if await request.is_disconnected():
                    logger.warning("客户端已断开，提前终止生成: query=%r", query[:40])
                    break

                if kind == "token":
                    payload = json.dumps(
                        {"type": "token", "content": data}, ensure_ascii=False
                    )
                    yield f"data: {payload}\n\n"
                elif kind == "result":
                    payload = json.dumps(
                        {"type": "citations", "data": data["citations"]},
                        ensure_ascii=False
                    )
                    yield f"data: {payload}\n\n"
        except asyncio.CancelledError:
            logger.info("流式请求被取消: query=%r", query[:40])
            raise
        except Exception as exc:
            logger.exception("SSE stream error: query=%r", query[:80])
            error_payload = json.dumps(
                {"type": "error", "message": str(exc)}, ensure_ascii=False
            )
            yield f"data: {error_payload}\n\n"
        finally:
            yield "data: [DONE]\n\n"
    
    return StreamingResponse(event_gen(), media_type="text/event-stream")