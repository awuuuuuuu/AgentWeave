"""
Reporter 节点：最终答案整合与输出

在所有 Worker 完成后运行，整合检索结果、分析结论和引用来源，
生成带 [N] 标注的最终用户可读答案。

设计原则：
- 始终是用户看到的最后一个"发言"节点
- 有 token 流（streaming），避免前端长时间空白等待
- 引用来源完整保留，支持前端 [N] 角标点击溯源
"""
from __future__ import annotations

import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from .prompts import REPORTER_SYSTEM
from .state import AgentState

logger = logging.getLogger(__name__)

AGENT_CARD = {
    "name": "reporter",
    "description": "整合各专家结论，输出最终答案",
    "routing_hint": "所有必要 Worker（Researcher/Analyst）已完成且需要整合输出最终答案时",
    "tools": [],
    "icon": "📋",
    "color": "blue",
}


def build_reporter(llm_model: str = "gpt-4o") -> object:
    llm = ChatOpenAI(model=llm_model, temperature=0)

    async def reporter_node(state: AgentState) -> dict:
        messages = state.get("messages", [])
        citations = state.get("citations", [])

        user_msgs = [m for m in messages if isinstance(m, HumanMessage)]
        user_query = user_msgs[-1].content if user_msgs else ""

        # 收集 Worker AI 消息：包含 researcher/analyst，排除 supervisor/reporter 自身
        _EXCLUDE_NAMES = {"supervisor", "reporter"}
        worker_outputs: list[str] = []
        for m in messages:
            if not isinstance(m, AIMessage) or not m.content:
                continue
            if getattr(m, "name", "") in _EXCLUDE_NAMES:
                continue
            worker_outputs.append(m.content.strip())

        if not worker_outputs and not citations:
            # 无任何实质内容（如超纲降级）→ 不生成，让 Supervisor 的 message_to_user 作为最终回复
            return {}

        # 构建检索文档上下文
        if citations:
            ref_block = "\n".join(
                f"[{c['ref']}] {c.get('source_file', '')}：{c.get('snippet', '')[:400]}"
                for c in citations
            )
            context_section = f"\n\n【参考文档原文】：\n{ref_block}"
        else:
            context_section = ""

        # 专家分析摘要（全部 worker 输出，reporter 已通过名称精确过滤，不会越界）
        expert_section = "\n\n---\n\n".join(worker_outputs)

        resp = await llm.ainvoke(
            [
                SystemMessage(content=REPORTER_SYSTEM),
                HumanMessage(content=(
                    f"【用户原始问题】：\n{user_query}\n\n"
                    f"【专家分析结果】：\n{expert_section}"
                    f"{context_section}"
                )),
            ]
        )
        answer = resp.content.strip()
        logger.info("Reporter: 生成最终答案 %d 字 | 引用 %d 条", len(answer), len(citations))

        return {
            "messages": [AIMessage(content=answer, name="reporter")],
        }

    return reporter_node
