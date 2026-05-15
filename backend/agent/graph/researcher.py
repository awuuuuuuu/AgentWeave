"""
Researcher 子图：带 Agentic RAG 自校正循环的检索-生成 Agent
"""
from __future__ import annotations

import json
import logging
import re
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
        logger.info("Researcher.retrieve_docs: query=%r | kb_ids=%s", query[:80], kb_ids)

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

    # 评分阈值：fusion/rerank score 高于此值直接视为相关，跳过 LLM 评估
    _SCORE_SHORTCUT = 0.45

    async def grade_docs(state: ResearcherState) -> dict:
        """评估检索结果是否足够回答问题。

        策略：先用检索分数快速判断（避免 LLM 误判领域术语）；
        仅当最高分低于阈值时才调 LLM 二次评估。
        同时将当前 retrieved_docs 更新到 best_retrieved_docs（保留历次最佳召回）。
        """
        docs = state.get("retrieved_docs") or []

        if not docs:
            return {"docs_relevant": False}

        # 保存当前这批文档为候选最佳（generate_answer 会优先用这批）
        # 只在本批 best_score 优于之前时更新
        best_docs = state.get("best_retrieved_docs") or []
        current_best = max(d.get("score", 0.0) for d in docs)
        prev_best = max((d.get("score", 0.0) for d in best_docs), default=0.0)
        update = {"best_retrieved_docs": docs} if current_best >= prev_best else {}

        # 高分直接通过，无需 LLM
        if current_best >= _SCORE_SHORTCUT:
            logger.info(
                "Researcher: 高分召回 (%.3f >= %.2f)，跳过 LLM 评估",
                current_best, _SCORE_SHORTCUT,
            )
            return {"docs_relevant": True, **update}

        # 低分走 LLM 评估
        task = state["task"]
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
            relevant = True  # 解析失败时保守放行

        logger.info(
            "Researcher: LLM 评估 relevant=%s (score=%.3f) | rewrite=%d/%d",
            relevant, current_best, state.get("rewrite_count", 0), MAX_REWRITES,
        )
        return {"docs_relevant": relevant, **update}
    
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
        # 优先用最新一批，没有时回退到历史最佳批（防止重写后零召回导致空答案）
        docs = state.get("retrieved_docs") or state.get("best_retrieved_docs") or []
        task = state["task"]

        # 构建上下文块
        if docs:
            context = "\n\n".join(
                f"[{d['id']}] 来源：{d['source']}\n{d['content']}" for d in docs
            )
            context_block = f"<context>\n{context}\n</context>"
        else:
            context_block = "<context>（未找到相关文档，请根据通用知识回答）</context>"

        resp = await llm.ainvoke(
            [
                SystemMessage(content=RESEARCHER_GENERATE_SYSTEM),
                HumanMessage(content=f"{context_block}\n\n问题：{task}"),
            ]
        )
        answer = resp.content.strip()

        # 只保留答案中实际引用过的编号，并重新连续编号（[2][5] → [1][2]）
        # 若 LLM 未添加任何 [N] 标记（如纯表格输出），回退到展示全部检索文档

        # LLM 有时输出全角方括号 【N】，统一规范化为半角 [N] 再处理
        answer = answer.replace('【', '[').replace('】', ']')
        used_ids = {int(m) for m in re.findall(r'\[(\d+)\]', answer)}
        used_docs = [d for d in docs if d["id"] in used_ids] if used_ids else docs

        if used_ids:
            # 有引用标记：建立旧→新连续编号映射，重写答案
            ref_remap = {d["id"]: new_i + 1 for new_i, d in enumerate(used_docs)}
            def _remap(m: re.Match) -> str:
                old = int(m.group(1))
                return f"[{ref_remap[old]}]" if old in ref_remap else ""
            answer = re.sub(r'\[(\d+)\]', _remap, answer)
        else:
            # 无引用标记（如纯表格输出）：顺序编号，不修改答案文本
            ref_remap = {d["id"]: new_i + 1 for new_i, d in enumerate(used_docs)}

        citations = [
            {
                "ref": ref_remap[d["id"]],
                "source_file": d["source"],
                "section_path": d.get("section_path", ""),
                "chunk_id": f"{d['source']}_{d.get('chunk_index', i)}",
                "score": d["score"],
                "snippet": d["content"][:300],
            }
            for i, d in enumerate(used_docs)
        ]

        logger.info(
            "Researcher: 生成答案 %d 字 | 引用 %d/%d 条 (cited_refs=%s, fallback=%s)",
            len(answer), len(citations), len(docs), sorted(used_ids), not used_ids,
        )
        return {
            "citations": citations,
            "messages": [AIMessage(content=answer)],
            "researcher_count": state.get("researcher_count", 0) + 1,
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

