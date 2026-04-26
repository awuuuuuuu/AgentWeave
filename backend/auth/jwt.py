from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from jose import JWTError, jwt

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

_SECRET_KEY = os.environ["JWT_SECRET_KEY"]
_ALGORITHM = "HS256"
_ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
_REFRESH_TOKEN_EXPIRE_DAYS = int(os.environ.get("REFRESH_TOKEN_EXPIRE_DAYS", "7"))


def create_access_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=_ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode(
        {"sub": user_id, "type": "access", "exp": expire},
        _SECRET_KEY,
        algorithm=_ALGORITHM,
    )


def create_refresh_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=_REFRESH_TOKEN_EXPIRE_DAYS)
    return jwt.encode(
        {"sub": user_id, "type": "refresh", "exp": expire},
        _SECRET_KEY,
        algorithm=_ALGORITHM,
    )


def decode_token(token: str, expected_type: str) -> str:
    """验证 token，返回 user_id；无效时抛出 ValueError。"""
    try:
        payload = jwt.decode(token, _SECRET_KEY, algorithms=[_ALGORITHM])
    except JWTError as e:
        raise ValueError(f"Invalid token: {e}") from e
    if payload.get("type") != expected_type:
        raise ValueError(f"Wrong token type: expected {expected_type}")
    user_id: str | None = payload.get("sub")
    if not user_id:
        raise ValueError("Token missing sub claim")
    return user_id
