from __future__ import annotations

import bcrypt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Organization, User
from .jwt import create_access_token, create_refresh_token
from .schemas import RegisterRequest, TokenResponse


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


async def register(req: RegisterRequest, session: AsyncSession) -> TokenResponse:
    """注册新用户，返回 token 对。

    - 邮箱已存在时抛出 ValueError
    - 填写了 invite_code 但无效时抛出 ValueError
    - 有效 invite_code → 用户自动归入对应部门
    """
    org_id: str | None = None

    if req.invite_code:
        org = await session.scalar(
            select(Organization).where(Organization.invite_code == req.invite_code)
        )
        if org is None:
            raise ValueError("邀请码无效，请核对后重试")
        org_id = org.id

    user = User(
        email=req.email,
        hashed_password=_hash_password(req.password),
        org_id=org_id,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise ValueError("Email already registered")
    await session.refresh(user)

    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


async def login(email: str, password: str, session: AsyncSession) -> TokenResponse:
    """验证邮箱密码，返回 token 对。凭证错误时抛出 ValueError。"""
    user = await session.scalar(select(User).where(User.email == email))
    if user is None or not _verify_password(password, user.hashed_password):
        raise ValueError("Invalid email or password")

    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )
