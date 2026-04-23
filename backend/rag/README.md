# RAG Chain（基础 RAG 链路）

将用户查询通过 HybridRetriever → ContextBuilder → LLM，生成带引用溯源的答案。

---

## 数据流

```
用户查询（str）
  │
  ▼ retrieve 节点
  │  HybridRetriever（BM25 + Dense）→ Reranker（可选）
  │  输出：list[RetrievedChunk]
  │
  ▼ build_context 节点
  │  ContextBuilder：分配 [1][2]... 编号，token 预算截断
  │  输出：context（XML 字符串）+ citations（CitationMeta 列表）
  │
  ▼ generate 节点
  │  ChatOpenAI（gpt-4o / gpt-4o-mini）
  │  Prompt = System + <context>...</context> + Question
  │  输出：answer（含 [N] 引用标注）+ cited_refs（已用编号列表）
  │
  ▼ _format_result
     过滤 cited_refs，返回 {answer, citations, chunks}
```

---

## 模块结构

```
rag/
├── state.py           # GraphState TypedDict + CitationMeta dataclass
├── context_builder.py # chunk → XML 上下文 + token 预算管理
├── nodes.py           # LangGraph 节点（retrieve / build_context / generate）
├── chain.py           # StateGraph 组装 + RAGChain 类
└── settings.py        # RAGChainSettings（从 .env 读取）

prompts/
└── rag_answer.py      # System/User prompt 模板 + NO_CONTEXT 降级字符串
```

---

## 快速使用

```python
from rag.chain import RAGChain

chain = RAGChain.from_settings()

# 同步调用
result = chain.invoke("什么是 RAG？", knowledge_base_id="kb1", top_k=5)
print(result["answer"])
for c in result["citations"]:
    print(f"  [{c['ref']}] {c['source_file']} | {c['section_path']}")

# 流式调用（SSE 场景）
import asyncio
async def stream():
    async for kind, data in chain.astream_full("RAG 和向量数据库的关系", "kb1"):
        if kind == "token":
            print(data, end="", flush=True)
        elif kind == "result":
            print("\n\n引用：", data["citations"])

asyncio.run(stream())
```

---

## 层间数据契约

### `GraphState`

| 字段 | 类型 | 来源节点 | 说明 |
|------|------|---------|------|
| `query` | `str` | 输入 | 用户原始查询（已 sanitize） |
| `knowledge_base_id` | `str` | 输入 | 知识库 ID |
| `top_k` | `int` | 输入 | 最终返回条数 |
| `chunks` | `list[RetrievedChunk]` | retrieve | 精排后的候选集 |
| `context` | `str` | build_context | XML 格式上下文 |
| `citations` | `list[CitationMeta]` | build_context | 引用元数据（含编号） |
| `has_context` | `bool` | build_context | False 时触发无结果降级 |
| `answer` | `str` | generate | LLM 生成答案（含 [N] 标注） |
| `cited_refs` | `list[int]` | generate | 答案中实际出现的引用编号 |

### `CitationMeta`

```python
@dataclass
class CitationMeta:
    ref: int         # [1][2]... 答案中的引用编号
    chunk_id: str    # SHA256 主键，对应 MilvusStore
    source_file: str
    section_path: str
```

---

## 关键设计决策

### 1. 引用格式：`[N]` + regex 提取（借鉴 RAGflow）

RAGflow 使用 `[ID:N]` 并做 regex + embedding fallback 修复。RAGent 简化为 `[N]`，理由：
- LLMgai在 prompt 明确约束下格式稳定，不需要修复机制
- `[N]` 比 `[ID:N]` 对用户更直观
- Fallback 嵌入相似度修复留作 Step 9 可观测性改进项

### 2. 上下文格式：XML 标签（借鉴 Dify）

```
<context>
[1] 来源：file.pdf | 第1节
内容...

[2] 来源：...
</context>
```

Dify 用 `<context>` XML 隔离注入内容与指令。RAGent 在此基础上增加 `[N]` 编号，方便 LLM 引用。

### 3. Token 预算：97% 截断于 chunk 迭代阶段（借鉴 RAGflow）

RAGflow 在 `kb_prompt()` 中按序迭代 chunk，累计 token 超 97% 预算时停止。RAGent 采用同样策略，在 `ContextBuilder.build()` 内截断，不做事后裁剪，保证 prompt 不超模型 context limit。

### 4. 无结果降级：显式 `NO_CONTEXT` 字符串（借鉴 RAGflow `empty_response`）

`has_context=False` 时，`<context>` 块替换为 `NO_CONTEXT` 哨兵文本，通知 LLM 当前知识库无相关信息，仍由 LLM 生成回复（而非直接返回固定字符串），便于 LLM 根据通用知识作答时提示用户。

### 5. 单次 graph 执行同时流式输出 token 和引用

`astream_full()` 使用 `astream_events(version="v2")`，在同一次 graph 执行中：
- 捕获 `on_chat_model_stream` → 产出 `("token", str)`
- 捕获 `on_chain_end`（含完整 state）→ 产出 `("result", dict)`

避免流式路径调用两次 LLM（一次 stream 一次取引用），节省 API 费用。

---

## 配置

```python
# backend/.env
RAG_LLM_MODEL=gpt-4o
RAG_MAX_CONTEXT_TOKENS=6000
RAG_OUTPUT_RESERVE_TOKENS=1000
RAG_USE_RERANKER=true
RAG_RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
RAG_CANDIDATE_MULTIPLIER=3
```

---

## TODO

| 优先级 | 描述 | 计划 Step |
|--------|------|-----------|
| 中 | Citation fallback：LLM 零引用时用嵌入相似度自动插入 | Step 9 |
| 中 | Few-shot 示例：在 prompt 中加入引用格式示例，提升低质量模型的格式遵从率 | Step 9 |
| 低 | RAGChain 支持多知识库联合检索 | Step 7 |
| 低 | 流式响应中间节点事件（retrieve 完成时推送 chunk 元数据） | Step 8 |
