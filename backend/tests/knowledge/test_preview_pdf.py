"""PDF 分段预览集成测试。

用 PyMuPDF (fitz) 程序生成测试 PDF，验证：
  1. fast 策略无需 UNSTRUCTURED_API_URL 即可完成 preview
  2. 返回 chunk 字段完整（index、text、content_type、page_number、token_count）
  3. page_number 正确映射到原始页码
  4. chunk_size 越小 → 分段越多
  5. parent_child 模式下 child chunk 比 recursive chunk 更多

运行（需要 Supabase DB 在线）：
    uv run pytest backend/tests/knowledge/test_preview_pdf.py -v -m integration
"""
from __future__ import annotations

import io
import json
import os

import fitz
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

os.environ.setdefault("JWT_SECRET_KEY", "integration-test-secret")

pytestmark = pytest.mark.asyncio(loop_scope="module")

_TEST_EMAIL = "integration_pdf_preview@ragent.dev"
_TEST_PASSWORD = "Test1234!"


# ---------------------------------------------------------------------------
# PDF 生成工具
# ---------------------------------------------------------------------------

# 每段的核心句，拼接 repeat_sentences 次后约 500 tokens
_SENTENCES = [
    "RAGent 是一个企业级 RAG 系统，集成了向量检索、BM25 全文检索和混合检索策略，支持多知识库管理。",
    "摄入流水线负责将原始文档解析为结构化 ParsedChunk，再经分段器切分后写入 Milvus 向量数据库。",
    "检索阶段通过 LangGraph 编排多路召回，将候选文档经 Reranker 精排后注入上下文，送往 LLM 生成答案。",
    "向量检索使用余弦相似度计算 Query Embedding 与文档 Embedding 之间的语义距离，返回最近邻候选。",
    "BM25 全文检索基于词频逆文档频率，擅长捕捉精确关键词匹配，作为向量检索的互补召回策略。",
    "混合检索通过 RRF（Reciprocal Rank Fusion）算法融合向量分和 BM25 分，提升召回的鲁棒性与覆盖度。",
    "Reranker 使用交叉编码器（Cross-Encoder）对候选文档进行精细评分，将最相关的文档排在最前面。",
    "LangSmith 可观测性平台负责追踪每次检索请求的 Trace，包括召回数量、Rerank 得分和 LLM 延迟。",
]


def _make_pdf(
    total_paragraphs: int = 24,
    repeat_sentences: int = 3,
    fontsize: int = 10,
) -> bytes:
    """生成内容丰富的纯文字 PDF，段落内容自动跨页溢出。

    每段 = repeat_sentences × 8 句 ≈ repeat_sentences × 400 chars
    default: 24 段 × 3 × 400 = ~28800 chars ≈ ~14000 tokens

    测试用量参考（cl100k_base tokens）：
        chunk_size=128 → ~110 chunks
        chunk_size=512 → ~28 chunks
        chunk_size=1024 → ~14 chunks

    Args:
        total_paragraphs: 总段落数（默认 24，跨 ~6 页 A4）
        repeat_sentences: 每段循环句子轮数（默认 3，每段 ~400 tokens）
        fontsize:         正文字号
    Returns:
        PDF 字节流
    """
    doc = fitz.open()
    line_width = 80      # 每行最多字符数（fitz 无自动换行）
    line_h = fontsize + 2
    margin_top = 50.0
    margin_bot = 810.0

    def _new_page() -> tuple:
        p = doc.new_page(width=595, height=842)
        return p, margin_top

    page, y = _new_page()
    page_num = 1

    for para_idx in range(total_paragraphs):
        prefix = f"[PARA{para_idx + 1:03d}] "
        body = prefix + "".join(_SENTENCES * repeat_sentences)

        pos = 0
        while pos < len(body):
            if y + line_h > margin_bot:  # 当前页写满，开新页
                page, y = _new_page()
                page_num += 1
            line = body[pos: pos + line_width]
            page.insert_text((50, y), line, fontsize=fontsize, fontname="helv")
            y += line_h
            pos += line_width
        y += 8  # 段间距

    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Fixtures（与 test_preview_chunking.py 相同模式，但用不同测试账号隔离）
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="module")
async def client():
    from api.main import app
    from unittest.mock import MagicMock
    app.state.rag_chain = MagicMock()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture(scope="module")
async def auth_headers(client):
    resp = await client.post(
        "/auth/register",
        json={"email": _TEST_EMAIL, "password": _TEST_PASSWORD},
    )
    if resp.status_code == 409:
        resp = await client.post(
            "/auth/login",
            json={"email": _TEST_EMAIL, "password": _TEST_PASSWORD},
        )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    yield {"Authorization": f"Bearer {token}"}

    # 清理
    from db.session import SessionLocal
    from db.models import Document, KnowledgeBase, User
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.email == _TEST_EMAIL))
        if not user:
            return
        kbs = (await session.scalars(
            select(KnowledgeBase).where(KnowledgeBase.user_id == user.id)
        )).all()
        kb_ids = [kb.id for kb in kbs]
        if kb_ids:
            await session.execute(delete(Document).where(Document.kb_id.in_(kb_ids)))
        await session.execute(delete(KnowledgeBase).where(KnowledgeBase.user_id == user.id))
        await session.execute(delete(User).where(User.id == user.id))
        await session.commit()


@pytest_asyncio.fixture(scope="module")
async def kb_id(client, auth_headers):
    resp = await client.post(
        "/kb",
        json={"name": "PDF Preview Test KB", "description": "PDF 分段预览测试"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

async def _preview_pdf(
    client,
    auth_headers,
    kb_id: str,
    content: bytes,
    chunk_size: int = 512,
    chunk_overlap: int = 64,
    splitter_type: str = "recursive",
    separators: list | None = None,
) -> list[dict]:
    data: dict = {
        "splitter_type": splitter_type,
        "chunk_size": str(chunk_size),
        "chunk_overlap": str(chunk_overlap),
    }
    if separators is not None:
        data["separators"] = json.dumps(separators)

    resp = await client.post(
        f"/kb/{kb_id}/documents/preview",
        files={"file": ("test_doc.pdf", io.BytesIO(content), "application/pdf")},
        data=data,
        headers=auth_headers,
    )
    assert resp.status_code == 200, f"preview failed [{resp.status_code}]: {resp.text[:300]}"
    return resp.json()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestPdfPreviewBasic:

    async def test_returns_nonempty_chunks(self, client, auth_headers, kb_id):
        """PDF preview 返回非空 chunk 列表（fast 策略，无需 API）。"""
        pdf = _make_pdf(total_paragraphs=6)
        chunks = await _preview_pdf(client, auth_headers, kb_id, pdf)
        assert isinstance(chunks, list)
        assert len(chunks) > 0

    async def test_chunk_fields_complete(self, client, auth_headers, kb_id):
        """每个 chunk 包含所有必填字段，且 text 非空。"""
        pdf = _make_pdf(total_paragraphs=4)
        chunks = await _preview_pdf(client, auth_headers, kb_id, pdf)
        for c in chunks:
            assert "index" in c,        f"missing 'index' in chunk {c}"
            assert "text" in c,         f"missing 'text' in chunk {c}"
            assert c["text"].strip(),   f"empty text in chunk {c}"
            assert "content_type" in c, f"missing 'content_type' in chunk {c}"
            assert "token_count" in c,  f"missing 'token_count' in chunk {c}"
            assert c["token_count"] > 0, f"zero token_count in chunk {c}"

    async def test_chunk_index_sequential(self, client, auth_headers, kb_id):
        """chunk index 从 0 起连续递增。"""
        pdf = _make_pdf(total_paragraphs=6)
        chunks = await _preview_pdf(client, auth_headers, kb_id, pdf)
        indices = [c["index"] for c in chunks]
        assert indices == list(range(len(chunks)))

    async def test_page_number_populated(self, client, auth_headers, kb_id):
        """fast 策略下 page_number 应被 fitz 正确填充（≥ 1）。"""
        pdf = _make_pdf(total_paragraphs=12)
        chunks = await _preview_pdf(client, auth_headers, kb_id, pdf)
        pages_seen = {c["page_number"] for c in chunks if c["page_number"] is not None}
        assert len(pages_seen) > 0, "no chunk has page_number set"
        assert all(p >= 1 for p in pages_seen), f"page_number < 1: {pages_seen}"

    async def test_multipage_coverage(self, client, auth_headers, kb_id):
        """内容足够多时 chunk 应覆盖 ≥ 2 个不同页码。"""
        # total_paragraphs=24, repeat_sentences=3 → ~6 页 A4
        pdf = _make_pdf(total_paragraphs=24)
        chunks = await _preview_pdf(client, auth_headers, kb_id, pdf)
        pages_seen = {c["page_number"] for c in chunks if c["page_number"] is not None}
        assert len(pages_seen) >= 2, (
            f"expected chunks from ≥2 pages, got pages: {sorted(pages_seen)}"
        )


@pytest.mark.integration
class TestPdfChunkSize:

    async def test_smaller_chunk_size_more_chunks(self, client, auth_headers, kb_id):
        """chunk_size 越小 → PDF 分段数越多。"""
        # 用 8 段避免单次请求过长导致 DB 连接超时；~3200 tokens 足以体现差异
        pdf = _make_pdf(total_paragraphs=8)
        chunks_large = await _preview_pdf(client, auth_headers, kb_id, pdf, chunk_size=1024)
        chunks_small = await _preview_pdf(client, auth_headers, kb_id, pdf, chunk_size=128)
        assert len(chunks_small) > len(chunks_large), (
            f"chunk_size=128 → {len(chunks_small)} chunks, "
            f"chunk_size=1024 → {len(chunks_large)} chunks; expected small > large"
        )

    async def test_token_count_within_limit(self, client, auth_headers, kb_id):
        """text chunk 的 token_count 不超过 1.5× chunk_size。"""
        chunk_size = 256
        pdf = _make_pdf(total_paragraphs=12)
        chunks = await _preview_pdf(
            client, auth_headers, kb_id, pdf,
            chunk_size=chunk_size,
            chunk_overlap=32,
        )
        text_chunks = [c for c in chunks if c["content_type"] == "text"]
        oversized = [c for c in text_chunks if c["token_count"] > chunk_size * 1.5]
        assert len(oversized) == 0, (
            f"{len(oversized)} chunks exceed 1.5× {chunk_size}: "
            + str([(c["index"], c["token_count"]) for c in oversized[:3]])
        )


@pytest.mark.integration
class TestPdfSplitterType:

    async def test_parent_child_more_chunks_than_recursive(self, client, auth_headers, kb_id):
        """parent_child 模式生成比 recursive 更多的 chunk（子块 + 父块）。"""
        pdf = _make_pdf(total_paragraphs=12)
        chunks_recursive = await _preview_pdf(
            client, auth_headers, kb_id, pdf,
            chunk_size=256,
            splitter_type="recursive",
        )
        chunks_pc = await _preview_pdf(
            client, auth_headers, kb_id, pdf,
            chunk_size=256,
            splitter_type="parent_child",
        )
        assert len(chunks_pc) > len(chunks_recursive), (
            f"parent_child={len(chunks_pc)} should exceed recursive={len(chunks_recursive)}"
        )

    async def test_parent_child_child_chunks_smaller(self, client, auth_headers, kb_id):
        """parent_child 模式下存在 token_count < chunk_size/2 的子块。"""
        chunk_size = 256
        pdf = _make_pdf(total_paragraphs=12)
        chunks = await _preview_pdf(
            client, auth_headers, kb_id, pdf,
            chunk_size=chunk_size,
            splitter_type="parent_child",
        )
        small_chunks = [c for c in chunks if c["token_count"] < chunk_size // 2]
        assert len(small_chunks) > 0, (
            "parent_child mode should produce child chunks smaller than chunk_size/2"
        )
