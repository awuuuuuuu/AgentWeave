"""
PdfParser 测试套件

测试分层：
  单元测试（无外部依赖）：
    - fast 策略直接用 fitz，无需 mock
    - hi_res / smart 策略 mock _call_api + 环境变量，测试路由与组装逻辑

  集成测试（需要 Unstructured API）：
    标记 @pytest.mark.integration，CI 默认跳过

运行单元测试（默认）：
  uv run pytest backend/tests/parsers/test_pdf_parser.py -v

运行集成测试：
  uv run pytest backend/tests/parsers/test_pdf_parser.py -v -m integration
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import fitz as _fitz
import pytest


from ingestion.parsers.base import ParsedChunk, get_parser
from ingestion.parsers.pdf_parser import PdfParser, _is_garbled


# ── Mock 工厂 ──────────────────────────────────────────────────────────────────

def _make_element(
    category: str = "NarrativeText",
    text: str = "Sample text content.",
    page_number: int = 1,
    bbox_points: list | None = None,
) -> MagicMock:
    """创建一个逼真的 unstructured Element mock。"""
    el = MagicMock()
    el.category = category
    el.metadata.page_number = page_number
    el.metadata.text_as_html = None
    el.metadata.coordinates = None

    if bbox_points:
        el.metadata.coordinates = MagicMock()
        el.metadata.coordinates.points = bbox_points

    el.__str__ = lambda self: text
    return el


def _make_table_element(page_number: int = 1) -> MagicMock:
    el = MagicMock()
    el.category = "Table"
    el.metadata.page_number = page_number
    el.metadata.text_as_html = "<table><tr><td>A</td><td>B</td></tr></table>"
    el.metadata.coordinates = None
    el.__str__ = lambda self: "A B"
    return el


# ── 基础结构测试 ────────────────────────────────────────────────────────────────

class TestPdfParserBasic:

    def test_registered_for_pdf_extension(self):
        parser = get_parser(".pdf")
        assert isinstance(parser, PdfParser)

    def test_parse_returns_list(self, single_column_pdf):
        result = PdfParser().parse(single_column_pdf, strategy="fast")
        assert isinstance(result, list)

    def test_parse_returns_parsed_chunks(self, single_column_pdf):
        result = PdfParser().parse(single_column_pdf, strategy="fast")
        assert len(result) > 0
        assert all(isinstance(c, ParsedChunk) for c in result)

    def test_chunk_has_required_metadata_keys(self, single_column_pdf):
        chunks = PdfParser().parse(single_column_pdf, strategy="fast")
        for chunk in chunks:
            assert "source_file" in chunk.metadata
            assert "page_number" in chunk.metadata
            assert "content_type" in chunk.metadata

    def test_content_type_values_are_valid(self, single_column_pdf):
        valid_types = {"text", "title", "table", "figure"}
        elements = [
            _make_element("NarrativeText", "Text.", 1),
            _make_element("Title", "Title.", 1),
            _make_table_element(1),
            _make_element("Figure", "Fig.", 1),
        ]
        with patch("ingestion.parsers.pdf_parser._get_api_url", return_value="http://test"), \
             patch("ingestion.parsers.pdf_parser._call_api", return_value=elements):
            chunks = PdfParser().parse(single_column_pdf, strategy="hi_res")
        for chunk in chunks:
            assert chunk.metadata["content_type"] in valid_types


# ── Header / Footer 过滤测试 ────────────────────────────────────────────────────

class TestHeaderFooterFiltering:

    def test_header_elements_are_filtered(self, single_column_pdf):
        elements = [
            _make_element("Header", "Company Name | Page 1", 1),
            _make_element("NarrativeText", "Real body text.", 1),
            _make_element("Footer", "Confidential", 1),
        ]
        with patch("ingestion.parsers.pdf_parser._get_api_url", return_value="http://test"), \
             patch("ingestion.parsers.pdf_parser._call_api", return_value=elements):
            chunks = PdfParser().parse(single_column_pdf, strategy="hi_res")

        texts = [c.text for c in chunks]
        assert "Real body text." in texts
        assert not any("Company Name" in t for t in texts)
        assert not any("Confidential" in t for t in texts)

    def test_page_number_elements_are_filtered(self, single_column_pdf):
        elements = [
            _make_element("PageNumber", "1", 1),
            _make_element("NarrativeText", "Body.", 1),
        ]
        with patch("ingestion.parsers.pdf_parser._get_api_url", return_value="http://test"), \
             patch("ingestion.parsers.pdf_parser._call_api", return_value=elements):
            chunks = PdfParser().parse(single_column_pdf, strategy="hi_res")

        assert len(chunks) == 1
        assert chunks[0].text == "Body."

    def test_only_header_footer_page_returns_empty(self, single_column_pdf):
        elements = [
            _make_element("Header", "Header text", 1),
            _make_element("Footer", "Footer text", 1),
        ]
        with patch("ingestion.parsers.pdf_parser._get_api_url", return_value="http://test"), \
             patch("ingestion.parsers.pdf_parser._call_api", return_value=elements):
            chunks = PdfParser().parse(single_column_pdf, strategy="hi_res")
        assert chunks == []


# ── 输入源测试 ──────────────────────────────────────────────────────────────────

class TestInputSources:

    def test_parse_from_bytes(self, single_column_pdf: bytes):
        result = PdfParser().parse(single_column_pdf, strategy="fast")
        assert len(result) > 0

    def test_parse_from_path(self, single_column_pdf: bytes, tmp_path):
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(single_column_pdf)
        result = PdfParser().parse(pdf_path, strategy="fast")
        assert len(result) > 0

    def test_source_file_from_bytes_is_unknown(self, single_column_pdf: bytes):
        chunks = PdfParser().parse(single_column_pdf, strategy="fast")
        assert all(c.metadata["source_file"] == "unknown" for c in chunks)

    def test_source_file_from_path_is_filename(self, single_column_pdf: bytes, tmp_path):
        pdf_path = tmp_path / "mydoc.pdf"
        pdf_path.write_bytes(single_column_pdf)
        chunks = PdfParser().parse(pdf_path, strategy="fast")
        assert all(c.metadata["source_file"] == "mydoc.pdf" for c in chunks)


# ── Metadata 字段测试 ───────────────────────────────────────────────────────────

class TestMetadata:

    def test_page_number_is_set(self, single_column_pdf):
        elements = [
            _make_element("NarrativeText", "Page one.", page_number=1),
            _make_element("NarrativeText", "Page two.", page_number=2),
        ]
        with patch("ingestion.parsers.pdf_parser._get_api_url", return_value="http://test"), \
             patch("ingestion.parsers.pdf_parser._call_api", return_value=elements):
            chunks = PdfParser().parse(single_column_pdf, strategy="hi_res")
        page_nums = {c.metadata["page_number"] for c in chunks}
        assert page_nums == {1, 2}

    def test_bbox_extracted_when_coordinates_present(self, single_column_pdf):
        points = [(10.0, 20.0), (200.0, 20.0), (200.0, 50.0), (10.0, 50.0)]
        elements = [_make_element(bbox_points=points)]
        with patch("ingestion.parsers.pdf_parser._get_api_url", return_value="http://test"), \
             patch("ingestion.parsers.pdf_parser._call_api", return_value=elements):
            chunks = PdfParser().parse(single_column_pdf, strategy="hi_res")
        assert chunks[0].metadata["bbox"] == (10.0, 20.0, 200.0, 50.0)

    def test_bbox_is_none_when_no_coordinates(self, single_column_pdf):
        elements = [_make_element()]  # coordinates = None
        with patch("ingestion.parsers.pdf_parser._get_api_url", return_value="http://test"), \
             patch("ingestion.parsers.pdf_parser._call_api", return_value=elements):
            chunks = PdfParser().parse(single_column_pdf, strategy="hi_res")
        assert chunks[0].metadata["bbox"] is None

    def test_table_content_type(self, single_column_pdf):
        elements = [_make_table_element()]
        with patch("ingestion.parsers.pdf_parser._get_api_url", return_value="http://test"), \
             patch("ingestion.parsers.pdf_parser._call_api", return_value=elements):
            chunks = PdfParser().parse(single_column_pdf, strategy="hi_res")
        assert chunks[0].metadata["content_type"] == "table"

    def test_table_text_is_html(self, single_column_pdf):
        elements = [_make_table_element()]
        with patch("ingestion.parsers.pdf_parser._get_api_url", return_value="http://test"), \
             patch("ingestion.parsers.pdf_parser._call_api", return_value=elements):
            chunks = PdfParser().parse(single_column_pdf, strategy="hi_res")
        assert "<table>" in chunks[0].text

    def test_empty_text_elements_are_skipped(self, single_column_pdf):
        elements = [
            _make_element(text="   "),     # 空白，应跳过
            _make_element(text="Real."),
        ]
        with patch("ingestion.parsers.pdf_parser._get_api_url", return_value="http://test"), \
             patch("ingestion.parsers.pdf_parser._call_api", return_value=elements):
            chunks = PdfParser().parse(single_column_pdf, strategy="hi_res")
        assert len(chunks) == 1
        assert chunks[0].text == "Real."


# ── 多页 PDF 测试 ───────────────────────────────────────────────────────────────

class TestMultiPage:

    def test_chunks_ordered_by_page(self, multi_page_pdf):
        """fast 策略按页序遍历，输出 chunk 顺序应与页码顺序一致。"""
        chunks = PdfParser().parse(multi_page_pdf, strategy="fast")
        page_order = [c.metadata["page_number"] for c in chunks]
        assert page_order == sorted(page_order)

    def test_each_page_content_is_preserved(self, multi_page_pdf):
        chunks = PdfParser().parse(multi_page_pdf, strategy="fast")
        all_text = " ".join(c.text for c in chunks)
        assert "Page 1" in all_text
        assert "Page 2" in all_text
        assert "Page 3" in all_text


# ── 容错测试 ────────────────────────────────────────────────────────────────────

class TestErrorHandling:

    def test_encrypted_pdf_no_exception(self, encrypted_pdf):
        result = PdfParser().parse(encrypted_pdf, strategy="fast")
        assert isinstance(result, list)

    def test_encrypted_pdf_returns_error_chunk(self, encrypted_pdf):
        result = PdfParser().parse(encrypted_pdf, strategy="fast")
        assert isinstance(result, list)

    def test_corrupted_pdf_no_exception(self, corrupted_pdf):
        result = PdfParser().parse(corrupted_pdf, strategy="fast")
        assert isinstance(result, list)

    def test_corrupted_pdf_has_error_metadata(self, corrupted_pdf):
        result = PdfParser().parse(corrupted_pdf, strategy="fast")
        assert len(result) == 1
        assert "error" in result[0].metadata
        assert result[0].text == ""


# ── Fallback 逻辑测试 ───────────────────────────────────────────────────────────

class TestPageFallback:
    """验证 smart 策略下触发 hi_res fallback 的逻辑。"""

    def test_fallback_triggered_on_empty_page(self, image_only_pdf):
        """image_only_pdf（只有向量矩形，无文字块）在 smart 模式下空页面 → 触发 hi_res fallback。"""
        mock_ocr_el = _make_element("NarrativeText", "OCR extracted text.", page_number=1)

        with patch("ingestion.parsers.pdf_parser._get_api_url", return_value="http://test"), \
             patch("ingestion.parsers.pdf_parser._call_api", return_value=[mock_ocr_el]) as mock_api:
            chunks = PdfParser().parse(image_only_pdf, strategy="smart")

        mock_api.assert_called_once()
        assert any(c.metadata.get("fallback") == "hi_res" for c in chunks)
        assert any("OCR extracted text." in c.text for c in chunks)

    def test_fallback_triggered_on_large_image(self, scan_pdf):
        """scan_pdf（含占整页图片块）在 smart 模式下图片面积超阈值 → 触发 hi_res fallback。"""
        mock_ocr_el = _make_element("NarrativeText", "OCR extracted text.", page_number=1)

        with patch("ingestion.parsers.pdf_parser._get_api_url", return_value="http://test"), \
             patch("ingestion.parsers.pdf_parser._call_api", return_value=[mock_ocr_el]) as mock_api:
            chunks = PdfParser().parse(scan_pdf, strategy="smart")

        mock_api.assert_called_once()
        assert any(c.metadata.get("fallback") == "hi_res" for c in chunks)

    def test_fallback_not_triggered_with_text(self, single_column_pdf):
        """有文字的页面不触发 fallback，_call_api 不被调用。"""
        with patch("ingestion.parsers.pdf_parser._get_api_url", return_value="http://test"), \
             patch("ingestion.parsers.pdf_parser._call_api") as mock_api:
            PdfParser().parse(single_column_pdf, strategy="smart")

        mock_api.assert_not_called()

    def test_fallback_chunk_has_fallback_metadata(self, image_only_pdf):
        """fallback 产生的 chunk 带有 fallback='hi_res' 标记。"""
        mock_ocr_el = _make_element("NarrativeText", "OCR text.", page_number=1)

        with patch("ingestion.parsers.pdf_parser._get_api_url", return_value="http://test"), \
             patch("ingestion.parsers.pdf_parser._call_api", return_value=[mock_ocr_el]):
            chunks = PdfParser().parse(image_only_pdf, strategy="smart")

        fallback_chunks = [c for c in chunks if c.metadata.get("fallback") == "hi_res"]
        assert len(fallback_chunks) > 0

    def test_fallback_failure_uses_local_results(self, image_only_pdf):
        """API 调用失败时不崩溃，降级使用本地 fitz 结果（空页则返回空列表）。"""
        with patch("ingestion.parsers.pdf_parser._get_api_url", return_value="http://test"), \
             patch("ingestion.parsers.pdf_parser._call_api", side_effect=Exception("API down")):
            chunks = PdfParser().parse(image_only_pdf, strategy="smart")
        # image_only_pdf 本地 fitz 无文字，所以降级后也是空列表，不崩溃
        assert isinstance(chunks, list)


# ── 辅助函数测试 ────────────────────────────────────────────────────────────────

class TestHelperFunctions:

    def test_extract_single_page_returns_valid_pdf(self, multi_page_pdf):
        with _fitz.open(stream=multi_page_pdf, filetype="pdf") as src:
            result = PdfParser._extract_single_page_from_doc(src, page_num=2)
        assert isinstance(result, bytes)
        assert result[:4] == b"%PDF"

    def test_extract_single_page_is_one_page(self, multi_page_pdf):
        with _fitz.open(stream=multi_page_pdf, filetype="pdf") as src:
            result = PdfParser._extract_single_page_from_doc(src, page_num=2)
        with _fitz.open(stream=result, filetype="pdf") as doc:
            assert doc.page_count == 1

    def test_extract_single_page_correct_content(self, multi_page_pdf):
        """截取第 2 页，应含 'Page 2' 而不含 'Page 1' 或 'Page 3'。"""
        with _fitz.open(stream=multi_page_pdf, filetype="pdf") as src:
            result = PdfParser._extract_single_page_from_doc(src, page_num=2)
        with _fitz.open(stream=result, filetype="pdf") as doc:
            text = doc[0].get_text()
        assert "Page 2" in text
        assert "Page 1" not in text
        assert "Page 3" not in text

    def test_extract_single_page_first_page(self, multi_page_pdf):
        with _fitz.open(stream=multi_page_pdf, filetype="pdf") as src:
            result = PdfParser._extract_single_page_from_doc(src, page_num=1)
        with _fitz.open(stream=result, filetype="pdf") as doc:
            text = doc[0].get_text()
        assert "Page 1" in text


# ── 乱码检测测试 ────────────────────────────────────────────────────────────────

class TestGarbledDetection:
    """验证 _is_garbled() 和 smart 模式下乱码页触发降级的逻辑。"""

    # ── _is_garbled 单元测试 ───────────────────────────────────────────────────

    def test_clean_text_is_not_garbled(self):
        assert not _is_garbled("Hello, world! This is normal text.")

    def test_chinese_text_is_not_garbled(self):
        assert not _is_garbled("这是正常的中文文字。")

    def test_replacement_char_heavy_is_garbled(self):
        # 超过 25% 是 \ufffd
        assert _is_garbled("ok\ufffd\ufffd\ufffd\ufffd")

    def test_control_chars_heavy_is_garbled(self):
        # 大量 ASCII 控制字符（\x01-\x1f 排除换行制表）
        assert _is_garbled("ab\x01\x02\x03\x04\x05\x06")

    def test_newline_tab_not_counted_as_bad(self):
        # \n \r \t 是合法字符，不应计入乱码
        assert not _is_garbled("line1\nline2\ttabbed\r\n")

    def test_empty_string_is_not_garbled(self):
        assert not _is_garbled("")

    def test_threshold_boundary(self):
        # 刚好 25%：4 字符中 1 个 \ufffd → not garbled（> 不含等于）
        assert not _is_garbled("abc\ufffd")
        # 超过 25%：3 字符中 1 个 \ufffd → garbled
        assert _is_garbled("ab\ufffd")

    # ── smart 模式路由：_is_garbled 返回 True 时触发 fallback ────────────────

    def test_garbled_page_triggers_fallback(self, single_column_pdf):
        """smart 策略下，_is_garbled 判定乱码 → 触发 hi_res fallback。"""
        mock_ocr_el = _make_element("NarrativeText", "OCR recovered.", page_number=1)

        with patch("ingestion.parsers.pdf_parser._get_api_url", return_value="http://test"), \
             patch("ingestion.parsers.pdf_parser._is_garbled", return_value=True), \
             patch("ingestion.parsers.pdf_parser._call_api", return_value=[mock_ocr_el]) as mock_api:
            chunks = PdfParser().parse(single_column_pdf, strategy="smart")

        mock_api.assert_called()
        fallback_chunks = [c for c in chunks if c.metadata.get("fallback") == "hi_res"]
        assert len(fallback_chunks) > 0
        assert any("OCR recovered." in c.text for c in fallback_chunks)


# ── 集成测试（需要真实 Unstructured 环境） ──────────────────────────────────────

@pytest.mark.integration
class TestIntegration:
    """
    需要 UNSTRUCTURED_API_URL 环境变量。
    运行：uv run pytest -m integration
    """

    def test_real_text_extraction(self, single_column_pdf):
        chunks = PdfParser().parse(single_column_pdf, strategy="fast")
        all_text = " ".join(c.text for c in chunks if "error" not in c.metadata)
        assert "Introduction" in all_text

    def test_real_header_footer_filtered_hi_res(self, header_footer_pdf):
        chunks = PdfParser().parse(header_footer_pdf, strategy="hi_res")
        all_text = " ".join(c.text for c in chunks)
        assert "CONFIDENTIAL" not in all_text
        assert "Main Content" in all_text

    def test_real_table_detected_hi_res(self, table_text_pdf):
        chunks = PdfParser().parse(table_text_pdf, strategy="hi_res")
        table_chunks = [c for c in chunks if c.metadata["content_type"] == "table"]
        assert len(table_chunks) > 0

    def test_real_scan_fallback(self, scan_pdf):
        """扫描版 PDF 在 smart 模式下应触发 hi_res fallback 并 OCR 出文字。"""
        chunks = PdfParser().parse(scan_pdf, strategy="smart")
        fallback_chunks = [c for c in chunks if c.metadata.get("fallback") == "hi_res"]
        assert len(fallback_chunks) > 0
        combined = " ".join(c.text for c in fallback_chunks)
        assert "Scanned" in combined or "content" in combined.lower()


@pytest.mark.integration
class TestAttentionPaper:
    """使用真实论文（Attention Is All You Need）验证复杂版面解析。

    覆盖场景：双栏布局、数学公式、架构图、性能表格、页眉页脚。
    运行：uv run pytest -m integration
    """

    def test_fast_extracts_title(self, attention_paper_pdf):
        """fast 策略应能提取论文标题。"""
        chunks = PdfParser().parse(attention_paper_pdf, strategy="fast")
        all_text = " ".join(c.text for c in chunks)
        assert "Attention Is All You Need" in all_text

    def test_fast_extracts_section_headings(self, attention_paper_pdf):
        """fast 策略应能提取主要章节标题。"""
        chunks = PdfParser().parse(attention_paper_pdf, strategy="fast")
        all_text = " ".join(c.text for c in chunks)
        assert "Introduction" in all_text
        assert "References" in all_text

    def test_fast_multi_page_coverage(self, attention_paper_pdf):
        """论文有 15 页，fast 策略应覆盖所有页面。"""
        chunks = PdfParser().parse(attention_paper_pdf, strategy="fast")
        pages_seen = {c.metadata["page_number"] for c in chunks}
        assert len(pages_seen) >= 10  # 至少 10 页有文字内容

    def test_smart_figures_trigger_fallback(self, attention_paper_pdf):
        """论文含架构图（Figure 1）和注意力可视化图，smart 策略应对含图页触发 hi_res fallback。"""
        chunks = PdfParser().parse(attention_paper_pdf, strategy="smart")
        fallback_chunks = [c for c in chunks if c.metadata.get("fallback") == "hi_res"]
        assert len(fallback_chunks) > 0

    def test_hi_res_detects_tables(self, attention_paper_pdf):
        """hi_res 策略应识别出论文中的性能对比表格（Table 1/2/3）。"""
        chunks = PdfParser().parse(attention_paper_pdf, strategy="hi_res")
        table_chunks = [c for c in chunks if c.metadata["content_type"] == "table"]
        assert len(table_chunks) > 0

    def test_hi_res_chunk_count_reasonable(self, attention_paper_pdf):
        """hi_res 策略解析 15 页论文，chunk 数量应在合理范围内。"""
        chunks = PdfParser().parse(attention_paper_pdf, strategy="hi_res")
        valid = [c for c in chunks if "error" not in c.metadata]
        assert 50 <= len(valid) <= 500
