from __future__ import annotations

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests-only")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from auth.schemas import RegisterRequest
from auth import service as auth_service
from db.models import User
from sqlalchemy.exc import IntegrityError


def _mock_session(scalar_return=None, commit_raises=None):
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=scalar_return)
    session.add = MagicMock()
    session.commit = AsyncMock(side_effect=commit_raises)
    session.rollback = AsyncMock()
    session.refresh = AsyncMock()
    return session


class TestRegister:
    @pytest.mark.asyncio
    async def test_new_user_gets_tokens(self):
        session = _mock_session()

        def _set_id(user):
            user.id = "generated-uuid"

        session.refresh.side_effect = _set_id

        req = RegisterRequest(email="alice@example.com", password="Secret123")
        result = await auth_service.register(req, session)

        assert result.access_token
        assert result.refresh_token
        assert result.token_type == "bearer"
        session.add.assert_called_once()
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_duplicate_email_raises(self):
        session = _mock_session(commit_raises=IntegrityError(None, None, Exception()))

        req = RegisterRequest(email="alice@example.com", password="Secret123")
        with pytest.raises(ValueError, match="already registered"):
            await auth_service.register(req, session)
        session.rollback.assert_awaited_once()


class TestLogin:
    @pytest.mark.asyncio
    async def test_correct_credentials_returns_tokens(self):
        import bcrypt
        hashed = bcrypt.hashpw(b"mypassword", bcrypt.gensalt()).decode()
        user = User(id="u1", email="bob@example.com", hashed_password=hashed)
        session = _mock_session(scalar_return=user)

        result = await auth_service.login("bob@example.com", "mypassword", session)

        assert result.access_token
        assert result.refresh_token

    @pytest.mark.asyncio
    async def test_wrong_password_raises(self):
        import bcrypt
        hashed = bcrypt.hashpw(b"correct", bcrypt.gensalt()).decode()
        user = User(id="u1", email="bob@example.com", hashed_password=hashed)
        session = _mock_session(scalar_return=user)

        with pytest.raises(ValueError, match="Invalid email or password"):
            await auth_service.login("bob@example.com", "wrong", session)

    @pytest.mark.asyncio
    async def test_unknown_email_raises(self):
        session = _mock_session(scalar_return=None)

        with pytest.raises(ValueError, match="Invalid email or password"):
            await auth_service.login("unknown@example.com", "pass", session)
