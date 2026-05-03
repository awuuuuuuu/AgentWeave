from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from retrieval.base import RetrievedChunk

@dataclass
class CitationMeta:
    """引用元数据，用于 ResponseParser 和 前端"""
    ref: int                # 答案中的编号，如 [1]
    chunk_id: str
    source_file: str
    section_path: str

class GraphState(TypedDict):
    """LangGraph 节点之间流转的状态变量"""
    query: str
    knowledge_base_id: str
    top_k: int
    # per-KB 检索设置（由 chat route 注入）
    retrieval_mode: str          # "vector" | "fulltext" | "hybrid"
    use_rerank: bool
    score_threshold: float
    hybrid_mode: str             # "weighted" | "rerank"（仅 hybrid 模式生效）
    vector_weight: float         # hybrid weighted 模式下语义权重，关键词权重 = 1 - vector_weight
    # retrieve 节点输出
    chunks: list[RetrievedChunk]
    # build_context 节点输出
    context: str                        # 已经格式化，可以直接拼接到 prompt
    citations: list[CitationMeta]
    has_context: bool                   # RAG是否检索到上下文，为 False 时，generate 节点启用无结果的降级策略
    # generate 节点输出
    answer: str
    cited_refs: list[int]               # 答案中出现的引用编号