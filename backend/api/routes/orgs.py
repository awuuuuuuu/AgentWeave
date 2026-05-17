"""
机构管理路由

POST /orgs              — 创建机构（开发/Demo 用，生产环境应鉴权）
GET  /orgs/validate     — 验证邀请码，返回机构基本信息（注册页预检）
GET  /orgs/me           — 当前用户所在机构详情
GET  /orgs/me/members   — 当前机构成员列表
"""
from __future__ import annotations

import secrets
import string
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import get_current_user
from db.models import Organization, User
from db.session import get_session

router = APIRouter(prefix="/orgs", tags=["orgs"])


def _gen_invite_code(length: int = 8) -> str:
    """生成大写字母+数字的随机邀请码，如 A3KX7DQF。"""
    chars = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


# ── Schema ────────────────────────────────────────────────────────────────────

class OrgCreate(BaseModel):
    name: str
    # "department" | "command"，默认 department
    type: str = "department"
    # 可自定义邀请码；不传时自动生成
    invite_code: str | None = None


class OrgOut(BaseModel):
    id: str
    name: str
    type: str
    invite_code: str
    created_at: datetime

    class Config:
        from_attributes = True


class OrgValidateOut(BaseModel):
    name: str
    type: str


class MemberOut(BaseModel):
    id: str
    email: str
    joined_at: datetime


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("", response_model=OrgOut, status_code=201)
async def create_org(
    body: OrgCreate,
    db: AsyncSession = Depends(get_session),
) -> OrgOut:
    """创建机构并生成（或使用自定义）邀请码。"""
    code = body.invite_code or _gen_invite_code()

    # 邀请码唯一性检查
    existing = await db.scalar(
        select(Organization).where(Organization.invite_code == code)
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="邀请码已被使用，请换一个")

    org = Organization(name=body.name, type=body.type, invite_code=code)
    db.add(org)
    await db.commit()
    await db.refresh(org)
    return OrgOut.model_validate(org)


@router.get("/me", response_model=OrgOut)
async def get_my_org(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> OrgOut:
    """返回当前用户所在机构详情。"""
    if not current_user.org_id:
        raise HTTPException(status_code=404, detail="您还未加入任何部门")
    org = await db.scalar(select(Organization).where(Organization.id == current_user.org_id))
    if org is None:
        raise HTTPException(status_code=404, detail="您还未加入任何部门")
    return OrgOut.model_validate(org)


@router.get("/me/members", response_model=list[MemberOut])
async def get_my_org_members(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> list[MemberOut]:
    """返回当前机构的成员列表。"""
    if not current_user.org_id:
        return []
    result = await db.scalars(
        select(User).where(User.org_id == current_user.org_id)
    )
    return [
        MemberOut(id=u.id, email=u.email, joined_at=u.created_at)
        for u in result.all()
    ]


@router.get("/validate", response_model=OrgValidateOut)
async def validate_invite_code(
    code: str,
    db: AsyncSession = Depends(get_session),
) -> OrgValidateOut:
    """根据邀请码返回机构基本信息（注册页实时预览用）。"""
    org = await db.scalar(
        select(Organization).where(Organization.invite_code == code)
    )
    if org is None:
        raise HTTPException(status_code=404, detail="邀请码无效")
    return OrgValidateOut(name=org.name, type=org.type)
