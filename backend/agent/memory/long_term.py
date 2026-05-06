"""
长期情景记忆(Episodic Memory)

基于 Milvus 独立 collection (memory_summaries), 存储历史对话摘要向量
支持跨会话语义检索，为新对话注入相关历史上下文。
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass

from pymilvus import DataType, MilvusClient

logger = logging.getLogger(__name__)

# ── Collection 配置 ──────────────────────────────────────────────────────────

_COLLECTION_NAME = "memory_summaries"
_VECTOR_DIM = 1536         # text-embedding-3-small 维度
_TEXT_MAX_BYTES = 65_535

# 字段名
_F_ID         = "id"          # VARCHAR 主键
_F_USER_ID    = "user_id"     # VARCHAR，Partition Key，多租户隔离
_F_SESSION_ID = "session_id"  # VARCHAR，scalar index
_F_CONTENT    = "content"     # VARCHAR，摘要文本
_F_VECTOR     = "vector"      # FLOAT_VECTOR，摘要语义向量
_F_CREATED_AT = "created_at"  # INT64，Unix 时间戳

# 每用户最多保留的摘要条数（超出时删除最旧的）
_MAX_SUMMARIES_PER_USER = 100


@dataclass
class MemorySummary:
    """检索返回的记忆条目"""
    id: str
    session_id: str
    content: str
    score: float
    created_at: int # Unix timestamp


class LongTermMemory:
    """
    情景记忆层：对话摘要 → 向量化 → Milvus 存储与检索。
    """

    def __init__(
        self,
        milvus_uri: str = "http://localhost:19530",
        client: MilvusClient | None = None
    ) -> None:
        self._client = client or MilvusClient(uri=milvus_uri)
        self._embedder: object | None = None
        self._ensure_collection()

    async def add_summary(
        self,
        user_id: str,
        session_id: str,
        summary_text: str
    ) -> str | None:
        """
        将对话摘要向量化并写入 Milvus。

        返回写入记录的 id, 失败时返回 None
        """
        if not summary_text.strip():
            return None
        if self._embedder is None:
            logger.error("LongTermMemory: 未设置 embedder, 无法添加摘要")
            return None
        
        try:
            text = summary_text[:_TEXT_MAX_BYTES]

            vector = await self._embedder.aembed_query(text)

            record_id = str(uuid.uuid4())
            now_ts = int(time.time())

            self._client.insert(
                collection_name=_COLLECTION_NAME,
                data=[
                    {
                        _F_ID: record_id,
                        _F_USER_ID: user_id,
                        _F_SESSION_ID: session_id,
                        _F_CONTENT: text,
                        _F_VECTOR: vector,
                        _F_CREATED_AT: now_ts,
                    }
                ]
            )
            logger.info("LongTermMemory: 成功添加摘要 | user=%s session=%s id=%s", user_id, session_id, record_id)
            
            await self._evict_oldest_if_needed(user_id)
            return record_id
        except Exception:
            logger.exception("LongTermMemory: 添加摘要失败 | user=%s session=%s", user_id, session_id)
            return None

    async def search_relevant(
        self,
        user_id: str,
        query: str,
        top_k: int = 3,
        score_threshold: float = 0.5,
    ) -> list[MemorySummary]:
        """
        语义检索与 query 最相关的历史摘要（仅限该用户）。

        返回按 created_at 升序排列（旧→新）的列表，利用 LLM 对 Context 尾部的 Recency Bias。
        score < score_threshold 的结果过滤掉，防止低相关度记忆污染上下文。
        """
        if not query.strip() or self._embedder is None:
            return []

        try:
            query_vector = await self._embedder.aembed_query(query)  # type: ignore[attr-defined]

            results = self._client.search(
                collection_name=_COLLECTION_NAME,
                data=[query_vector],
                limit=top_k,
                filter=f'{_F_USER_ID} == "{user_id}"',
                output_fields=[_F_SESSION_ID, _F_CONTENT, _F_CREATED_AT],
                search_params={"metric_type": "COSINE", "params": {"ef": 64}},
            )

            summaries: list[MemorySummary] = []
            for hit in results[0]:
                score = float(hit["distance"])
                if score < score_threshold:
                    continue
                summaries.append(
                    MemorySummary(
                        id=hit["id"],
                        session_id=hit["entity"][_F_SESSION_ID],
                        content=hit["entity"][_F_CONTENT],
                        score=score,
                        created_at=hit["entity"][_F_CREATED_AT],
                    )
                )

            summaries.sort(key=lambda s: s.created_at)
            return summaries
        except Exception:
            logger.exception("LongTermMemory: 语义检索失败 (用户=%s)", user_id)
            return []

    async def _evict_oldest_if_needed(self, user_id: str) -> None:
        """
        超出配额时删除最旧的记录（按 created_at 升序排序后取最旧的超额部分）
        """
        try:
            count_result = self._client.query(
                collection_name=_COLLECTION_NAME,
                filter=f'{_F_USER_ID} == "{user_id}"',
                output_fields=["count(*)"]
            )
            total = count_result[0].get("count(*)", 0) if count_result else 0
            if total <= _MAX_SUMMARIES_PER_USER:
                return
            
            all_records = self._client.query(
                collection_name=_COLLECTION_NAME,
                filter=f'{_F_USER_ID} == "{user_id}"',
                output_fields=[_F_ID, _F_CREATED_AT],
                limit=total,
            )

            all_records.sort(key=lambda r: r[_F_CREATED_AT])
            excess = total - _MAX_SUMMARIES_PER_USER
            ids_to_delete = [r[_F_ID] for r in all_records[:excess]]

            if ids_to_delete:
                id_list = '", "'.join(ids_to_delete)
                self._client.delete(
                    collection_name=_COLLECTION_NAME,
                    filter=f'{_F_ID} in ["{id_list}"]',
                )
                logger.info(
                    "LongTermMemory: 已淘汰 %d 条最旧的摘要 (用户=%s)",
                    len(ids_to_delete), user_id,
                )
        except Exception:
            logger.exception("LongTermMemory: 淘汰最旧摘要失败 (用户=%s)", user_id)


    def set_embedder(self, embedder: object) -> None:
        """注入 Embedder (启动时由 main.py 调用) """
        self._embedder = embedder

    def delete_by_session(self, user_id: str, session_id: str) -> int:
        """
        删除指定会话的所有记忆记录

        返回删除条数，失败返回 -1
        """
        return self._delete_by_filter(
            f'{_F_USER_ID} == "{user_id}" and {_F_SESSION_ID} == "{session_id}"',
            label=f"user={user_id} session={session_id}",
        )

    def delete_all_for_user(self, user_id: str) -> int:
        """
        彻底删除某用户的所有记忆

        返回删除条数，失败返回 -1
        """
        return self._delete_by_filter(
            f'{_F_USER_ID} == "{user_id}"',
            label=f"user={user_id}",
        )

    def _delete_by_filter(self, filter_expr: str, label: str) -> int:
        try:
            result = self._client.delete(
                collection_name=_COLLECTION_NAME,
                filter=filter_expr,
            )
            deleted = result.get("delete_count", 0)
            logger.info("LongTermMemory: 成功删除 %d 条记录 (目标: %s)", deleted, label)
            return deleted
        except Exception:
            logger.exception("LongTermMemory: 删除失败 (目标: %s)", label)
            return -1

    def _ensure_collection(self) -> None:
        """collection 不存在时创建，已存在则跳过"""
        if self._client.has_collection(_COLLECTION_NAME):
            logger.debug("LongTermMemory: 集合 %s 已存在", _COLLECTION_NAME)
            return

        schema = self._client.create_schema(
            auto_id=False,
            enable_dynamic_field=False,
        )
        schema.add_field(_F_ID, DataType.VARCHAR, max_length=36, is_primary=True)
        schema.add_field(_F_USER_ID, DataType.VARCHAR, max_length=36, is_partition_key=True)
        schema.add_field(_F_SESSION_ID, DataType.VARCHAR, max_length=36)
        schema.add_field(_F_CONTENT, DataType.VARCHAR, max_length=_TEXT_MAX_BYTES)
        schema.add_field(_F_VECTOR, DataType.FLOAT_VECTOR, dim=_VECTOR_DIM)
        schema.add_field(_F_CREATED_AT, DataType.INT64)

        index_params = self._client.prepare_index_params()
        index_params.add_index(
            field_name=_F_VECTOR,
            index_type="HNSW",
            metric_type="COSINE",
            params={"M": 16, "efConstruction": 128},
        )
        # 按 session_id 过滤时使用 INVERTED 索引
        index_params.add_index(
            field_name=_F_SESSION_ID,
            index_type="INVERTED",
        )
        index_params.add_index(
            field_name=_F_CREATED_AT,
            index_type="STL_SORT",
        )

        self._client.create_collection(
            collection_name=_COLLECTION_NAME,
            schema=schema,
            index_params=index_params,
        )
        logger.info("LongTermMemory: 成功创建集合 %s", _COLLECTION_NAME)

