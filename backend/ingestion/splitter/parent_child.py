from __future__ import annotations

from dataclasses import dataclass

from ..parsers.base import ParsedChunk
from .base import BaseSplitter
from .recursive import RecursiveConfig, RecursiveSplitter

@dataclass
class ParentChildConfig:
    parent_chunk_size: int = 512
    child_chunk_size: int = 128
    child_overlap: int = 16
    encoding_name: str = "cl100k_base"
    parent_separators: list[str] | None = None  # None = 使用 RecursiveSplitter 默认分隔符
    child_separators: list[str] | None = None   # None = 使用 RecursiveSplitter 默认分隔符

class ParentChildSplitter(BaseSplitter):
    """
    父子两级切分

    父块（parent）：较大的语义单元
    子块（child）：精细切分，送入向量库做 embedding 检索

    每个子块 metadata 中携带：
    - parent_text：所属父块的完整文本
    - parent_index：父块编号
    - chunk_index / chunk_total：子块在父块内的位置

    检索流程：
      向量库检索子块 → 取 parent_text → 拼入 LLM prompt
    """

    def __init__(self, config: ParentChildConfig | None = None) -> None:
        cfg = config or ParentChildConfig()
        super().__init__(cfg.encoding_name)
        self.config = cfg

        parent_cfg = RecursiveConfig(
            chunk_size=cfg.parent_chunk_size,
            chunk_overlap=0,
            encoding_name=cfg.encoding_name,
        )
        if cfg.parent_separators:
            parent_cfg.separators = cfg.parent_separators
        self._parent_splitter = RecursiveSplitter(parent_cfg)

        child_cfg = RecursiveConfig(
            chunk_size=cfg.child_chunk_size,
            chunk_overlap=cfg.child_overlap,
            encoding_name=cfg.encoding_name,
        )
        if cfg.child_separators:
            child_cfg.separators = cfg.child_separators
        self._child_splitter = RecursiveSplitter(child_cfg)

    def _split_chunk(self, chunk: ParsedChunk) -> list[ParsedChunk]:
        parent_chunks = self._parent_splitter.split([chunk])

        results: list[ParsedChunk] = []
        for p_idx, parent in enumerate(parent_chunks):
            
            child_chunks = self._child_splitter.split([parent])
            for child in child_chunks:
                results.append(ParsedChunk(
                    text=child.text,
                    metadata={
                        **child.metadata,
                        "parent_text": parent.text,
                        "parent_index": p_idx,
                        # TODO(Step3-KV分离): parent_text 直接存入 metadata 导致存储冗余
                        # 每个父块被切成 N 个子块，parent_text 会被复制 N 次写入向量库。
                        # 待 Step 3 Milvus 入库时重构：
                        #   1. 为每个父块生成唯一 parent_id（UUID）
                        #   2. 将 {parent_id: parent.text} 存入 Redis/文档库
                        #   3. 子块 metadata 只存 parent_id，检索时再查 KV 取全文
                        # 检索时：向量库查出子块 -> 拿到 parent_id -> 去 KV 库秒查 parent.text -> 喂给 LLM。
                    }
                ))

        return results