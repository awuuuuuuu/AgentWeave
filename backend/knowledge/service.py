from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Document, DocumentStatus, KnowledgeBase
from .schemas import KBCreate, KBUpdate

async def list_kbs(
    user_id: str, session: AsyncSession, limit: int = 50, offset: int = 0
) -> list[KnowledgeBase]:
    result = await session.scalars(
        select(KnowledgeBase).where(
            KnowledgeBase.user_id == user_id,
            KnowledgeBase.is_deleted == False,  # noqa: E712
        ).offset(offset).limit(limit)
    )
    return list(result.all())

async def get_kb(kb_id: str, user_id: str, session: AsyncSession) -> KnowledgeBase:
    """返回KB, 不存在或已删除或不属于该用户时抛出 ValueError"""
    kb = await session.scalar(
        select(KnowledgeBase).where(
            KnowledgeBase.id == kb_id,
            KnowledgeBase.user_id == user_id,
            KnowledgeBase.is_deleted == False,  # noqa: E712
        )
    )
    if kb is None:
        raise ValueError("Knowledge base not found")
    return kb

async def create_kb(req: KBCreate, user_id: str, session: AsyncSession) -> KnowledgeBase:
    kb = KnowledgeBase(name=req.name, description=req.description, user_id=user_id)
    session.add(kb)
    await session.commit()
    await session.refresh(kb)
    return kb

async def update_kb(
    kb_id: str, req: KBUpdate, user_id: str, session: AsyncSession
) -> KnowledgeBase:
    kb = await get_kb(kb_id, user_id, session)
    if req.name is not None:
        kb.name = req.name
    if req.description is not None:
        kb.description = req.description
    await session.commit()
    await session.refresh(kb)
    return kb

async def delete_kb(kb_id: str, user_id: str, session: AsyncSession) -> None:
    """软删除 KB，异步清理 Milvus chunks + MinIO 对象。"""
    kb = await get_kb(kb_id, user_id, session)
    kb.is_deleted = True
    # 同时软删除旗下所有文档
    docs = await session.scalars(
        select(Document).where(Document.kb_id == kb_id, Document.is_deleted == False)  # noqa: E712
    )
    object_keys = []
    for doc in docs.all():
        doc.is_deleted = True
        if doc.object_key:
            object_keys.append(doc.object_key)
    await session.commit()

    # 异步清理 Milvus + MinIO
    from tasks.cleanup import cleanup_kb
    cleanup_kb.delay(kb_id, object_keys)

async def list_documents(
    kb_id: str, user_id: str, session: AsyncSession, limit: int = 100, offset: int = 0
) -> list[Document]:
    await get_kb(kb_id, user_id, session)
    result = await session.scalars(
        select(Document).where(
            Document.kb_id == kb_id,
            Document.is_deleted == False,  # noqa: E712
        ).offset(offset).limit(limit)
    )
    return list(result.all())

async def get_document(
    doc_id: str, kb_id: str, user_id: str, session: AsyncSession
) -> Document:
    await get_kb(kb_id, user_id, session)
    doc = await session.scalar(
        select(Document).where(
            Document.id == doc_id,
            Document.kb_id == kb_id,
            Document.is_deleted == False,  # noqa: E712
        )
    )
    if doc is None:
        raise ValueError("Document not found")
    return doc

async def create_document(
    kb_id: str, filename: str, task_id: str, session: AsyncSession,
    object_key: str | None = None
) -> Document:
    doc = Document(
        kb_id=kb_id,
        filename=filename,
        status=DocumentStatus.PENDING.value,
        task_id=task_id,
        object_key=object_key
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)
    return doc

async def update_document_status(
    doc_id: str,
    status: DocumentStatus,
    session: AsyncSession,
    error_message: str | None = None
) -> None:
    doc = await session.scalar(
        select(Document).where(Document.id == doc_id)
    )
    if doc is None:
        return
    doc.status = status.value
    doc.error_message = error_message
    await session.commit()