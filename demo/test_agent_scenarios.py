"""
部门 Agent 自动化测试脚本

直接调用 LangGraph（绕过 HTTP 层），验证各部门全部 13 个测试场景。

检查项：
  1. 路由链（supervisor 路由决策序列）
  2. MCP 工具调用顺序（从 Analyst logger 捕获）
  3. RAG 召回质量（retrieved 数量、top score、relevant、fallback）
  4. HITL 触发（写操作场景必须中断）
  5. 最终答案非空且有引用（非 fallback 场景）

前提：
  - MCP servers 已启动（python demo/start_mcp_servers.py）
  - 环境变量已配置（DATABASE_URL / MILVUS_URI / OPENAI_API_KEY 等）
  - 知识库已摄入（python demo/seed_departments.py）

用法：
  cd backend
  uv run python ../demo/test_agent_scenarios.py
  uv run python ../demo/test_agent_scenarios.py --dept env_agency
  uv run python ../demo/test_agent_scenarios.py --case "情景A-扩散疏散"
  uv run python ../demo/test_agent_scenarios.py --no-hitl   # 跳过所有 HITL 场景（不等人工审批）
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

# Windows: force UTF-8 output so Chinese and Unicode symbols render correctly
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── 确保 backend 在 sys.path ────────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).parent
_BACKEND_DIR = _SCRIPT_DIR.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from config import settings
from db.models import KnowledgeBase, Organization, User
from agent.graph.agent_graph import build_agent_graph
from ingestion.embedder.openai_embedder import OpenAIEmbedder
from ingestion.store.milvus_store import MilvusStore, MilvusStoreConfig
from retrieval.hybrid_retriever import HybridRetriever, HybridRetrieverConfig
from retrieval.reranker import Reranker, RerankerConfig

# ── 测试场景定义 ──────────────────────────────────────────────────────────────

SCENARIOS: dict[str, dict] = {
    "env_agency": {
        "dept_label": "环保局",
        "mcp_names": ["atmospheric_dispersion", "enterprise_sensor"],
        "kb_name": "环保局应急知识库",
        "cases": [
            {
                "name": "情景A-扩散疏散",
                "query": (
                    "港城大道388号液氨泄漏，传感器显示什么异常？"
                    "根据当前风速风向计算 ERPG-1/2/3 半径，"
                    "结合氨气安全技术说明书中的毒性参数，给出疏散分区方案。"
                ),
                "expected_chain": ["researcher", "analyst", "reporter"],
                "expected_tools_ordered": ["get_sensor_readings", "get_sensor_readings", "calculate_plume"],
                "expect_hitl": False,
                "expect_rag_fallback": False,
            },
            {
                "name": "情景B-事故定级",
                "query": (
                    "根据当前传感器数据和天津市突发环境事件应急预案，"
                    "此次泄漏应定为哪个级别？需要在多少分钟内向哪个机构报告？"
                ),
                "expected_chain": ["researcher", "analyst", "reporter"],
                "expect_hitl": False,
                "expect_rag_fallback": True,  # 问题含"当前传感器数据"，researcher 无实时数据，可能部分 fallback
            },
        ],
    },
    "medical_ems": {
        "dept_label": "医疗急救",
        "mcp_names": ["ambulance_dispatch", "amap"],
        "kb_name": "医疗急救知识库",
        "cases": [
            {
                "name": "情景A-派车HITL",
                "query": (
                    "现场8名重度氨中毒伤员需紧急后送。"
                    "根据急性氨中毒卫生应急预案的分级标准，确认接诊医院级别要求。"
                    "查询当前可用救护车和医院 ICU 容量，制定接送方案并派遣。"
                ),
                "expected_chain": ["researcher", "analyst", "hitl", "reporter"],
                "expected_tools_ordered": ["get_hospital_capacity", "list_ambulances"],
                "expect_hitl": True,
                "expect_rag_fallback": False,
            },
            {
                "name": "情景B-现场急救规程",
                "query": (
                    "急性氨中毒现场急救的标准流程是什么？"
                    "洗消、给氧、转运各阶段需要哪些设备和药品？"
                ),
                "expected_chain": ["researcher", "reporter"],
                "expect_hitl": False,
                "expect_rag_fallback": False,
            },
            {
                "name": "情景C-路线规划",
                "query": "A3救护车从泰达医院出发前往港城大道388号，规划最优路线。",
                "expected_chain": ["analyst"],   # 路线规划后直接 __end__，无 reporter
                "expected_tools_ordered": ["plan_driving_route"],
                "expect_hitl": False,
                "expect_rag_fallback": False,
                "expect_map_updates": True,   # plan_driving_route → 1条路线地图气泡
            },
            {
                "name": "情景D-坐标定位",
                "query": "泰达医院的经纬度坐标是多少？",
                "expected_chain": ["analyst"],   # 坐标查询后直接 __end__
                "expected_tools_ordered": ["geocode"],
                "expect_hitl": False,
                "expect_rag_fallback": False,
                "expect_map_updates": False,  # geocode 不产生地图气泡（只作中间步骤）
            },
        ],
    },
    "traffic_control": {
        "dept_label": "交通管控",
        "mcp_names": ["signal_control", "amap"],
        "kb_name": "交通管制知识库",
        "cases": [
            {
                "name": "情景A-批量路口管控HITL",
                "query": (
                    "港城大道388号氨气泄漏，根据应急疏散路线方案，"
                    "当前需要实施哪个级别的交通管控？"
                    "请查询各路口状态并按 Ⅲ 级预案批量设置。"
                ),
                "expected_chain": ["researcher", "analyst", "hitl", "reporter"],
                "expected_tools_ordered": ["list_intersections"],
                "expect_hitl": True,
                "expect_rag_fallback": False,
            },
            {
                "name": "情景B-救援走廊",
                "query": (
                    "救护车需要从泰达医院快速到达港城大道388号，"
                    "规划救援走廊并设置沿途路口为应急绿波。"
                ),
                "expected_chain": ["analyst", "hitl", "reporter"],  # 含信号写操作，保留 reporter
                "expected_tools_ordered": ["plan_driving_route"],
                "expect_hitl": True,
                "expect_rag_fallback": False,
                "expect_map_updates": True,   # plan_driving_route → 路线地图气泡
            },
            {
                "name": "情景C-地点定位",
                "query": "港城大道388号的精确经纬度坐标是多少？",
                "expected_chain": ["analyst"],  # 坐标查询后直接 __end__
                "expected_tools_ordered": ["geocode"],
                "expect_hitl": False,
                "expect_rag_fallback": False,
                "expect_map_updates": False,  # geocode 不产生地图气泡
            },
        ],
    },
    "emergency_supplies": {
        "dept_label": "应急物资",
        "mcp_names": ["warehouse"],
        "kb_name": "应急物资知识库",
        "cases": [
            {
                "name": "情景A-标准包调拨HITL",
                "query": (
                    "事故等级 Ⅲ 级，现场救援人员30人。"
                    "根据调拨规程确认 Ⅲ 级标准包清单，"
                    "查询当前库存是否满足，并办理调拨出库。"
                ),
                "expected_chain": ["researcher", "analyst", "hitl", "reporter"],
                "expected_tools_ordered": ["check_alerts", "get_inventory"],
                "expect_hitl": True,
                "expect_rag_fallback": False,
            },
            {
                "name": "情景B-告警库存核查",
                "query": (
                    "当前哪些物资已低于告警阈值？"
                    "对照调拨规程中的最低储备要求，给出补充建议。"
                ),
                "expected_chain": ["researcher", "analyst", "reporter"],
                "expected_tools_ordered": ["check_alerts"],
                "expect_hitl": False,
                "expect_rag_fallback": False,
            },
        ],
    },
    "enterprise_safety": {
        "dept_label": "企业安全",
        "mcp_names": ["enterprise_sensor"],
        "kb_name": "企业安全知识库",
        "cases": [
            {
                "name": "情景A-根因分析",
                "query": (
                    "结合传感器告警数据和事故快报，分析本次液氨管道泄漏的根本原因。"
                    "T-202 缓冲罐压力偏高是否是诱因之一？"
                ),
                "expected_chain": ["researcher", "analyst", "reporter"],
                "expected_tools_ordered": ["get_critical_alarms"],  # 两工具顺序非确定性，只验证 get_critical_alarms 必定被调
                "expect_hitl": False,
                "expect_rag_fallback": True,  # KB 有事故快报但无根因分析方法论，researcher 可能部分 fallback
            },
            {
                "name": "情景B-现场处置方法",
                "query": (
                    "根据 HG/T4686-2014 液氨泄漏处理处置方法，"
                    "当前泄漏量级对应哪种处置方案？"
                    "需要哪些专业防护装备才能进入泄漏区域？"
                ),
                "expected_chain": ["researcher", "reporter"],
                "expect_hitl": False,
                "expect_rag_fallback": True,  # 查询含"当前泄漏量级"（实时量），researcher 无法直接回答
            },
            {
                "name": "情景C-事故时间线+超限区域",
                "query": (
                    "梳理本次事故完整时间线，"
                    "并查询当前哪些传感器读数已超过 ERPG-3 阈值（750ppm）？"
                ),
                "expected_chain": ["analyst", "reporter"],
                "expected_tools_ordered": ["get_incident_timeline", "get_critical_alarms"],  # get_critical_alarms 是阈值超限的语义对应工具
                "expect_hitl": False,
                "expect_rag_fallback": True,  # 若 supervisor 误路由 researcher（应直接 analyst），KB 无阈值超限内容会 fallback
            },
        ],
    },
}

# ── MCP server 预检 ───────────────────────────────────────────────────────────

async def _tcp_ok(host: str, port: int, timeout: float = 2.0) -> bool:
    """TCP 端口连通性检查，不依赖 httpx。"""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


async def _check_mcp_servers(mcp_connections: list[dict]) -> list[str]:
    """返回连接失败的 MCP server 名称列表。"""
    offline: list[str] = []
    for conn in mcp_connections:
        url = conn.get("url", "")
        name = conn.get("name", "")
        if not url or not name:
            continue
        try:
            # http://localhost:8101/mcp → host=127.0.0.1, port=8101
            # 强制用 127.0.0.1 避免 Windows 将 localhost 解析为 ::1（IPv6）
            without_scheme = url.replace("http://", "").replace("https://", "")
            host_raw, rest = without_scheme.split(":", 1)
            host = "127.0.0.1" if host_raw in ("localhost", "127.0.0.1") else host_raw
            port = int(rest.split("/")[0])
        except (ValueError, IndexError):
            continue
        if not await _tcp_ok(host, port):
            offline.append(name)
    return offline


# 泛化 current_task 检测：supervisor 给 researcher 的任务不应是这类措辞
# 注：只检测明确的泛化短语，"的内容"过于常见（如"报告要求的内容"）不纳入
_VAGUE_TASK_RE = re.compile(r"(详细内容|相关内容|全部内容|文档内容)")

# ── 日志捕获 Handler ──────────────────────────────────────────────────────────

class _LogCapture(logging.Handler):
    """拦截 agent.graph.* 的 INFO/WARNING 日志，从中提取测试指标。"""

    def __init__(self):
        super().__init__()
        self.tool_calls: list[str] = []          # MCP 工具调用顺序
        self.rag_retrieved: int = 0              # 召回文档数
        self.rag_top_score: float = 0.0          # 最高 score
        self.rag_relevant: bool | None = None    # LLM 评估是否相关
        self.rag_fallback: bool = False          # 是否输出 fallback
        self.rag_citations: int = 0              # researcher 实际引用文档数（从日志读取）
        self.researcher_queries: list[str] = []  # researcher 每轮实际检索词（含重写后）
        self.map_update_count: int = 0           # analyst 输出的地图更新条数

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()

        # MCP 工具调用：Analyst: MCP 工具 'xxx' 返回 N 字符
        m = re.search(r"MCP 工具 '(\w+)' 返回", msg)
        if m:
            self.tool_calls.append(m.group(1))

        # RAG 召回数量
        m = re.search(r"检索到 (\d+) 条文档", msg)
        if m:
            self.rag_retrieved = int(m.group(1))

        # RAG top score（第一条 doc 日志）
        m = re.search(r"\[doc1\].*score=([\d.]+)", msg)
        if m and self.rag_top_score == 0.0:
            self.rag_top_score = float(m.group(1))

        # RAG relevant 评估
        m = re.search(r"LLM 评估 relevant=(True|False)", msg)
        if m:
            self.rag_relevant = m.group(1) == "True"

        # fallback 检测（来自 researcher generate_answer 日志）
        if "fallback=True" in msg or "cited_refs=[]" in msg:
            self.rag_fallback = True

        # 引用数（"引用 N/M 条"，取 N=实际引用数）
        m = re.search(r"引用 (\d+)/\d+ 条", msg)
        if m:
            self.rag_citations = int(m.group(1))

        # researcher 实际检索词（retrieve_docs 每次调用都记录，含重写后的词）
        m = re.search(r"Researcher\.retrieve_docs: query='([^']+)'", msg)
        if m:
            self.researcher_queries.append(m.group(1))

        # 地图更新条数：Analyst [N]: 生成分析结果 N 字，地图更新 N 条
        m = re.search(r"地图更新 (\d+) 条", msg)
        if m:
            self.map_update_count += int(m.group(1))


# ── 测试结果数据类 ────────────────────────────────────────────────────────────

class _CaseResult:
    def __init__(self, dept: str, case_name: str, skip_hitl: bool = False):
        self.dept = dept
        self.case_name = case_name
        self.skip_hitl = skip_hitl
        self.routing_chain: list[str] = []
        self.tool_calls: list[str] = []
        self.has_hitl: bool = False
        self.final_answer: str = ""
        self.citation_count: int = 0
        self.rag_retrieved: int = 0
        self.rag_top_score: float = 0.0
        self.rag_relevant: bool | None = None
        self.rag_fallback: bool = False
        self.researcher_queries: list[str] = []  # researcher 实际检索词（用于 current_task 质量检测）
        self.map_update_count: int = 0           # analyst 输出的地图更新条数
        self.elapsed: float = 0.0
        self.errors: list[str] = []

    def check(self, case: dict) -> tuple[bool, list[str]]:
        """对照期望值断言，返回 (passed, failure_reasons)。"""
        failures = []

        # 路由链：只检查 expected_chain 中出现的节点是否按顺序出现
        expected_chain = case.get("expected_chain", [])
        # --no-hitl 模式：截断 hitl 之后的期望节点（不等待人工审批，reporter 不会执行）
        if self.skip_hitl and self.has_hitl and "hitl" in expected_chain:
            hitl_idx = expected_chain.index("hitl")
            expected_chain = expected_chain[: hitl_idx + 1]
        if expected_chain:
            actual_non_end = [n for n in self.routing_chain if n != "__end__"]
            # 顺序子序列检查
            idx = 0
            for node in expected_chain:
                found = False
                while idx < len(actual_non_end):
                    if actual_non_end[idx] == node:
                        idx += 1
                        found = True
                        break
                    idx += 1
                if not found:
                    failures.append(
                        f"路由链缺少节点 '{node}'（实际: {' → '.join(actual_non_end) or '(空)'}，"
                        f"期望: {' → '.join(expected_chain)}）"
                    )
                    break

        # 工具调用顺序（有序子序列）
        expected_tools = case.get("expected_tools_ordered", [])
        if expected_tools:
            idx = 0
            for tool in expected_tools:
                found = False
                while idx < len(self.tool_calls):
                    if self.tool_calls[idx] == tool:
                        idx += 1
                        found = True
                        break
                    idx += 1
                if not found:
                    failures.append(
                        f"工具调用缺少 '{tool}'（实际: {self.tool_calls or '(无)'}，"
                        f"期望顺序含: {expected_tools}）"
                    )
                    break

        # HITL
        if case.get("expect_hitl") and not self.has_hitl:
            failures.append("期望触发 HITL 但未触发")
        if not case.get("expect_hitl") and self.has_hitl:
            failures.append("不期望触发 HITL 但实际触发了")

        # fallback
        if case.get("expect_rag_fallback") is False and self.rag_fallback:
            failures.append("RAG 输出了 fallback（无法回答），可能 KB 召回质量不足")

        # map_updates：amap 工具被调用时应产生地图数据
        if case.get("expect_map_updates") and self.map_update_count == 0:
            failures.append("期望 analyst 输出地图数据（map_updates）但实际为 0 条，amap 工具可能未被调用或结果解析失败")

        # citation：非 fallback + 有召回 → 期望至少 1 条引用
        if (
            not case.get("expect_rag_fallback")
            and self.rag_retrieved > 0
            and self.citation_count == 0
            and not (self.skip_hitl and self.has_hitl)
        ):
            failures.append("非 fallback 场景但 citation_count=0，引用缺失（KB 召回了但 reporter 未引用）")

        # current_task 质量：researcher 收到的检索词不应是泛化表述
        for q in self.researcher_queries:
            if _VAGUE_TASK_RE.search(q):
                failures.append(
                    f"Supervisor 给 Researcher 的 current_task 过于泛化（会导致召回差）: '{q[:80]}'"
                )
                break

        # 最终答案非空（--no-hitl 模式且触发了 HITL 时，答案为空是正常的）
        if not self.final_answer and not (self.skip_hitl and self.has_hitl):
            failures.append("最终答案为空")

        return (len(failures) == 0, failures)


# ── 核心：运行单个场景 ─────────────────────────────────────────────────────────

async def _run_case(
    graph: Any,
    case: dict,
    dept_label: str,
    org_mcp_connections: list[dict],
    org_mcp_summary: str,
    org_supervisor_hints: str,
    org_analyst_context: str,
    kb_ids: list[str],
    skip_hitl: bool = False,
) -> _CaseResult:
    result = _CaseResult(dept_label, case["name"], skip_hitl=skip_hitl)

    # 安装日志捕获
    capture = _LogCapture()
    capture.setLevel(logging.DEBUG)
    for name in ("agent.graph.supervisor", "agent.graph.analyst", "agent.graph.researcher"):
        logging.getLogger(name).addHandler(capture)

    session_id = str(uuid.uuid4())
    config = {
        "configurable": {"thread_id": f"test:{session_id}"},
        "recursion_limit": 30,
    }
    graph_input = {
        "messages": [HumanMessage(content=case["query"])],
        "kb_ids": kb_ids,
        "org_mcp_connections": org_mcp_connections,
        "org_mcp_summary": org_mcp_summary,
        "org_supervisor_hints": org_supervisor_hints,
        "org_analyst_context": org_analyst_context,
        "next_agent": "",
        "task": "",
        "message_to_user": "",
        "supervisor_count": 0,
        "researcher_count": 0,
        "analyst_count": 0,
        "pending_approval": None,
        "citations": [],
        "user_id": "test_user",
        "session_id": session_id,
        "memory_context": "",
        "memory_injected": True,  # 跳过记忆注入
    }

    t0 = time.monotonic()
    try:
        async for event in graph.astream_events(graph_input, config=config, version="v2"):
            ev_type = event.get("event", "")
            ev_name = event.get("name", "")

            # 路由决策
            if ev_type == "on_chain_end" and ev_name == "supervisor":
                output = event.get("data", {}).get("output", {})
                next_agent = output.get("next_agent", "")
                if next_agent:
                    result.routing_chain.append(next_agent)

            # HITL 中断
            if ev_type == "on_chain_stream":
                chunk = event.get("data", {}).get("chunk", {})
                if isinstance(chunk, dict) and "__interrupt__" in chunk:
                    result.has_hitl = True
                    if skip_hitl:
                        # 不等待人工审批，直接结束本场景
                        break

            # Reporter 最终答案（reporter 节点不 return citations，仅读 messages）
            if ev_type == "on_chain_end" and ev_name == "reporter":
                output = event.get("data", {}).get("output", {})
                msgs = output.get("messages", [])
                for msg in msgs:
                    c = getattr(msg, "content", "")
                    if c and len(c) > len(result.final_answer):
                        result.final_answer = c

    except Exception as exc:
        result.errors.append(str(exc))

    result.elapsed = time.monotonic() - t0

    # 从日志捕获中读取 RAG 指标（citation_count 从 researcher 日志读，reporter 不 return citations）
    result.tool_calls = capture.tool_calls[:]
    result.rag_retrieved = capture.rag_retrieved
    result.rag_top_score = capture.rag_top_score
    result.rag_relevant = capture.rag_relevant
    result.rag_fallback = capture.rag_fallback
    result.citation_count = capture.rag_citations
    result.researcher_queries = capture.researcher_queries[:]
    result.map_update_count = capture.map_update_count

    # 移除日志 handler
    for name in ("agent.graph.supervisor", "agent.graph.analyst", "agent.graph.researcher"):
        logging.getLogger(name).removeHandler(capture)

    return result


# ── 打印结果 ──────────────────────────────────────────────────────────────────

def _print_result(result: _CaseResult, case: dict, passed: bool, failures: list[str]) -> None:
    status = "✓" if passed else "✗"
    print(f"\n━━━ [{result.dept}] {result.case_name}  {status} ━━━")

    # RAG
    if result.rag_retrieved > 0:
        relevant_str = "✓" if result.rag_relevant else ("✗" if result.rag_relevant is False else "?")
        fallback_str = "✗" if result.rag_fallback else "✓"
        print(
            f"  RAG:   {result.rag_retrieved} docs, "
            f"top={result.rag_top_score:.3f}, "
            f"relevant={relevant_str}, "
            f"fallback={fallback_str}, "
            f"cited={result.citation_count}"
        )
        # 显示 researcher 实际检索词（帮助判断 current_task 质量）
        for i, q in enumerate(result.researcher_queries):
            marker = "⚠" if _VAGUE_TASK_RE.search(q) else " "
            print(f"  {marker}检索词[{i+1}]: {q[:100]}")

    # 路由链
    chain_str = " → ".join(result.routing_chain) if result.routing_chain else "(空)"
    expected_chain = case.get("expected_chain", [])
    chain_ok = "✓" if not failures or not any("路由链" in f for f in failures) else "✗"
    print(f"  路由链: {chain_str}  {chain_ok}  (期望含: {' → '.join(expected_chain)})")

    # 工具调用
    if result.tool_calls or case.get("expected_tools_ordered"):
        tools_str = " → ".join(result.tool_calls) if result.tool_calls else "(无)"
        tools_ok = "✓" if not any("工具" in f for f in failures) else "✗"
        expected_tools = case.get("expected_tools_ordered", [])
        print(f"  工具:   {tools_str}  {tools_ok}  (期望含: {expected_tools})")

    # HITL
    hitl_actual = "已触发" if result.has_hitl else "未触发"
    hitl_expected = "已触发" if case.get("expect_hitl") else "未触发"
    hitl_ok = "✓" if hitl_actual == hitl_expected else "✗"
    print(f"  HITL:  {hitl_actual}  {hitl_ok}  (期望: {hitl_expected})")

    # 最终答案
    ans_len = len(result.final_answer)
    ans_ok = "✓" if ans_len > 50 else "✗"
    print(f"  答案:  {ans_len} 字，引用 {result.citation_count} 条  {ans_ok}")

    # 地图数据
    if case.get("expect_map_updates") is not None:
        map_ok = "✓" if result.map_update_count > 0 else "✗"
        print(f"  地图:  {result.map_update_count} 条 map_update  {map_ok}  (期望: {'有' if case.get('expect_map_updates') else '无'})")

    # 耗时
    print(f"  耗时:  {result.elapsed:.0f}s")

    # 失败原因
    if failures:
        for f in failures:
            print(f"  ⚠ {f}")

    if result.errors:
        for e in result.errors:
            print(f"  ✗ 异常: {e}")


# ── 数据库：按部门读取 KB IDs 和 MCP 配置 ─────────────────────────────────────

async def _load_dept_context(sf: Any, dept_code: str, kb_name: str) -> tuple[list[str], list[dict], str, str, str]:
    """返回 (kb_ids, mcp_connections, mcp_summary, supervisor_hints, analyst_context)"""
    async with sf() as db:
        org = await db.scalar(
            select(Organization).where(Organization.dept_code == dept_code)
        )
        if org is None:
            return [], [], "", "", ""

        user = await db.scalar(
            select(User).where(User.org_id == org.id)
        )
        if user is None:
            return [], [], "", "", ""

        kb = await db.scalar(
            select(KnowledgeBase).where(
                KnowledgeBase.user_id == user.id,
                KnowledgeBase.name == kb_name,
            )
        )
        kb_ids = [kb.id] if kb else []
        mcp_connections = org.mcp_connections or []
        dept_prompts = org.dept_prompts or {}

        parts = [
            f"{c['name']}（{c.get('description', '')}）"
            for c in mcp_connections if c.get("name")
        ]
        mcp_summary = "Analyst 可调用以下 MCP 工具：" + "、".join(parts) if parts else ""

        return (
            kb_ids,
            mcp_connections,
            mcp_summary,
            dept_prompts.get("supervisor_hints", ""),
            dept_prompts.get("analyst_context", ""),
        )


# ── 主函数 ────────────────────────────────────────────────────────────────────

async def main(
    filter_dept: str | None = None,
    filter_case: str | None = None,
    skip_hitl: bool = False,
) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler()],
    )
    # 抑制 httpx / openai / mcp 噪音
    for noisy in ("httpx", "openai", "mcp", "langchain", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # ── 基础设施 ────────────────────────────────────────────────────────────
    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=2,
        connect_args={"ssl": False},
    )
    sf = async_sessionmaker(engine, expire_on_commit=False)

    embedder = OpenAIEmbedder()
    store_cfg = MilvusStoreConfig(uri=settings.milvus_uri)
    retriever = HybridRetriever(
        embedder=embedder,
        config=HybridRetrieverConfig(store_config=store_cfg),
    )
    reranker = Reranker(RerankerConfig())

    checkpointer = MemorySaver()  # 不持久化，每次测试独立
    graph = build_agent_graph(
        retriever=retriever,
        reranker=reranker,
        checkpointer=checkpointer,
        memory_manager=None,   # 跳过记忆注入
        llm_model=settings.llm_model,
    )

    # ── 运行场景 ────────────────────────────────────────────────────────────
    all_results: list[tuple[_CaseResult, dict, bool, list[str]]] = []
    passed_count = 0
    total_count = 0

    for dept_code, dept_cfg in SCENARIOS.items():
        if filter_dept and filter_dept != dept_code:
            continue

        dept_label = dept_cfg["dept_label"]
        kb_ids, mcp_connections, mcp_summary, supervisor_hints, analyst_context = (
            await _load_dept_context(sf, dept_code, dept_cfg["kb_name"])
        )

        if not kb_ids:
            print(f"\n⚠ [{dept_label}] 未找到知识库 '{dept_cfg['kb_name']}'，跳过（请先运行 seed_departments.py）")
            continue

        # MCP server 预检：提前发现离线 server，避免工具断言失败时难以定位原因
        if mcp_connections:
            offline = await _check_mcp_servers(mcp_connections)
            if offline:
                print(f"\n⚠ [{dept_label}] MCP server 离线: {offline}（请先运行 start_mcp_servers.py）")
                print(f"  工具调用断言将全部失败，本部门场景仍会运行以记录路由和 RAG 指标")

        # dept_prompts 完整性检查
        if not supervisor_hints:
            print(f"\n⚠ [{dept_label}] supervisor_hints 为空，请重新运行 seed_departments.py 更新 dept_prompts")

        for case in dept_cfg["cases"]:
            if filter_case and filter_case != case["name"]:
                continue

            total_count += 1
            print(f"\n▶ [{dept_label}] {case['name']} ...", flush=True)

            result = await _run_case(
                graph=graph,
                case=case,
                dept_label=dept_label,
                org_mcp_connections=mcp_connections,
                org_mcp_summary=mcp_summary,
                org_supervisor_hints=supervisor_hints,
                org_analyst_context=analyst_context,
                kb_ids=kb_ids,
                skip_hitl=skip_hitl,
            )
            passed, failures = result.check(case)
            if passed:
                passed_count += 1

            _print_result(result, case, passed, failures)
            all_results.append((result, case, passed, failures))

    # ── 汇总 ───────────────────────────────────────────────────────────────
    print(f"\n{'━'*50}")
    print(f"总计: {passed_count}/{total_count} 通过")

    failed = [(r, c, fs) for r, c, ok, fs in all_results if not ok]
    if failed:
        print("\n失败清单:")
        for r, c, fs in failed:
            print(f"  [{r.dept}] {r.case_name}")
            for f in fs:
                print(f"    · {f}")

    await engine.dispose()

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="部门 Agent 自动化测试")
    parser.add_argument("--dept", help="只运行指定部门（dept_code）")
    parser.add_argument("--case", help="只运行指定场景名称")
    parser.add_argument("--no-hitl", action="store_true", help="跳过 HITL 等待（不发送审批信号，直接结束）")
    args = parser.parse_args()

    asyncio.run(main(
        filter_dept=args.dept,
        filter_case=args.case,
        skip_hitl=args.no_hitl,
    ))
