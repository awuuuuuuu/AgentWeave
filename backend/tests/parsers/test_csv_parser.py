"""
CsvParser 测试套件

运行：
  uv run pytest backend/tests/parsers/test_csv_parser.py -v
"""
from __future__ import annotations

from pathlib import Path


from ingestion.parsers.base import ParsedChunk, get_parser
from ingestion.parsers.csv_parser import CsvParser, _detect_header, _row_to_text


# ── 工具 ────────────────────────────────────────────────────────────────────────

def _parse(text: str) -> list[ParsedChunk]:
    return CsvParser().parse(text.encode("utf-8"))


# ── 基础结构 ────────────────────────────────────────────────────────────────────

class TestCsvParserBasic:

    def test_registered_for_csv(self):
        assert isinstance(get_parser(".csv"), CsvParser)

    def test_returns_list(self):
        assert isinstance(_parse("name,age\nAlice,30"), list)

    def test_empty_returns_empty(self):
        assert _parse("") == []

    def test_all_blank_lines_returns_empty(self):
        assert _parse("\n\n\n") == []

    def test_chunk_has_required_metadata(self):
        chunks = _parse("name,age\nAlice,30")
        assert "source_file" in chunks[0].metadata
        assert "content_type" in chunks[0].metadata
        assert "section_path" in chunks[0].metadata
        assert "row_number" in chunks[0].metadata

    def test_content_type_is_table(self):
        chunks = _parse("name,age\nAlice,30")
        assert all(c.metadata["content_type"] == "table" for c in chunks)

    def test_section_path_is_empty(self):
        chunks = _parse("name,age\nAlice,30")
        assert all(c.metadata["section_path"] == "" for c in chunks)

    def test_source_file_from_path(self, tmp_path):
        p = tmp_path / "data.csv"
        p.write_text("name,age\nAlice,30", encoding="utf-8")
        chunks = CsvParser().parse(p)
        assert chunks[0].metadata["source_file"] == "data.csv"


# ── 行转文本 ────────────────────────────────────────────────────────────────────

class TestRowToText:

    def test_basic_key_value_format(self):
        assert _row_to_text(["name", "age"], ["Alice", "30"]) == "name: Alice; age: 30"

    def test_empty_values_skipped(self):
        result = _row_to_text(["name", "age", "email"], ["Alice", "30", ""])
        assert "email" not in result
        assert "name: Alice" in result

    def test_extra_columns_get_col_n_names(self):
        result = _row_to_text(["name"], ["Alice", "extra"])
        assert "col_1: extra" in result

    def test_all_empty_values_returns_empty_string(self):
        assert _row_to_text(["name", "age"], ["", ""]) == ""

    def test_whitespace_values_stripped(self):
        result = _row_to_text(["name"], ["  Alice  "])
        assert "name: Alice" in result


# ── 表头检测 ────────────────────────────────────────────────────────────────────

class TestHeaderDetection:

    def test_first_row_with_two_non_empty_is_header(self):
        rows = [["name", "age"], ["Alice", "30"]]
        assert _detect_header(rows) == 0

    def test_skips_leading_empty_rows(self):
        rows = [["", ""], ["name", "age"], ["Alice", "30"]]
        assert _detect_header(rows) == 1

    def test_selects_row_with_most_non_empty(self):
        rows = [["name"], ["name", "age", "email"]]
        assert _detect_header(rows) == 1

    def test_all_empty_returns_minus_one(self):
        rows = [["", ""], ["", ""]]
        assert _detect_header(rows) == -1

    def test_only_scans_first_ten_rows(self):
        # 第 11 行有更多非空列，但超出扫描范围
        rows = [["a", "b"]] * 10 + [["a", "b", "c", "d"]]
        idx = _detect_header(rows)
        assert idx == 0  # 第一行满足 ≥2 非空列


# ── 数据解析 ────────────────────────────────────────────────────────────────────

class TestCsvDataParsing:

    def test_one_row_one_chunk(self):
        chunks = _parse("name,age\nAlice,30\nBob,25")
        assert len(chunks) == 2

    def test_chunk_text_format(self):
        chunks = _parse("name,age\nAlice,30")
        assert chunks[0].text == "name: Alice; age: 30"

    def test_blank_data_rows_skipped(self):
        chunks = _parse("name,age\nAlice,30\n\nBob,25")
        assert len(chunks) == 2

    def test_row_number_in_metadata(self):
        chunks = _parse("name,age\nAlice,30\nBob,25")
        assert chunks[0].metadata["row_number"] == 2
        assert chunks[1].metadata["row_number"] == 3

    def test_single_column_falls_back_to_col0(self):
        # 单列无法区分表头与数据，全部作为数据行，用 col_0 命名
        chunks = _parse("Alice\nBob\nCarol")
        assert all("col_0" in c.text for c in chunks)

    def test_all_empty_fallback_to_col_names(self):
        # 全空行才触发 col_N 兜底（_detect_header 返回 -1）
        from ingestion.parsers.csv_parser import _detect_header
        assert _detect_header([["", ""], ["", ""]]) == -1

    def test_partial_empty_row_still_parsed(self):
        # 行中部分字段为空，非空字段仍输出
        chunks = _parse("name,age,email\nAlice,,alice@example.com")
        assert "name: Alice" in chunks[0].text
        assert "age" not in chunks[0].text
        assert "email: alice@example.com" in chunks[0].text


# ── 编码处理 ────────────────────────────────────────────────────────────────────

class TestPreamble:

    def test_colon_preamble_in_metadata(self):
        csv = "公司名称：XX集团\n报告期：2024Q1\n\nname,amount\nAlice,100"
        chunks = _parse(csv)
        assert chunks[0].metadata.get("公司名称") == "XX集团"
        assert chunks[0].metadata.get("报告期") == "2024Q1"

    def test_multi_kv_preamble_row(self):
        # 一行并排多组 kv：制表人,张三,审核人,李四
        from ingestion.parsers.csv_parser import _extract_preamble
        rows = [["制表人", "张三", "审核人", "李四"], ["name", "amount"]]
        preamble = _extract_preamble(rows, header_idx=1)
        assert preamble.get("制表人") == "张三"
        assert preamble.get("审核人") == "李四"

    def test_no_preamble_no_extra_metadata(self):
        chunks = _parse("name,age\nAlice,30")
        assert "row_0" not in chunks[0].metadata

    def test_preamble_propagated_to_all_chunks(self):
        csv = "源：系统导出\nname,age\nAlice,30\nBob,25"
        chunks = _parse(csv)
        assert all(c.metadata.get("源") == "系统导出" for c in chunks)


class TestDelimiterDetection:

    def test_semicolon_delimiter(self):
        chunks = _parse("name;age\nAlice;30")
        assert "name: Alice" in chunks[0].text

    def test_tab_delimiter(self):
        chunks = _parse("name\tage\nAlice\t30")
        assert "name: Alice" in chunks[0].text


class TestCsvEncoding:

    def test_utf8_bom_decoded(self):
        raw = b"\xef\xbb\xbfname,age\nAlice,30"
        chunks = CsvParser().parse(raw)
        assert "name: Alice" in chunks[0].text

    def test_gbk_encoding(self):
        raw = "姓名,年龄\n张三,30".encode("gbk")
        chunks = CsvParser().parse(raw)
        assert len(chunks) == 1
        assert "张三" in chunks[0].text

    def test_utf8_encoding(self):
        chunks = _parse("姓名,年龄\n张三,30")
        assert "张三" in chunks[0].text
