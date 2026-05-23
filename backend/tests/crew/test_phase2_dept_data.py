"""
Phase 2 — 各部门数据质量测试（crew_integration）

验证每个部门 A2A Server 的研判响应：
- status == "completed"
- RAG 有检索结果（citations >= 1）
- MCP 工具白名单命中
- map_events 坐标格式合法
- key_facts 包含数值
"""
from __future__ import annotations

import pytest

from tests.crew.evaluators import (
    RESEARCH_MCP_WHITELIST,
    SELECTED_DEPTS,
    check_key_facts_have_numbers,
    check_map_event_coordinates,
    check_mcp_whitelist,
)

pytestmark = pytest.mark.crew_integration


class TestDeptResponseStatus:
    @pytest.mark.parametrize("dept_code", SELECTED_DEPTS)
    def test_status_completed(self, dept_responses, dept_code):
        resp = dept_responses[dept_code]
        status = resp.get("status")
        assert status == "completed", (
            f"[{dept_code}] status={status!r}，期望 'completed'\n"
            f"  summary: {resp.get('summary', '')[:200]}\n"
            f"  调优建议：查看 A2A Server 日志，检查 LLM 调用是否超时"
        )


class TestDeptRagCitations:
    @pytest.mark.parametrize("dept_code", SELECTED_DEPTS)
    def test_has_rag_citations(self, dept_responses, dept_code):
        resp = dept_responses[dept_code]
        citations = resp.get("citations", [])
        assert len(citations) >= 1, (
            f"[{dept_code}] citations 为空，RAG 未检索到相关内容\n"
            f"  调优建议：检查该部门 KB 是否已索引相关文档"
        )


class TestDeptMcpSources:
    @pytest.mark.parametrize("dept_code", SELECTED_DEPTS)
    def test_has_mcp_sources(self, dept_responses, dept_code):
        resp = dept_responses[dept_code]
        mcp_sources = resp.get("mcp_sources", [])
        assert len(mcp_sources) >= 1, (
            f"[{dept_code}] mcp_sources 为空，Analyst 未调用任何 MCP 工具\n"
            f"  调优建议：在 dept_prompts.analyst_context 中添加"
            f"「必须调用 MCP 工具获取实时数据」强制约束"
        )

    @pytest.mark.parametrize("dept_code", SELECTED_DEPTS)
    def test_mcp_whitelist(self, dept_responses, dept_code):
        resp = dept_responses[dept_code]
        mcp_sources = resp.get("mcp_sources", [])
        ok, msg = check_mcp_whitelist(dept_code, mcp_sources, RESEARCH_MCP_WHITELIST)
        assert ok, msg


class TestDeptMapEvents:
    @pytest.mark.parametrize("dept_code", SELECTED_DEPTS)
    def test_map_event_coordinates_valid(self, dept_responses, dept_code):
        resp = dept_responses[dept_code]
        events = resp.get("map_events", [])
        ok, msg = check_map_event_coordinates(events)
        assert ok, msg


class TestDeptKeyFacts:
    @pytest.mark.parametrize("dept_code", SELECTED_DEPTS)
    def test_key_facts_have_numbers(self, dept_responses, dept_code):
        resp = dept_responses[dept_code]
        ok, msg = check_key_facts_have_numbers(resp.get("key_facts", []))
        assert ok, msg
