"""
Agent 图状态定义

AgentState    : Supervisor 主图的共享状态 (所有节点可读写) 
ResearcherState: Researcher 子图的私有状态 (对主图透明) 
"""
from __future__ import annotations

from typing import Annotated, Any

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

# ── 循环控制常量 ─────────────────────────────────────────────────────────────

MAX_REWRITES = 3         # Agentic RAG 最多重写 query 的次数
MAX_SUPERVISOR_LOOPS = 8 # Supervisor 最多路由次数 (防止大模型幻觉导致的无限循环) 


# ── 主图状态 ──────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    """Supervisor 主图状态 (所有节点共享) """

    # 消息历史
    messages: Annotated[list[BaseMessage], add_messages]

    # 对话元数据
    user_id: str
    session_id: str
    kb_ids: list[str]           # 允许检索的知识库 ID 列表

    # Supervisor 路由控制
    next_agent: str             # "researcher" | "analyst" | "hitl" | "__end__"
    task: str                   # Supervisor 给当前 worker 的任务描述 (自然语言)
    message_to_user: str        # Supervisor 向用户展示的自然语言回复
    
    # 记忆注入（memory_inject 写入，Supervisor 读取用于路由决策）
    memory_context: str

    # 循环控制计数器
    critic_count: int           # Critic 已重试次数
    supervisor_count: int       # Supervisor 已路由次数, 达到 MAX_SUPERVISOR_LOOPS 强制结束
    researcher_count: int       # Researcher 已实际执行次数（含无结果的调用）
    analyst_count: int          # Analyst 已实际执行次数

    # Human-in-the-Loop:待审批的高风险工具调用
    pending_approval: dict[str, Any] | None

    # RAG 引用 (由 Researcher 填入, 最终输出携带)
    citations: list[dict]

    # Critic 评审结果
    critic_score: float
    critic_approved: bool
    critic_feedback: str


# ── Researcher 子图状态 ───────────────────────────────────────────────────────

class ResearcherState(TypedDict):
    """Researcher 子图状态

    与 AgentState 共享的 key (LangGraph 自动同步):
        messages, kb_ids, task, citations
        - 检索中间步骤 (retrieve/grade/rewrite) 不写 messages, 保持历史干净。

    子图私有 key:
        rewrite_count, retrieved_docs, docs_relevant
    """

    messages: Annotated[list[BaseMessage], add_messages]
    kb_ids: list[str]
    task: str

    # Agentic RAG 循环控制
    rewrite_count: int          # 已重写次数，超过 MAX_REWRITES 强制生成
    retrieved_docs: list[dict]  # 当前检索结果
    best_retrieved_docs: list[dict]  # 历次召回中得分最高的一批（防止重写后零召回）
    docs_relevant: bool         # 当前结果是否足够相关

    # 子图输出（citations / researcher_count 会同步回 AgentState）
    citations: list[dict]
    researcher_count: int       # 与 AgentState 对齐，每次 generate_answer 完成后 +1
