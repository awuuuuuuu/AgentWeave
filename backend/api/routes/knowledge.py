from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import get_current_user
from db.models import User
from db.session import get_session
from knowledge import service as kb_service
from knowledge.schemas import (
    KBCreate,
    KBResponse,
    KBUpdate,
    DocumentResponse,
    UploadResponse
)

from storage import minio_client
from tasks.ingest import ingest_document

router = APIRouter(prefix="/kb", tags=["knowledge"])

_ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt", ".md", ".html", ".csv", ".xlsx"}


# ── 知识库 CRUD ───────────────────────────────────────────────────────────────

@router.get("", response_model=list[KBResponse])
async def list_kbs(
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session)
) -> list[KBResponse]:
    kbs = await kb_service.list_kbs(current_user.id, session, limit=limit, offset=offset)
    return [KBResponse.model_validate(kb) for kb in kbs]

@router.post("", response_model=KBResponse, status_code=status.HTTP_201_CREATED)
async def create_kb(
    req: KBCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session)
) -> KBResponse:
    kb = await kb_service.create_kb(req, current_user.id, session)
    return KBResponse.model_validate(kb)

@router.get("/{kb_id}", response_model=KBResponse)
async def get_db(
    kb_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> KBResponse:
    try:
        kb = await kb_service.get_kb(kb_id, current_user.id, session)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return KBResponse.model_validate(kb)

@router.patch("/{kb_id}", response_model=KBResponse)
async def update_kb(
    kb_id: str,
    req: KBUpdate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session)
) -> KBResponse:
    try:
        kb = await kb_service.update_kb(kb_id, req, current_user.id, session)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return KBResponse.model_validate(kb)

@router.delete("/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_kb(
    kb_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    try:
        await kb_service.delete_kb(kb_id, current_user.id, session)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


# ── 文档管理 ──────────────────────────────────────────────────────────────────

@router.get("/{kb_id}/documents", response_model=list[DocumentResponse])
async def list_documents(
    kb_id: str,
    limit: int = 100,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[DocumentResponse]:
    try:
        docs = await kb_service.list_documents(kb_id, current_user.id, session, limit=limit, offset=offset)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return [DocumentResponse.model_validate(d) for d in docs]


@router.get("/{kb_id}/documents/{doc_id}", response_model=DocumentResponse)
async def get_document(
    kb_id: str,
    doc_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> DocumentResponse:
    try:
        doc = await kb_service.get_document(doc_id, kb_id, current_user.id, session)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return DocumentResponse.model_validate(doc)


@router.delete("/{kb_id}/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    kb_id: str,
    doc_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    try:
        await kb_service.delete_document(doc_id, kb_id, current_user.id, session)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.post("/{kb_id}/documents/upload", response_model=UploadResponse)
async def upload_document(
    kb_id: str,
    file: UploadFile,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session)
) -> UploadResponse:
    # 验证 KB 是不是用户的
    try:
        await kb_service.get_kb(kb_id, current_user.id, session)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    # 验证文件类型是否合法
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"File type '{ext}' not supported. Allowed: {sorted(_ALLOWED_EXTENSIONS)}",
        )
    
    # 上传到 MinIO（在线程池中执行，避免阻塞事件循环）
    object_key = f"uploads/{kb_id}/{uuid.uuid4()}{ext}"
    from fastapi.concurrency import run_in_threadpool
    await run_in_threadpool(minio_client.upload_fileobj, file.file, object_key)

    # 先创建 Document 记录拿到 doc_id，再发 Celery 任务（避免 Worker 先跑查不到记录的竞态）
    original_filename = file.filename or object_key
    doc = await kb_service.create_document(
        kb_id, original_filename, task_id="pending", session=session, object_key=object_key
    )
    task = ingest_document.delay(object_key, kb_id, original_filename, doc.id)

    # 回填 task_id
    doc.task_id = task.id
    await session.commit()

    return UploadResponse(
        document_id=doc.id,
        task_id=doc.task_id,
        filename=doc.filename,
        status=doc.status,
    )