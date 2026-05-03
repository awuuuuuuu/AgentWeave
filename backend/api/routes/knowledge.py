from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, status, UploadFile
from typing import Literal
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import get_current_user
from db.models import User
from db.session import get_session
from knowledge import service as kb_service
from knowledge.schemas import (
    KBCreate,
    KBResponse,
    KBRetrievalSettings,
    KBUpdate,
    DocumentResponse,
    UploadResponse,
    ChunkPreviewItem,
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

@router.patch("/{kb_id}/retrieval-settings", response_model=KBResponse)
async def update_retrieval_settings(
    kb_id: str,
    req: KBRetrievalSettings,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> KBResponse:
    try:
        kb = await kb_service.update_kb_retrieval_settings(kb_id, req, current_user.id, session)
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
    splitter_type: Literal["recursive", "parent_child"] = Form("recursive"),
    chunk_size: int = Form(512),
    chunk_overlap: int = Form(64),
    separators: str = Form(""),          # JSON 数组字符串，由前端 JSON.stringify 传入
    child_separators: str = Form(""),    # 子块分隔符（仅 parent_child 模式）
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
    import json as _json

    def _parse_separators(raw: str) -> list[str] | None:
        if not raw:
            return None
        try:
            return _json.loads(raw)
        except (ValueError, TypeError):
            return None

    task = ingest_document.delay(
        object_key, kb_id, original_filename, doc.id,
        splitter_type=splitter_type,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=_parse_separators(separators),
        child_separators=_parse_separators(child_separators),
    )

    # 回填 task_id
    doc.task_id = task.id
    await session.commit()

    return UploadResponse(
        document_id=doc.id,
        task_id=doc.task_id,
        filename=doc.filename,
        status=doc.status,
    )


@router.post("/{kb_id}/documents/preview", response_model=list[ChunkPreviewItem])
async def preview_document(
    kb_id: str,
    file: UploadFile,
    splitter_type: Literal["recursive", "parent_child"] = Form("recursive"),
    chunk_size: int = Form(512),
    chunk_overlap: int = Form(64),
    separators: str = Form(""),          # JSON 数组字符串，空串代表使用默认值
    child_separators: str = Form(""),    # 子块分隔符（仅 parent_child 模式）
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ChunkPreviewItem]:
    """解析 + 分段预览，不做 embedding 也不写 Milvus。"""
    try:
        await kb_service.get_kb(kb_id, current_user.id, session)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"File type '{ext}' not supported.",
        )

    import json as _json
    import shutil
    import tempfile
    from fastapi.concurrency import run_in_threadpool

    _MAX_PREVIEW_CHUNKS = 50

    def _parse_json_sep(raw: str) -> list[str] | None:
        if not raw:
            return None
        try:
            return _json.loads(raw)
        except (ValueError, TypeError):
            return None

    custom_separators = _parse_json_sep(separators)
    custom_child_separators = _parse_json_sep(child_separators)

    def _parse_and_split() -> list[ChunkPreviewItem]:
        import os as _os
        from ingestion.parsers.base import get_parser
        import ingestion.parsers  # noqa: F401 – triggers @register_parser decorators

        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = tmp.name

        try:
            from ingestion.parsers.pdf_parser import PdfParser
            parser = get_parser(ext)
            # Preview 只看分段结构，不需要 OCR；PDF 固定 fast 策略避免 API 依赖
            parse_kwargs = {"strategy": "fast"} if isinstance(parser, PdfParser) else {}
            raw_chunks = parser.parse(tmp_path, **parse_kwargs)

            if splitter_type == "parent_child":
                from ingestion.splitter.parent_child import ParentChildSplitter, ParentChildConfig
                splitter = ParentChildSplitter(ParentChildConfig(
                    parent_chunk_size=chunk_size,
                    child_chunk_size=max(chunk_size // 4, 64),
                    child_overlap=max(chunk_overlap // 4, 8),
                    parent_separators=custom_separators or None,
                    child_separators=custom_child_separators or None,
                ))
            else:
                from ingestion.splitter.recursive import RecursiveSplitter, RecursiveConfig
                cfg = RecursiveConfig(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
                if custom_separators:
                    cfg.separators = custom_separators
                splitter = RecursiveSplitter(cfg)

            chunks = splitter.split(raw_chunks)[:_MAX_PREVIEW_CHUNKS]
            return [
                ChunkPreviewItem(
                    index=i,
                    text=c.text,
                    content_type=c.metadata.get("content_type", "text"),
                    page_number=c.metadata.get("page_number"),
                    section_path=c.metadata.get("section_path", ""),
                    token_count=splitter.count_tokens(c.text),
                )
                for i, c in enumerate(chunks)
            ]
        finally:
            _os.unlink(tmp_path)

    return await run_in_threadpool(_parse_and_split)