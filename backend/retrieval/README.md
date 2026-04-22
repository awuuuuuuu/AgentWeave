# 混合检索系统（Retrieval）

将用户查询转化为精准的上下文候选集，供 RAG Chain 生成答案使用。

---

## 整体数据流

```
用户查询（str）
  │
  ├─────────────────────────────────────────────────┐
  ▼ VectorRetriever（稠密向量检索）                  ▼ BM25Retriever（稀疏向量检索）
  │  embed_query → Milvus HNSW COSINE               │  原始文本 → Milvus BM25 Function
  │  输出：list[RetrievedChunk]（vector_score）       │  输出：list[RetrievedChunk]（bm25_score）
  └──────────────────┬──────────────────────────────┘
                     ▼ HybridRetriever（融合）
                     │  Query-level min-max 归一化
                     │  fusion = α×norm(vec) + (1-α)×norm(bm25)
                     │  输出：list[RetrievedChunk]（fusion_score）
                     │
                     ▼ Reranker（精排，可选）
                        cross-encoder 重新评分
                        输出：list[RetrievedChunk]（rerank_score）
```

---

## 模块结构

```
retrieval/
├── base.py               # RetrievedChunk 数据契约 + BaseRetriever 抽象基类
├── vector_retriever.py   # Milvus 稠密向量（COSINE/IP）检索
├── bm25_retriever.py     # Milvus 内置 BM25 稀疏向量检索
├── hybrid_retriever.py   # 双路并发融合检索器
└── reranker.py           # Cross-encoder 精排
```

---

## 快速使用

```python
from ingestion.embedder.openai_embedder import OpenAIEmbedder
from ingestion.store.milvus_store import MilvusStoreConfig
from retrieval.hybrid_retriever import HybridRetriever, HybridRetrieverConfig
from retrieval.reranker import Reranker

store_cfg = MilvusStoreConfig(uri="http://localhost:19530")
embedder  = OpenAIEmbedder()

# 混合检索
retriever = HybridRetriever(
    embedder=embedder,
    config=HybridRetrieverConfig(store_config=store_cfg, alpha=0.7),
)
chunks = retriever.retrieve("RAG 和向量数据库的关系", knowledge_base_id="kb1", top_k=10)

# 精排（可选）
reranker = Reranker()
ranked   = reranker.rerank("RAG 和向量数据库的关系", chunks, top_k=5)
```

---

## 层间数据契约

### `RetrievedChunk`

```python
@dataclass
class RetrievedChunk:
    chunk_id:            str    # SHA256 主键（与 MilvusStore 一致）
    text:                str
    source_file:         str
    section_path:        str
    chunk_index_in_doc:  int    # 原文序号，引用溯源时按原文顺序排列

    vector_score:  float = 0.0  # 原始余弦相似度（未归一化）
    bm25_score:    float = 0.0  # 原始 BM25 分数（无上界）
    fusion_score:  float = 0.0  # Weighted Sum 归一化后融合分
    rerank_score:  float = 0.0  # cross-encoder 精排分（未经过精排时为 0.0）

    retrieval_method: str = "hybrid"  # "vector" | "bm25" | "hybrid"
    extra_meta:       dict = field(default_factory=dict)
```

四个分数字段全部透传给上层，便于调试、日志和阈值调优，无需重新查库。

---

## 关键设计决策

### 1. Weighted Sum 而非 RRF

业界常用 Reciprocal Rank Fusion（RRF）做混合检索融合，但 RRF 只使用排名、丢弃原始分数。RAGent 选择 **Weighted Sum**：

```
fusion_score = α × norm(vector_score) + (1-α) × norm(bm25_score)
```

理由：保留双路原始分数，便于在 `RetrievedChunk` 中透传给上层做调试和阈值调优；RRF 输出的融合分无法反映"某路检索完全没命中"的情况。

### 2. Query-level min-max 归一化

BM25 分数无上界（取决于词频和文档长度），不能用全局固定的 min/max 做归一化。归一化在**本次查询的候选集内部**进行：

```python
norm(s) = (s - min) / (max - min)   # 全相同时返回 1.0
```

每次查询独立计算，与历史查询无关，保证两路分数在同一尺度上比较。

### 3. Milvus 内置 BM25 Function

BM25 使用 Milvus 2.5 的内置 Function，在 insert 时自动将 `text` 字段转为稀疏向量存入 `sparse_vector` 字段，查询时同样自动转换。Python 侧传入原始字符串即可，零摄入改造。

对比方案：
- Python 侧 `BM25EmbeddingFunction`：需要在摄入时预计算稀疏向量，摄入 pipeline 需要修改
- Milvus 内置：摄入 pipeline 完全不变，模型与 Milvus 侧保持一致，避免线上/线下不一致

### 4. 双路并发，不串行

`_fuse()` 中向量检索和 BM25 检索完全独立，并发执行：

```
串行：total = t_vec + t_bm25 ≈ 180ms
并发：total = max(t_vec, t_bm25) ≈ 100ms
```

- **同步路径**：模块级 `_FUSE_EXECUTOR`（`ThreadPoolExecutor(max_workers=2)`），避免每次调用重建线程池
- **异步路径**：`aretrieve()` override，用 `asyncio.gather` 并发两路 `aretrieve()`

### 5. HybridRetriever 统一控制候选倍数

子 retriever（VectorRetriever / BM25Retriever）的 `candidate_multiplier` 固定为 `1`，候选放大由 `HybridRetriever._fuse()` 统一控制（`fetch_k = top_k * multiplier`）。

避免双重放大：若子 retriever 自带 multiplier=2，HybridRetriever 再乘 2，实际拿到的是 `top_k * 4`，与配置语义不符。

### 6. HNSW ef 动态跟随 candidates

Milvus HNSW 索引有硬性约束：`ef >= limit`。若 `ef` 写死为 64，当 `top_k=50, candidates=100` 时 Milvus C++ 层直接崩溃。

```python
ef = max(64, candidates)   # 保底 64，超出时动态扩展
```

---

## Milvus 专项注意事项

| 事项 | 说明 |
|------|------|
| `enable_bm25=False` 时禁止使用 BM25Retriever | 初始化时报 `ValueError`，避免运行时在 `sparse_vector` 上查询失败 |
| BM25 查询传原始字符串 | `data=[query]`，Milvus Function 自动转稀疏向量，不能传向量 |
| `drop_ratio_search=0.2` | 丢弃 query 中权重最低的 20% 词汇，长 query 含停用词时提升精度 |
| schema 过期时报错不删库 | `_needs_migration()` 检测缺失字段后抛 `RuntimeError`，需运行 `scripts/migrate_add_bm25.py` |

---

## TODO

| 优先级 | 描述 | 计划 Step |
|--------|------|-----------|
| 中 | `arerank()` 异步方法：`model.predict` 是 CPU/GPU 密集型阻塞，应用 `run_in_executor` 封装 | Step 3 |
| 低 | metric_type 切换为 IP：OpenAI text-embedding-3-small 输出 L2 归一化向量，IP ≡ COSINE 但省去浮点除法，性能提升 15~30% | Step 3（重建 collection） |
| 低 | 检索结果缓存：相同 query+kb_id 命中 Redis 缓存，跳过 Milvus 查询 | Step 3（Redis 引入后） |
