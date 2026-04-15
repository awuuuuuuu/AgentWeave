# 向量存储层（Store）

Store 层的职责：**把 Embedder 输出的 `EmbeddedChunk` 列表持久化到 Milvus 向量数据库**，供后续混合检索使用。

---

## 架构

```
embedded_chunks (list[EmbeddedChunk])
  └─ MilvusStore.upsert()
        ├─ error chunk（skipped=True）→ 自动跳过，不写入
        ├─ _to_row()：EmbeddedChunk → Milvus 行数据
        │     ├─ 字节安全截断（防中文超出 VARCHAR 字节限制）
        │     └─ extra_meta JSON 安全序列化（防 datetime/UUID 崩溃）
        └─ 按 batch_size 分批 upsert（默认 200 条/批）
```

---

## Schema 设计

| 字段 | 类型 | 说明 |
|------|------|------|
| `chunk_id` | VARCHAR(64)，主键 | SHA256(kb_id + source_file + text) 前 32 位，确定性生成 |
| `knowledge_base_id` | VARCHAR(512)，**Partition Key** | 按知识库分区，隔离多租户数据 |
| `source_file` | VARCHAR(65535)，INVERTED 索引 | 原始文件名，高频过滤字段 |
| `content_type` | VARCHAR(128)，INVERTED 索引 | chunk 类型，高频过滤字段 |
| `section_path` | VARCHAR(2048) | 章节路径，用于引用展示 |
| `embed_model` | VARCHAR(128) | 实际调用的 embedding 模型名 |
| `text` | VARCHAR(65535) | 原始 chunk 文本 |
| `extra_meta` | JSON | source_file/content_type/section_path 以外的其余 metadata |
| `vector` | FLOAT_VECTOR(1536) | 稠密向量，HNSW 索引 |

**设计决策**：`source_file`、`content_type` 等高频过滤字段提升为顶层 VARCHAR，建立 INVERTED 标量索引；其余非结构化 metadata 存入 `extra_meta` JSON。这一设计比 Dify 的"全部打包进 JSON"方案过滤性能高一个量级，同时比 RAGflow 的"每字段独立建索引"方案维护成本低。

---

## 向量索引

```
类型：HNSW
度量：COSINE（Milvus 内部归一化，无需外部保证向量 L2 范数 = 1）
参数：M=16，efConstruction=128
```

选 COSINE 而非 IP（内积）的原因：IP 要求向量预先归一化，OpenAI 的 text-embedding-3 系列默认归一化，但如果未来切换到本地开源模型，未归一化向量会导致相似度得分异常。COSINE 在 Milvus 底层自动处理，无需外部约束。

---

## 使用示例

```python
from ingestion.store import MilvusStore, MilvusStoreConfig

store = MilvusStore(
    config=MilvusStoreConfig(
        uri="http://localhost:19530",
        collection_name="ragent_chunks",
        vector_dim=1536,
    )
)

# 写入
count = store.upsert(embedded_chunks, knowledge_base_id="kb_001")
print(f"写入 {count} 条")

# 删除某文件的全部 chunk（文件重新解析时使用）
store.delete_by_source(knowledge_base_id="kb_001", source_file="report.pdf")
```

---

## 关键实现细节

### 1. 确定性 chunk_id（幂等写入）

```python
SHA256(knowledge_base_id + "\x00" + source_file + "\x00" + text)[:32]
```

相同内容重复调用 `upsert()` 时生成相同 `chunk_id`，Milvus 的 upsert 语义自动覆盖旧数据，不产生重复向量。

### 2. 字节安全截断

Milvus `VARCHAR(max_length)` 的限制单位是**字节**，而 Python `str[:n]` 按字符截断。中文字符 UTF-8 编码占 3 字节，直接字符截断会导致 `String length exceeds max length` 错误。通过 `encode("utf-8")[:max_bytes].decode(errors="ignore")` 按字节截断，`errors="ignore"` 丢弃末尾不完整的多字节序列，防止 `UnicodeDecodeError`。

### 3. 分页删除（防 OOM）

`delete_by_source()` 每次最多拉取 1000 条 `chunk_id`，删除后继续拉取下一页，直到为空。避免大文件（数万 chunk）时一次性把所有 ID 拉入内存导致 OOM 或 gRPC 超时。

> **注意**：Milvus 的 Partition Key 字段不支持直接作为 `delete(filter=...)` 的过滤条件，因此无法用单条 delete 表达式删除，必须先 query 取 ID 再 delete by ids。

---

## 测试

```
backend/tests/store/
├── conftest.py                         # make_embedded / make_mock_client 共用工具
├── test_milvus_store.py                # 单元测试 27 条（全 mock，无需真实 Milvus）
└── test_milvus_store_integration.py    # 集成测试 6 条（需真实 Milvus）
```

**运行单元测试**：
```bash
uv run pytest backend/tests/store/test_milvus_store.py -v
```

**运行集成测试**（需 `.env` 中配置 `MILVUS_URI`）：
```bash
uv run pytest backend/tests/store/test_milvus_store_integration.py -v -s
```

覆盖项：chunk_id 确定性与唯一性、upsert 基础行为、error chunk 跳过、批量分批、幂等写入、分页删除、Collection 初始化逻辑。
