"""
Agent 主图工厂

组装 Supervisor + Researcher + Analyst + Critic + HITL 为完整 LangGraph。
"""
from __future__ import annotations

import logging
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph

from .analyst import AGENT_CARD as ANALYST_CARD
from .analyst import build_analyst
from .critic import AGENT_CARD as CRITIC_CARD
from .critic import build_critic
from .hitl import AGENT_CARD as HITL_CARD
from .hitl import build_hitl
from .researcher import AGENT_CARD as RESEARCHER_CARD
from .researcher import build_researcher
from .state import AgentState
from .supervisor import build_supervisor

# 所有已注册的 Worker Agent card（供前端展示全家桶使用）
_WORKER_CARDS: list[dict] = [RESEARCHER_CARD, ANALYST_CARD, HITL_CARD, CRITIC_CARD]

# 仅参与 Supervisor 路由的 card
# 注意：Critic 由图结构底层拦截调用，不暴露给 Supervisor
_ROUTABLE_CARDS: list[dict] = [RESEARCHER_CARD, ANALYST_CARD, HITL_CARD]

logger = logging.getLogger(__name__)

def build_agent_graph(
    retriever: Any,
    reranker: Any | None,
    checkpointer: Any | None = None,
    llm_model: str = "gpt-4o"
) -> Any:
    """
    工厂函数：构建并返回编译后的 Agent 主图。

    参数
    ----
    retriever        HybridRetriever 实例（来自 app.state）
    reranker         Reranker 实例，可为 None
    checkpointer     LangGraph checkpointer；None 则自动创建 InMemorySaver
    llm_model        Supervisor / Researcher / Analyst 使用的主模型
    """
    if checkpointer is None:
        checkpointer = InMemorySaver()
        logger.info(
            "AgentGraph: 使用 InMemorySaver（HITL 状态不跨 server 重启持久化，"
            "生产环境请换用 AsyncPostgresSaver）"
        )

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

    # ── 路由函数 ──────────────────────────────────────────────────────────────

    def route_from_supervisor(state: AgentState) -> str:
        """Supervisor 完成后，根据 next_agent 决定走向"""
        next_agent = state.get("next_agent", "__end__")
        # __end__ 表示 Supervisor 认为任务完成，先过 Critic 审核
        if next_agent == "__end__":
            return "critic"
        return next_agent

    def route_from_critic(state: AgentState) -> str:
        """Critic 评审后：通过 → END，打回 → supervisor"""
        return state.get("next_agent", "__end__")
    
    def route_from_hitl(state: AgentState) -> str:
        return "__end__" if state.get("next_agent") == "__end__" else "supervisor"
    
    # ── 构建主图 ──────────────────────────────────────────────────────────────
    graph = StateGraph(AgentState)

    graph.add_node("supervisor", supervisor_fn)
    graph.add_node("researcher", researcher_sg)
    graph.add_node("analyst", analyst_fn)
    graph.add_node("hitl", hitl_fn)
    graph.add_node("critic", critic_fn)

    graph.set_entry_point("supervisor")

    # Supervisor 出边
    graph.add_conditional_edges(
        "supervisor",
        route_from_supervisor,
        {
            "researcher": "researcher",
            "analyst": "analyst",
            "hitl": "hitl",
            "critic": "critic",      # 任务完成，送 Critic 审核
        },
    )

    # Workers 完成后回到 Supervisor（让 Supervisor 决定下一步）
    graph.add_edge("researcher", "supervisor")
    graph.add_edge("analyst", "supervisor")

    graph.add_conditional_edges(
        "hitl",
        route_from_hitl,
        {"supervisor": "supervisor", "__end__": END},
    )

    # Critic 出边
    graph.add_conditional_edges(
        "critic",
        route_from_critic,
        {
            "supervisor": "supervisor",   # 打回重做
            "__end__": END,               # 通过，结束
        },
    )

    compiled = graph.compile(checkpointer=checkpointer)
    logger.info("AgentGraph: 编译完成")
    return compiled


def get_agent_cards() -> list[dict]:
    """返回所有群成员 card，供 AgentRegistry 侧边栏展示"""
    return _WORKER_CARDS
