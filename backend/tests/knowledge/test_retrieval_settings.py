"""检索设置集成测试。

验证：
  1. PATCH /kb/{id}/retrieval-settings 正确保存到 DB
  2. GET /kb/{id} 返回更新后的值
  3. 非法 retrieval_mode 返回 422
  4. top_k / score_threshold 边界值验证
  5. 其他用户无法修改 KB 检索设置

运行（需要 Supabase DB 在线）：
    uv run pytest backend/tests/knowledge/test_retrieval_settings.py -v -m integration
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

os.environ.setdefault("JWT_SECRET_KEY", "integration-test-secret")

pytestmark = pytest.mark.asyncio(loop_scope="module")

_TEST_EMAIL = "integration_retrieval@ragent.dev"
_TEST_PASSWORD = "Test1234!"
_OTHER_EMAIL = "integration_retrieval_other@ragent.dev"
_OTHER_PASSWORD = "Test1234!"


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


async def _register_or_login(client, email, password):
    resp = await client.post("/auth/register", json={"email": email, "password": password})
    if resp.status_code == 409:
        resp = await client.post("/auth/login", json={"email": email, "password": password})
    resp.raise_for_status()
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest_asyncio.fixture(scope="module")
async def auth_headers(client):
    headers = await _register_or_login(client, _TEST_EMAIL, _TEST_PASSWORD)
    yield headers
    # 清理
    from db.session import SessionLocal
    from db.models import Document, KnowledgeBase, User
    async with SessionLocal() as session:
        for email in (_TEST_EMAIL, _OTHER_EMAIL):
            user = await session.scalar(select(User).where(User.email == email))
            if not user:
                continue
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
async def other_auth_headers(client):
    return await _register_or_login(client, _OTHER_EMAIL, _OTHER_PASSWORD)


@pytest_asyncio.fixture(scope="module")
async def kb(client, auth_headers):
    """创建测试 KB，返回完整 KB 对象。"""
    resp = await client.post(
        "/kb",
        json={"name": "Retrieval Settings Test KB"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    return resp.json()


# ---------------------------------------------------------------------------
# PATCH retrieval-settings
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestPatchRetrievalSettings:

    async def test_default_values_after_create(self, client, auth_headers, kb):
        """新建 KB 应有合理的默认检索设置。"""
        resp = await client.get(f"/kb/{kb['id']}", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["retrieval_mode"] in ("vector", "fulltext", "hybrid")
        assert isinstance(data["use_rerank"], bool)
        assert isinstance(data["top_k"], int) and data["top_k"] >= 1
        assert isinstance(data["score_threshold"], float)

    async def test_patch_all_fields(self, client, auth_headers, kb):
        """一次性修改所有检索设置字段，GET 后应一致。"""
        payload = {
            "retrieval_mode": "vector",
            "use_rerank": False,
            "top_k": 10,
            "score_threshold": 0.3,
        }
        resp = await client.patch(
            f"/kb/{kb['id']}/retrieval-settings",
            json=payload,
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["retrieval_mode"] == "vector"
        assert data["use_rerank"] is False
        assert data["top_k"] == 10
        assert data["score_threshold"] == pytest.approx(0.3)

    async def test_get_reflects_patched_values(self, client, auth_headers, kb):
        """PATCH 后 GET /kb/{id} 返回的值应与 PATCH 一致。"""
        await client.patch(
            f"/kb/{kb['id']}/retrieval-settings",
            json={"retrieval_mode": "fulltext", "use_rerank": True, "top_k": 7, "score_threshold": 0.0},
            headers=auth_headers,
        )
        resp = await client.get(f"/kb/{kb['id']}", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["retrieval_mode"] == "fulltext"
        assert data["use_rerank"] is True
        assert data["top_k"] == 7
        assert data["score_threshold"] == pytest.approx(0.0)

    async def test_patch_partial_updates_fields(self, client, auth_headers, kb):
        """Pydantic defaults — 提交默认值覆盖现有值（当前设计是全量替换）。"""
        # 先设一个已知状态
        await client.patch(
            f"/kb/{kb['id']}/retrieval-settings",
            json={"retrieval_mode": "hybrid", "use_rerank": True, "top_k": 5, "score_threshold": 0.0},
            headers=auth_headers,
        )
        # 再改 top_k 和 score_threshold
        resp = await client.patch(
            f"/kb/{kb['id']}/retrieval-settings",
            json={"retrieval_mode": "hybrid", "use_rerank": True, "top_k": 20, "score_threshold": 0.5},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["top_k"] == 20
        assert resp.json()["score_threshold"] == pytest.approx(0.5)


@pytest.mark.integration
class TestRetrievalSettingsValidation:

    async def test_invalid_retrieval_mode_422(self, client, auth_headers, kb):
        """不合法的 retrieval_mode 应返回 422。"""
        resp = await client.patch(
            f"/kb/{kb['id']}/retrieval-settings",
            json={"retrieval_mode": "semantic", "use_rerank": False, "top_k": 5, "score_threshold": 0.0},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    async def test_top_k_zero_422(self, client, auth_headers, kb):
        resp = await client.patch(
            f"/kb/{kb['id']}/retrieval-settings",
            json={"retrieval_mode": "hybrid", "use_rerank": False, "top_k": 0, "score_threshold": 0.0},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    async def test_top_k_over_limit_422(self, client, auth_headers, kb):
        resp = await client.patch(
            f"/kb/{kb['id']}/retrieval-settings",
            json={"retrieval_mode": "hybrid", "use_rerank": False, "top_k": 999, "score_threshold": 0.0},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    async def test_score_threshold_over_1_422(self, client, auth_headers, kb):
        resp = await client.patch(
            f"/kb/{kb['id']}/retrieval-settings",
            json={"retrieval_mode": "hybrid", "use_rerank": False, "top_k": 5, "score_threshold": 1.5},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    async def test_score_threshold_negative_422(self, client, auth_headers, kb):
        resp = await client.patch(
            f"/kb/{kb['id']}/retrieval-settings",
            json={"retrieval_mode": "hybrid", "use_rerank": False, "top_k": 5, "score_threshold": -0.1},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    async def test_top_k_boundary_max(self, client, auth_headers, kb):
        """top_k=50 是最大允许值，应返回 200。"""
        resp = await client.patch(
            f"/kb/{kb['id']}/retrieval-settings",
            json={"retrieval_mode": "hybrid", "use_rerank": False, "top_k": 50, "score_threshold": 0.0},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["top_k"] == 50

    async def test_score_threshold_boundary_max(self, client, auth_headers, kb):
        """score_threshold=1.0 是最大允许值。"""
        resp = await client.patch(
            f"/kb/{kb['id']}/retrieval-settings",
            json={"retrieval_mode": "hybrid", "use_rerank": False, "top_k": 5, "score_threshold": 1.0},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["score_threshold"] == pytest.approx(1.0)


@pytest.mark.integration
class TestRetrievalSettingsIsolation:

    async def test_other_user_cannot_patch(self, client, auth_headers, other_auth_headers, kb):
        """其他用户修改检索设置应返回 404（不泄露 KB 存在信息）。"""
        resp = await client.patch(
            f"/kb/{kb['id']}/retrieval-settings",
            json={"retrieval_mode": "vector", "use_rerank": False, "top_k": 1, "score_threshold": 0.9},
            headers=other_auth_headers,
        )
        assert resp.status_code == 404

    async def test_401_without_token(self, client, kb):
        resp = await client.patch(
            f"/kb/{kb['id']}/retrieval-settings",
            json={"retrieval_mode": "hybrid", "use_rerank": True, "top_k": 5, "score_threshold": 0.0},
        )
        assert resp.status_code == 401

    async def test_all_three_modes_persist(self, client, auth_headers, kb):
        """三种检索模式依次设置，每次 GET 应返回正确值。"""
        for mode in ("vector", "fulltext", "hybrid"):
            await client.patch(
                f"/kb/{kb['id']}/retrieval-settings",
                json={"retrieval_mode": mode, "use_rerank": True, "top_k": 5, "score_threshold": 0.0},
                headers=auth_headers,
            )
            resp = await client.get(f"/kb/{kb['id']}", headers=auth_headers)
            assert resp.json()["retrieval_mode"] == mode, \
                f"After setting mode={mode}, GET returned {resp.json()['retrieval_mode']}"
