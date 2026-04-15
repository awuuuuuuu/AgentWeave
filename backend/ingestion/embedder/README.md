# 文本向量化（Embedder）

Embedder 层的职责：**把 Splitter 输出的 `ParsedChunk` 列表转化为带向量的 `EmbeddedChunk` 列表**，供后续写入 Milvus。

---

## 架构

```
chunks (list[ParsedChunk])
  └─ BaseEmbedder.embed()
        ├─ error chunk → 短路，输出空向量（skipped=True），不调用 API
        ├─ 按 batch_size 分批（count 级）
        └─ _embed_texts(batch)   ← 子类实现
              └─ OpenAIEmbedder
                    ├─ 单条截断：超 8191 token → 截断，不报错
                    ├─ token 感知二次分批：单批累计 token ≤ 300K
                    └─ 指数退避重试：429 / 5xx / 网络异常自动重试
```

---

## 数据结构

### `EmbeddedChunk`

```python
@dataclass
class EmbeddedChunk:
    chunk: ParsedChunk        # 原始 chunk，保留全部 metadata 和 text
    embedding: list[float]    # 向量（error chunk 为 []）
    embed_model: str          # 模型名（error chunk 为 ""）

    @property
    def skipped(self) -> bool:  # embedding == [] 时为 True
        ...
```

---

## 两层类结构

### `BaseEmbedder`（[base.py](base.py)）

统一处理所有 Embedder 共有的逻辑，子类只需实现单批文本的向量化：

| 职责 | 说明 |
|------|------|
| error chunk 短路 | `content_type == "error"` 的 chunk 输出空向量，不调用 API |
| count 级分批 | 按 `batch_size` 把文本列表切成多批，逐批调用 `_embed_texts` |
| 顺序保证 | 输出顺序严格与输入一致（含 error chunk 的占位） |

### `OpenAIEmbedder`（[openai_embedder.py](openai_embedder.py)）

| 特性 | 说明 |
|------|------|
| token 感知二次分批 | 单批累计 token 超过 300K 时自动拆批（防止极端 table chunk 场景） |
| 单条截断 | 超过模型上限（8191 token）时截断，记 warning 日志，不报错 |
| 指数退避重试 | `429 / 5xx / APIConnectionError` 自动重试，非 429 的 4xx 直接抛出 |

```python
@dataclass
class OpenAIEmbedderConfig:
    model: str = "text-embedding-3-small"
    batch_size: int = 512          # 单批最多条数（OpenAI 上限 2048，512 延迟更低）
    max_retries: int = 3           # 遇到 429 / 5xx 的最大重试次数
    retry_base_delay: float = 1.0  # 指数退避基础等待秒数
    encoding_name: str = "cl100k_base"
```

---

## 使用示例

```python
from openai import OpenAI
from ingestion.embedder import OpenAIEmbedder, OpenAIEmbedderConfig

embedder = OpenAIEmbedder(
    config=OpenAIEmbedderConfig(model="text-embedding-3-small"),
    client=OpenAI(),  # 不传则自动创建
)

embedded_chunks = embedder.embed(chunks)  # list[ParsedChunk] → list[EmbeddedChunk]

for ec in embedded_chunks:
    if ec.skipped:
        continue  # error chunk，跳过
    print(ec.embedding[:5], ec.embed_model)
```

---

## FastAPI 集成注意事项

`_embed_batch_with_retry` 内部使用 `time.sleep()` 同步阻塞等待。在 FastAPI 中：

```python
# 正确：普通 def，FastAPI 自动放入线程池
@app.post("/ingest")
def ingest(file: UploadFile):
    return embedder.embed(chunks)

# 错误：async def 会阻塞主事件循环
@app.post("/ingest")
async def ingest(file: UploadFile):
    return embedder.embed(chunks)  # ❌ time.sleep 阻塞 event loop
```

后续若需全异步，可替换为 `AsyncOpenAI` + `await asyncio.sleep()`。

---

## 缓存扩展（TODO Step 3）

Step 3 引入 Redis 后，在外部用 `CachedEmbedder` 包装，无需修改 `OpenAIEmbedder`：

```python
# 接口完全一样，OpenAIEmbedder 代码零改动
embedder = CachedEmbedder(OpenAIEmbedder(config), redis_client)
results = embedder.embed(chunks)
```

`CachedEmbedder` 按文本哈希查 Redis：命中直接返回向量，未命中调用内层 embedder 并写入缓存（TTL 1天）。对重复文档重新入库时节省 Token 费用。

---

## 测试

```
backend/tests/embedder/
├── conftest.py                          # make_chunk / make_mock_client 共用工具
├── test_openai_embedder.py              # 单元测试 20 条（全 mock，无需 API Key）
└── test_openai_embedder_integration.py  # 集成测试 5 条（需真实 API Key）
```

**运行单元测试**：
```bash
uv run pytest backend/tests/embedder/test_openai_embedder.py -v
```

**运行集成测试**（需 `.env` 中配置 `OPENAI_API_KEY`）：
```bash
uv run pytest backend/tests/embedder/test_openai_embedder_integration.py -v -s
```

覆盖项：基础行为、error chunk 短路、count 批处理、token 截断、429/5xx/网络异常重试、最后一次不 sleep、4xx 直接抛出、重试耗尽抛 RuntimeError。
