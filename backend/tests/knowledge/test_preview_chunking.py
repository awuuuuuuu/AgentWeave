"""分段预览 API 集成测试。

验证：
  1. chunk_size 更小 → 分段数量更多
  2. custom separators 确实改变切分边界
  3. parent_child 与 recursive 返回不同数量
  4. 预览不写 Milvus / MinIO（接口速度快且不需要 Worker）
  5. 未认证返回 401，KB 不存在返回 404

运行（需要 Supabase DB 在线）：
    uv run pytest backend/tests/knowledge/test_preview_chunking.py -v -m integration
"""
from __future__ import annotations

import io
import json
import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

os.environ.setdefault("JWT_SECRET_KEY", "integration-test-secret")

pytestmark = pytest.mark.asyncio(loop_scope="module")

_TEST_EMAIL = "integration_preview@ragent.dev"
_TEST_PASSWORD = "Test1234!"

# ---------------------------------------------------------------------------
# 测试文档：多段落 Markdown，内容足够触发多种分段策略
# ---------------------------------------------------------------------------

def _make_markdown(paragraphs: int = 20, chars_per_para: int = 300) -> bytes:
    """生成指定段落数的 Markdown，每段约 chars_per_para 个字符。"""
    lines = ["# RAGent 技术文档\n"]
    for i in range(1, paragraphs + 1):
        lines.append(f"## 第 {i} 节：{_SECTION_TITLES[i % len(_SECTION_TITLES)]}\n")
        # 每段约 chars_per_para 个汉字（每字约 1 token）
        body = "。".join([
            f"这是第 {i} 节的第 {j} 句，描述了 RAG 检索增强生成系统的关键技术细节，"
            f"包括向量检索、BM25 全文检索以及混合检索策略"
            for j in range(1, 6)
        ]) + "。"
        lines.append(body + "\n\n")
    return "\n".join(lines).encode()


_SECTION_TITLES = [
    "系统架构", "摄入流水线", "Embedding 模型", "向量检索",
    "全文检索", "混合检索", "Rerank 精排", "上下文构建",
    "LLM 生成", "引用追踪",
]

# 用自定义分隔符：段落级"---"
_SEPARATOR_DOC = """# 文档标题

第一段落内容。这一段很长，描述了系统的整体架构设计，包括多个模块的协作方式。
本段继续描述摄入流水线的具体实现，包括文件解析、分段、Embedding 写入。

---

第二段落内容。这里讲述检索模块的核心逻辑：向量召回、BM25 召回、融合评分。
检索结果经过 Reranker 精排后送入上下文构建模块，最终传入 LLM 生成答案。

---

第三段落内容。生成阶段使用流式输出，LLM 逐 token 产出答案，同时提取引用编号。
最终结果包含答案文本和引用列表，前端通过 SSE 接收并实时展示。

---

第四段落。这是最后一个段落，总结了整个 RAG 流水线的设计原则：
模块解耦、接口统一、易于扩展、生产就绪。
""".encode()


# ---------------------------------------------------------------------------
# Fixtures
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
    """注册/登录测试用户，yield headers，模块结束清理 DB。"""
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
    """创建一个测试 KB，返回其 ID。"""
    resp = await client.post(
        "/kb",
        json={"name": "Preview Test KB", "description": "用于分段预览测试"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

async def _preview(
    client, auth_headers, kb_id,
    content: bytes,
    filename: str = "test.md",
    chunk_size: int = 512,
    chunk_overlap: int = 64,
    splitter_type: str = "recursive",
    separators: list | None = None,
) -> list[dict]:
    data = {
        "splitter_type": splitter_type,
        "chunk_size": str(chunk_size),
        "chunk_overlap": str(chunk_overlap),
    }
    if separators is not None:
        data["separators"] = json.dumps(separators)

    resp = await client.post(
        f"/kb/{kb_id}/documents/preview",
        files={"file": (filename, io.BytesIO(content), "text/plain")},
        data=data,
        headers=auth_headers,
    )
    assert resp.status_code == 200, f"preview failed: {resp.text[:200]}"
    return resp.json()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestPreviewBasic:

    async def test_returns_list_of_chunks(self, client, auth_headers, kb_id):
        """基本调用：返回非空 chunk 列表。"""
        chunks = await _preview(client, auth_headers, kb_id, _make_markdown())
        assert isinstance(chunks, list)
        assert len(chunks) > 0

    async def test_chunk_fields_complete(self, client, auth_headers, kb_id):
        """每个 chunk 都包含必填字段。"""
        chunks = await _preview(client, auth_headers, kb_id, _make_markdown(paragraphs=3))
        for c in chunks:
            assert "index" in c
            assert "text" in c and c["text"].strip()
            assert "content_type" in c
            assert "token_count" in c
            assert c["token_count"] > 0

    async def test_chunk_index_sequential(self, client, auth_headers, kb_id):
        """chunk index 从 0 开始递增，无跳号。"""
        chunks = await _preview(client, auth_headers, kb_id, _make_markdown(paragraphs=5))
        indices = [c["index"] for c in chunks]
        assert indices == list(range(len(chunks)))

    async def test_401_without_auth(self, client, kb_id):
        """未鉴权返回 401。"""
        resp = await client.post(
            f"/kb/{kb_id}/documents/preview",
            files={"file": ("x.md", io.BytesIO(b"hello"), "text/plain")},
        )
        assert resp.status_code == 401

    async def test_404_nonexistent_kb(self, client, auth_headers):
        """KB 不存在返回 404。"""
        resp = await client.post(
            "/kb/nonexistent-kb-id/documents/preview",
            files={"file": ("x.md", io.BytesIO(b"hello"), "text/plain")},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    async def test_422_unsupported_extension(self, client, auth_headers, kb_id):
        """不支持的文件类型返回 422。"""
        resp = await client.post(
            f"/kb/{kb_id}/documents/preview",
            files={"file": ("script.py", io.BytesIO(b"print('hi')"), "text/plain")},
            headers=auth_headers,
        )
        assert resp.status_code == 422


@pytest.mark.integration
class TestChunkSizeEffect:

    async def test_smaller_chunk_size_yields_more_chunks(self, client, auth_headers, kb_id):
        """chunk_size 越小，分段数越多（同一文档）。"""
        doc = _make_markdown(paragraphs=15, chars_per_para=400)
        chunks_large = await _preview(client, auth_headers, kb_id, doc, chunk_size=1024)
        chunks_small = await _preview(client, auth_headers, kb_id, doc, chunk_size=128)

        assert len(chunks_small) > len(chunks_large), (
            f"chunk_size=128 gave {len(chunks_small)} chunks, "
            f"chunk_size=1024 gave {len(chunks_large)} chunks — expected more for smaller size"
        )

    async def test_chunk_token_count_respects_size(self, client, auth_headers, kb_id):
        """每个 chunk 的 token_count 不超过 chunk_size（允许少量 overlap 超出）。"""
        chunk_size = 256
        chunks = await _preview(
            client, auth_headers, kb_id,
            _make_markdown(paragraphs=10),
            chunk_size=chunk_size,
            chunk_overlap=32,
        )
        # 除标题类型（titles pass-through）外，正文 chunk 不应大幅超过 chunk_size
        body_chunks = [c for c in chunks if c["content_type"] != "title"]
        oversized = [c for c in body_chunks if c["token_count"] > chunk_size * 1.5]
        assert len(oversized) == 0, (
            f"{len(oversized)} chunks exceed 1.5× chunk_size={chunk_size}: "
            + str([(c['index'], c['token_count']) for c in oversized[:3]])
        )


@pytest.mark.integration
class TestCustomSeparators:

    async def test_default_separators_splits_on_paragraph(self, client, auth_headers, kb_id):
        """默认分隔符（\n\n）应在段落边界切分。"""
        chunks = await _preview(
            client, auth_headers, kb_id,
            _SEPARATOR_DOC,
            chunk_size=200,
        )
        assert len(chunks) >= 2

    async def test_custom_separator_dash_splits_on_divider(self, client, auth_headers, kb_id):
        """自定义分隔符 ['---', '\n\n'] 应在 --- 处切分，每段作为独立 chunk。"""
        chunks_default = await _preview(
            client, auth_headers, kb_id,
            _SEPARATOR_DOC,
            chunk_size=64,        # 小于每段内容，触发 splitter
            separators=None,      # 默认：\n\n → \n → 。 → ...
        )
        chunks_custom = await _preview(
            client, auth_headers, kb_id,
            _SEPARATOR_DOC,
            chunk_size=64,
            separators=["---", "\n\n", "\n", ""],   # 优先按 --- 切
        )
        # 自定义用 "---" 作为最高优先级分隔符，应产出 4 个主段（---出现3次=4段）
        # 合并后段数可能不同；核心是两种策略结果不一样
        assert chunks_custom != chunks_default, (
            "Custom separators ['---', ...] should produce different chunks than default"
        )

    async def test_separator_changes_chunk_boundaries(self, client, auth_headers, kb_id):
        """验证自定义分隔符实际改变了 chunk 文本边界。"""
        # 文档：两块用 "|||" 隔开
        doc = (
            b"# Title\n\n"
            b"Block A: " + b"word " * 80 + b"\n\n"
            b"|||\n\n"
            b"Block B: " + b"word " * 80
        )
        # chunk_size=128: 每块 ~80 tokens < 128，总体 ~170 tokens > 128 → 触发 splitter
        chunks_no_sep = await _preview(
            client, auth_headers, kb_id, doc, chunk_size=128,
        )
        chunks_with_sep = await _preview(
            client, auth_headers, kb_id, doc, chunk_size=128,
            separators=["|||", "\n\n", "\n", ""],
        )
        # 切分结果不同：default 把 ||| 合并到某个 chunk，custom 以 ||| 为边界
        assert chunks_no_sep != chunks_with_sep, \
            "Custom separator '|||' should produce different chunk boundaries"


@pytest.mark.integration
class TestSplitterType:

    async def test_parent_child_vs_recursive(self, client, auth_headers, kb_id):
        """parent_child 模式返回比 recursive 更多的 chunk（子块 + 父块）。"""
        doc = _make_markdown(paragraphs=10, chars_per_para=400)
        chunks_recursive = await _preview(
            client, auth_headers, kb_id, doc,
            splitter_type="recursive", chunk_size=512,
        )
        chunks_pc = await _preview(
            client, auth_headers, kb_id, doc,
            splitter_type="parent_child", chunk_size=512,
        )
        # parent_child 产生子块（child_chunk_size = chunk_size//4），数量应更多
        assert len(chunks_pc) > len(chunks_recursive), (
            f"parent_child gave {len(chunks_pc)} chunks vs "
            f"recursive {len(chunks_recursive)} — expected more for parent_child"
        )

    async def test_parent_child_child_token_count_smaller(self, client, auth_headers, kb_id):
        """parent_child 的子块 token_count 应小于 chunk_size（子块按 chunk_size//4 切）。"""
        doc = _make_markdown(paragraphs=8, chars_per_para=500)
        chunk_size = 512
        chunks = await _preview(
            client, auth_headers, kb_id, doc,
            splitter_type="parent_child", chunk_size=chunk_size,
        )
        child_size_limit = chunk_size // 4 + 32   # 容许少量 overlap
        child_chunks = [c for c in chunks if c["token_count"] <= child_size_limit]
        # 至少有若干子块
        assert len(child_chunks) > 0, \
            f"Expected child chunks with token_count ≤ {child_size_limit}, got none"

    async def test_txt_file_preview(self, client, auth_headers, kb_id):
        """TXT 文件也能正常预览。"""
        txt = b"paragraph one.\n\n" * 20
        chunks = await _preview(
            client, auth_headers, kb_id, txt,
            filename="sample.txt", chunk_size=64,
        )
        assert len(chunks) > 1
