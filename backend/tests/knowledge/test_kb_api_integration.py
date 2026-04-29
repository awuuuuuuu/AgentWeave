"""知识库 API 集成测试（真实 Supabase DB + MinIO + Celery）。

运行：
    uv run pytest backend/tests/knowledge/test_kb_api_integration.py -v -m integration
"""
from __future__ import annotations

import io
import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

os.environ.setdefault("JWT_SECRET_KEY", "integration-test-secret")

# 所有测试与 module-scoped fixtures 共用同一个 event loop，避免 asyncpg 跨 loop 错误
pytestmark = pytest.mark.asyncio(loop_scope="module")

_TEST_EMAIL = "integration_test_kb@ragent.dev"
_TEST_PASSWORD = "Test1234!"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="module")
async def client():
    from api.main import app
    from unittest.mock import MagicMock

    # 跳过 RAGChain lifespan（不连 Milvus）
    app.state.rag_chain = MagicMock()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest_asyncio.fixture(scope="module")
async def auth_headers(client):
    """注册测试用户，返回 Bearer header；模块结束后清理 DB + MinIO。"""
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

    # ── 清理 ──────────────────────────────────────────────────────────────
    from db.session import SessionLocal
    from db.models import Document, KnowledgeBase, User
    from storage import minio_client

    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.email == _TEST_EMAIL))
        if not user:
            return

        # 删 MinIO 对象
        kbs = (
            await session.scalars(
                select(KnowledgeBase).where(KnowledgeBase.user_id == user.id)
            )
        ).all()
        for kb in kbs:
            docs = (
                await session.scalars(
                    select(Document).where(Document.kb_id == kb.id)
                )
            ).all()
            for doc in docs:
                if doc.object_key:
                    try:
                        minio_client.delete_object(doc.object_key)
                    except Exception:
                        pass

        # 删 DB 记录（级联顺序：documents → knowledge_bases → users）
        kb_ids = [kb.id for kb in kbs]
        if kb_ids:
            await session.execute(
                delete(Document).where(Document.kb_id.in_(kb_ids))
            )
        await session.execute(
            delete(KnowledgeBase).where(KnowledgeBase.user_id == user.id)
        )
        await session.execute(delete(User).where(User.id == user.id))
        await session.commit()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

async def _create_kb(client, headers, name="Test KB", description="desc") -> dict:
    resp = await client.post(
        "/kb", json={"name": name, "description": description}, headers=headers
    )
    assert resp.status_code == 201
    return resp.json()


# ---------------------------------------------------------------------------
# 知识库 CRUD
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestKBCRUD:

    async def test_create_kb_201(self, client, auth_headers):
        kb = await _create_kb(client, auth_headers, name="Integration KB")
        assert kb["name"] == "Integration KB"
        assert "id" in kb

    async def test_list_kbs_includes_created(self, client, auth_headers):
        await _create_kb(client, auth_headers, name="List Test KB")
        resp = await client.get("/kb", headers=auth_headers)
        assert resp.status_code == 200
        names = [kb["name"] for kb in resp.json()]
        assert "List Test KB" in names

    async def test_get_kb_200(self, client, auth_headers):
        kb = await _create_kb(client, auth_headers, name="Get Test KB")
        resp = await client.get(f"/kb/{kb['id']}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["id"] == kb["id"]

    async def test_get_kb_404_not_found(self, client, auth_headers):
        resp = await client.get("/kb/nonexistent-id-xyz", headers=auth_headers)
        assert resp.status_code == 404

    async def test_update_kb_200(self, client, auth_headers):
        kb = await _create_kb(client, auth_headers, name="Before Update")
        resp = await client.patch(
            f"/kb/{kb['id']}",
            json={"name": "After Update"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "After Update"

    async def test_delete_kb_204(self, client, auth_headers):
        kb = await _create_kb(client, auth_headers, name="To Be Deleted")
        resp = await client.delete(f"/kb/{kb['id']}", headers=auth_headers)
        assert resp.status_code == 204

        resp = await client.get(f"/kb/{kb['id']}", headers=auth_headers)
        assert resp.status_code == 404

    async def test_401_without_token(self, client):
        resp = await client.get("/kb")
        assert resp.status_code == 401  # HTTPBearer 缺失时返回 401

    async def test_cannot_access_other_users_kb(self, client, auth_headers):
        """访问不属于当前用户的 KB 应返回 404（不泄露资源存在信息）。"""
        resp = await client.get("/kb/fake-other-user-kb", headers=auth_headers)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 文档管理（真实 MinIO + Celery）
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestDocumentUpload:

    async def test_upload_txt_200(self, client, auth_headers):
        kb = await _create_kb(client, auth_headers, name="Upload KB")
        resp = await client.post(
            f"/kb/{kb['id']}/documets/upload",
            files={"file": ("hello.txt", io.BytesIO(b"hello world"), "text/plain")},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["filename"] == "hello.txt"
        assert data["status"] == "pending"
        assert data["task_id"] != "pending"  # Celery 已分配真实 task_id

    async def test_upload_unsupported_ext_422(self, client, auth_headers):
        kb = await _create_kb(client, auth_headers, name="Ext Test KB")
        resp = await client.post(
            f"/kb/{kb['id']}/documets/upload",
            files={"file": ("malware.exe", io.BytesIO(b"MZ"), "application/octet-stream")},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    async def test_list_documents_after_upload(self, client, auth_headers):
        kb = await _create_kb(client, auth_headers, name="Doc List KB")
        await client.post(
            f"/kb/{kb['id']}/documets/upload",
            files={"file": ("a.txt", io.BytesIO(b"content"), "text/plain")},
            headers=auth_headers,
        )
        resp = await client.get(f"/kb/{kb['id']}/documents", headers=auth_headers)
        assert resp.status_code == 200
        docs = resp.json()
        assert len(docs) >= 1
        assert docs[0]["filename"] == "a.txt"

    async def test_get_document_200(self, client, auth_headers):
        kb = await _create_kb(client, auth_headers, name="Get Doc KB")
        upload_resp = await client.post(
            f"/kb/{kb['id']}/documets/upload",
            files={"file": ("b.txt", io.BytesIO(b"world"), "text/plain")},
            headers=auth_headers,
        )
        doc_id = upload_resp.json()["document_id"]
        resp = await client.get(f"/kb/{kb['id']}/documents/{doc_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["id"] == doc_id

    async def test_upload_to_nonexistent_kb_404(self, client, auth_headers):
        resp = await client.post(
            "/kb/nonexistent-kb/documets/upload",
            files={"file": ("x.txt", io.BytesIO(b"x"), "text/plain")},
            headers=auth_headers,
        )
        assert resp.status_code == 404
