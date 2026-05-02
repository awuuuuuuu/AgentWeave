from __future__ import annotations

import logging

from tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
def cleanup_kb(self, kb_id: str, object_keys: list[str]) -> None:
    """
    软删除后的异步清理：批量删除 MinIO 对象 + Milvus chunks

    MinIO 使用批量 delete_objects,
    NoSuchKey 在 minio_client.delete_objects 内静默跳过，其他错误触发 retry
    """

    # Step 1：批量删除 MinIO
    if object_keys:
        from storage import minio_client
        failed = minio_client.delete_objects(object_keys)
        if failed:
            logger.warning("MinIO 批量删除部分失败，将重试: %s", failed)
            raise self.retry(exc=RuntimeError(f"MinIO delete failed for keys: {failed}"))

    # Step 2：删除 Milvus chunks
    try:
        from ingestion.store.milvus_store import MilvusStore, MilvusStoreConfig
        from rag.settings import RAGChainSettings
        cfg = RAGChainSettings()
        store = MilvusStore(MilvusStoreConfig(uri=cfg.milvus_uri))
        store.delete_by_kb(kb_id)
    except Exception as exc:
        logger.exception("Milvus 清理失败 kb=%s", kb_id)
        raise self.retry(exc=exc)