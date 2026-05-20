"""
会话 CRUD 路由

GET    /sessions           — 列出当前用户所有会话（按 updated_at 倒序）
POST   /sessions           — 创建新会话
PATCH  /sessions/{id}      — 更新标题 / 状态 / message_count
DELETE /sessions/{id}      — 软删除（status=deleted）
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import get_current_user
from db.models import ChatMessage, ConversationSession, User
from db.session import get_session

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Schema ────────────────────────────────────────────────────────────────────

class SessionCreate(BaseModel):
    kb_ids: list[str] = []
    session_type: str = "chat"


class SessionUpdate(BaseModel):
    title: Optional[str] = None
    status: Optional[str] = None
    message_count: Optional[int] = None


class SessionOut(BaseModel):
    id: str
    title: Optional[str]
    status: str
    session_type: str
    message_count: int
    kb_ids: Optional[list[str]]
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[SessionOut])
async def list_sessions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> list[SessionOut]:
    """列出用户所有非删除会话（按 updated_at 倒序）"""
    result = await db.execute(
        select(ConversationSession)
        .where(
            ConversationSession.user_id == current_user.id,
            ConversationSession.status != "deleted",
        )
        .order_by(ConversationSession.updated_at.desc().nullslast())
    )
    sessions = result.scalars().all()
    return [SessionOut.model_validate(s) for s in sessions]


@router.post("", response_model=SessionOut, status_code=201)
async def create_session(
    body: SessionCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> SessionOut:
    """创建新会话"""
    session = ConversationSession(
        user_id=current_user.id,
        kb_ids=body.kb_ids or None,
        session_type=body.session_type,
        status="active",
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return SessionOut.model_validate(session)


@router.patch("/{session_id}", response_model=SessionOut)
async def update_session(
    session_id: str,
    body: SessionUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> SessionOut:
    """更新会话标题 / 状态 / message_count，同时刷新 updated_at"""
    result = await db.execute(
        select(ConversationSession).where(
            ConversationSession.id == session_id,
            ConversationSession.user_id == current_user.id,
        )
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    if body.title is not None:
        session.title = body.title
    if body.status is not None:
        session.status = body.status
    if body.message_count is not None:
        session.message_count = body.message_count
    session.updated_at = _now()

    await db.commit()
    await db.refresh(session)
    return SessionOut.model_validate(session)


@router.delete("/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> None:
    """软删除会话（status=deleted）"""
    result = await db.execute(
        select(ConversationSession).where(
            ConversationSession.id == session_id,
            ConversationSession.user_id == current_user.id,
        )
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    session.status = "deleted"
    await db.commit()


# ── 消息 CRUD ─────────────────────────────────────────────────────────────────

class MessageIn(BaseModel):
    seq: int
    agent: str
    content: str
    citations: list | None = None
    hitl_data: dict | None = None
    reply_to: dict | None = None
    is_final_answer: bool = False


class MessageOut(BaseModel):
    id: str
    seq: int
    agent: str
    content: str
    citations: list | None
    hitl_data: dict | None
    reply_to: dict | None
    is_final_answer: bool

    class Config:
        from_attributes = True


@router.get("/{session_id}/messages", response_model=list[MessageOut])
async def list_messages(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> list[MessageOut]:
    """返回会话内所有消息，按 seq 升序。"""
    # 鉴权：确认会话属于当前用户
    sess_result = await db.execute(
        select(ConversationSession).where(
            ConversationSession.id == session_id,
            ConversationSession.user_id == current_user.id,
            ConversationSession.status != "deleted",
        )
    )
    if sess_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.seq)
    )
    return [MessageOut.model_validate(m) for m in result.scalars().all()]


@router.post("/{session_id}/messages", response_model=list[MessageOut], status_code=201)
async def save_messages(
    session_id: str,
    body: list[MessageIn],
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> list[MessageOut]:
    """批量追加消息到会话（幂等：按 session_id+seq 去重）。"""
    sess_result = await db.execute(
        select(ConversationSession).where(
            ConversationSession.id == session_id,
            ConversationSession.user_id == current_user.id,
            ConversationSession.status != "deleted",
        )
    )
    if sess_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    # 查出已存在的 seq，避免重复插入
    existing = await db.execute(
        select(ChatMessage.seq).where(ChatMessage.session_id == session_id)
    )
    existing_seqs = {row for row in existing.scalars()}

    created: list[ChatMessage] = []
    for msg in body:
        if msg.seq in existing_seqs:
            continue
        m = ChatMessage(
            session_id=session_id,
            seq=msg.seq,
            agent=msg.agent,
            content=msg.content,
            citations=msg.citations,
            hitl_data=msg.hitl_data,
            reply_to=msg.reply_to,
            is_final_answer=msg.is_final_answer,
        )
        db.add(m)
        created.append(m)

    await db.commit()
    for m in created:
        await db.refresh(m)
    return [MessageOut.model_validate(m) for m in created]
