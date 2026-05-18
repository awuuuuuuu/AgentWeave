"""
KBSearchTool — 知识库混合检索工具

复用 HybridRetriever + Reranker，与召回测试接口使用相同检索路径。
Agent 通过此工具向指定知识库提问，拿到语义相关文本片段。
"""
from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field

from agent.tools.base_tool import BaseTool, ToolResult
from retrieval.base import RetrievedChunk


class KBSearchArgs(BaseModel):
    query: Annotated[str, Field(description="检索查询语句")]
    kb_ids: Annotated[
        list[str],
        Field(description="要检索的知识库 ID 列表（可同时检索多个）")
    ]
    top_k: Annotated[int, Field(description="每个知识库返回的最大片段数", ge=1, le=20)] = 5
    mode: Annotated[
        str,
        Field(description="检索模式：hybrid（混合）/ vector（向量）/ fulltext（全文）"),
    ] = "hybrid"


class KBSearchTool(BaseTool):
    """向知识库执行混合检索，返回相关文本片段及来源"""

    name = "kb_search"
    description = (
        "在指定知识库中检索与查询最相关的文本片段。"
        "适合回答需要参考私有文档、上传资料的问题。"
    )
    cacheable = True
    timeout = 30

    def __init__(self, retriever: Any, reranker: Any | None = None) -> None:
        self._retriever = retriever
        self._reranker = reranker

    @classmethod
    def get_args_schema(cls) -> type[BaseModel]:
        return KBSearchArgs
    
    async def _arun(
        self,
        query: str,
        kb_ids: list[str],
        top_k: int = 5,
        mode: str = "hybrid"
    ) -> ToolResult:
        all_chunks: list[RetrievedChunk] = []

        for kb_id in kb_ids:
            chunks = await self._retriever.aretrieve_by_mode(
                query=query,
                knowledge_base_id=kb_id,
                mode=mode,
                top_k=top_k
            )
            all_chunks.extend(chunks)

        if self._reranker and all_chunks:
            all_chunks = await self._reranker.arerank(query=query, chunks=all_chunks, top_k=top_k)
        else:
            all_chunks = sorted(all_chunks, key=lambda c: c.fusion_score, reverse=True)[:top_k]

        if not all_chunks:
            return ToolResult(
                tool_name=self.name,
                content="未在知识库中找到相关内容。",
                metadata={"chunks": [], "query": query},
            )
        
        _MAX_CHARS = 8000
        lines: list[str] = []
        records: list[dict] = []
        total_chars = 0
        for i, chunk in enumerate(all_chunks, 1):
            # 父子切分模式：子块命中后返回父块完整上下文给 LLM
            display_text = chunk.extra_meta.get("parent_text") or chunk.text
            chunk_str = f"[{i}] {display_text}\n来源：{chunk.source_file}"
            if total_chars + len(chunk_str) > _MAX_CHARS:
                lines.append(f"... （已截断，更多结果请缩小查询范围）")
                break
            lines.append(chunk_str)
            total_chars += len(chunk_str)
            records.append(
                {
                    "chunk_id": chunk.chunk_id,
                    "text": display_text,
                    "source_file": chunk.source_file,
                    "score": round(chunk.rerank_score or chunk.fusion_score, 4),
                }
            )

        return ToolResult(
            tool_name=self.name,
            content="\n\n".join(lines),
            metadata={"chunks": records, "query": query, "kb_ids": kb_ids},
        )