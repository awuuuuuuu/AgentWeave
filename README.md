# RAGent

企业级 Agent + RAG 系统。

## 开发进度

**Step 1 — 文档摄入**

| 子模块 | 状态 |
|--------|------|
| Parsers（PDF / Word / HTML / Markdown / TXT / Fallback） | 🚧 建设中 |
| Splitter（按语义边界切块，控制 chunk 大小与重叠） | 🔜 |
| Embedding（文本 → 向量，OpenAI text-embedding-3-small） | 🔜 |
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

## 模块文档

- [文档解析器](backend/ingestion/parsers/README.md) — PDF / Word / HTML / Markdown / TXT / Fallback

## 技术栈

- **LLM**：claude-sonnet-4-6 / claude-haiku-4-5
- **Embedding**：text-embedding-3-small（OpenAI）
- **向量库**：Milvus（Docker 本地）
- **关系库**：PostgreSQL（Supabase）
- **缓存**：Redis（Docker 本地）
- **前端**：Next.js 14 + shadcn/ui + Tailwind
- **运行时**：Python 3.11+、uv
- **测试**：pytest 8+
