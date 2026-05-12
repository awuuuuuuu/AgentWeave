"""
Researcher 子图：带 Agentic RAG 自校正循环的检索-生成 Agent
"""
from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

from .prompts import (
    GRADE_DOCS_SYSTEM,
    RESEARCHER_GENERATE_SYSTEM,
    REWRITE_QUERY_SYSTEM,
)
from .state import MAX_REWRITES, ResearcherState

logger = logging.getLogger(__name__)

AGENT_CARD = {
    "name": "researcher",
    "description": "从企业内部知识库检索信息，回答需要参考文档的问题；支持自动改写查询词和多轮自校正",
    "routing_hint": "需要查阅内部文档、知识库、历史记录、技术规范时",
    "tools": ["kb_search"],
    "icon": "🔍",
    "color": "blue",
}

_RETRIEVE_TOP_K = 5
_DOC_MAX_CHARS = 1500   # 单条文档截断，防止 context 爆炸

# 模型有时会在改写结果前加礼貌性前缀，清洗掉
_REWRITE_PREAMBLE_PREFIXES = (
    "改写后的查询为：", "改写后的查询为:", "改写后：", "改写后:",
    "优化查询：", "优化查询:", "查询词：", "查询词:",
    "改写：", "改写:", "优化后：", "优化后:",
)


def _strip_rewrite_preamble(text: str) -> str:
    for prefix in _REWRITE_PREAMBLE_PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix):].strip()
    return text


def build_researcher(
    retriever: Any,
    reranker: Any | None,
    llm_model: str = "gpt-4o",
) -> Any:
    """
    工厂函数：注入检索基础设施，返回编译后的 Researcher 子图。

    Researcher 只检索企业内部知识库。KB 中没有答案时如实告知，不做联网降级。
    """
    llm = ChatOpenAI(model=llm_model, temperature=0)

    # ── 节点：文档检索 ────────────────────────────────────────────────────────

    async def retrieve_docs(state: ResearcherState) -> dict:
        """多知识库并发检索，可选 rerank"""
        query = state["task"]
        kb_ids = state.get("kb_ids") or []

        all_chunks: list[Any] = await retriever.aretrieve_by_mode(
            query=query,
            kb_ids=kb_ids,
            mode="hybrid",
            top_k=_RETRIEVE_TOP_K
        )

        if reranker and all_chunks:
            try:
                all_chunks = await reranker.arerank(
                    query=query, chunks=all_chunks, top_k=_RETRIEVE_TOP_K
                )
            except Exception:
                logger.warning("Researcher: rerank 失败，降级使用 fusion_score 排序")

        all_chunks = sorted(
            all_chunks, key=lambda c: c.fusion_score, reverse=True
        )[:_RETRIEVE_TOP_K]

        docs = [
            {
                "id": i + 1,
                "content": c.text[:_DOC_MAX_CHARS],
                "source": c.source_file,
                "section_path": getattr(c, "section_path", ""),
                "chunk_index": getattr(c, "chunk_index_in_doc", 0),
                "score": round(
                    getattr(c, "rerank_score", None) or getattr(c, "fusion_score", 0.0), 4
                ),
                "vector_score": round(getattr(c, "vector_score", 0.0), 4),
                "bm25_score": round(getattr(c, "bm25_score", 0.0), 4),
            }
            for i, c in enumerate(all_chunks)
        ]
        logger.info(
            "Researcher: 检索到 %d 条文档 | query=%r", len(docs), query[:60]
        )
        return {"retrieved_docs": docs}
    
    # ── 节点：相关性评估 ──────────────────────────────────────────────────────

    async def grade_docs(state: ResearcherState) -> dict:
        """LLM 评估检索结果是否足够回答问题"""
        docs = state.get("retrieved_docs") or []
        task = state["task"]

        if not docs:
            return {"docs_relevant": False}

        doc_preview = "\n\n".join(
            f"[{d['id']}] {d['source']}\n{d['content'][:600]}" for d in docs[:4]
        )
        resp = await llm.ainvoke(
            [
                SystemMessage(content=GRADE_DOCS_SYSTEM),
                HumanMessage(content=f"问题：{task}\n\n检索到的文档：\n{doc_preview}"),
            ]
        )
        try:
            result = json.loads(resp.content.strip())
            relevant = bool(result.get("relevant", False))
        except (json.JSONDecodeError, AttributeError):
            relevant = True

        logger.info(
            "Researcher: 相关性=%s | rewrite_count=%d/%d",
            relevant, state.get("rewrite_count", 0), MAX_REWRITES,
        )
        return {"docs_relevant": relevant}
    
    # ── 节点：查询重写 ────────────────────────────────────────────────────────

    async def rewrite_query(state: ResearcherState) -> dict:
        """重写查询以提升召回效果"""
        original = state["task"]
        resp = await llm.ainvoke(
            [
                SystemMessage(content=REWRITE_QUERY_SYSTEM),
                HumanMessage(content=f"原始查询：{original}"),
            ]
        )

        new_query = _strip_rewrite_preamble(resp.content.strip())
        new_count = state.get("rewrite_count", 0) + 1
        logger.info(
            "Researcher: 重写查询 (%d/%d) | %r → %r",
            new_count, MAX_REWRITES, original[:50], new_query[:50],
        )
        return {"task": new_query, "rewrite_count": new_count}
    
    # ── 节点：答案生成 ────────────────────────────────────────────────────────

    async def generate_answer(state: ResearcherState) -> dict:
        """基于检索文档生成最终答案，并构建引用列表"""
        docs = state.get("retrieved_docs") or []
        task = state["task"]

        # 构建上下文块
        if docs:
            context = "\n\n".join(
                f"[{d['id']}] 来源：{d['source']}\n{d['content']}" for d in docs
            )
            context_block = f"<context>\n{context}\n</context>"
        else:
            context_block = "<context>（未找到相关文档，请根据通用知识回答）</context>"

        # 取最近一条 HumanMessage 作为用户问题（备用：task）
        messages = state.get("messages") or []
        user_msgs = [m for m in messages if isinstance(m, HumanMessage)]
        user_query = user_msgs[-1].content if user_msgs else task

        resp = await llm.ainvoke(
            [
                SystemMessage(content=RESEARCHER_GENERATE_SYSTEM),
                HumanMessage(content=f"{context_block}\n\n问题：{user_query}"),
            ]
        )
        answer = resp.content.strip()

        citations = [
            {"id": d["id"], "source": d["source"], "score": d["score"]}
            for d in docs
        ]

        logger.info(
            "Researcher: 生成答案 %d 字 | 引用 %d 条", len(answer), len(citations)
        )
        return {
            "citations": citations,
            "messages": [AIMessage(content=answer)],
        }

    # ── 路由：评估后决策 ──────────────────────────────────────────────────────

    def route_after_grade(state: ResearcherState) -> str:
        if state.get("docs_relevant", False):
            return "generate"
        if state.get("rewrite_count", 0) >= MAX_REWRITES:
            logger.info("Researcher: 达到最大重写次数，强制生成")
            return "generate"
        return "rewrite"

    # ── 组装子图 ──────────────────────────────────────────────────────────────

    graph = StateGraph(ResearcherState)
    graph.add_node("retrieve", retrieve_docs)
    graph.add_node("grade", grade_docs)
    graph.add_node("rewrite", rewrite_query)
    graph.add_node("generate", generate_answer)

    graph.set_entry_point("retrieve")
    graph.add_edge("retrieve", "grade")
    graph.add_conditional_edges(
        "grade",
        route_after_grade,
        {"generate": "generate", "rewrite": "rewrite"},
    )
    graph.add_edge("rewrite", "retrieve")
    graph.add_edge("generate", END)

    return graph.compile()

