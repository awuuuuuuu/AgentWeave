"""
机构管理路由

POST /orgs                        — 创建机构（开发/Demo 用，生产环境应鉴权）
GET  /orgs/validate               — 验证邀请码，返回机构基本信息（注册页预检）
GET  /orgs/me                     — 当前用户所在机构详情
GET  /orgs/me/members             — 当前机构成员列表
GET  /orgs/me/mcp-connections     — 查询 MCP 连接列表
PUT  /orgs/me/mcp-connections     — 全量更新 MCP 连接列表
POST /orgs/me/mcp-connections     — 追加单条 MCP 连接
DELETE /orgs/me/mcp-connections/{name} — 按 name 删除单条 MCP 连接
GET  /orgs/me/mcp-status          — 探活所有 MCP 连接，返回在线状态
"""
from __future__ import annotations

import asyncio
import ipaddress
import secrets
import string
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import httpx
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


class DeptOut(BaseModel):
    id: str
    name: str
    dept_code: Optional[str] = None
    a2a_url: Optional[str] = None
    dept_prompts: Optional[dict] = None

    class Config:
        from_attributes = True


class DeptCreate(BaseModel):
    name: str
    dept_code: str
    a2a_url: Optional[str] = None
    supervisor_hints: Optional[str] = None
    analyst_context: Optional[str] = None
    invite_code: Optional[str] = None


class DeptUpdate(BaseModel):
    name: Optional[str] = None
    a2a_url: Optional[str] = None
    supervisor_hints: Optional[str] = None
    analyst_context: Optional[str] = None


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


@router.get("/departments", response_model=list[DeptOut])
async def list_departments(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> list[DeptOut]:
    """返回所有 type=='department' 的机构（供 Weave 会话创建时选择协作部门）。"""
    result = await db.scalars(
        select(Organization)
        .where(Organization.type == "department")
        .order_by(Organization.name)
    )
    return [DeptOut.model_validate(o) for o in result.all()]


@router.post("/departments", response_model=DeptOut, status_code=201)
async def create_department(
    body: DeptCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> DeptOut:
    """创建新部门机构（仅供指挥中心管理员使用）。"""
    await _require_command_org(current_user, db)
    existing = await db.scalar(
        select(Organization).where(Organization.dept_code == body.dept_code)
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"dept_code '{body.dept_code}' 已存在")

    code = body.invite_code or _gen_invite_code()
    existing_code = await db.scalar(
        select(Organization).where(Organization.invite_code == code)
    )
    if existing_code is not None:
        raise HTTPException(status_code=409, detail="邀请码已被使用，请换一个")

    prompts: dict | None = None
    if body.supervisor_hints or body.analyst_context:
        prompts = {}
        if body.supervisor_hints:
            prompts["supervisor_hints"] = body.supervisor_hints
        if body.analyst_context:
            prompts["analyst_context"] = body.analyst_context

    org = Organization(
        name=body.name,
        type="department",
        invite_code=code,
        dept_code=body.dept_code,
        a2a_url=body.a2a_url,
        dept_prompts=prompts,
    )
    db.add(org)
    await db.commit()
    await db.refresh(org)
    return DeptOut.model_validate(org)


@router.put("/departments/{dept_code}", response_model=DeptOut)
async def update_department(
    dept_code: str,
    body: DeptUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> DeptOut:
    """更新部门的名称、A2A 地址或提示词。"""
    await _require_command_org(current_user, db)
    org = await db.scalar(
        select(Organization)
        .where(Organization.dept_code == dept_code, Organization.type == "department")
    )
    if org is None:
        raise HTTPException(status_code=404, detail=f"部门 '{dept_code}' 不存在")

    if body.name is not None:
        org.name = body.name
    if body.a2a_url is not None:
        org.a2a_url = body.a2a_url

    if body.supervisor_hints is not None or body.analyst_context is not None:
        current = dict(org.dept_prompts or {})
        if body.supervisor_hints is not None:
            current["supervisor_hints"] = body.supervisor_hints
        if body.analyst_context is not None:
            current["analyst_context"] = body.analyst_context
        org.dept_prompts = current

    await db.commit()
    await db.refresh(org)
    return DeptOut.model_validate(org)


@router.delete("/departments/{dept_code}", status_code=204)
async def delete_department(
    dept_code: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> None:
    """删除部门机构（不能删除有成员的部门）。"""
    await _require_command_org(current_user, db)
    org = await db.scalar(
        select(Organization)
        .where(Organization.dept_code == dept_code, Organization.type == "department")
    )
    if org is None:
        raise HTTPException(status_code=404, detail=f"部门 '{dept_code}' 不存在")

    any_member = await db.scalar(
        select(User).where(User.org_id == org.id)
    )
    if any_member is not None:
        raise HTTPException(status_code=409, detail="该部门仍有成员，无法删除")

    await db.delete(org)
    await db.commit()


@router.get("/departments/{dept_code}/a2a-status")
async def get_dept_a2a_status(
    dept_code: str,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> dict:
    """探活部门 A2A 端点，返回在线状态和延迟。"""
    org = await db.scalar(
        select(Organization)
        .where(Organization.dept_code == dept_code, Organization.type == "department")
    )
    if org is None:
        raise HTTPException(status_code=404, detail=f"部门 '{dept_code}' 不存在")
    if not org.a2a_url:
        return {"dept_code": dept_code, "online": False, "latency_ms": None, "error": "未配置 A2A 地址"}

    url = org.a2a_url.rstrip("/")
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{url}/.well-known/agent.json", timeout=2.0)
        latency_ms = int((time.monotonic() - t0) * 1000)
        online = resp.status_code < 400
    except Exception as exc:
        latency_ms = None
        online = False
        return {"dept_code": dept_code, "online": online, "latency_ms": latency_ms, "error": str(exc)}
    return {"dept_code": dept_code, "online": online, "latency_ms": latency_ms}


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


# ── MCP 连接管理 ───────────────────────────────────────────────────────────────

class McpConnection(BaseModel):
    name: str
    url: str
    description: str = ""


async def _require_command_org(user: User, db: AsyncSession) -> None:
    """调用者必须属于 command 类型机构，否则 403。"""
    if not user.org_id:
        raise HTTPException(status_code=403, detail="需要指挥中心账号权限")
    org = await db.scalar(select(Organization).where(Organization.id == user.org_id))
    if org is None or org.type != "command":
        raise HTTPException(status_code=403, detail="需要指挥中心账号权限")


async def _get_my_org(current_user: User, db: AsyncSession) -> Organization:
    if not current_user.org_id:
        raise HTTPException(status_code=404, detail="您还未加入任何部门")
    org = await db.scalar(select(Organization).where(Organization.id == current_user.org_id))
    if org is None:
        raise HTTPException(status_code=404, detail="机构不存在")
    return org


@router.get("/me/mcp-connections", response_model=list[McpConnection])
async def list_mcp_connections(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> list[McpConnection]:
    """查询当前机构的 MCP 连接列表。"""
    org = await _get_my_org(current_user, db)
    return [McpConnection(**c) for c in (org.mcp_connections or [])]


@router.put("/me/mcp-connections", response_model=list[McpConnection])
async def replace_mcp_connections(
    body: list[McpConnection],
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> list[McpConnection]:
    """全量替换当前机构的 MCP 连接列表。"""
    org = await _get_my_org(current_user, db)
    org.mcp_connections = [c.model_dump() for c in body]
    await db.commit()
    await db.refresh(org)
    return [McpConnection(**c) for c in org.mcp_connections]


@router.post("/me/mcp-connections", response_model=list[McpConnection], status_code=201)
async def add_mcp_connection(
    body: McpConnection,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> list[McpConnection]:
    """向当前机构追加一条 MCP 连接（name 重复则报错）。"""
    org = await _get_my_org(current_user, db)
    existing = org.mcp_connections or []
    if any(c["name"] == body.name for c in existing):
        raise HTTPException(status_code=409, detail=f"名称 '{body.name}' 已存在")
    org.mcp_connections = existing + [body.model_dump()]
    await db.commit()
    await db.refresh(org)
    return [McpConnection(**c) for c in org.mcp_connections]


class McpStatusItem(BaseModel):
    name: str
    url: str
    description: str = ""
    online: bool
    latency_ms: int | None = None


def _is_safe_probe_url(raw: str) -> bool:
    """仅允许 http/https；拒绝非法 scheme 防止 SSRF。
    注意：Demo 环境中 MCP 服务跑在 localhost，此处不限制私有 IP 段，
    生产部署时应追加 addr.is_private / addr.is_loopback 校验。
    """
    try:
        parsed = urlparse(raw)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.hostname or ""
        return bool(host)
    except Exception:
        return False


async def _probe(client: httpx.AsyncClient, conn: dict) -> McpStatusItem:
    """GET {url}/ — 2 秒超时；任意 2xx/3xx 视为 online。"""
    raw_url = conn["url"]
    if not _is_safe_probe_url(raw_url):
        return McpStatusItem(
            name=conn["name"], url=raw_url,
            description=conn.get("description", ""),
            online=False, latency_ms=None,
        )
    url = raw_url.rstrip("/")
    t0 = time.monotonic()
    try:
        resp = await client.get(f"{url}/", timeout=2.0)
        latency_ms = int((time.monotonic() - t0) * 1000)
        online = resp.status_code < 400
    except Exception:
        latency_ms = None
        online = False
    return McpStatusItem(
        name=conn["name"],
        url=raw_url,
        description=conn.get("description", ""),
        online=online,
        latency_ms=latency_ms,
    )


@router.get("/me/mcp-status", response_model=list[McpStatusItem])
async def get_mcp_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> list[McpStatusItem]:
    """并发探活当前机构所有 MCP 连接，返回 online/offline 及延迟。"""
    org = await _get_my_org(current_user, db)
    connections = org.mcp_connections or []
    if not connections:
        return []
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*(_probe(client, c) for c in connections))
    return list(results)


@router.delete("/me/mcp-connections/{name}", response_model=list[McpConnection])
async def delete_mcp_connection(
    name: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> list[McpConnection]:
    """按 name 删除一条 MCP 连接。"""
    org = await _get_my_org(current_user, db)
    existing = org.mcp_connections or []
    updated = [c for c in existing if c["name"] != name]
    if len(updated) == len(existing):
        raise HTTPException(status_code=404, detail=f"未找到名称为 '{name}' 的 MCP 连接")
    org.mcp_connections = updated
    await db.commit()
    await db.refresh(org)
    return [McpConnection(**c) for c in org.mcp_connections]
