# AgentWeave 层级 Agent 跨域并行推理与私域知识隔离系统

> 让每个部门拥有自己的 AI 大脑，再让这些大脑协同工作。

AgentWeave 的核心是**层级 Agent 网络**：多个 AgentWeave 实例通过 A2A 协议互联，由 Weave Supervisor 并行调度各部门 Agent 同步推理——各部门的私有知识库和 MCP 工具集完全不出域，只有推理结论通过标准接口向上汇聚，再由指挥层整合决策、人工审批、落地执行，全程以高德地图实时渲染态势可视化。

这在现有开源框架（Dify / CrewAI / AutoGen）中是空白：它们要么只做单机 Agent，要么靠共享数据库协作，无法做到数据隔离与跨域推理并存。

每个 AgentWeave 实例本身也是一个完整的部门级知识助手：Agentic RAG 自校正检索、三层跨会话记忆、HITL 高风险操作拦截——这些能力既服务于单部门日常使用，也是组成层级网络时每个节点的推理基础。

---

## 两种使用模式

### 普通对话（Chat）

部门内部的 Agent 群聊。Supervisor 动态路由到 Researcher（Agentic RAG 检索）或 Analyst（MCP 工具调用）；Analyst 检测到写操作后触发 HITL 等待人工审批，审批通过由 Supervisor 路由到 Executor 确定性执行；Reporter 汇总答案。记忆系统在会话间持续积累。

```
用户 → Supervisor → Researcher（CRAG 自校正检索）
                  → Analyst（ReAct + MCP 工具）→ [HITL 审批] → Executor
                  → Reporter（引用汇总）
```

### 应急指挥（Weave）

跨部门的层级编排。Weave Supervisor 并行调用多个部门的 A2A Agent，聚合研判报告，生成执行计划，关键节点双级 HITL 审批，执行结果实时渲染到高德地图。

```
Weave Supervisor
  ├─ 环保局 A2A Agent → 扩散预测报告
  ├─ 医疗急救 A2A Agent → 医疗资源方案
  ├─ 消防救援 A2A Agent → 处置建议
  ├─ 交通管控 A2A Agent → 道路管控方案
  └─ 应急物资 A2A Agent → 物资调拨清单
         ↓
  HITL-1 计划审批 → 并行执行 → HITL-2 高危步骤逐一审批
         ↓
  高德地图实时图层渲染
```

---

## 核心能力

| 能力 | 实现 |
|------|------|
| **Agentic RAG** | CRAG 思路：score ≥ 0.72 快速通道；低于阈值走 LLM 评估；自动改写查询词重试最多 3 次；best_retrieved_docs 跨迭代保留 |
| **混合检索** | BM25（Milvus 内置 Function）+ 向量双路并发，Weighted Sum 融合（保留四路原始分数），qwen3-rerank 重排 |
| **三层记忆** | 短期（AsyncPostgresSaver）+ 长期语义（Milvus）+ 用户画像（PostgreSQL + Redis），MemoryManager 统一协调 |
| **Multi-Agent** | Supervisor `with_structured_output` 四字段路由（next / current_task / message_to_user / reasoning）；三重防幻觉（上下文窗口 + 内容截断 + 硬路由防火墙）；@mention 直接调度 |
| **HITL** | LangGraph `interrupt()` 持久化到 PostgreSQL，跨重启恢复；Weave 场景三级审批（位置确认 / 计划审批 / 高危步骤逐一）|
| **A2A 协议** | AgentCard 自描述（`/.well-known/agent.json`）；`/a2a/tasks/send` 标准推理接口；下游自动代批 HITL |
| **MCP 工具集成** | 部门专属 MCP Server（传感器 / 调度 / 信号灯）；Analyst read-only 拦截写操作；Executor 确定性执行 |
| **SSE 全驱动 UI** | 20+ 类 SSE 事件，指挥中心无轮询；in-place 卡片更新；高德地图 10+ 图层按事件 key 增量渲染 |

---

## 技术差异化

| 能力 | Dify | CrewAI | AutoGen | AgentWeave |
|------|------|--------|---------|------------|
| 私有 RAG | ✅ | ❌ | ❌ | ✅ Agentic RAG（自校正） |
| 跨会话记忆 | ❌ | ❌ | 部分 | ✅ 三层记忆 |
| Multi-Agent 路由 | 固定工作流 | 代码定义 | 代码定义 | Supervisor 动态路由 |
| HITL | ❌ | ❌ | ❌ | ✅ interrupt()，持久化 |
| 多租户 | ✅ | ❌ | ❌ | ✅ org_id 隔离 |
| A2A 外部 Agent | ❌ | ❌ | ❌ | ✅ |
| **层级 Agent 网络** | ❌ | ❌ | ❌ | **✅（市场空白）** |

---

## 开发进度

| Step | 模块 | 状态 |
|------|------|------|
| 1 | 文档摄入流水线（Parser / Splitter / Embedder / Store） | ✅ |
| 2 | 混合检索 + 重排序（BM25 + 向量 + qwen3-rerank） | ✅ |
| 3 | RAG Chain + SSE 流式 API + 前端基础 | ✅ |
| 4 | Auth + 知识库管理 + 文件上传 + 召回测试 | ✅ |
| 5 | 工具体系（kb_search / web_search / calculator） | ✅ |
| 6 | 三层记忆系统（短期 / 长期语义 / 用户画像） | ✅ |
| 7 | Multi-Agent 群聊 + Agentic RAG + HITL + Critic 门控 | ✅ |
| 8 | 多租户（org_id 隔离）+ 会话管理 + 前端群聊 UI | ✅ |
| 9 | 层级 Agent 网络：A2A + Weave Supervisor + 应急指挥中心 UI | ✅ |
| 10 | LangSmith 全链路追踪 + 自动评估 + `/analytics` 仪表盘 | 🔜 |

---

## 本地运行

### 前置依赖

- Python 3.11+、[uv](https://github.com/astral-sh/uv)
- Node.js 18+
- Docker（Milvus、Redis）
- PostgreSQL（Supabase 或本地）
- MinIO（本地或云端）

### 后端

```bash
# 在项目根目录执行（不要在 backend/ 内执行）
uv sync
PYTHONPATH=backend uv run uvicorn api.main:app --reload --port 8000

# Celery Worker（Windows 用 --pool=solo）
PYTHONPATH=backend uv run celery -A tasks.celery_app worker --loglevel=info --pool=solo

# 数据库迁移
PYTHONPATH=backend uv run alembic upgrade head
```

### Demo A2A 服务（Step 9 应急场景）

```bash
# 启动 5 个部门 A2A 服务器（端口 9001–9005）
PYTHONPATH=backend uv run python demo/scripts/a2a_servers.py

# 初始化浦东氨气泄漏场景数据
PYTHONPATH=backend uv run python demo/scripts/seed_departments.py
```

### 前端

```bash
cd frontend
npm install
npm run dev   # http://localhost:3000
```

### 环境变量

后端 `.env`（项目根目录）：

```env
# LLM
OPENAI_API_KEY=sk-...

# PostgreSQL（主库 + LangGraph checkpointer 共用）
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/dbname

# JWT
JWT_SECRET_KEY=your-secret-key

# MinIO
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=ragent
MINIO_SECURE=false

# Redis
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1
TOOL_CACHE_REDIS_URL=redis://localhost:6379/2
MEMORY_REDIS_URL=redis://localhost:6379/3

# 检索 & 记忆
MILVUS_URI=http://localhost:19530
MEMORY_LLM_MODEL=gpt-4o-mini

# 工具（留空则跳过对应工具）
TAVILY_API_KEY=tvly-...

# 高德地图（Weave 位置消歧 + 路径规划）
AMAP_API_KEY=your-amap-key
```

前端 `frontend/.env.local`：

```env
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_AMAP_KEY=your-amap-key
```

---

## 项目结构

```
AgentWeave/
├── backend/
│   ├── agent/
│   │   ├── graph/              # Agent 图节点
│   │   │   ├── agent_graph.py      # Chat 图编译（Supervisor 中心拓扑）
│   │   │   ├── supervisor.py       # 路由决策（with_structured_output）
│   │   │   ├── researcher.py       # Agentic RAG 子图（CRAG 自校正）
│   │   │   ├── analyst.py          # ReAct + MCP 工具（read-only 拦截）
│   │   │   ├── executor.py         # 确定性写操作执行
│   │   │   ├── hitl.py             # interrupt/resume 节点
│   │   │   ├── reporter.py         # 答案汇总 + 引用整合
│   │   │   ├── memory_nodes.py     # 记忆注入节点
│   │   │   ├── weave_supervisor.py # Weave 图（多部门编排，~1400 行）
│   │   │   ├── weave_state.py      # WeaveState TypedDict
│   │   │   └── state.py            # AgentState + ResearcherState
│   │   ├── a2a/
│   │   │   └── base.py             # A2A Server 基类 + AgentCard 端点
│   │   ├── memory/                 # 三层记忆实现
│   │   └── tools/                  # 工具注册表 + 执行引擎
│   ├── api/routes/
│   │   └── agent.py               # /stream、/resume、/state SSE 端点
│   ├── ingestion/                  # 文档摄入流水线
│   ├── retrieval/                  # 混合检索 + 重排序
│   └── config.py                   # 统一全局配置（Settings）
├── demo/
│   ├── city_state.db               # 浦东场景地理数据（SQLite）
│   ├── mock_servers/               # 5 个部门 MCP Server 实现
│   ├── scripts/                    # 启动脚本 + 种子数据
│   └── */knowledge/                # 各部门知识库 Markdown 文档
└── frontend/
    ├── components/
    │   ├── command-center/         # 应急指挥中心 UI（双栏 + 高德地图）
    │   └── agent-chat/             # 普通对话 UI（群聊气泡）
    └── app/(dashboard)/
        ├── agent/                  # Agent 群组管理
        ├── knowledge/              # 知识库管理
        └── command-center/         # Weave 会话入口
```

---

## 技术亮点

详细实现决策记录见 [技术亮点.md](技术亮点.md)，共 49 条，覆盖从文档摄入到层级 Agent 网络的完整技术故事。

代表性亮点：

- **摄入层**：PDF smart 模式按页三重降级路由；content_type 驱动全链路；Error Chunk 错误隔离
- **检索层**：Weighted Sum 融合保留四路原始分数；Milvus 内置 BM25 Function；双路并发检索
- **RAG 链**：单次 LangGraph 执行同时流出 token + 引用；`[N]` 引用格式 + ReactMarkdown 自定义渲染
- **记忆系统**：AsyncPostgresSaver checkpoint 原地裁剪；Recency Bias 时序注入；with_structured_output 画像提取
- **Multi-Agent**：Supervisor 三重防幻觉；with_structured_output 三字段路由（非 tool_calls）；Researcher 评分快速通道 + 跨迭代最优保留；Analyst 写操作信令协议；Executor 确定性执行
- **层级网络**：A2A AgentCard 自描述动态任务生成；四类意图分类多路快速通道；位置消歧 HITL-0；计划场景规则代码层兜底；事故中心三级推断；A2A 差异化重试；路线合成兜底；三级 HITL 审批；20+ 类 SSE 事件体系；AMap 10+ 图层增量渲染

---

## 技术栈

| 层 | 技术 |
|----|------|
| LLM | gpt-4o / gpt-4o-mini（OpenAI） |
| Embedding | text-embedding-3-small（OpenAI） |
| Reranker | qwen3-rerank（硅基流动） |
| Agent 编排 | LangGraph（AsyncPostgresSaver checkpointer） |
| 向量库 | Milvus 2.5（内置 BM25 Function） |
| 关系库 | PostgreSQL（Supabase） |
| 缓存 | Redis |
| 对象存储 | MinIO |
| 任务队列 | Celery + Redis |
| A2A / MCP | 自实现 HTTP 协议 + MultiServerMCPClient |
| 地图 | 高德地图 JS API（指挥中心） |
| 前端 | Next.js 15 + shadcn/ui + Tailwind CSS |
| 运行时 | Python 3.11+、uv |
| 测试 | pytest 8+ |

---

## 模块文档

- [技术亮点详解](技术亮点.md) — 49 条实现决策，含 Why & How
- [RAG Chain](backend/rag/README.md) — 数据流、引用溯源、SSE 流式
- [混合检索系统](backend/retrieval/README.md) — 双路并发、Weighted Sum、Reranker
- [文档摄入系统](backend/ingestion/README.md) — Parser / Splitter / Embedder / Store 层间契约

---

## 技术债

| 优先级 | 模块 | 描述 |
|--------|------|------|
| 中 | `ParentChildSplitter` | `parent_text` 直接存入子块 metadata，每个父块被复制 N 次写入向量库。重构方案：父块生成 UUID `parent_id` 存 Redis，子块只存 `parent_id`，检索时查 KV |
| 低 | `OpenAIEmbedder` | 无向量缓存，相同文本重复入库仍调用 OpenAI API。改造：`CachedEmbedder` 按文本哈希查 Redis |
| 中 | `auth/jwt.py` | JWT 无法主动失效（登出/改密场景）。改造：`jti` 字段 + Redis 黑名单，TTL = token 剩余有效期 |
