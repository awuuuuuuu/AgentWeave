from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Union

from .embedder.base import BaseEmbedder
from .parsers.base import get_parser
from .parsers.pdf_parser import PdfParser
from .splitter.base import BaseSplitter
from .splitter.recursive import RecursiveSplitter
from .store.milvus_store import MilvusStore

logger = logging.getLogger(__name__)

@dataclass
class PipelineConfig:
    pdf_strategy: str = "smart"         # PDF的解析策略 "fast" | "smart" | "hi_res"
    max_retries: int = 1                # 文件处理失败的最多重试次数
    retry_base_delay: float = 2.0       # 指数退避的基础等待秒数

@dataclass
class FileResult:
    """单个文件的处理结果"""
    filename: str
    succeeded: bool
    chunks_written: int = 0
    error: str = ""
    elapsed_seconds: float = 0.0

@dataclass
class IngestionResult:
    """一批文件的结果汇总"""
    total_files: int
    succeeded: int
    failed: int
    total_chunks_written: int
    errors: dict[str, str]          # {filename : error_message}
    elapsed_seconds: float = 0.0

    @classmethod
    def from_file_results(cls, results: list[FileResult], elapsed: float) -> IngestionResult:
        succeeded = [r for r in results if r.succeeded]
        failed = [r for r in results if not r.succeeded]
        return cls(
            total_files=len(results),
            succeeded=len(succeeded),
            failed=len(failed),
            total_chunks_written=sum(r.chunks_written for r in succeeded),
            errors={r.filename: r.error for r in failed},
            elapsed_seconds=elapsed,
        )
    
class IngestionPipeline:
    """文档摄入 Pipeline：Parser → Splitter → Embedder → Store。"""

    def __init__(
        self,
        embedder: BaseEmbedder,
        store: MilvusStore,
        splitter: BaseSplitter | None = None,
        config: PipelineConfig | None = None
    ) -> None:
        self._embedder = embedder
        self._store = store
        self._splitter = splitter or RecursiveSplitter()
        self._cfg = config or PipelineConfig()

    def run(
        self,
        files: list[Union[str, Path]],
        knowledge_base_id: str,
    ) -> IngestionResult:
        """
        批量摄入文件

        Args:
            files:              文件路径列表
            knowledge_base_id:  目标知识库 ID

        Returns:
            IngestionResult 汇总统计
        """

        start = time.monotonic()
        file_results: list[FileResult] = []

        for idx, file_path in enumerate(files, start = 1):
            path = Path(file_path)
            logger.info("[%d/%d] 开始处理: %s", idx, len(files), path.name)
            
            result = self._process_with_retry(path, knowledge_base_id)
            file_results.append(result)

            if result.succeeded:
                logger.info("[%d/%d] ✓ %s  写入 %d 条  %.1fs", idx, len(files), path.name, result.chunks_written, result.elapsed_seconds)
            else:
                logger.warning("[%d/%d] ✗ %s  %s", idx, len(files), path.name, result.error)
            
        total_elapsed = time.monotonic() - start
        summary = IngestionResult.from_file_results(file_results, total_elapsed)

        logger.info("摄入完成：%d/%d 成功，写入 %d 个 chunk，耗时 %.1fs", summary.succeeded, summary.total_files, summary.total_chunks_written, summary.elapsed_seconds)
        return summary
    
    def _process_with_retry(
        self,
        path: Path,
        knowledge_base_id: str
    ) -> FileResult:
        """单个文件的处理流"""
        last_error = ""
        for attempt in range(self._cfg.max_retries + 1):
            is_last = attempt == self._cfg.max_retries
            try:
                start = time.monotonic()
                chunks_written = self._process_file(path, knowledge_base_id)
                return FileResult(
                    filename=path.name,
                    succeeded=True,
                    chunks_written=chunks_written,
                    elapsed_seconds=time.monotonic() - start
                )
            except Exception as exc:
                last_error = str(exc)
                if not is_last:
                    wait = self._cfg.retry_base_delay * (2 ** attempt)
                    logger.warning("%s 处理失败（第 %d/%d 次），%.1fs 后重试：%s", path.name, attempt + 1, self._cfg.max_retries, wait, exc)
                    time.sleep(wait)

        return FileResult(
            filename=path.name,
            succeeded=False,
            error=last_error
        )
    
    def _process_file(self, path: Path, knowledge_base_id: str) -> str:
        """
        单文件的四层处理链，返回写入的 chunk 数量
        """

        # 1. Parser： 根据扩展名路由，PDF 透传 strategy 配置
        parser = get_parser(path.suffix)
        parse_kwargs: dict = {}
        if isinstance(parser, PdfParser):
            parse_kwargs["strategy"] = self._cfg.pdf_strategy
        raw_chunks = parser.parse(path, **parse_kwargs)

        if not raw_chunks:
            logger.debug("%s 解析结果为空，跳过", path.name)
            return 0
        
        # 2. Splitter
        split_chunks = self._splitter.split(raw_chunks)

        # 3. Embedder
        embedded = self._embedder.embed(split_chunks)

        # 4. Store
        written = self._store.upsert(embedded, knowledge_base_id=knowledge_base_id)

        return written