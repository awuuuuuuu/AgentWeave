# 文本切分器（Splitters）

Splitter 层只有一个职责：**把 Parser 输出的超长 chunk 切到 token 上限内**。
结构感知已在 Parser 层完成，Splitter 不跨越章节/页/Sheet 边界。

---

## 与 Parser 层的分工

```
文档
  └─ Parser（结构感知）
        ├─ 按标题层级切块（Word / HTML / Markdown）
        ├─ 按页切块（PDF）
        └─ 按 Sheet 切块（Excel）
              └─ Splitter（token 约束）
                    ├─ table / title → 直接透传，不切
                    └─ text → 超出 chunk_size 则切分
```

---

## 公共约定（`BaseSplitter`）

所有策略继承 `BaseSplitter`，共享以下行为：

| 约定 | 说明 |
|------|------|
| `content_type` 路由 | `table` / `title` 直接返回，不切 |
| metadata 继承 | 子 chunk 继承父 chunk 全部 metadata |
| `chunk_index` / `chunk_total` | 切分后追加到每个子 chunk 的 metadata |
| token 计数 | 统一使用 tiktoken `cl100k_base` |

---

## 三种切分策略

### 1. RecursiveSplitter（默认策略）

**适用场景**：通用文本，语义边界不规则。

**算法**：
1. 按分隔符层级从粗到细尝试：`\n\n → \n → 。→ . → ； → ; → 空格 → 逐字符`
2. 切出的碎片 merge 至接近 `chunk_size`
3. 相邻 chunk 保留 `chunk_overlap` 个 token 的重叠

**关键细节**：
- 分隔符被保留在片段末尾（如 `"A。"` 而非 `"A"`），避免文本粘连
- overlap 自适应裁剪：保证 `overlap + 下一片段 ≤ chunk_size`

```python
RecursiveSplitter(RecursiveConfig(
    chunk_size=512,
    chunk_overlap=64,
    separators=["\n\n", "\n", "。", ".", "；", ";", " ", ""],
))
```

---

### 2. SemanticSplitter

**适用场景**：需要按话题边界切分，而非固定大小切分。

**算法**：
1. 按句子分割文本（`。` / `.` / `\n`）
2. 调用外部 `embed_fn` 对每句话做向量化
3. 计算相邻句子的余弦相似度，低于 `breakpoint_threshold` 处视为话题切换，在此切断
4. 短于 `chunk_size` 的文本跳过 embed，直接返回
5. `embed_fn` 失败时降级为返回原 chunk，不中断 pipeline

**embed_fn 注入**（解耦 OpenAI SDK，便于测试和替换）：

```python
from openai import OpenAI

client = OpenAI()

def embed_fn(sentences: list[str]) -> list[list[float]]:
    resp = client.embeddings.create(model="text-embedding-3-small", input=sentences)
    return [d.embedding for d in resp.data]

splitter = SemanticSplitter(embed_fn, SemanticConfig(
    chunk_size=512,
    breakpoint_threshold=0.7,  # 相似度低于此值则切分
))
```

---

### 3. ParentChildSplitter

**适用场景**：需要同时支持精准检索（小块）和丰富上下文（大块）的两阶段 RAG。

**算法**（参考 Dify Parent-Child Retrieval）：
1. 用 `parent_splitter`（chunk_size=512）把原文切成父块
2. 对每个父块再用 `child_splitter`（chunk_size=128）切成子块
3. 子块携带 `parent_text` 和 `parent_index`，检索阶段可回溯父块

**两阶段检索流程**：

```
查询 → 向量检索（子块，128 token，精准定位）
     → 取出 parent_text（512 token，完整上下文喂给 LLM）
```

```python
ParentChildSplitter(ParentChildConfig(
    parent_chunk_size=512,   # 父块：送给 LLM 的上下文
    child_chunk_size=128,    # 子块：向量库检索的粒度
    child_overlap=16,
))
```

**子块 metadata 示例**：
```python
{
    "source_file": "report.pdf",
    "content_type": "text",
    "section_path": "第三章 > 3.2 节",
    "parent_text": "...(512 token 父块全文)...",
    "parent_index": 2,        # 第 3 个父块
    "chunk_index": 0,         # 在父块内的位置
    "chunk_total": 4,
}
```

---

## 策略选型指南

| 场景 | 推荐策略 |
|------|---------|
| 通用文档（报告、手册、文章） | `RecursiveSplitter` |
| 话题明显跨越的长文（学术论文、新闻） | `SemanticSplitter` |
| 需要精准检索 + 完整上下文（合同、技术文档） | `ParentChildSplitter` |

---

## 测试

```
backend/tests/test_splitter.py
```

覆盖项：基础行为、content_type 路由、metadata 继承、chunk_size/overlap 约束、递归切分逻辑、语义切分触发条件、embed_fn 失败降级、ParentChildSplitter 父子关系校验。
