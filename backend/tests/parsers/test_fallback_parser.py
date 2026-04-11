"""
FallbackParser 测试套件

运行：
  uv run pytest backend/tests/parsers/test_fallback_parser.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ingestion.parsers.base import ParsedChunk, get_parser
from ingestion.parsers.fallback_parser import FallbackParser, _is_binary


# ── 工具 ────────────────────────────────────────────────────────────────────────

def _parse(raw: bytes) -> list[ParsedChunk]:
    return FallbackParser().parse(raw)


# ── 注册表行为 ──────────────────────────────────────────────────────────────────

class TestRegistryBehavior:

    def test_unknown_extension_returns_fallback(self):
        """未注册扩展名不抛异常，返回 FallbackParser 实例。"""
        parser = get_parser(".xyz_unknown")
        assert isinstance(parser, FallbackParser)

    def test_fallback_not_registered_directly(self):
        """FallbackParser 不应抢占任何已注册扩展名。"""
        for ext in (".txt", ".md", ".html", ".htm", ".pdf", ".docx"):
            parser = get_parser(ext)
            assert not isinstance(parser, FallbackParser), f"{ext} 不应返回 FallbackParser"


# ── 基础行为 ────────────────────────────────────────────────────────────────────

class TestFallbackParserBasic:

    def test_returns_list(self):
        assert isinstance(_parse(b"hello"), list)

    def test_text_content_returns_chunk(self):
        chunks = _parse(b"Some plain text content.")
        assert len(chunks) == 1

    def test_chunk_is_parsed_chunk(self):
        chunks = _parse(b"Content.")
        assert isinstance(chunks[0], ParsedChunk)

    def test_chunk_metadata_has_parser_key(self):
        chunks = _parse(b"Content.")
        assert chunks[0].metadata.get("parser") == "fallback"

    def test_chunk_has_section_path(self):
        chunks = _parse(b"Content.")
        assert "section_path" in chunks[0].metadata
        assert chunks[0].metadata["section_path"] == ""

    def test_empty_bytes_returns_empty(self):
        assert _parse(b"") == []

    def test_whitespace_only_returns_empty(self):
        assert _parse(b"   \n\n\t  ") == []

    def test_source_file_from_path(self, tmp_path):
        p = tmp_path / "data.xyz"
        p.write_text("some content", encoding="utf-8")
        chunks = FallbackParser().parse(p)
        assert chunks[0].metadata["source_file"] == "data.xyz"

    def test_content_type_is_text(self):
        chunks = _parse(b"Some text.")
        assert chunks[0].metadata["content_type"] == "text"


# ── 二进制嗅探 ──────────────────────────────────────────────────────────────────

class TestBinaryDetection:

    def test_null_byte_triggers_binary(self):
        assert _is_binary(b"hello\x00world") is True

    def test_clean_text_not_binary(self):
        assert _is_binary(b"Hello, World!\n") is False

    def test_binary_file_returns_empty(self):
        """含 null byte 的二进制内容不应产生 chunk。"""
        chunks = _parse(b"PK\x03\x04\x14\x00\x08\x00\x00binary data")
        assert chunks == []

    def test_zip_magic_bytes_skipped(self):
        # ZIP 文件头包含 null byte
        zip_header = b"PK\x03\x04" + b"\x00" * 20 + b"fake zip content"
        chunks = _parse(zip_header)
        assert chunks == []

    def test_exe_magic_bytes_skipped(self):
        exe_header = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 50
        chunks = _parse(exe_header)
        assert chunks == []

    def test_only_first_8kb_checked(self):
        """8 KB 之后才出现的 null byte 不触发二进制判定。"""
        safe_prefix = b"A" * 8192
        binary_suffix = b"\x00"
        assert _is_binary(safe_prefix + binary_suffix) is False

    def test_null_byte_at_boundary(self):
        """恰好在 8 KB 内的 null byte 触发判定。"""
        data = b"A" * 8191 + b"\x00"
        assert _is_binary(data) is True


# ── 编码兼容 ────────────────────────────────────────────────────────────────────

class TestEncoding:

    def test_utf8_text_decoded(self):
        chunks = _parse("中文内容".encode("utf-8"))
        assert "中文内容" in chunks[0].text

    def test_gbk_text_decoded(self):
        chunks = _parse("中文内容".encode("gbk"))
        assert len(chunks) == 1  # 不崩溃，chardet 应能识别

    def test_latin1_fallback(self):
        # 含高字节，chardet 可能检测为 latin-1
        raw = b"caf\xe9 content"
        chunks = _parse(raw)
        assert len(chunks) == 1
