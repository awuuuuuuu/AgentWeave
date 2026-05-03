from __future__ import annotations

import asyncio
import logging
import re
from typing import Callable

from langchain_core.language_models.chat_models import BaseChatModel

from prompts.rag_answer import RAG_PROMPT
from retrieval.hybrid_retriever import HybridRetriever
from retrieval.reranker import Reranker
from retrieval.base import BaseRetriever
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
    hybrid_retriever: HybridRetriever,
    reranker: Reranker | None,
    rerank_fetch: int = 20
) -> Callable[[GraphState], dict]:
    """
    构建 retrieve 节点。

    per-KB 设置从 state 读取：
      - retrieval_mode: "vector" | "fulltext" | "hybrid"
      - use_rerank: bool（需要 reranker 实例存在才生效）
      - score_threshold: float（过滤低分 chunk）
    """
    async def retrieve_node(state: GraphState) -> dict:
        top_k = state["top_k"]
        mode = state.get("retrieval_mode", "hybrid")
        threshold = state.get("score_threshold", 0.0)
        hybrid_mode = state.get("hybrid_mode", "weighted")
        vector_weight = state.get("vector_weight", 0.7)

        # hybrid rerank 子模式：先双路召回再精排
        # 其他情况：use_rerank 控制是否精排
        if mode == "hybrid" and hybrid_mode == "rerank":
            use_rr = reranker is not None
        else:
            use_rr = state.get("use_rerank", True) and reranker is not None

        fetch_k = rerank_fetch if use_rr else top_k

        if mode == "vector":
            chunks = await hybrid_retriever._vector.aretrieve(
                query=state["query"],
                knowledge_base_id=state["knowledge_base_id"],
                top_k=fetch_k,
            )
            for c in chunks:
                c.fusion_score = c.vector_score
        elif mode == "fulltext":
            chunks = await hybrid_retriever._bm25.aretrieve(
                query=state["query"],
                knowledge_base_id=state["knowledge_base_id"],
                top_k=fetch_k,
            )
            for c in chunks:
                c.fusion_score = c.bm25_score
        else:  # hybrid
            # weighted 模式用 vector_weight 作为 alpha；rerank 模式用等权融合后精排
            alpha = vector_weight if hybrid_mode == "weighted" else 0.5
            chunks = await hybrid_retriever.aretrieve(
                query=state["query"],
                knowledge_base_id=state["knowledge_base_id"],
                top_k=fetch_k,
                alpha=alpha,
            )

        if use_rr:
            chunks = await asyncio.to_thread(
                reranker.rerank, state["query"], chunks, top_k
            )
        else:
            chunks = chunks[:top_k]

        # score_threshold 过滤（仅 vector/hybrid 模式有效；BM25 分数无上界，fulltext 跳过）
        if threshold > 0.0 and mode != "fulltext":
            chunks = [c for c in chunks if c.fusion_score >= threshold]

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