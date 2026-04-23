from __future__ import annotations

import tiktoken

from retrieval.base import RetrievedChunk
from .state import CitationMeta

_ENCODING = tiktoken.get_encoding("o200k_base")     # gpt-4o 系列的分词器
_TOKEN_BUDGET_RATIO = 0.97                          # 97% 截断，留 3% 给 prompt 结构开销
_MIN_REMAINING_TOKENS = 50                          # 剩余 budget 太少时不做截


def _count_tokens(text: str) -> int:
    return len(_ENCODING.encode(text))


class ContextBuilder:
    """
    将 RetrievedChunk 列表转化为结构化上下文字符串和引用元数据
    """

    def __init__(self, max_context_tokens: int = 6000) -> None:
        self._budget = int(max_context_tokens * _TOKEN_BUDGET_RATIO)
    
    def build(
        self,
        chunks: list[RetrievedChunk]
    ) -> tuple[str, list[CitationMeta], bool]:
        """
        构建上下文字符串和引用元数据

        当 chunk 超出剩余 budget 时，优先做 token 级文本截断而非直接丢弃

        Returns:
            context:     格式化后的 XML 上下文字符串，直接插入 prompt
            citations:   按引用编号排列的元数据列表
            has_context: False 表示 chunks 为空或全部超出 budget
        """
        if not chunks:
            return "", [], False
        
        parts: list[str] = []
        citations: list[CitationMeta] = []
        used_tokens = 0

        for idx, chunk in enumerate(chunks, start=1):
            header = f"[{idx}] 来源：{chunk.source_file} | {chunk.section_path}\n"
            header_tokens = _count_tokens(header)
            text_tokens = _count_tokens(chunk.text)
            block_tokens = header_tokens + text_tokens

            if used_tokens + block_tokens <= self._budget:
                parts.append(header + chunk.text)
                citations.append(CitationMeta(
                    ref=idx,
                    chunk_id=chunk.chunk_id,
                    source_file=chunk.source_file,
                    section_path=chunk.section_path
                ))
                used_tokens += block_tokens
            else:
                remaining = self._budget - used_tokens - header_tokens
                if remaining >= _MIN_REMAINING_TOKENS:
                    truncated_text = _ENCODING.decode(
                        _ENCODING.encode(chunk.text)[:remaining]
                    ) + "...(截断)"
                    parts.append(header + truncated_text)
                    citations.append(CitationMeta(
                        ref=idx,
                        chunk_id=chunk.chunk_id,
                        source_file=chunk.source_file,
                        section_path=chunk.section_path
                    ))
                break
        
        if not parts:
            return "", [], False
        
        context = "<context>\n" + "\n\n".join(parts) + "\n</context>"
        return context, citations, True