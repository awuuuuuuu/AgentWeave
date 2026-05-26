"""
Agent 主图工厂

组装 Supervisor + Researcher + Analyst + HITL + Reporter + Memory 为完整 LangGraph。

拓扑：
    START ──(首轮)──→ memory_inject → supervisor ──→ researcher ──┐
          ──(续轮)──→ supervisor                  ──→ analyst    ──┤──→ supervisor ──→ reporter → END
                                                  ──→ hitl       ──┘
                                                  ──→ END（Supervisor 直接 __end__）

on_session_end 已移出图流程，由前端在关闭会话时调用 POST /agent/sessions/{id}/close 触发。

注：Critic 节点已保留在代码库中，但未接入默认图。
    质量门控由 HITL（人工审批）承担，适合跨组织应急响应等有人在链路上的场景。
"""
from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, START, StateGraph

from .analyst import AGENT_CARD as ANALYST_CARD
from .analyst import build_analyst
from .executor import build_executor
from .hitl import AGENT_CARD as HITL_CARD
from .hitl import build_hitl
from .memory_nodes import build_memory_nodes
from .reporter import AGENT_CARD as REPORTER_CARD
from .reporter import build_reporter
from .researcher import AGENT_CARD as RESEARCHER_CARD
from .researcher import build_researcher
from .state import AgentState
from .supervisor import build_supervisor

# 默认群成员 card（供前端侧边栏展示）
# Critic 已移出默认集合；如需质量门控，通过 enabled_agents 按会话启用
# executor 不在此列表中：由系统自动路由（HITL 批准后或 A2A 注入执行意图时），LLM 不可见
_AGENT_CARDS: list[dict] = [RESEARCHER_CARD, ANALYST_CARD, HITL_CARD, REPORTER_CARD]

logger = logging.getLogger(__name__)


def build_agent_graph(
    retriever: Any,
    reranker: Any | None,
    checkpointer: Any,
    memory_manager: Any | None = None,
    llm_model: str = "gpt-4o"
) -> Any:
    """
    工厂函数：构建并返回编译后的 Agent 主图。

    参数
    ----
    retriever        HybridRetriever 实例（来自 app.state）
    reranker         Reranker 实例，可为 None
    checkpointer     LangGraph checkpointer（必填，由 lifespan 传入 AsyncPostgresSaver）
    memory_manager   MemoryManager 实例；None 时跳过记忆节点（退化为无记忆模式）
    llm_model        Supervisor / Researcher / Analyst / Reporter 使用的主模型
    """
    # ── 构建各节点 / 子图 ─────────────────────────────────────────────────────
    supervisor_fn = build_supervisor(llm_model=llm_model, agent_cards=_AGENT_CARDS)
    researcher_sg = build_researcher(
        retriever=retriever,
        reranker=reranker,
        llm_model=llm_model,
    )
    analyst_fn = build_analyst(llm_model=llm_model)
    executor_fn = build_executor()
    hitl_fn = build_hitl()
    reporter_fn = build_reporter(llm_model=llm_model)

    memory_inject_fn = (
        build_memory_nodes(memory_manager)
        if memory_manager else None
    )

    # ── 路由函数 ──────────────────────────────────────────────────────────────

    def route_from_start(state: AgentState) -> str:
        """首轮对话时注入记忆，后续轮次直接进入 Supervisor。"""
        if memory_inject_fn and not state.get("memory_injected", False):
            return "memory_inject"
        return "supervisor"

    def route_from_supervisor(state: AgentState) -> str:
        next_agent = state.get("next_agent", "__end__")

        if next_agent == "__end__":
            return "__end__"

        if next_agent == "researcher":
            researcher_count = state.get("researcher_count", 0)
            if researcher_count > 0 and not state.get("citations", []):
                logger.info(
                    "Supervisor: Researcher 已运行 %d 次但无结果，强制路由到 Reporter",
                    researcher_count,
                )
                return "reporter"

        return next_agent

    def route_from_hitl(state: AgentState) -> str:
        return "__end__" if state.get("next_agent") == "__end__" else "supervisor"

    # ── 构建主图 ──────────────────────────────────────────────────────────────
    graph = StateGraph(AgentState)

    graph.add_node("supervisor", supervisor_fn)
    graph.add_node("researcher", researcher_sg)
    graph.add_node("analyst", analyst_fn)
    graph.add_node("executor", executor_fn)
    graph.add_node("hitl", hitl_fn)
    graph.add_node("reporter", reporter_fn)

    # 入口：首轮注入记忆，后续直接进 Supervisor
    if memory_inject_fn:
        graph.add_node("memory_inject", memory_inject_fn)
        graph.add_edge("memory_inject", "supervisor")
        graph.add_conditional_edges(
            START,
            route_from_start,
            {"memory_inject": "memory_inject", "supervisor": "supervisor"},
        )
    else:
        graph.add_edge(START, "supervisor")

    supervisor_targets: dict[str, Any] = {
        "researcher": "researcher",
        "analyst":    "analyst",
        "executor":   "executor",
        "hitl":       "hitl",
        "reporter":   "reporter",
        "__end__":    END,
    }
    graph.add_conditional_edges("supervisor", route_from_supervisor, supervisor_targets)

    graph.add_edge("researcher", "supervisor")
    graph.add_edge("analyst", "supervisor")
    graph.add_edge("executor", "supervisor")

    graph.add_conditional_edges(
        "hitl",
        route_from_hitl,
        {"supervisor": "supervisor", "__end__": END},
    )

    # Reporter 完成即结束；memory_save 由前端调用 /agent/sessions/{id}/close 触发
    graph.add_edge("reporter", END)

    compiled = graph.compile(checkpointer=checkpointer)
    logger.info(
        "AgentGraph: 编译完成 (记忆节点=%s)",
        "启用" if memory_inject_fn else "禁用",
    )
    return compiled


def get_agent_cards() -> list[dict]:
    """返回默认群成员 card，供 AgentRegistry 侧边栏展示"""
    return _AGENT_CARDS
