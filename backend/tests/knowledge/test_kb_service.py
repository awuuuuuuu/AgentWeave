from __future__ import annotations

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from knowledge import service as kb_service
from knowledge.schemas import KBCreate, KBUpdate
from db.models import KnowledgeBase, Document, DocumentStatus


def _mock_session(scalar_return=None, scalars_return=None):
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=scalar_return)
    mock_result = MagicMock()
    mock_result.all.return_value = scalars_return or []
    session.scalars = AsyncMock(return_value=mock_result)
    session.add = MagicMock()
    session.delete = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    return session


class TestListKBs:
    @pytest.mark.asyncio
    async def test_returns_user_kbs(self):
        kbs = [KnowledgeBase(id="kb1", name="KB1", user_id="u1")]
        session = _mock_session(scalars_return=kbs)
        result = await kb_service.list_kbs("u1", session)
        assert result == kbs

    @pytest.mark.asyncio
    async def test_empty_list(self):
        session = _mock_session(scalars_return=[])
        result = await kb_service.list_kbs("u1", session)
        assert result == []


class TestGetKB:
    @pytest.mark.asyncio
    async def test_found(self):
        kb = KnowledgeBase(id="kb1", name="KB1", user_id="u1")
        session = _mock_session(scalar_return=kb)
        result = await kb_service.get_kb("kb1", "u1", session)
        assert result is kb

    @pytest.mark.asyncio
    async def test_not_found_raises(self):
        session = _mock_session(scalar_return=None)
        with pytest.raises(ValueError, match="not found"):
            await kb_service.get_kb("missing", "u1", session)


class TestCreateKB:
    @pytest.mark.asyncio
    async def test_creates_and_returns(self):
        session = _mock_session()

        def _set_id(kb):
            kb.id = "new-kb-id"

        session.refresh.side_effect = _set_id
        req = KBCreate(name="My KB", description="desc")
        result = await kb_service.create_kb(req, "u1", session)
        session.add.assert_called_once()
        session.commit.assert_awaited_once()
        assert result.name == "My KB"


class TestDeleteKB:
    @pytest.mark.asyncio
    async def test_deletes_existing(self):
        kb = KnowledgeBase(id="kb1", name="KB1", user_id="u1")
        session = _mock_session(scalar_return=kb)
        await kb_service.delete_kb("kb1", "u1", session)
        session.delete.assert_awaited_once_with(kb)
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_not_found_raises(self):
        session = _mock_session(scalar_return=None)
        with pytest.raises(ValueError, match="not found"):
            await kb_service.delete_kb("missing", "u1", session)


class TestCreateDocument:
    @pytest.mark.asyncio
    async def test_creates_pending_document(self):
        session = _mock_session()
        doc = await kb_service.create_document("kb1", "file.pdf", "task-123", session)
        assert doc.status == DocumentStatus.PENDING.value
        assert doc.filename == "file.pdf"
        assert doc.task_id == "task-123"
        session.add.assert_called_once()
        session.commit.assert_awaited_once()
