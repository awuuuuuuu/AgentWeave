from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .session import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


# ── 枚举 ────────────────────────────────────────────────────────────────────


class DocumentStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    ERROR = "error"


# ── 表模型 ───────────────────────────────────────────────────────────────────


class User(Base):
    """系统用户。"""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    knowledge_bases: Mapped[list[KnowledgeBase]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )


class KnowledgeBase(Base):
    """知识库，归属于某个用户，对应 Milvus 中的 kb_id。"""

    __tablename__ = "knowledge_bases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 检索设置（per-KB，查询时覆盖全局默认值）
    retrieval_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="hybrid")
    use_rerank: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    top_k: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    score_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # hybrid 子模式："weighted"（权重融合）或 "rerank"（Rerank精排）
    hybrid_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="weighted")
    # hybrid weighted 模式下语义向量权重（关键词权重 = 1 - vector_weight）
    vector_weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.7)
    # 分段模式（首次上传后锁定）：None = 尚未设置，"recursive" / "parent_child"
    splitter_type: Mapped[str | None] = mapped_column(String(32), nullable=True, default=None)
    chunk_size: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    chunk_overlap: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    separators: Mapped[list | None] = mapped_column(JSON, nullable=True, default=None)
    child_separators: Mapped[list | None] = mapped_column(JSON, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    owner: Mapped[User] = relationship(back_populates="knowledge_bases")
    documents: Mapped[list[Document]] = relationship(
        back_populates="knowledge_base", cascade="all, delete-orphan"
    )


class Document(Base):
    """上传到知识库的文件，追踪摄入状态。"""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    kb_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=DocumentStatus.PENDING.value
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    object_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    doc_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="documents")


class ConversationSession(Base):
    """对话会话，对应 LangGraph 的一个 thread_id。"""

    __tablename__ = "conversation_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # active / ended（结束后触发摘要异步写入长期记忆）
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # LLM 生成的本次会话摘要（会话结束后异步写入）
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship()


class UserProfile(Base):
    """用户语义记忆：偏好、专业方向、常用知识库等，每用户一条记录。"""

    __tablename__ = "user_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    preferred_language: Mapped[str] = mapped_column(String(16), nullable=False, default="zh")
    # beginner / intermediate / expert
    expertise_level: Mapped[str] = mapped_column(String(32), nullable=False, default="intermediate")
    frequent_topics: Mapped[list[str] | None] = mapped_column(JSON, nullable=True, default=None)
    frequent_kb_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True, default=None)
    # LLM 提取的自由格式偏好 key-value
    preferences: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    user: Mapped[User] = relationship()


class HitTestingLog(Base):
    """召回测试查询记录"""

    __tablename__ = "hit_testing_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    kb_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    query: Mapped[str] = mapped_column(Text, nullable=False)
    result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 召回快照：[{chunk_id, score, text, source_file}]，供 RAGAS 离线评估用
    retrieved_chunks: Mapped[list | None] = mapped_column(JSON, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
