# 文档摄入系统（Ingestion）

将原始文档转化为可检索的向量数据，写入 Milvus 向量数据库。

---

## 整体数据流

```
原始文件（PDF / Word / Excel / HTML / Markdown / TXT）
  │
  ▼ Parser（结构感知解析）
  │  按文档原生结构切块，保留章节层级、表格、标题
  │  输出：list[ParsedChunk]
  │
  ▼ Splitter（token 约束切分）
  │  把超长 chunk 切到模型 token 上限内，不跨语义边界
  │  输出：list[ParsedChunk]
  │
  ▼ Embedder（向量化）
  │  调用 OpenAI text-embedding-3-small，error chunk 自动跳过
  │  输出：list[EmbeddedChunk]
  │
  ▼ Store（持久化）
     upsert 进 Milvus，按 knowledge_base_id 分区，chunk_id 幂等写入
     输出：写入条数（int）
```

四层由 `IngestionPipeline` 串联，支持批量文件输入、文件级错误隔离与指数退避重试。

**设计原则**

- **文件级错误隔离**：单文件失败不中断其余文件，失败信息收集在 `IngestionResult.errors`
- **文件级重试**：可配置重试次数（默认 1 次），指数退避，适用于网络抖动、Milvus 短暂不可用等瞬时错误
- **四层均可注入**：`embedder` / `store` / `splitter` 均通过构造函数注入，便于测试替换和策略切换
- **PDF 策略可配置**：`fast` / `smart` / `hi_res` 三档，通过 `PipelineConfig.pdf_strategy` 控制
- **内部批处理透明**：大文件产生数千个 chunk 时无需在 Pipeline 层手动分批——BaseEmbedder 已按 `batch_size` 条数分批调用 API，MilvusStore 已按 `batch_size` 条数分批 upsert，Pipeline 只负责串联逻辑

**待实现（Step 3）**

| 功能 | 说明 |
|---|---|
| Celery 异步任务队列 | 当前为同步串行，单文件阻塞整个 pipeline |
| 数值进度追踪 | DB 增加摄入任务表，记录文件级状态（PENDING / PROCESSING / DONE / FAILED）+ 0~1 进度，前端轮询 |
| 并发控制 | asyncio semaphore 或 ThreadPoolExecutor，限制同时处理文件数，防止争抢连接 |
| 死信队列与自动重试上报 | 重试耗尽的文件写入死信队列，统一上报告警 |

---

## 模块结构

```
ingestion/
├── pipeline.py          # 摄入 Pipeline：四层串联入口
├── parsers/             # 文档解析层
│   ├── base.py          # ParsedChunk、BaseParser、Parser 注册表
│   ├── pdf_parser.py    # PDF（fast / smart / hi_res 三档）
│   ├── word_parser.py   # Word（.docx）
│   ├── excel_parser.py  # Excel（.xlsx）
│   ├── html_parser.py   # HTML
│   ├── markdown_parser.py
│   ├── txt_parser.py
│   ├── csv_parser.py
│   └── fallback_parser.py  # 未知格式兜底
├── splitter/            # 文本切分层
│   ├── base.py          # BaseSplitter、content_type 路由
│   ├── recursive.py     # RecursiveSplitter（默认）
│   ├── semantic.py      # SemanticSplitter（话题边界切分）
│   └── parent_child.py  # ParentChildSplitter（两阶段检索）
├── embedder/            # 向量化层
│   ├── base.py          # EmbeddedChunk、BaseEmbedder
│   └── openai_embedder.py  # OpenAIEmbedder
└── store/               # 向量存储层
    └── milvus_store.py  # MilvusStore
```

---

## 快速使用

```python
from ingestion.pipeline import IngestionPipeline, PipelineConfig
from ingestion.embedder.openai_embedder import OpenAIEmbedder, OpenAIEmbedderConfig
from ingestion.store.milvus_store import MilvusStore, MilvusStoreConfig

pipeline = IngestionPipeline(
    embedder=OpenAIEmbedder(OpenAIEmbedderConfig()),
    store=MilvusStore(MilvusStoreConfig(uri="http://localhost:19530")),
    config=PipelineConfig(
        pdf_strategy="smart",   # "fast" | "smart" | "hi_res"
        max_retries=1,          # 文件级失败最多重试次数（不含首次）
        retry_base_delay=2.0,   # 指数退避基础秒数：第1次等 2s，第2次等 4s…
    ),
)

result = pipeline.run(
    files=["report.pdf", "manual.docx", "data.xlsx"],
    knowledge_base_id="kb_finance_001",
)

print(f"成功 {result.succeeded}/{result.total_files}，写入 {result.total_chunks_written} 个 chunk")
if result.errors:
    for filename, err in result.errors.items():
        print(f"  ✗ {filename}: {err}")
```

---

## Pipeline 配置（PipelineConfig）

| 字段 | 默认值 | 说明 |
|---|---|---|
| `pdf_strategy` | `"smart"` | PDF 解析模式：`fast`（本地 fitz）/ `smart`（按页三重降级）/ `hi_res`（全页远端 OCR） |
| `max_retries` | `1` | 单文件失败后最多重试次数（不含首次尝试） |
| `retry_base_delay` | `2.0` | 指数退避基础秒数，第 k 次重试前等待 `base * 2^(k-1)` 秒，最后一次失败不等待 |

重试适用于网络抖动、Milvus 短暂不可用、OpenAI 限速等瞬时错误；设 `max_retries=0` 可关闭重试。

---

## 层间契约

| 层 | 输入 | 输出 | 关键约束 |
|---|---|---|---|
| Parser | 文件路径 / bytes | `list[ParsedChunk]` | 每个 chunk 必须携带 `source_file`、`content_type`、`section_path`（`__post_init__` 校验） |
| Splitter | `list[ParsedChunk]` | `list[ParsedChunk]` | `title` 恒透传；`table` ≤ 2048 token 透传；`error` 透传 |
| Embedder | `list[ParsedChunk]` | `list[EmbeddedChunk]` | `error` chunk 短路，`skipped=True`，不调用 API |
| Store | `list[EmbeddedChunk]` | `int`（写入条数） | `skipped=True` 的 chunk 不写入 Milvus |

---

## 各层详细文档

- [Parser 层](parsers/README.md) — 多格式解析策略、PDF 三档模式、smart 按页三重降级路由
- [Splitter 层](splitter/README.md) — Recursive / Semantic / ParentChild 三种策略
- [Embedder 层](embedder/README.md) — 两级批处理、token 截断、指数退避重试
- [Store 层](store/README.md) — Milvus Schema、HNSW 索引、幂等写入、分页删除
