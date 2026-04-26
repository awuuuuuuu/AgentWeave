from __future__ import annotations

import os
import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests-only")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from auth.jwt import create_access_token, create_refresh_token, decode_token


class TestAccessToken:
    def test_roundtrip(self):
        token = create_access_token("user-123")
        user_id = decode_token(token, expected_type="access")
        assert user_id == "user-123"

    def test_wrong_type_rejected(self):
        token = create_access_token("user-123")
        with pytest.raises(ValueError, match="Wrong token type"):
            decode_token(token, expected_type="refresh")

    def test_invalid_signature_rejected(self):
        token = create_access_token("user-123")
        tampered = token[:-4] + "XXXX"
        with pytest.raises(ValueError, match="Invalid token"):
            decode_token(tampered, expected_type="access")

    def test_garbage_rejected(self):
        with pytest.raises(ValueError):
            decode_token("not.a.token", expected_type="access")


class TestRefreshToken:
    def test_roundtrip(self):
        token = create_refresh_token("user-456")
        user_id = decode_token(token, expected_type="refresh")
        assert user_id == "user-456"

    def test_access_token_rejected_as_refresh(self):
        token = create_access_token("user-456")
        with pytest.raises(ValueError, match="Wrong token type"):
            decode_token(token, expected_type="refresh")
