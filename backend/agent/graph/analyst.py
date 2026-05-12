"""
Analyst 节点：逻辑推理与结构化分析

基于对话历史中已有的信息进行推理、计算、总结。
"""
from __future__ import annotations

import logging
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from agent.tools.builtin.calculator import CalculatorTool
from .prompts import ANALYST_SYSTEM
from .state import AgentState

logger = logging.getLogger(__name__)

AGENT_CARD = {
    "name": "analyst",
    "description": "逻辑推理、数学计算、数据分析、结构化总结；基于已有信息进行深度分析",
    "routing_hint": "需要推理、计算或结构化分析，且对话中已有所需数据时",
    "tools": ["calculator"],
    "icon": "📊",
    "color": "orange",
}

# Analyst 只需最近 N 条消息，避免把 Researcher 检索出来的大量文档全量传入
ANALYST_CONTEXT_WINDOW = 8

_calculator = CalculatorTool()


def build_analyst(llm_model: str = "gpt-4o") -> object:
    """构建 Analyst 节点函数（带 calculator 工具绑定）"""
    llm = ChatOpenAI(model=llm_model, temperature=0)
    llm_with_tools = llm.bind_tools([_calculator.to_function_schema()])

    async def analyst_node(state: AgentState) -> dict:
        task = state.get("task", "")
        messages = state.get("messages", [])
        recent = messages[-ANALYST_CONTEXT_WINDOW:]

        prompt = [
            SystemMessage(content=ANALYST_SYSTEM),
            *recent,
            HumanMessage(content=f"当前分析任务：{task}"),
        ]

        resp: AIMessage = await llm_with_tools.ainvoke(prompt)

        # LLM 决定调用工具
        if resp.tool_calls:
            tool_messages: list[ToolMessage] = []
            for tc in resp.tool_calls:
                if tc["name"] == "calculator":
                    expression = tc["args"].get("expression", "")
                    result = await _calculator._arun(expression=expression)
                    logger.info("Analyst: 调用计算器计算 %r，结果 = %s", expression, result.content)
                    tool_messages.append(
                        ToolMessage(
                            content=result.content,
                            tool_call_id=tc["id"],
                        )
                    )
                else:
                    # 未知工具，返回错误消息让 LLM 继续
                    tool_messages.append(
                        ToolMessage(
                            content=f"工具 {tc['name']} 不可用",
                            tool_call_id=tc["id"],
                        )
                    )

            # 携带工具结果再次调用 LLM 生成最终答案
            final_resp: AIMessage = await llm.ainvoke(
                [*prompt, resp, *tool_messages]
            )
            answer = final_resp.content.strip()
        else:
            answer = resp.content.strip()

        logger.info("Analyst: 生成分析结果 %d 字", len(answer))
        return {"messages": [AIMessage(content=answer)]}

    return analyst_node
