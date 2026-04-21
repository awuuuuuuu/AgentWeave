"""
IngestionPipeline 单元测试（全 mock，无需真实 API / Milvus / 文件系统）

运行：
  uv run pytest backend/tests/pipeline/test_pipeline.py -v
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from ingestion.pipeline import IngestionPipeline, IngestionResult, PipelineConfig

from .conftest import (
    make_chunk, make_embedded,
    make_mock_embedder, make_mock_store, make_mock_splitter,
)


# ── 工具 ─────────────────────────────────────────────────────────────────────

def _pipeline(
    embedder=None,
    store=None,
    splitter=None,
    config=None,
) -> IngestionPipeline:
    return IngestionPipeline(
        embedder=embedder or make_mock_embedder(),
        store=store or make_mock_store(),
        splitter=splitter or make_mock_splitter(),
        config=config or PipelineConfig(),
    )


def _fake_file(tmp_path: Path, name: str = "doc.txt") -> Path:
    p = tmp_path / name
    p.write_text("dummy content")
    return p


# ── 基础行为 ──────────────────────────────────────────────────────────────────

class TestBasicBehavior:

    def test_empty_file_list_returns_zero(self, tmp_path):
        result = _pipeline().run([], knowledge_base_id="kb1")
        assert result.total_files == 0
        assert result.succeeded == 0
        assert result.total_chunks_written == 0

    def test_single_file_success(self, tmp_path):
        f = _fake_file(tmp_path)
        store = make_mock_store()
        store.upsert.return_value = 3

        with patch("ingestion.pipeline.get_parser") as mock_gp:
            mock_parser = MagicMock()
            mock_parser.parse.return_value = [make_chunk()]
            mock_gp.return_value = mock_parser

            result = _pipeline(store=store).run([f], knowledge_base_id="kb1")

        assert result.succeeded == 1
        assert result.failed == 0
        assert result.total_chunks_written == 3

    def test_result_counts_match_files(self, tmp_path):
        files = [_fake_file(tmp_path, f"doc{i}.txt") for i in range(5)]

        with patch("ingestion.pipeline.get_parser") as mock_gp:
            mock_parser = MagicMock()
            mock_parser.parse.return_value = [make_chunk()]
            mock_gp.return_value = mock_parser

            result = _pipeline().run(files, knowledge_base_id="kb1")

        assert result.total_files == 5
        assert result.succeeded == 5

    def test_four_layers_called_in_order(self, tmp_path):
        """Parser → Splitter → Embedder → Store 顺序调用。"""
        f = _fake_file(tmp_path)
        call_order = []

        splitter = make_mock_splitter()
        splitter.split.side_effect = lambda c: (call_order.append("split"), [make_chunk()])[1]

        embedder = make_mock_embedder()
        embedder.embed.side_effect = lambda c: (call_order.append("embed"), [make_embedded()])[1]

        store = make_mock_store()
        store.upsert.side_effect = lambda ec, **kw: (call_order.append("store"), 1)[1]

        with patch("ingestion.pipeline.get_parser") as mock_gp:
            mock_parser = MagicMock()
            mock_parser.parse.side_effect = lambda *a, **kw: (
                call_order.append("parse"), [make_chunk()]
            )[1]
            mock_gp.return_value = mock_parser

            _pipeline(embedder=embedder, store=store, splitter=splitter).run(
                [f], knowledge_base_id="kb1"
            )

        assert call_order == ["parse", "split", "embed", "store"]


# ── 文件路由 ──────────────────────────────────────────────────────────────────

class TestFileRouting:

    def test_get_parser_called_with_suffix(self, tmp_path):
        f = _fake_file(tmp_path, "report.pdf")

        with patch("ingestion.pipeline.get_parser") as mock_gp:
            mock_gp.return_value = MagicMock(parse=MagicMock(return_value=[make_chunk()]))
            _pipeline().run([f], knowledge_base_id="kb1")

        mock_gp.assert_called_once_with(".pdf")

    def test_pdf_parser_receives_strategy(self, tmp_path):
        """PdfParser 被调用时要传入 pdf_strategy。"""
        from ingestion.parsers.pdf_parser import PdfParser
        f = _fake_file(tmp_path, "report.pdf")

        mock_pdf_parser = MagicMock(spec=PdfParser)
        mock_pdf_parser.parse.return_value = [make_chunk()]

        with patch("ingestion.pipeline.get_parser", return_value=mock_pdf_parser):
            _pipeline(config=PipelineConfig(pdf_strategy="fast")).run(
                [f], knowledge_base_id="kb1"
            )

        mock_pdf_parser.parse.assert_called_once()
        _, kwargs = mock_pdf_parser.parse.call_args
        assert kwargs.get("strategy") == "fast"

    def test_non_pdf_parser_no_strategy_kwarg(self, tmp_path):
        """非 PDF parser 调用 parse() 时不传 strategy。"""
        f = _fake_file(tmp_path, "doc.docx")

        mock_parser = MagicMock()
        mock_parser.parse.return_value = [make_chunk()]

        with patch("ingestion.pipeline.get_parser", return_value=mock_parser):
            _pipeline().run([f], knowledge_base_id="kb1")

        _, kwargs = mock_parser.parse.call_args
        assert "strategy" not in kwargs


# ── 错误隔离 ──────────────────────────────────────────────────────────────────

class TestErrorIsolation:

    def test_single_file_failure_doesnt_stop_others(self, tmp_path):
        files = [_fake_file(tmp_path, f"doc{i}.txt") for i in range(3)]
        call_count = 0

        def fake_parse(path, **kw):
            nonlocal call_count
            call_count += 1
            if "doc1" in str(path):
                raise RuntimeError("解析失败")
            return [make_chunk()]

        with patch("ingestion.pipeline.get_parser") as mock_gp:
            mock_gp.return_value = MagicMock(parse=fake_parse)
            result = _pipeline(config=PipelineConfig(max_retries=0)).run(
                files, knowledge_base_id="kb1"
            )

        assert call_count == 3        # 3 个文件全部尝试（无重试）
        assert result.succeeded == 2
        assert result.failed == 1

    def test_failed_file_recorded_in_errors(self, tmp_path):
        f = _fake_file(tmp_path, "bad.txt")

        with patch("ingestion.pipeline.get_parser") as mock_gp:
            mock_gp.return_value = MagicMock(
                parse=MagicMock(side_effect=ValueError("坏文件"))
            )
            result = _pipeline(config=PipelineConfig(max_retries=0)).run(
                [f], knowledge_base_id="kb1"
            )

        assert "bad.txt" in result.errors
        assert "坏文件" in result.errors["bad.txt"]

    def test_empty_parse_result_writes_zero(self, tmp_path):
        f = _fake_file(tmp_path)

        with patch("ingestion.pipeline.get_parser") as mock_gp:
            mock_gp.return_value = MagicMock(parse=MagicMock(return_value=[]))
            result = _pipeline().run([f], knowledge_base_id="kb1")

        assert result.succeeded == 1
        assert result.total_chunks_written == 0

    def test_store_failure_marks_file_failed(self, tmp_path):
        f = _fake_file(tmp_path)
        store = make_mock_store()
        store.upsert.side_effect = RuntimeError("Milvus 写入失败")

        with patch("ingestion.pipeline.get_parser") as mock_gp:
            mock_gp.return_value = MagicMock(parse=MagicMock(return_value=[make_chunk()]))
            result = _pipeline(store=store, config=PipelineConfig(max_retries=0)).run(
                [f], knowledge_base_id="kb1"
            )

        assert result.failed == 1


# ── 重试机制 ──────────────────────────────────────────────────────────────────

class TestRetry:

    def test_retries_on_failure(self, tmp_path):
        f = _fake_file(tmp_path)
        call_count = 0

        def flaky_parse(path, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("第一次失败")
            return [make_chunk()]

        with patch("ingestion.pipeline.get_parser") as mock_gp, \
             patch("ingestion.pipeline.time.sleep"):
            mock_gp.return_value = MagicMock(parse=flaky_parse)
            result = _pipeline(config=PipelineConfig(max_retries=1)).run(
                [f], knowledge_base_id="kb1"
            )

        assert call_count == 2
        assert result.succeeded == 1

    def test_all_retries_exhausted_marks_failed(self, tmp_path):
        f = _fake_file(tmp_path)

        with patch("ingestion.pipeline.get_parser") as mock_gp, \
             patch("ingestion.pipeline.time.sleep"):
            mock_gp.return_value = MagicMock(
                parse=MagicMock(side_effect=RuntimeError("持续失败"))
            )
            result = _pipeline(config=PipelineConfig(max_retries=2)).run(
                [f], knowledge_base_id="kb1"
            )

        assert result.failed == 1

    def test_retry_count_correct(self, tmp_path):
        """max_retries=2 → 最多调用 3 次（1 次首次 + 2 次重试）。"""
        f = _fake_file(tmp_path)
        call_count = 0

        def always_fail(path, **kw):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("fail")

        with patch("ingestion.pipeline.get_parser") as mock_gp, \
             patch("ingestion.pipeline.time.sleep"):
            mock_gp.return_value = MagicMock(parse=always_fail)
            _pipeline(config=PipelineConfig(max_retries=2)).run(
                [f], knowledge_base_id="kb1"
            )

        assert call_count == 3

    def test_no_retry_when_max_retries_zero(self, tmp_path):
        f = _fake_file(tmp_path)
        call_count = 0

        def fail_once(path, **kw):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("fail")

        with patch("ingestion.pipeline.get_parser") as mock_gp:
            mock_gp.return_value = MagicMock(parse=fail_once)
            _pipeline(config=PipelineConfig(max_retries=0)).run(
                [f], knowledge_base_id="kb1"
            )

        assert call_count == 1

    def test_exponential_backoff_delay(self, tmp_path):
        """重试等待时间按指数增长：第1次 2s，第2次 4s。"""
        f = _fake_file(tmp_path)

        with patch("ingestion.pipeline.get_parser") as mock_gp, \
             patch("ingestion.pipeline.time.sleep") as mock_sleep:
            mock_gp.return_value = MagicMock(
                parse=MagicMock(side_effect=RuntimeError("fail"))
            )
            _pipeline(config=PipelineConfig(max_retries=2, retry_base_delay=2.0)).run(
                [f], knowledge_base_id="kb1"
            )

        sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
        assert sleep_calls == [2.0, 4.0]   # 2^0*2, 2^1*2；最后一次不 sleep

    def test_no_sleep_on_last_retry(self, tmp_path):
        """重试耗尽后不再 sleep，直接返回失败。"""
        f = _fake_file(tmp_path)

        with patch("ingestion.pipeline.get_parser") as mock_gp, \
             patch("ingestion.pipeline.time.sleep") as mock_sleep:
            mock_gp.return_value = MagicMock(
                parse=MagicMock(side_effect=RuntimeError("fail"))
            )
            _pipeline(config=PipelineConfig(max_retries=1, retry_base_delay=1.0)).run(
                [f], knowledge_base_id="kb1"
            )

        assert mock_sleep.call_count == 1   # max_retries=1 → 只 sleep 一次


# ── IngestionResult 汇总 ──────────────────────────────────────────────────────

class TestIngestionResult:

    def test_total_chunks_sums_all_files(self, tmp_path):
        files = [_fake_file(tmp_path, f"doc{i}.txt") for i in range(3)]
        store = make_mock_store()
        store.upsert.return_value = 4  # 每个文件写 4 条

        with patch("ingestion.pipeline.get_parser") as mock_gp:
            mock_gp.return_value = MagicMock(parse=MagicMock(return_value=[make_chunk()]))
            result = _pipeline(store=store).run(files, knowledge_base_id="kb1")

        assert result.total_chunks_written == 12

    def test_elapsed_seconds_positive(self, tmp_path):
        f = _fake_file(tmp_path)

        with patch("ingestion.pipeline.get_parser") as mock_gp:
            mock_gp.return_value = MagicMock(parse=MagicMock(return_value=[make_chunk()]))
            result = _pipeline().run([f], knowledge_base_id="kb1")

        assert result.elapsed_seconds >= 0

    def test_errors_dict_contains_failed_files(self, tmp_path):
        files = [_fake_file(tmp_path, f"f{i}.txt") for i in range(3)]

        def fail_on_f1(path, **kw):
            if "f1" in str(path):
                raise RuntimeError("err")
            return [make_chunk()]

        with patch("ingestion.pipeline.get_parser") as mock_gp:
            mock_gp.return_value = MagicMock(parse=fail_on_f1)
            result = _pipeline(config=PipelineConfig(max_retries=0)).run(
                files, knowledge_base_id="kb1"
            )

        assert set(result.errors.keys()) == {"f1.txt"}
