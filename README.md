# RAGent

企业级 Agent + RAG 系统。

## 开发进度

**Step 1 — 文档摄入**

| 子模块 | 状态 |
|--------|------|
| Parsers（PDF / Word / HTML / Markdown / TXT / Fallback） | ✅ 完成 |
| Splitter（按语义边界切块，控制 chunk 大小与重叠） | ✅ 完成 |
| Embedding（文本 → 向量，OpenAI text-embedding-3-small） | ✅ 完成 |
| Milvus 写入（chunk + 向量 + metadata 入库） | 🔜 |
| 摄入 Pipeline（串联以上四步，支持批量文件处理） | 🔜 |

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
| 中 | `ParentChildSplitter` | `parent_text` 直接存入子块 metadata，导致每个父块被复制 N 次写入向量库。重构方案：为父块生成 UUID `parent_id`，将 `{parent_id: parent_text}` 存入 Redis，子块只存 `parent_id`，检索时再查 KV | Step 3（Milvus 入库） |
| 低 | `OpenAIEmbedder` | 无向量缓存，相同文本重复入库时仍调用 OpenAI API，产生重复 Token 费用。改造方案：用 `CachedEmbedder` 包装，按文本哈希查 Redis，命中直接返回向量 | Step 3（Redis 引入后） |

---

## 模块文档

- [文档解析器](backend/ingestion/parsers/README.md) — PDF / Word / HTML / Markdown / TXT / Fallback
- [文本切分器](backend/ingestion/splitter/README.md) — Recursive / Semantic / ParentChild
- [向量化层](backend/ingestion/embedder/README.md) — OpenAIEmbedder，token 截断、批处理、指数退避重试

## 技术亮点

### 1. 分层架构：结构感知 vs. token 约束

文档处理分为两个独立层，各司其职，互不越界：

- **Parser 层**完成所有结构感知工作——Word/HTML/Markdown 按标题层级递归遍历，动态构建 `section_path`；PDF 提供 fast/smart/hi_res 三档模式；Excel 按 Sheet 切块。Parser 输出的每个 chunk 已在一个语义单元内（同章节、同页、同 Sheet）。
- **Splitter 层**只做 token 长度约束，完全不需要理解文档结构。它能保证不跨语义边界，不是因为 Splitter 知道结构，而是 Parser 已经保证了边界。

这一分层使两层可以独立演进：替换 PDF 解析策略不影响 Splitter，切换切分算法也不影响任何 Parser。

### 2. content_type 驱动的差异化处理

`content_type` 字段贯穿 Parser → Splitter → Embedder 整条链路，每层按此字段做路由：

| content_type | Parser 产出 | Splitter 策略 | Embedder 策略 |
|---|---|---|---|
| `title` | 章节标题 | 恒定透传，不切分 | 正常 embed |
| `table` | 结构化表格 | ≤ 2048 token 透传；超限强制切分（防 TokenLimitExceeded） | 正常 embed |
| `text` | 正文段落 | 超出 chunk_size 时切分，保留 overlap | 正常 embed |
| `error` | 解析失败 | 透传 | 短路，输出空向量，不调用 API |

`content_type` 路由逻辑集中在 `BaseSplitter.split()` 中，三种子策略（Recursive / Semantic / ParentChild）继承后无需重复实现。

### 3. Error Chunk 错误隔离模式

解析失败时不抛出异常，而是返回 `content_type="error"` 的占位 chunk，携带 `error` 字段记录原因。这保证了批量处理时单个文件损坏不中断整个 pipeline。Embedder 识别到 error chunk 后直接短路，不调用 OpenAI API，最终写入向量库时可选择过滤或记录。

### 4. ParsedChunk 契约式 metadata 校验

所有 Parser 输出的 chunk 在 `ParsedChunk.__post_init__` 中强制校验三个必填字段（`source_file`、`content_type`、`section_path`），缺失字段在对象构造时立即报错。这将"数据完整性保障"从运行时下沉到对象构造阶段，下游 Splitter 和 Embedder 无需做任何 None 防御，向量库入库时字段也始终完整。

### 5. ParentChild 两阶段检索架构

参考 Dify Parent-Child Retrieval 设计，解决"检索精度"与"上下文质量"之间的矛盾：

```
查询 → 向量检索子块（128 token，精准定位）
     → 回溯 parent_text（512 token，完整上下文喂给 LLM）
```

子块粒度细，向量相似度更精准；父块粒度大，LLM 获得足够上下文。两者在检索阶段动态关联，无需在向量库中存两份索引。

### 6. Embedder 两级批处理设计

面对"批量文档 + 超大 table chunk"的场景，单层批处理无法同时满足"条数"和"token 总量"两个限制：

- **BaseEmbedder**（count 级）：按 `batch_size` 条数分批，保证单次 API 调用不超过 OpenAI 的条数上限
- **OpenAIEmbedder**（token 级）：在每批内再按累计 token 数（≤ 300K/次）二次分批，防止超大 table chunk 撑爆单次请求

同时，单条文本超过模型 token 上限（8191）时自动截断而非报错，整个批次仍正常完成。

### 7. 精细化重试策略

重试逻辑区分四种情况，避免过度重试或漏重试：

| 异常类型 | 处理方式 |
|---|---|
| `RateLimitError`（HTTP 429） | 指数退避重试 |
| `APIStatusError`（HTTP 5xx） | 指数退避重试 |
| `APIConnectionError`（网络异常） | 指数退避重试 |
| `APIStatusError`（HTTP 4xx，非 429） | 直接抛出，不重试 |

关键细节：`RateLimitError` 在 OpenAI SDK 中是 `APIStatusError` 的子类，需在 4xx 直接抛出的判断中显式排除，否则 429 会被误判为客户端错误而跳过重试。最后一次重试失败后直接抛出，不再执行无意义的 sleep 等待。

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
