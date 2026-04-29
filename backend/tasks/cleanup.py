from __future__ import annotations

import logging
import os

from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _delete_minio_objects(object_keys: list[str]) -> None:
    """幂等删除 MinIO 对象：对象不存在时静默跳过。"""
    from storage import minio_client

    for key in object_keys:
        try:
            minio_client.delete_object(key)
        except ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                pass  # 已删除，幂等
            else:
                logger.warning("MinIO 删除失败 %s: %s", key, e)
        except Exception as e:
            logger.warning("MinIO 删除失败 %s: %s", key, e)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
def cleanup_kb(self, kb_id: str, object_keys: list[str]) -> None:
    """软删除后的异步清理：删除 MinIO 对象 + Milvus chunks。

    两步均幂等：MinIO NoSuchKey 静默跳过，Milvus delete 空结果无副作用。
    若 Milvus 失败则 retry，MinIO 步骤不会重复报错。
    """
    # Step 1：幂等删除 MinIO（即使 retry 也不产生虚假警告）
    _delete_minio_objects(object_keys)

    # Step 2：幂等删除 Milvus chunks（查不到直接结束循环）
    try:
        from ingestion.store.milvus_store import MilvusStore, MilvusStoreConfig
        from rag.settings import RAGChainSettings
        cfg = RAGChainSettings()
        store = MilvusStore(MilvusStoreConfig(uri=cfg.milvus_uri))
        store.delete_by_kb(kb_id)
    except Exception as exc:
        logger.exception("Milvus 清理失败 kb=%s", kb_id)
        raise self.retry(exc=exc)
