# RAGent

企业级 Agent + RAG 系统。

## 开发进度

**Step 1 — 文档摄入**

| 子模块 | 状态 |
|--------|------|
| Parsers（PDF / Word / HTML / Markdown / TXT / Fallback） | ✅ 完成 |
| Splitter（按语义边界切块，控制 chunk 大小与重叠） | ✅ 完成 |
| Embedding（文本 → 向量，OpenAI text-embedding-3-small） | ✅ 完成 |
| Milvus 写入（chunk + 向量 + metadata 入库） | ✅ 完成 |
| 摄入 Pipeline（串联以上四步，支持批量文件处理） | ✅ 完成 |

**后续 Steps**

| Step | 模块 | 状态 |
|------|------|------|
| 2 | 混合检索 + 重排序（BM25 + 向量 + Reranker） | 🔜 |
| 3 | RAG Chain + API + 前端基础 | 🔜 |
| 4 | 工具体系 | 🔜 |
| 5 | 记忆模块 | 🔜 |
| 6 | LangGraph Agent 编排 | 🔜 |
| 7 | 会话管理 + 多模型路由 | 🔜 |
| 8 | 前端完整界面 | 🔜 |
| 9 | 可观测性 + 评估（LangSmith） | 🔜 |

## 技术债 / TODO

| 优先级 | 所属模块 | 描述 | 计划在哪步解决 |
|--------|---------|------|--------------|
| 中 | `ParentChildSplitter` | `parent_text` 直接存入子块 metadata，导致每个父块被复制 N 次写入向量库。重构方案：为父块生成 UUID `parent_id`，将 `{parent_id: parent_text}` 存入 Redis，子块只存 `parent_id`，检索时再查 KV | Step 3（Redis 引入后） |
| 低 | `OpenAIEmbedder` | 无向量缓存，相同文本重复入库时仍调用 OpenAI API，产生重复 Token 费用。改造方案：用 `CachedEmbedder` 包装，按文本哈希查 Redis，命中直接返回向量 | Step 3（Redis 引入后） |
| 低 | `OpenAIEmbedder` | `_embed_batch_with_retry` 使用 `time.sleep()` 同步阻塞。FastAPI 路由须用普通 `def`（非 `async def`）避免阻塞事件循环。全异步改造需替换为 `AsyncOpenAI` + `await asyncio.sleep()` | Step 3（API 层引入后） |
| 高 | `IngestionPipeline` | 当前为同步串行处理，单文件阻塞整个 pipeline。改造方案：引入 Celery 异步任务队列，每个文件作为独立 Celery task，支持多 worker 并发摄入 | Step 3（API 层引入后） |
| 中 | `IngestionPipeline` | 无进度追踪，无法从外部感知"已处理 N/M 个文件"。改造方案：在 DB 增加摄入任务表，记录文件级状态（PENDING / PROCESSING / DONE / FAILED）和 0~1 数值进度，前端轮询 | Step 3（API 层引入后） |
| 低 | `IngestionPipeline` | 无并发控制，多用户同时触发摄入时会争抢 Embedder / Milvus 连接。改造方案：asyncio semaphore 或 ThreadPoolExecutor 限制同时处理文件数 | Step 3（API 层引入后） |

---

## 模块文档

- [文档摄入系统总览](backend/ingestion/README.md) — 数据流、模块结构、层间契约
  - [Parser 层](backend/ingestion/parsers/README.md) — PDF / Word / HTML / Markdown / TXT / Fallback
  - [Splitter 层](backend/ingestion/splitter/README.md) — Recursive / Semantic / ParentChild
  - [Embedder 层](backend/ingestion/embedder/README.md) — OpenAIEmbedder，token 截断、批处理、指数退避重试
  - [Store 层](backend/ingestion/store/README.md) — MilvusStore，HNSW 索引、幂等写入、多租户分区

## 技术亮点

### 1. 分层架构：结构感知 vs. token 约束

文档处理的核心分层决策：**Parser 层负责结构感知，Splitter 层只做 token 约束**，两层职责边界清晰。

Parser 层为每种格式单独实现结构提取逻辑：
- **Word / HTML / Markdown**：递归遍历标题层级（`<h1>`、`## `、`Heading 1` 样式），每个标题节点对应一个 chunk，`section_path` 动态拼接为 `"第三章 > 3.2节"` 的层级路径
- **PDF**：提供三档模式——`fast`（PyMuPDF 按页提取，速度优先）、`smart`（逐页智能路由，见下方）、`hi_res`（全页走远端 Unstructured API，最高精度）
- **Excel**：按 Sheet 独立切块，表头单独作为 `content_type=title` 的 chunk，每行或分块作为 `content_type=table`

Splitter 层不感知文档结构，之所以能保证不跨章节/页面边界，不是因为 Splitter 知道结构，而是 Parser 输出的每个 chunk 已在一个语义单元内。这一分层使两层可以完全独立演进。

### 2. PDF smart 模式：按页三重降级路由

`smart` 模式解决 PDF 解析的核心矛盾：**本地提取速度快但无法处理图表/扫描页，远端 OCR 精度高但费时且收费**。

`smart` 模式逐页分析，只对真正需要的页面调用远端 Unstructured API：

```
第 1 页：纯文字 → fitz 本地提取（< 1ms，零成本）
第 3 页：含图表 → 单页截图 → 远端 hi_res OCR
第 5 页：空白/扫描 → 单页截图 → 远端 hi_res OCR
```

触发远端调用的三重条件（任一满足）：

| 条件 | 判断逻辑 | 典型场景 |
|------|---------|---------|
| `page_has_image` | 光栅图面积 > 页面 10%（排除 logo 等小图） | 含图表的研报、PPT 转 PDF |
| `not page_text` | 全页无可提取文字 | 扫描件、图片型 PDF |
| `_is_garbled(text)` | 不可打印字符比例 > 25% | 字体层编码损坏、CID 字体乱码 |

远端调用失败时自动退回本地 fitz 结果，不丢页、不中断 pipeline。相比 `fast`（全本地）和 `hi_res`（全远端），`smart` 在成本和精度之间取得最优平衡。

### 3. content_type 驱动的全链路路由

`content_type` 字段由 Parser 层写入，贯穿 Splitter → Embedder → Store 整条链路，每层按此字段做差异化处理：

| content_type | Parser 产出 | Splitter 策略 | Embedder 策略 | Store 策略 |
|---|---|---|---|---|
| `title` | 章节标题 | 恒定透传，不切分（标题极短，切分无意义） | 正常 embed | 正常写入 |
| `table` | 结构化表格 | ≤ 2048 token 透传；超限强制切分（防 TokenLimitExceeded） | 正常 embed | 正常写入 |
| `text` | 正文段落 | 超出 chunk_size 时切分，保留 overlap | 正常 embed | 正常写入 |
| `error` | 解析失败 | 透传 | 短路，返回空向量，不调用 API | 跳过写入 |

路由逻辑集中在 `BaseSplitter.split()` 中，三种切分策略（Recursive / Semantic / ParentChild）继承后无需各自重复实现。`error` chunk 的隔离语义从 Parser 一路传递到 Store，无需各层单独判断"这个文件坏了怎么办"。

### 4. Error Chunk 错误隔离：失败不中断

传统做法在解析失败时抛出异常，导致批量任务中一个损坏文件就中断整个 pipeline。RAGent 的设计是：解析失败时返回 `content_type="error"` 的占位 chunk，携带 `error` 字段记录原因，之后的层自动跳过它。

- Parser 层：捕获所有异常，返回 error chunk，不向上抛出
- Splitter 层：error chunk 直接透传（`content_type` 路由）
- Embedder 层：识别 `skipped=True`，不调用 OpenAI API
- Store 层：识别 `skipped=True`，不写入 Milvus

批量处理 1000 个文件时，即使其中 5 个损坏，其余 995 个正常完成，损坏文件的 error chunk 可在 pipeline 末尾统一收集上报。

### 5. ParsedChunk 契约式 metadata 校验

所有 Parser 输出的 chunk 在 `ParsedChunk.__post_init__` 中强制校验三个必填字段（`source_file`、`content_type`、`section_path`），缺失字段在对象构造时立即报错，而非在下游运行时才发现 `KeyError`。

这将"数据完整性保障"从运行时下沉到对象构造阶段：下游 Splitter 和 Embedder 无需任何 `if metadata.get("source_file") is None` 防御性判断，向量库入库时字段也始终完整。这是一种**契约式编程（Design by Contract）**思想的体现。

### 6. RecursiveSplitter 的分隔符保留与 overlap 自适应

RecursiveSplitter 在实现时处理了两个容易被忽略的边界问题：

**分隔符保留**：按 `\n\n` 切分后，若直接丢弃分隔符，相邻段落文本会粘连，LLM 和向量模型失去段落边界感知。通过 `[p + sep for p in parts[:-1]] + [parts[-1]]` 将分隔符保留在片段末尾，而非丢弃。

**overlap 自适应裁剪**：当 `chunk_overlap ≥ chunk_size` 时，overlap 片段本身超过限制，会导致合并后的 chunk 越来越大，产生死循环。通过 `overlap_size = min(chunk_overlap, max(0, chunk_size - len(new)))` 自适应裁剪，保证 overlap 部分加上新片段始终不超过 chunk_size。

### 7. ParentChild 两阶段检索架构

参考 Dify Parent-Child Retrieval 设计，解决"检索精度"与"上下文质量"之间的内在矛盾：chunk 越小，向量检索越精准；chunk 越大，LLM 获得的上下文越完整。两者无法兼得。

```
文档
  └─ 父块（512 token）→ 喂给 LLM，提供完整上下文
        └─ 子块（128 token）→ 向量化入库，精准定位

查询 → 向量检索子块（精准匹配） → 取出对应 parent_text → 送给 LLM
```

子块只入向量库，父块文本以 `parent_text` 字段存在子块的 metadata 中，检索阶段命中子块后直接取出父块上下文。兼顾精度和质量，无需在向量库中存两份独立索引。

### 8. Embedder 两级批处理设计

面对"批量文档 + 超大 table chunk"的场景，单层批处理无法同时满足"条数"和"token 总量"两个限制：

- **BaseEmbedder**（count 级）：按 `batch_size` 条数分批，保证单次 API 调用不超过 OpenAI 的条数上限
- **OpenAIEmbedder**（token 级）：在每批内再按累计 token 数（≤ 300K/次）二次分批，防止超大 table chunk 撑爆单次请求

同时，单条文本超过模型 token 上限（8191）时自动截断而非报错，整个批次仍正常完成。

### 9. 精细化重试策略

重试逻辑区分四种情况，避免过度重试或漏重试：

| 异常类型 | 处理方式 |
|---|---|
| `RateLimitError`（HTTP 429） | 指数退避重试 |
| `APIStatusError`（HTTP 5xx） | 指数退避重试 |
| `APIConnectionError`（网络异常） | 指数退避重试 |
| `APIStatusError`（HTTP 4xx，非 429） | 直接抛出，不重试 |

关键细节：`RateLimitError` 在 OpenAI SDK 中是 `APIStatusError` 的子类，需在 4xx 直接抛出的判断中显式排除，否则 429 会被误判为客户端错误而跳过重试。最后一次重试失败后直接抛出，不再执行无意义的 sleep 等待。

### 10. Milvus Schema：顶层字段 + Partition Key 多租户隔离

不同于 Dify 把所有 metadata 打包进单个 JSON 字段（无法建标量索引）、也不同于 RAGflow 给每个字段单独建索引（维护成本高），RAGent 采用中间路线：高频过滤字段（`source_file`、`content_type`）提升为顶层 VARCHAR 字段并建 INVERTED 索引，其余非结构化 metadata 存入 `extra_meta` JSON。按 `knowledge_base_id` 作为 Partition Key 分区，天然支持多知识库数据隔离，同时规避 Dify 的 Collection-per-dataset 方案在 Milvus 10K collection 上限的扩展瓶颈。

---

## 技术栈

- **LLM**：claude-sonnet-4-6 / claude-haiku-4-5
- **Embedding**：text-embedding-3-small（OpenAI）
- **向量库**：Milvus（Docker 本地）
- **关系库**：PostgreSQL（Supabase）
- **缓存**：Redis（Docker 本地）
- **前端**：Next.js 14 + shadcn/ui + Tailwind
- **运行时**：Python 3.11+、uv
- **测试**：pytest 8+
