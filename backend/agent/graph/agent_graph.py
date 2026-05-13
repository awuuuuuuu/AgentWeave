"""
Agent 主图工厂

组装 Supervisor + Researcher + Analyst + Critic + HITL + Memory 为完整 LangGraph。

拓扑：
    memory_inject → supervisor ──→ researcher ──┐
                               ──→ analyst    ──┤──→ supervisor ──→ critic ──→ memory_save → END
                               ──→ hitl       ──┘               ↑─────────┘（打回重做）
                               ──→ critic（任务完成时）
"""
from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, StateGraph

from .analyst import AGENT_CARD as ANALYST_CARD
from .analyst import build_analyst
from .critic import AGENT_CARD as CRITIC_CARD
from .critic import build_critic
from .hitl import AGENT_CARD as HITL_CARD
from .hitl import build_hitl
from .memory_nodes import build_memory_nodes
from .researcher import AGENT_CARD as RESEARCHER_CARD
from .researcher import build_researcher
from .state import AgentState
from .supervisor import build_supervisor

# 所有已注册的 Worker Agent card（供前端展示全家桶使用）
_WORKER_CARDS: list[dict] = [RESEARCHER_CARD, ANALYST_CARD, HITL_CARD, CRITIC_CARD]

# 仅参与 Supervisor 路由的 card
# 注意：Critic 由图结构底层固定调用，不暴露给 Supervisor
_ROUTABLE_CARDS: list[dict] = [RESEARCHER_CARD, ANALYST_CARD, HITL_CARD]

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
    llm_model        Supervisor / Researcher / Analyst 使用的主模型
    """
    # ── 构建各节点 / 子图 ─────────────────────────────────────────────────────
    supervisor_fn = build_supervisor(llm_model=llm_model, agent_cards=_ROUTABLE_CARDS)
    researcher_sg = build_researcher(
        retriever=retriever,
        reranker=reranker,
        llm_model=llm_model,
    )
    analyst_fn = build_analyst(llm_model=llm_model)
    critic_fn = build_critic(llm_model=llm_model)
    hitl_fn = build_hitl()

    memory_inject_fn, memory_save_fn = (
        build_memory_nodes(memory_manager)
        if memory_manager else (None, None)
    )

    # ── 路由函数 ──────────────────────────────────────────────────────────────

    def route_from_supervisor(state: AgentState) -> str:
        """Supervisor 完成后，根据 next_agent 决定走向"""
        next_agent = state.get("next_agent", "__end__")
        if next_agent == "__end__":
            return "critic"
        return next_agent

    def route_from_critic(state: AgentState) -> str:
        """Critic 评审后：通过 → memory_save（或 END），打回 → supervisor"""
        result = state.get("next_agent", "__end__")
        if result == "__end__":
            return "memory_save" if memory_save_fn else "__end__"
        return result

    def route_from_hitl(state: AgentState) -> str:
        return "__end__" if state.get("next_agent") == "__end__" else "supervisor"

    # ── 构建主图 ──────────────────────────────────────────────────────────────
    graph = StateGraph(AgentState)

    graph.add_node("supervisor", supervisor_fn)
    graph.add_node("researcher", researcher_sg)
    graph.add_node("analyst", analyst_fn)
    graph.add_node("hitl", hitl_fn)
    graph.add_node("critic", critic_fn)

    if memory_inject_fn and memory_save_fn:
        graph.add_node("memory_inject", memory_inject_fn)
        graph.add_node("memory_save", memory_save_fn)
        graph.set_entry_point("memory_inject")
        graph.add_edge("memory_inject", "supervisor")
    else:
        graph.set_entry_point("supervisor")

    # Supervisor 出边
    graph.add_conditional_edges(
        "supervisor",
        route_from_supervisor,
        {
            "researcher": "researcher",
            "analyst": "analyst",
            "hitl": "hitl",
            "critic": "critic",
        },
    )

    # Workers 完成后回到 Supervisor
    graph.add_edge("researcher", "supervisor")
    graph.add_edge("analyst", "supervisor")

    graph.add_conditional_edges(
        "hitl",
        route_from_hitl,
        {"supervisor": "supervisor", "__end__": END},
    )

    # Critic 出边：通过 → memory_save → END；打回 → supervisor
    critic_targets: dict[str, Any] = {"supervisor": "supervisor"}
    if memory_save_fn:
        critic_targets["memory_save"] = "memory_save"
        graph.add_edge("memory_save", END)
    else:
        critic_targets["__end__"] = END

    graph.add_conditional_edges("critic", route_from_critic, critic_targets)

    compiled = graph.compile(checkpointer=checkpointer)
    logger.info(
        "AgentGraph: 编译完成 (记忆节点=%s)",
        "启用" if memory_inject_fn else "禁用",
    )
    return compiled


def get_agent_cards() -> list[dict]:
    """返回所有群成员 card，供 AgentRegistry 侧边栏展示"""
    return _WORKER_CARDS
