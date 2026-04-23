from __future__ import annotations

import asyncio
import logging
import re
from typing import Callable

from langchain_core.language_models.chat_models import BaseChatModel

from prompts.rag_answer import RAG_PROMPT
from retrieval.hybrid_retriever import HybridRetriever
from retrieval.reranker import Reranker
from .context_builder import ContextBuilder
from .state import GraphState

logger = logging.getLogger(__name__)

_CITATION_RE = re.compile(r"\[(\d+)\]")

# 无命中context时的硬编码兜底回复（不走 LLM，零成本，零延迟）
_FALLBACK_ANSWER = "根据现有文档，我无法找到与您问题相关的信息。请尝试换一种表述，或确认该内容是否已上传至知识库。"



# ---------------------------------------------------------------------------
# Node factories（使用闭包返回可注册到 StateGraph 的节点函数）
# ---------------------------------------------------------------------------
def make_retrieve_node(
    retriever: HybridRetriever,
    reranker: Reranker | None,
    rerank_fetch: int = 20
) -> Callable[[GraphState], dict]:
    """
    构建 retrieve 节点

    使用 reranker 时先取 rerank_fetch 条候选，再精排到 top_k
    不使用 reranker 时直接取 top_k
    """
    async def retrieve_node(state: GraphState) -> dict:
        top_k = state["top_k"]
        fetch_k = rerank_fetch if reranker is not None else top_k

        chunks = await retriever.aretrieve(
            query=state["query"],
            knowledge_base_id=state["knowledge_base_id"],
            top_k=fetch_k
        )

        if reranker is not None:
            chunks = await asyncio.to_thread(
                reranker.rerank, state["query"], chunks, top_k
            )
        else:
            chunks = chunks[:top_k]
        
        return {"chunks": chunks}

    return retrieve_node


def make_build_context_node(
    max_context_tokens: int = 6000
) -> callable[[GraphState], dict]:
    """
    构建 build_context 节点，注入 max_context_tokens 配置
    """
    builder = ContextBuilder(max_context_tokens=max_context_tokens)

    def build_context_node(state: GraphState) -> dict:
        context, citations, has_context = builder.build(state["chunks"])
        return {
            "context": context,
            "citations": citations,
            "has_context": has_context,
        }
    
    return build_context_node


def fallback_node(state: GraphState) -> dict:
    """has_context=False 时直接降级回复，不调用 LLM"""
    return {"answer": _FALLBACK_ANSWER, "cited_refs": []}


def make_generate_node(llm: BaseChatModel) -> Callable[[GraphState], dict]:
    """
    构建 generate 节点
    """

    chain = RAG_PROMPT | llm

    async def generate_node(state: GraphState) -> dict:
        msg = await chain.ainvoke({"context": state["context"], "question": state["query"]})
        answer: str = msg.content

        # 提取答案中实际出现的引用编号（去重，保序）
        max_ref = max((c.ref for c in state["citations"]), default=0)
        cited_refs = list(dict.fromkeys(
            int(m) for m in _CITATION_RE.findall(answer)
            if 1 <= int(m) <= max_ref
        ))

        if not cited_refs:
            logger.warning(
                "generate_node: has_context=True 但答案中未提取到有效引用，"
                "query=%r（可能是模型未遵循引用格式）",
                state["query"][:80],
            )
        
        return {"answer": answer, "cited_refs": cited_refs}

    return generate_node


def route_after_context(state: GraphState) -> str:
    """build_context 后的条件路由：无命中 → fallback，有命中 → generate。"""
    return "generate" if state["has_context"] else "fallback"