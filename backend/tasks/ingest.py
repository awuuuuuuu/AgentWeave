from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

_DATABASE_URL = os.environ["DATABASE_URL"]

@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def ingest_document(
    self, object_key: str, kb_id: str, original_filename: str, doc_id: str
) -> dict:
    """
    Celery 摄入任务：从 MinIO 下载 → 解析 → embedding → 写入 Milvus
    整个任务生命周期共用一个 async engine, 减少连接池创建开销
    """
    from db.models import DocumentStatus
    from ingestion.embedder.openai_embedder import OpenAIEmbedder
    from ingestion.store.milvus_store import MilvusStore, MilvusStoreConfig
    from ingestion.pipeline import IngestionPipeline
    from knowledge.service import update_document_status
    from rag.settings import RAGChainSettings
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from storage.minio_client import download_to_tempfile

    async def _run() -> None:
        engine = create_async_engine(_DATABASE_URL, pool_pre_ping=True, pool_size=2)
        session = AsyncSession(engine, expire_on_commit=False)
        local_path: str | None = None

        try:
            await update_document_status(doc_id, DocumentStatus.PROCESSING, session)

            # 从 MinIO 下载到本地临时文件
            ext = os.path.splitext(original_filename)[1].lower()
            local_path = download_to_tempfile(object_key, suffix=ext)

            # 摄入 pipeline
            cfg = RAGChainSettings()
            embedder = OpenAIEmbedder()
            store = MilvusStore(MilvusStoreConfig(uri=cfg.milvus_uri))
            pipeline = IngestionPipeline(embedder=embedder, store=store)
            pipeline.run([local_path], knowledge_base_id=kb_id)

            await update_document_status(doc_id, DocumentStatus.READY, session)
        
        except Exception:
            raise

        finally:
            if local_path:
                try:
                    os.remove(local_path)
                except OSError:
                    pass
            await session.close()
            await engine.dispose()

    try:
        asyncio.run(_run())
    except Exception as exc:
        logger.exception("摄入失败: %s (kb=%s)", original_filename, kb_id)

        is_final_failure = self.request.retries >= self.max_retries
        if is_final_failure:
            # 只有用完所有重试次数才标 ERROR
            async def _mark_error() -> None:
                from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
                engine = create_async_engine(_DATABASE_URL, pool_pre_ping=True, pool_size=1)
                session = AsyncSession(engine, expire_on_commit=False)
                try:
                    await update_document_status(
                        doc_id, DocumentStatus.ERROR, session, error_message=str(exc)
                    )
                finally:
                    await session.close()
                    await engine.dispose()
            asyncio.run(_mark_error())

        raise self.retry(exc=exc)
    
    return {"doc_id": doc_id, "kb_id": kb_id, "filename": original_filename, "status": "ready"}