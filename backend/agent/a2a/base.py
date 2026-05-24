"""
部门 A2A HTTP Server 公共工厂

每个部门 A2A Server 启动时：
1. 从 DB 读取该部门 org 的 mcp_connections + dept_prompts（seed_departments.py 已写入）
2. 连接部门 KB 的 retriever（共享 Milvus/OpenAI，但 kb_ids 不同）
3. 调用现有 build_agent_graph() 构建图（MemorySaver，每次请求独立 thread_id）
4. 暴露 A2A 协议端点

A2A 协议：
  GET  /.well-known/agent.json   → 部门能力描述
  POST /a2a/tasks/send           → 运行 graph → 返回结构化 JSON

HITL 处理：A2A 模式下自动批准所有 HITL 中断（人工审批在 Emergency Supervisor 层）

运行方式（在 backend/ 目录下）：
  uv run python agent/a2a/en_server.py
"""
from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from contextlib import asynccontextmanager
from typing import Literal

import uvicorn
from fastapi import FastAPI
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from config import settings
from db.models import KnowledgeBase, Organization, User
from agent.graph.agent_graph import build_agent_graph
from ingestion.embedder.openai_embedder import OpenAIEmbedder
from ingestion.store.milvus_store import MilvusStore, MilvusStoreConfig
from retrieval.hybrid_retriever import HybridRetriever, HybridRetrieverConfig
from retrieval.reranker import Reranker, RerankerConfig

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


# ── A2A 协议 Schema ───────────────────────────────────────────────────────────

class A2ATaskRequest(BaseModel):
    task_id: str
    task: str
    context: dict = Field(default_factory=dict)
    timeout_sec: int = 90


class McpSource(BaseModel):
    idx: int
    tool_name: str
    key_result: str


class DeptMetric(BaseModel):
    label: str                       # 指标名，如 "ERPG-2 半径"
    value: str = ""                  # 指标值，如 "890"
    unit: str = ""                   # 单位，如 "m"
    severity: str = ""               # critical | warn | ok | info（空=未分级）
    source_idx: int | None = None    # 关联 mcp_sources[idx]，供前端点跳


class A2ATaskResponse(BaseModel):
    task_id: str
    dept_code: str
    status: Literal["completed", "failed", "timeout"]
    summary: str = ""
    key_facts: list[str] = []
    metrics: list[DeptMetric] = []
    map_events: list[dict] = []
    citations: list[dict] = []
    mcp_sources: list[McpSource] = []
    duration_ms: int = 0


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def _build_mcp_summary(mcp_connections: list[dict]) -> str:
    if not mcp_connections:
        return ""
    parts = [
        f"{c['name']}（{c.get('description', '')}）"
        for c in mcp_connections
        if c.get("name")
    ]
    return "Analyst 可调用以下 MCP 工具：" + "、".join(parts)


def _extract_key_facts(text: str, max_facts: int = 8) -> list[str]:
    """从摘要文本提取关键数据条目，过滤 markdown 结构行。"""
    facts: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        # 跳过 markdown 标题行（#）和纯分隔符行
        if s.startswith("#") or s in ("---", "***", "___"):
            continue
        # 去掉列表前缀 `- ` / `* ` / `• ` / `1. `
        s = re.sub(r"^[\-•·\*]+\s+", "", s)
        s = re.sub(r"^\d+[\.、\)]\s*", "", s)
        # 去掉 **bold** 标记
        s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
        # 去掉引用标注 [N] 和 [MN]
        s = re.sub(r"\[M?\d+\]", "", s).strip()
        # 过滤太短或纯标点的行
        if len(s) < 6:
            continue
        facts.append(s)
        if len(facts) >= max_facts:
            break
    return facts


_METRICS_BLOCK_RE = re.compile(
    r"```(?:json)?\s*\n?\s*(?:\"?metrics\"?\s*[:=]\s*)?(\[[\s\S]*?\])\s*```",
    re.IGNORECASE,
)


def _extract_metrics(summary: str, key_facts: list[str]) -> tuple[list[DeptMetric], str]:
    """从 reporter 输出末尾解析 metrics JSON 代码块。

    返回 (metrics, cleaned_summary)：
    - 成功：解析 JSON → list[DeptMetric]，并从 summary 中剥离该代码块（避免前端显示裸 JSON）
    - 失败/缺失：降级用 key_facts 包装为 [{label: 整行, value: ""}]，summary 原样返回
    """
    import json as _json

    # 取最后一个匹配，避免正文内的示例代码块被误命中
    matches = list(_METRICS_BLOCK_RE.finditer(summary))
    m = matches[-1] if matches else None
    if m:
        try:
            raw = _json.loads(m.group(1))
            metrics: list[DeptMetric] = []
            for item in raw:
                if not isinstance(item, dict):
                    continue
                label = str(item.get("label", "")).strip()
                if not label:
                    continue
                metrics.append(DeptMetric(
                    label=label,
                    value=str(item.get("value", "")).strip(),
                    unit=str(item.get("unit", "")).strip(),
                    severity=str(item.get("severity", "")).strip().lower(),
                    source_idx=item.get("source_idx") if isinstance(item.get("source_idx"), int) else None,
                ))
            if metrics:
                cleaned = summary[: m.start()].rstrip() + summary[m.end():]
                return metrics, cleaned.rstrip()
        except Exception:
            logger.warning("A2A: metrics JSON 解析失败，降级用 key_facts")

    # 降级：把 key_facts 当作无结构化值的指标行
    fallback = [DeptMetric(label=f) for f in key_facts[:8]]
    return fallback, summary


# ── 核心：自动 HITL 批准执行器 ───────────────────────────────────────────────

async def _run_with_auto_hitl(graph, graph_input: dict, config: dict) -> dict:
    """
    运行图，遇到 HITL interrupt 自动 approve，直到图完全结束。
    返回最终状态 values dict（含 mcp_sources，由 analyst 节点写入）。
    """
    result = await graph.ainvoke(graph_input, config=config)

    for _ in range(5):
        state = await graph.aget_state(config)
        if not state or not state.next:
            break
        logger.info("A2A: 遇到 HITL interrupt，自动批准")
        result = await graph.ainvoke(Command(resume="approve"), config=config)

    return result if isinstance(result, dict) else {}


# ── 工厂函数 ──────────────────────────────────────────────────────────────────

def build_dept_a2a_app(dept_code: str, port: int) -> FastAPI:
    """
    构建并返回部门 A2A FastAPI 应用。

    参数
    ----
    dept_code   对应 Organization.dept_code（如 "env_agency"）
    port        本服务监听端口（9001-9005）
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("A2A[%s]: 启动，读取 DB 配置...", dept_code)

        # ── 读取 org 配置 ─────────────────────────────────────────────────────
        engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=2,
            connect_args={"ssl": False},
        )
        sf = async_sessionmaker(engine, expire_on_commit=False)

        async with sf() as db:
            org = await db.scalar(
                select(Organization).where(Organization.dept_code == dept_code)
            )
            if org is None:
                raise RuntimeError(
                    f"A2A[{dept_code}]: 未找到 org，请先运行 demo/seed_departments.py"
                )

            app.state.dept_name = org.name
            app.state.org_mcp_connections = org.mcp_connections or []
            prompts = org.dept_prompts or {}
            app.state.org_supervisor_hints = prompts.get("supervisor_hints", "")
            app.state.org_analyst_context = prompts.get("analyst_context", "")

            # 获取该 org 下所有未删除 KB
            result = await db.execute(
                select(KnowledgeBase)
                .join(User, User.id == KnowledgeBase.user_id)
                .where(
                    User.org_id == org.id,
                    KnowledgeBase.is_deleted.is_(False),
                )
            )
            app.state.kb_ids = [kb.id for kb in result.scalars().all()]

        await engine.dispose()

        logger.info(
            "A2A[%s]: %s | KB数=%d | MCP工具=%d",
            dept_code, app.state.dept_name,
            len(app.state.kb_ids), len(app.state.org_mcp_connections),
        )

        # ── 构建检索组件 + graph ───────────────────────────────────────────────
        store_cfg = MilvusStoreConfig(uri=settings.milvus_uri)
        embedder = OpenAIEmbedder()
        retriever = HybridRetriever(
            embedder=embedder,
            config=HybridRetrieverConfig(
                store_config=store_cfg,
                candidate_multiplier=settings.candidate_multiplier,
            ),
        )
        reranker = (
            Reranker(RerankerConfig(
                reranker_type=settings.reranker_type,
                model_name=settings.reranker_model,
                api_key=settings.dashscope_api_key,
                base_url=settings.dashscope_base_url,
            ))
            if settings.use_reranker
            else None
        )

        # MemorySaver：每次请求使用独立 thread_id，无跨请求持久化
        checkpointer = MemorySaver()
        app.state.graph = build_agent_graph(
            retriever=retriever,
            reranker=reranker,
            checkpointer=checkpointer,
            memory_manager=None,
            llm_model=settings.llm_model,
        )

        logger.info("A2A[%s]: 就绪，监听 port=%d", dept_code, port)
        yield
        logger.info("A2A[%s]: 关闭", dept_code)

    # ── FastAPI 应用 ──────────────────────────────────────────────────────────
    app = FastAPI(title=f"A2A Server: {dept_code}", lifespan=lifespan)

    @app.get("/.well-known/agent.json")
    async def agent_card() -> dict:
        return {
            "name": app.state.dept_name,
            "dept_code": dept_code,
            "capabilities": ["emergency_response", "kb_search", "mcp_tools"],
            "endpoint": f"http://localhost:{port}/a2a/tasks/send",
            "kb_ids": app.state.kb_ids,
            "mcp_tools": [
                {"name": c["name"], "description": c.get("description", "")}
                for c in app.state.org_mcp_connections
            ],
        }

    @app.post("/a2a/tasks/send", response_model=A2ATaskResponse)
    async def handle_task(body: A2ATaskRequest) -> A2ATaskResponse:
        t0 = time.monotonic()
        thread_id = f"a2a:{dept_code}:{body.task_id}:{secrets.token_hex(4)}"
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 20}

        mcp_connections = app.state.org_mcp_connections
        graph_input = {
            "messages": [HumanMessage(content=body.task)],
            "user_id": f"a2a_{dept_code}",
            "session_id": body.task_id,
            "kb_ids": app.state.kb_ids,
            "org_mcp_connections": mcp_connections,
            "org_mcp_summary": _build_mcp_summary(mcp_connections),
            "org_supervisor_hints": app.state.org_supervisor_hints,
            "org_analyst_context": app.state.org_analyst_context,
            "dept_code": dept_code,
            "emit_metrics": True,
            "next_agent": "",
            "task": "",
            "message_to_user": "",
            "supervisor_count": 0,
            "researcher_count": 0,
            "analyst_count": 0,
            "pending_approval": None,
            "citations": [],
        }

        try:
            result = await asyncio.wait_for(
                _run_with_auto_hitl(app.state.graph, graph_input, config),
                timeout=body.timeout_sec,
            )
            duration_ms = int((time.monotonic() - t0) * 1000)

            summary = ""
            for m in reversed(result.get("messages", [])):
                if isinstance(m, AIMessage) and m.content:
                    summary = str(m.content)
                    break
                if isinstance(m, dict) and m.get("type") == "ai" and m.get("content"):
                    summary = str(m["content"])
                    break

            map_events = result.get("map_updates", []) or []
            citations = result.get("citations", []) or []

            # analyst 节点直接将 mcp_sources 写入 state，ainvoke 即可读取
            mcp_sources: list[McpSource] = [
                McpSource(
                    idx=s.get("idx", i + 1),
                    tool_name=s.get("tool_name", "tool"),
                    key_result=s.get("key_result", ""),
                )
                for i, s in enumerate(result.get("mcp_sources", []))
            ]

            # 解析结构化指标（剥离 summary 末尾的 metrics JSON 代码块）
            key_facts = _extract_key_facts(summary)
            metrics, summary = _extract_metrics(summary, key_facts)

            logger.info(
                "A2A[%s]: task_id=%s 完成 duration=%dms summary_len=%d mcp_calls=%d metrics=%d",
                dept_code, body.task_id, duration_ms, len(summary), len(mcp_sources), len(metrics),
            )
            return A2ATaskResponse(
                task_id=body.task_id,
                dept_code=dept_code,
                status="completed",
                summary=summary,
                key_facts=key_facts,
                metrics=metrics,
                map_events=map_events,
                citations=citations,
                mcp_sources=mcp_sources,
                duration_ms=duration_ms,
            )

        except asyncio.TimeoutError:
            logger.warning("A2A[%s]: task_id=%s 超时", dept_code, body.task_id)
            return A2ATaskResponse(
                task_id=body.task_id, dept_code=dept_code,
                status="timeout", duration_ms=body.timeout_sec * 1000,
            )
        except Exception:
            logger.exception("A2A[%s]: task_id=%s 执行失败", dept_code, body.task_id)
            return A2ATaskResponse(
                task_id=body.task_id, dept_code=dept_code, status="failed",
            )

    return app


def run_server(dept_code: str, port: int) -> None:
    """各 server 文件的入口。在 backend/ 目录下运行：uv run python agent/a2a/en_server.py"""
    app = build_dept_a2a_app(dept_code=dept_code, port=port)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
