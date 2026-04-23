from __future__ import annotations

from typing import AsyncIterator

from langgraph.graph import START, END, StateGraph

from ingestion.embedder.openai_embedder import OpenAIEmbedder
from ingestion.store.milvus_store import MilvusStoreConfig
from model_router.providers.openai_provider import create_llm
from retrieval.hybrid_retriever import HybridRetriever, HybridRetrieverConfig
from retrieval.reranker import Reranker, RerankerConfig

from .nodes import (
    make_retrieve_node,
    make_build_context_node,
    route_after_context,
    make_generate_node,
    fallback_node,
)
from .settings import RAGChainSettings
from .state import GraphState


class RAGChain:
    """RAG Chain: HybridRetriever → ContextBuilder → LLM"""

    def __init__(self, graph: StateGraph, settings: RAGChainSettings) -> None:
        self._graph = graph.compile()
        self._settings = settings

    # ------------------------------------------------------------------
    # 工厂方法
    # ------------------------------------------------------------------

    @classmethod
    def from_settings(cls, settings: RAGChainSettings | None = None) -> "RAGChain":
        cfg = settings or RAGChainSettings()

        store_cfg = MilvusStoreConfig(uri=cfg.milvus_uri)
        embedder = OpenAIEmbedder()
        retriever = HybridRetriever(
            embedder=embedder,
            config=HybridRetrieverConfig(
                store_config=store_cfg,
                candidate_multiplier=cfg.candidate_multiplier
            )
        )

        reranker: Reranker | None = None
        if cfg.use_reranker:
            reranker = Reranker(RerankerConfig(model_name=cfg.reranker_model))

        llm = create_llm(
            model=cfg.llm_model,
            max_tokens=cfg.output_reserve_tokens
        )

        graph = cls._build_graph(retriever, reranker, llm, cfg.max_context_tokens)
        return cls(graph, cfg)
    
    @classmethod
    def _build_graph(retriever, reranker, llm, max_context_tokens: int) -> StateGraph:
        g = StateGraph(GraphState)

        g.add_node("retrieve", make_retrieve_node(retriever, reranker))
        g.add_node("build_context", make_build_context_node(max_context_tokens))
        g.add_node("generate", make_generate_node(llm))
        g.add_node("fallback", fallback_node)

        g.add_edge(START, "retrieve")
        g.add_edge("retrieve", "build_context")
        g.add_conditional_edges(
            "build_context", 
            route_after_context, 
            {
                "generate": "generate",
                "fallback": "fallback"
            }
        )
        g.add_edge("generate", END)
        g.add_edge("fallback", END)
        return g
    
    def invoke(
        self,
        query: str,
        knowledge_base_id: str,
        top_k: int = 5
    ) -> dict:
        """
        同步调用, 返回完整结果
        
        Returns:
            {"answer": str, "citations": list[CitationMeta], "chunks": list}
        """

        result: GraphState = self._graph.invoke(
            _init_state(query, knowledge_base_id, top_k)
        )
        return _format_result(result)
    
    async def ainvoke(
        self,
        query: str,
        knowledge_base_id: str,
        top_k: int = 5
    ) -> dict:
        """异步调用(不流式), 返回完整结果"""
        result: GraphState = await self._graph.ainvoke(
            _init_state(query, knowledge_base_id, top_k)
        )
        return _format_result(result)
    

    async def astream_full(
        self,
        query: str,
        knowledge_base_id: str,
        top_k: int = 5,
    ) -> AsyncIterator[tuple[str, object]]:
        """
        异步流式调用
        单次 graph 执行，同时产出 token 和最终引用元数据。

        Yields:
            ("token", str)   — 逐 token LLM 输出
            ("result", dict) — graph 完成后的完整结果（answer + citations）
        """
        final_state: GraphState | None = None

        async for event in self._graph.astream_events(
            _init_state(query, knowledge_base_id, top_k),
            version="v2"
        ):
            kind = event["event"]

            if (
                kind == "on_chat_model_stream"
                and event.get("metadata", {}).get("langgraph_node") == "generate"
            ):
                token: str = event["data"]["chunk"].content
                if token:
                    yield ("token", token)

            elif kind == "on_chain_end":
                output = event["data"].get("output")
                # LangGraph 根图结束时 output 是最终 GraphState dict
                if isinstance(output, dict) and "answer" in output:
                    final_state = output
        
        if final_state is not None:
            yield ("result", _format_result(final_state))



def _init_state(query: str, knowledge_base_id: str, top_k: int) -> dict:
    return {
        "query": query,
        "knowledge_base_id": knowledge_base_id,
        "top_k": top_k,
        "chunks": [],
        "context": "",
        "citations": [],
        "has_context": False,
        "answer": "",
        "cited_refs": [],
    }

def _format_result(state: GraphState) -> dict:
    cited_set = set(state.get("cited_refs", []))
    used_citations = [c for c in state.get("citations", []) if c.ref in cited_set]
    return {
        "answer": state.get("answer", ""),
        "citations": [
            {
                "ref": c.ref,
                "source_file": c.source_file,
                "section_path": c.section_path,
                "chunk_id": c.chunk_id,
            }
            for c in used_citations
        ],
        "chunks": state.get("chunks", []),
    }