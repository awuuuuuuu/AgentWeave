"use client";

import React, { useState, useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CC, agentColor, DEPT_ICONS } from "./tokens";
import type { CommandCard, Citation, McpSource } from "./types";

// Teal color for MCP citations (distinct from RAG blue)
const MCP_COLOR = "#0891b2";

// MCP 工具名 → 中文展示名（前缀匹配，兼容 MultiServerMCPClient 添加的服务名前缀）
const MCP_TOOL_NAMES: [string, string][] = [
  ["plan_driving_route",      "路线规划"],
  ["geocode",                 "地址解析"],
  ["get_hospital_capacity",   "医院容量查询"],
  ["list_ambulances",         "救护车状态"],
  ["dispatch_ambulance",      "救护车调度"],
  ["calculate_plume",         "气体扩散计算"],
  ["get_sensor_readings",     "传感器读数"],
  ["get_critical_alarms",     "高风险告警"],
  ["get_incident_timeline",   "事故时间线"],
  ["list_intersections",      "路口信号状态"],
  ["set_intersection_mode",   "路口信号设置"],
  ["batch_set_intersections", "批量路口设置"],
  ["get_inventory",           "应急物资库存"],
  ["check_alerts",            "库存告警"],
  ["dispatch_materials",      "物资调拨"],
  ["get_equipment_status",    "设备状态"],
];

function getMcpToolLabel(toolName: string): string {
  for (const [key, label] of MCP_TOOL_NAMES) {
    if (toolName.includes(key)) return label;
  }
  return toolName;
}

// ── Agent 元信息 ─────────────────────────────────────────────────────────────

const AGENT_META: Record<string, { name: string; short: string }> = {
  PL: { name: "指挥中心", short: "PL" },
  EN: { name: "环保局", short: "EN" },
  ME: { name: "医疗急救", short: "ME" },
  TR: { name: "交通管控", short: "TR" },
  LG: { name: "应急物资", short: "LG" },
  SF: { name: "企业安全", short: "SF" },
};

// ── 部门 Workflow 步骤（研判阶段 5步 / 执行阶段 3步）──────────────────────────

type WorkflowStep = { label: string; call: string; result?: string };

/** 研判阶段：5步，展现完整 Supervisor→Researcher→Analyst×2→Reporter 工作流 */
const DEPT_RESEARCH_STEPS: Record<string, WorkflowStep[]> = {
  EN: [
    { label: "🎯 Supervisor: 分析研判任务", call: 'supervisor.analyze("液氨泄漏 大气扩散评估")' },
    { label: "📚 Researcher: RAG检索扩散历史案例", call: 'rag.search("液氨 ERPG-2 扩散半径")', result: "12篇命中" },
    { label: "🔧 Analyst: MCP获取实时气象数据", call: "mcp.weather.realtime(station='TJ_001')" },
    { label: "🔧 Analyst: 高斯扩散模型计算", call: "gaussian_model(Q=12.5, u=3.2, dir=225)" },
    { label: "📝 Reporter: 综合输出评估报告", call: "reporter.compile(findings)" },
  ],
  ME: [
    { label: "🎯 Supervisor: 分析研判任务", call: 'supervisor.analyze("液氨中毒 医疗急救响应")' },
    { label: "📚 Researcher: RAG检索急救SOP", call: 'rag.search("液氨中毒 急救 SOP")', result: "8篇命中" },
    { label: "🔧 Analyst: MCP查询医院床位", call: "mcp.hospital.beds(radius_km=10)" },
    { label: "🔧 Analyst: MCP规划救护车路线", call: "mcp.amap.route(start=incident, dest=hosp)" },
    { label: "📝 Reporter: 综合输出评估报告", call: "reporter.compile(findings)" },
  ],
  TR: [
    { label: "🎯 Supervisor: 分析研判任务", call: 'supervisor.analyze("交通管控 疏散路线规划")' },
    { label: "📚 Researcher: RAG检索交通管控规程", call: 'rag.search("危化品 交通管控 SOP")', result: "6篇命中" },
    { label: "🔧 Analyst: MCP查询实时路况", call: "mcp.traffic.query(area=radius_2km)" },
    { label: "🔧 Analyst: MCP规划疏散路线", call: "mcp.amap.evacuation_route(zones)" },
    { label: "📝 Reporter: 综合输出评估报告", call: "reporter.compile(findings)" },
  ],
  LG: [
    { label: "🎯 Supervisor: 分析研判任务", call: 'supervisor.analyze("应急物资 调配方案")' },
    { label: "📚 Researcher: RAG检索物资配备标准", call: 'rag.search("液氨泄漏 应急物资 SOP")', result: "5篇命中" },
    { label: "🔧 Analyst: MCP查询库存", call: "mcp.inventory.query(type='chem_emergency')" },
    { label: "🔧 Analyst: 物资调配方案计算", call: "logistics.plan(demand, supply_map)" },
    { label: "📝 Reporter: 综合输出评估报告", call: "reporter.compile(findings)" },
  ],
  SF: [
    { label: "🎯 Supervisor: 分析研判任务", call: 'supervisor.analyze("企业安全 风险评估")' },
    { label: "📚 Researcher: RAG检索安全规程", call: 'rag.search("液氨储罐 安全规程")', result: "9篇命中" },
    { label: "🔧 Analyst: MCP查询传感器数据", call: "mcp.sensor.query(zone='factory_A')" },
    { label: "🔧 Analyst: 风险评估计算", call: "risk.assess(incident_scale, sensor_data)" },
    { label: "📝 Reporter: 综合输出评估报告", call: "reporter.compile(findings)" },
  ],
};

/** 执行阶段：3步，Analyst 调用根据实际任务文本动态推导 */
function buildExecSteps(code: string, task: string): WorkflowStep[] {
  const t = task ?? "";
  const short = t.length > 18 ? t.slice(0, 18) + "…" : t;

  let analystLabel: string;
  let analystCall: string;

  if (/救护|医疗|伤员|病人|急救/.test(t)) {
    analystLabel = "🔧 Analyst: 调度急救资源";
    analystCall = `mcp.ems.dispatch(task="${short}")`;
  } else if (/封路|管控|封锁|疏散|交通|路口|信号/.test(t)) {
    analystLabel = "🔧 Analyst: 推送交通管控指令";
    analystCall = `mcp.traffic.control(task="${short}")`;
  } else if (/物资|调配|防护|设备|器材/.test(t)) {
    analystLabel = "🔧 Analyst: 下发物资调配单";
    analystCall = `mcp.logistics.dispatch(task="${short}")`;
  } else if (/停机|停产|工厂|储罐|阀门|关闭/.test(t)) {
    analystLabel = "🔧 Analyst: 触发应急处置";
    analystCall = `mcp.factory.control(task="${short}")`;
  } else if (/预警|发布|通报|扩散|警报/.test(t)) {
    analystLabel = "🔧 Analyst: 发布预警通报";
    analystCall = `mcp.alert.publish(task="${short}")`;
  } else {
    analystLabel = "🔧 Analyst: 执行任务指令";
    analystCall = `mcp.execute(dept=${code.toLowerCase()}, task="${short}")`;
  }

  return [
    { label: "🎯 Supervisor: 解析执行指令", call: "supervisor.parse_directive(step)" },
    { label: analystLabel, call: analystCall },
    { label: "📝 Reporter: 汇报执行结果", call: "reporter.report_result()" },
  ];
}

// ── 小工具 ───────────────────────────────────────────────────────────────────

function Avatar({ code }: { code: string }) {
  const color = agentColor(code);
  const icon = DEPT_ICONS[code];
  return (
    <div style={{
      width: 28, height: 28, borderRadius: 8, flexShrink: 0,
      background: `color-mix(in oklab, ${color} 18%, ${CC.panel2})`,
      border: `1px solid color-mix(in oklab, ${color} 30%, ${CC.line})`,
      color,
      display: "flex", alignItems: "center", justifyContent: "center",
      fontSize: icon ? 14 : 9, fontWeight: 700,
      fontFamily: icon ? "inherit" : "monospace",
      letterSpacing: icon ? 0 : "0.04em",
    }}>
      {icon ?? (AGENT_META[code]?.short ?? code).slice(0, 2)}
    </div>
  );
}

function AgentDot({ code }: { code: string }) {
  return (
    <span style={{
      width: 5, height: 5, borderRadius: "50%",
      background: agentColor(code), display: "inline-block", flexShrink: 0,
    }} />
  );
}

function Badge({ label, color }: { label: string; color?: string }) {
  const c = color ?? CC.muted2;
  return (
    <span style={{
      fontSize: 9, fontWeight: 700, letterSpacing: "0.06em",
      padding: "1px 6px", borderRadius: 4,
      background: `color-mix(in oklab, ${c} 14%, transparent)`,
      border: `1px solid color-mix(in oklab, ${c} 28%, transparent)`,
      color: c,
    }}>
      {label}
    </span>
  );
}

function ReplyTo({ agent, text }: { agent: string; text: string }) {
  const color = agentColor(agent);
  return (
    <div style={{
      borderLeft: `2px solid color-mix(in oklab, ${color} 50%, transparent)`,
      paddingLeft: 8, marginBottom: 8,
      fontSize: 11, color: CC.muted,
    }}>
      <span style={{ fontWeight: 600, color }}>@{AGENT_META[agent]?.name ?? agent}</span>
      {" · "}{text}
    </div>
  );
}

/** Timeline row 包装：头像 + 右侧内容 */
function TlRow({ code, badge, badgeColor, elapsed, children }: {
  code: string;
  badge?: string;
  badgeColor?: string;
  elapsed?: number;
  children: React.ReactNode;
}) {
  const color = agentColor(code);
  const meta = AGENT_META[code] ?? { name: code };
  return (
    <div style={{ display: "flex", gap: 10, padding: "3px 16px", alignItems: "flex-start" }}>
      <Avatar code={code} />
      <div style={{ flex: 1, minWidth: 0 }}>
        {/* 头部：点 + 名 + badge + 耗时 */}
        <div style={{
          fontSize: 11, marginBottom: 5,
          display: "flex", alignItems: "center", gap: 5,
        }}>
          <AgentDot code={code} />
          <span style={{ fontWeight: 600, color }}>{meta.name}</span>
          {badge && <Badge label={badge} color={badgeColor ?? color} />}
          {elapsed != null && (
            <span style={{ fontSize: 10, color: CC.muted, fontFamily: "monospace", marginLeft: "auto" }}>
              {(elapsed / 1000).toFixed(1)}s
            </span>
          )}
        </div>
        {children}
      </div>
    </div>
  );
}

/** 通用卡片容器 */
function Card({ stripe, children }: { stripe?: string; children: React.ReactNode }) {
  return (
    <div style={{
      padding: "10px 12px",
      borderRadius: "0 8px 8px 0",
      fontSize: 13, lineHeight: 1.7,
      color: CC.text2,
      background: CC.panel,
      border: `1px solid ${CC.line}`,
      borderLeft: stripe ? `2.5px solid ${stripe}` : `1px solid ${CC.line}`,
    }}>
      {children}
    </div>
  );
}

// ── 时间戳分隔线 ─────────────────────────────────────────────────────────────

function TimestampCard({ label }: { label: string }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 10,
      padding: "4px 16px", margin: "4px 0", opacity: 0.55,
    }}>
      <div style={{ flex: 1, height: 1, background: CC.lineSoft }} />
      <span style={{
        fontSize: 10, color: CC.muted, whiteSpace: "nowrap",
        fontFamily: "monospace", padding: "1px 8px",
        background: CC.bg2, border: `1px solid ${CC.line}`, borderRadius: 8,
      }}>{label}</span>
      <div style={{ flex: 1, height: 1, background: CC.lineSoft }} />
    </div>
  );
}

// ── 用户消息 ─────────────────────────────────────────────────────────────────

function UserMsgCard({ content, operator }: { content: string; operator?: string }) {
  return (
    <div style={{ padding: "3px 16px", display: "flex", justifyContent: "flex-end" }}>
      <div style={{ maxWidth: "72%" }}>
        {operator && (
          <div style={{
            fontSize: 10, color: CC.muted, textAlign: "right",
            marginBottom: 4, fontFamily: "monospace",
          }}>
            {operator}
          </div>
        )}
        <div style={{
          padding: "9px 13px", borderRadius: "10px 10px 3px 10px",
          background: CC.info,
          fontSize: 13, lineHeight: 1.65, color: "#fff",
        }}>
          {content}
        </div>
      </div>
    </div>
  );
}

// ── Orchestrator 推理卡 ──────────────────────────────────────────────────────

function OrchReasoningCard({ think_lines, summary, incident, dept_tasks }: {
  think_lines: string[];
  summary: string;
  incident?: string;
  dept_tasks?: Array<{ code: string; name: string; task: string }>;
}) {
  const plColor = agentColor("PL");

  // Fallback: parse think_lines if no dept_tasks provided
  const rows: Array<{ code: string; name: string; task: string }> = dept_tasks?.length
    ? dept_tasks
    : think_lines.filter((l) => l.startsWith("@")).map((line) => {
      const match = line.match(/^@([A-Z]+)\s+([\s\S]*)/);
      const code = match?.[1] ?? "PL";
      return { code, name: AGENT_META[code]?.name ?? code, task: match?.[2]?.trim() ?? line };
    });

  const count = rows.length;

  return (
    <TlRow code="PL" badge="PLANNER">
      <Card stripe={CC.agPl}>
        {/* ① 流程标题行 */}
        <div style={{ display: "flex", alignItems: "center", gap: 5, marginBottom: 8, flexWrap: "wrap" }}>
          {(
            [
              { label: "SOP 分析", color: plColor },
              null,
              { label: "任务拆解", color: CC.text },
              null,
              { label: `${count} 路并行子任务`, color: CC.info },
            ] as (null | { label: string; color: string })[]
          ).map((item, i) =>
            item === null ? (
              <span key={i} style={{ fontSize: 10, color: CC.muted2 }}>→</span>
            ) : (
              <span key={i} style={{
                fontSize: 10, fontFamily: "monospace", fontWeight: 700, color: item.color,
                background: `color-mix(in oklab, ${item.color} 10%, transparent)`,
                border: `1px solid color-mix(in oklab, ${item.color} 20%, transparent)`,
                padding: "1px 7px", borderRadius: 4,
              }}>{item.label}</span>
            )
          )}
        </div>

        {/* ② 事故标签 chip */}
        {incident && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 10 }}>
            <span style={{
              fontSize: 10.5, fontWeight: 600, padding: "2px 9px", borderRadius: 99,
              background: `color-mix(in oklab, ${CC.emerg} 10%, transparent)`,
              border: `1px solid color-mix(in oklab, ${CC.emerg} 22%, transparent)`,
              color: CC.emerg,
            }}>{incident}</span>
          </div>
        )}

        {/* ③ 部门任务行（新设计：emoji图标 + @DeptName + 任务文本独占一行） */}
        <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
          {rows.map((d, i) => {
            const color = agentColor(d.code);
            return (
              <div key={i} style={{
                display: "flex", gap: 9, alignItems: "flex-start",
                padding: "7px 9px", borderRadius: 7,
                background: `color-mix(in oklab, ${color} 6%, transparent)`,
                border: `1px solid color-mix(in oklab, ${color} 14%, transparent)`,
              }}>
                {/* emoji 图标 */}
                <div style={{
                  width: 30, height: 30, borderRadius: 7, flexShrink: 0, marginTop: 1,
                  background: `color-mix(in oklab, ${color} 16%, ${CC.panel2})`,
                  border: `1.5px solid color-mix(in oklab, ${color} 28%, ${CC.line})`,
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontSize: 14,
                }}>
                  {DEPT_ICONS[d.code] ?? "🏢"}
                </div>
                {/* 名称 + 任务 */}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 700, color, fontSize: 12, marginBottom: 3 }}>
                    @{d.name}
                  </div>
                  <div style={{ fontSize: 11.5, color: CC.text2, lineHeight: 1.5 }}>
                    {d.task.length > 70 ? d.task.slice(0, 70) + "…" : d.task}
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        {summary && rows.length > 0 && (
          <div style={{
            fontSize: 11, color: CC.muted, marginTop: 8,
            paddingTop: 6, borderTop: `1px solid ${CC.lineSoft}`,
          }}>
            {summary}
          </div>
        )}
      </Card>
    </TlRow>
  );
}

// ── Handoff 连接线 ───────────────────────────────────────────────────────────

function HandoffCard({ from, to, label, payload }: {
  from: string[]; to: string[]; label?: string; payload?: string;
}) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 6,
      padding: "0 28px", margin: "0",
      opacity: 0.55,
    }}>
      <div style={{ display: "flex", gap: 2 }}>
        {from.map((code) => (
          <span key={code} style={{
            fontSize: 8, fontFamily: "monospace", fontWeight: 600,
            color: agentColor(code),
          }}>{AGENT_META[code]?.short ?? code}</span>
        ))}
      </div>

      <div style={{ flex: 1, display: "flex", alignItems: "center", gap: 4 }}>
        <div style={{ flex: 1, height: 1, background: CC.lineSoft }} />
        {label && <span style={{ fontSize: 8, color: CC.muted2, whiteSpace: "nowrap" }}>{label}</span>}
        {payload && (
          <span style={{ fontSize: 8, color: CC.muted, whiteSpace: "nowrap" }}>{payload}</span>
        )}
        <div style={{ flex: 1, height: 1, background: CC.lineSoft }} />
        <div style={{
          width: 0, height: 0,
          borderTop: "2px solid transparent", borderBottom: "2px solid transparent",
          borderLeft: `3px solid ${CC.muted2}`,
        }} />
      </div>

      <div style={{ display: "flex", gap: 2 }}>
        {to.map((code) => (
          <span key={code} style={{
            fontSize: 8, fontFamily: "monospace", fontWeight: 600,
            color: agentColor(code),
          }}>{AGENT_META[code]?.short ?? code}</span>
        ))}
      </div>
    </div>
  );
}

// ── 执行计划（顺序甘特）─────────────────────────────────────────────────────

function DispatchPlanCard({ agents, progress }: {
  agents: { code: string; name: string; task: string }[];
  progress: ("done" | "running" | "error" | "idle")[];
}) {
  const done = progress.filter((s) => s === "done").length;
  const total = agents.length;
  const runningCount = progress.filter((s) => s === "running").length;

  return (
    <TlRow code="PL" badge="执行计划" badgeColor={CC.ok}>
      <Card stripe={CC.ok}>
        {/* 标题行 */}
        <div style={{
          display: "flex", alignItems: "center", gap: 8,
          marginBottom: 10, paddingBottom: 7,
          borderBottom: `1px solid ${CC.lineSoft}`,
        }}>
          <span style={{ fontSize: 11, fontWeight: 600, color: CC.text }}>
            顺序执行计划
          </span>
          <span style={{
            fontSize: 9.5, fontFamily: "monospace",
            color: CC.ok,
            background: `color-mix(in oklab, ${CC.ok} 10%, transparent)`,
            border: `1px solid color-mix(in oklab, ${CC.ok} 25%, transparent)`,
            padding: "1px 6px", borderRadius: 4,
          }}>
            {done}/{total} 步完成
          </span>
          {runningCount > 0 && (
            <span style={{
              fontSize: 9.5, fontFamily: "monospace",
              color: CC.warn,
              background: `color-mix(in oklab, ${CC.warn} 10%, transparent)`,
              border: `1px solid color-mix(in oklab, ${CC.warn} 25%, transparent)`,
              padding: "1px 6px", borderRadius: 4,
            }}>
              ⟳ {runningCount > 1 ? `${runningCount} 步并行执行中` : "执行中"}
            </span>
          )}
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {agents.map((agent, i) => {
            const st = progress[i] ?? "idle";
            const color = agentColor(agent.code);
            const isActive = st === "running";
            const isDone = st === "done";
            const isError = st === "error";
            const isPending = st === "idle";

            const rowBg = isActive
              ? `color-mix(in oklab, ${CC.warn} 6%, transparent)`
              : isDone
                ? `color-mix(in oklab, ${CC.ok} 5%, transparent)`
                : "transparent";

            return (
              <div key={i} style={{
                display: "flex", alignItems: "flex-start", gap: 8,
                padding: "5px 7px", borderRadius: 6,
                background: rowBg,
                border: `1px solid ${isActive
                  ? `color-mix(in oklab, ${CC.warn} 22%, transparent)`
                  : isDone
                    ? `color-mix(in oklab, ${CC.ok} 16%, transparent)`
                    : CC.lineSoft}`,
                transition: "all 0.3s ease",
              }}>
                {/* 步骤序号 */}
                <div style={{
                  width: 20, height: 20, borderRadius: 5, flexShrink: 0, marginTop: 2,
                  background: isDone
                    ? `color-mix(in oklab, ${CC.ok} 20%, ${CC.panel2})`
                    : isActive
                      ? `color-mix(in oklab, ${CC.warn} 18%, ${CC.panel2})`
                      : `color-mix(in oklab, ${CC.line} 60%, ${CC.panel2})`,
                  border: `1px solid ${isDone ? `color-mix(in oklab, ${CC.ok} 35%, transparent)` : isActive ? `color-mix(in oklab, ${CC.warn} 35%, transparent)` : CC.line}`,
                  color: isDone ? CC.ok : isActive ? CC.warn : CC.muted2,
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontFamily: "monospace", fontSize: 9, fontWeight: 700,
                }}>
                  {isDone ? "✓" : isError ? "✕" : i + 1}
                </div>
                {/* 部门 emoji 图标 */}
                <div style={{
                  width: 22, height: 22, borderRadius: 5, flexShrink: 0, marginTop: 2,
                  background: `color-mix(in oklab, ${color} 14%, ${CC.panel2})`,
                  border: `1px solid color-mix(in oklab, ${color} 24%, ${CC.line})`,
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontSize: 12,
                }}>
                  {DEPT_ICONS[agent.code] ?? agent.code.slice(0, 2)}
                </div>
                {/* 部门名 + 任务描述 */}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <span style={{
                    fontSize: 11, fontWeight: 500,
                    color: isActive ? CC.text : isDone ? CC.text : isPending ? CC.text2 : CC.muted,
                    display: "block",
                  }}>
                    {agent.name}
                  </span>
                  <span style={{
                    fontSize: 10.5,
                    color: isActive ? CC.text2 : isDone ? CC.muted : isPending ? CC.muted : CC.muted2,
                    display: "-webkit-box",
                    WebkitLineClamp: 2,
                    WebkitBoxOrient: "vertical",
                    overflow: "hidden",
                    lineHeight: 1.45,
                  }}>
                    {agent.task}
                  </span>
                </div>
                {/* 状态文字 */}
                <span style={{
                  flex: "0 0 52px", textAlign: "right", fontSize: 10, marginTop: 2,
                  color: isDone ? CC.ok : isActive ? CC.warn : isError ? CC.emerg : CC.muted2,
                }}>
                  {isDone ? "✓ 完成" : isActive ? "⟳ 执行中" : isError ? "✕ 错误" : "待命"}
                </span>
              </div>
            );
          })}
        </div>

        {/* 总进度条 */}
        <div style={{ marginTop: 8, height: 3, borderRadius: 2, background: CC.lineSoft, overflow: "hidden" }}>
          <div style={{
            height: "100%", width: `${(done / total) * 100}%`,
            background: CC.ok, borderRadius: 2, transition: "width 0.5s ease",
          }} />
        </div>
      </Card>
      <style>{`
        @keyframes gantt-slide {
          0%   { background-position: 200% 0; }
          100% { background-position: -200% 0; }
        }
      `}</style>
    </TlRow>
  );
}

// ── 行内引用角标 [N] → 可点击徽章 ──────────────────────────────────────────────

function CiteBadge({ refNum, active, onClick }: {
  refNum: number;
  active?: boolean;
  onClick?: (ref: number) => void;
}) {
  return (
    <button
      onClick={() => onClick?.(refNum)}
      style={{
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        minWidth: 18, height: 18, borderRadius: 4, padding: "0 3px",
        border: active
          ? `1px solid color-mix(in oklab, ${CC.info} 60%, transparent)`
          : `1px solid color-mix(in oklab, ${CC.info} 28%, transparent)`,
        background: active
          ? `color-mix(in oklab, ${CC.info} 30%, transparent)`
          : `color-mix(in oklab, ${CC.info} 14%, transparent)`,
        color: CC.info, fontSize: 10, fontWeight: 700,
        fontFamily: "monospace", cursor: "pointer",
        verticalAlign: "middle", margin: "0 1.5px", lineHeight: 1,
        transition: "background 0.15s",
      }}
    >
      {refNum}
    </button>
  );
}

function injectCiteBadges(
  text: string,
  activeRef: number | null,
  onClick?: (ref: number) => void,
): React.ReactNode {
  const parts = text.split(/(\[\d+\])/);
  if (parts.length === 1) return text;
  return parts.map((p, i) => {
    const m = p.match(/^\[(\d+)\]$/);
    if (!m) return p || null;
    const ref = parseInt(m[1]);
    return <CiteBadge key={i} refNum={ref} active={activeRef === ref} onClick={onClick} />;
  });
}

// ── MCP 实时数据角标 [M1] → 青色徽章 ───────────────────────────────────────────

function McpCiteBadge({ refNum, active, onClick }: {
  refNum: number;
  active?: boolean;
  onClick?: (ref: number) => void;
}) {
  return (
    <button
      onClick={() => onClick?.(refNum)}
      style={{
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        minWidth: 22, height: 18, borderRadius: 4, padding: "0 3px",
        border: active
          ? `1px solid color-mix(in oklab, ${MCP_COLOR} 60%, transparent)`
          : `1px solid color-mix(in oklab, ${MCP_COLOR} 28%, transparent)`,
        background: active
          ? `color-mix(in oklab, ${MCP_COLOR} 30%, transparent)`
          : `color-mix(in oklab, ${MCP_COLOR} 14%, transparent)`,
        color: MCP_COLOR, fontSize: 9, fontWeight: 700,
        fontFamily: "monospace", cursor: "pointer",
        verticalAlign: "middle", margin: "0 1.5px", lineHeight: 1,
        transition: "background 0.15s",
        letterSpacing: "0.02em",
      }}
    >
      M{refNum}
    </button>
  );
}

function injectMcpBadges(
  text: string,
  activeMcpRef: number | null,
  onMcpClick?: (ref: number) => void,
): React.ReactNode {
  // Match [M1], [M2], etc.
  const parts = text.split(/(\[M\d+\])/);
  if (parts.length === 1) return text;
  return parts.map((p, i) => {
    const m = p.match(/^\[M(\d+)\]$/);
    if (!m) return p || null;
    const ref = parseInt(m[1]);
    return <McpCiteBadge key={i} refNum={ref} active={activeMcpRef === ref} onClick={onMcpClick} />;
  });
}

function processChildren(
  children: React.ReactNode,
  activeRef: number | null,
  onClick?: (ref: number) => void,
  activeMcpRef?: number | null,
  onMcpClick?: (ref: number) => void,
): React.ReactNode {
  if (typeof children === "string") {
    // First inject RAG [N] badges, then MCP [MN] badges
    const withRag = injectCiteBadges(children, activeRef, onClick);
    if (!onMcpClick || typeof withRag !== "string") {
      // If RAG injection produced ReactNodes, we can't further inject MCP — do MCP first on original
      const withMcp = injectMcpBadges(children, activeMcpRef ?? null, onMcpClick);
      if (withMcp === children) return withRag; // no MCP markers, return RAG result
      // Both exist: process original string for both
      const parts = children.split(/(\[\d+\]|\[M\d+\])/);
      return parts.map((p, i) => {
        const rag = p.match(/^\[(\d+)\]$/);
        if (rag) return <CiteBadge key={i} refNum={parseInt(rag[1])} active={activeRef === parseInt(rag[1])} onClick={onClick} />;
        const mcp = p.match(/^\[M(\d+)\]$/);
        if (mcp) return <McpCiteBadge key={i} refNum={parseInt(mcp[1])} active={activeMcpRef === parseInt(mcp[1])} onClick={onMcpClick} />;
        return p || null;
      });
    }
    return withRag;
  }
  if (Array.isArray(children)) {
    return children.map((child, i) =>
      typeof child === "string"
        ? <React.Fragment key={i}>{processChildren(child, activeRef, onClick, activeMcpRef, onMcpClick)}</React.Fragment>
        : child
    );
  }
  return children;
}

// ── 引用来源列表（dark 主题，与普通对话 CitationList 视觉一致）─────────────────

function DeptCitationList({ citations, activeRef, onCitationClick }: {
  citations: Citation[];
  activeRef?: number | null;
  onCitationClick?: (ref: number) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  if (!citations || citations.length === 0) return null;

  return (
    <div style={{ marginTop: 8, paddingTop: 6, borderTop: `1px solid ${CC.lineSoft}` }}>
      {/* 折叠标题行 */}
      <button
        onClick={() => setExpanded((v) => !v)}
        style={{
          display: "flex", alignItems: "center", gap: 6,
          width: "100%", textAlign: "left", background: "none", border: "none",
          cursor: "pointer", padding: "2px 0", marginBottom: expanded ? 6 : 0,
        }}
      >
        <span style={{ fontSize: 9, color: CC.muted2 }}>{expanded ? "▼" : "▶"}</span>
        <span style={{
          fontSize: 9.5, fontWeight: 700, letterSpacing: "0.07em",
          color: CC.muted, fontFamily: "monospace", textTransform: "uppercase" as const,
        }}>
          引用来源
        </span>
        <span style={{
          fontSize: 9, padding: "0px 5px", borderRadius: 3,
          background: `color-mix(in oklab, ${CC.info} 12%, transparent)`,
          border: `1px solid color-mix(in oklab, ${CC.info} 22%, transparent)`,
          color: CC.info, fontFamily: "monospace",
        }}>
          {citations.length}
        </span>
      </button>

      {expanded && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {citations.map((c) => {
            const isActive = activeRef === c.ref;
            return (
              <div
                key={c.chunk_id}
                onClick={() => onCitationClick?.(c.ref)}
                style={{
                  display: "flex", gap: 8, alignItems: "flex-start",
                  padding: "5px 8px", borderRadius: 6, cursor: onCitationClick ? "pointer" : "default",
                  background: isActive
                    ? `color-mix(in oklab, ${CC.info} 10%, transparent)`
                    : `color-mix(in oklab, ${CC.bg2} 80%, transparent)`,
                  border: isActive
                    ? `1px solid color-mix(in oklab, ${CC.info} 40%, transparent)`
                    : `1px solid ${CC.line}`,
                  transition: "all 0.15s",
                }}
              >
                <span style={{
                  width: 20, height: 20, borderRadius: 4, flexShrink: 0,
                  background: isActive
                    ? `color-mix(in oklab, ${CC.info} 30%, transparent)`
                    : `color-mix(in oklab, ${CC.info} 18%, transparent)`,
                  border: `1px solid color-mix(in oklab, ${CC.info} ${isActive ? 50 : 28}%, transparent)`,
                  color: CC.info, fontSize: 10, fontWeight: 700,
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontFamily: "monospace", marginTop: 1,
                }}>
                  {c.ref}
                </span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{
                    fontSize: 11.5, fontWeight: 600, color: CC.text,
                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                  }}>
                    {c.source_file}
                  </div>
                  {c.section_path && (
                    <div style={{
                      fontSize: 10.5, color: CC.muted,
                      overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                    }}>
                      {c.section_path}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── MCP 实时数据来源列表 ─────────────────────────────────────────────────────

function McpSourceList({ sources, activeMcpRef, onMcpClick }: {
  sources: McpSource[];
  activeMcpRef?: number | null;
  onMcpClick?: (ref: number) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  if (!sources || sources.length === 0) return null;

  return (
    <div style={{ marginTop: 6, paddingTop: 6, borderTop: `1px solid ${CC.lineSoft}` }}>
      <button
        onClick={() => setExpanded((v) => !v)}
        style={{
          display: "flex", alignItems: "center", gap: 6,
          width: "100%", textAlign: "left", background: "none", border: "none",
          cursor: "pointer", padding: "2px 0", marginBottom: expanded ? 6 : 0,
        }}
      >
        <span style={{ fontSize: 9, color: CC.muted2 }}>{expanded ? "▼" : "▶"}</span>
        <span style={{
          fontSize: 9.5, fontWeight: 700, letterSpacing: "0.07em",
          color: CC.muted, fontFamily: "monospace", textTransform: "uppercase" as const,
        }}>
          MCP 实时数据
        </span>
        <span style={{
          fontSize: 9, padding: "0px 5px", borderRadius: 3,
          background: `color-mix(in oklab, ${MCP_COLOR} 12%, transparent)`,
          border: `1px solid color-mix(in oklab, ${MCP_COLOR} 22%, transparent)`,
          color: MCP_COLOR, fontFamily: "monospace",
        }}>
          {sources.length}
        </span>
      </button>

      {expanded && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {sources.map((s) => {
            const isActive = activeMcpRef === s.idx;
            return (
              <div
                key={s.idx}
                onClick={() => onMcpClick?.(s.idx)}
                style={{
                  display: "flex", gap: 8, alignItems: "flex-start",
                  padding: "5px 8px", borderRadius: 6,
                  cursor: onMcpClick ? "pointer" : "default",
                  background: isActive
                    ? `color-mix(in oklab, ${MCP_COLOR} 10%, transparent)`
                    : `color-mix(in oklab, ${CC.bg2} 80%, transparent)`,
                  border: isActive
                    ? `1px solid color-mix(in oklab, ${MCP_COLOR} 40%, transparent)`
                    : `1px solid ${CC.line}`,
                  transition: "all 0.15s",
                }}
              >
                {/* M编号徽章 */}
                <span style={{
                  width: 24, height: 20, borderRadius: 4, flexShrink: 0,
                  background: isActive
                    ? `color-mix(in oklab, ${MCP_COLOR} 30%, transparent)`
                    : `color-mix(in oklab, ${MCP_COLOR} 18%, transparent)`,
                  border: `1px solid color-mix(in oklab, ${MCP_COLOR} ${isActive ? 50 : 28}%, transparent)`,
                  color: MCP_COLOR, fontSize: 9, fontWeight: 700,
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontFamily: "monospace", marginTop: 1,
                }}>
                  M{s.idx}
                </span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  {/* 工具名（中文 + 原始名） */}
                  <div style={{ display: "flex", alignItems: "baseline", gap: 5, marginBottom: 2 }}>
                    <span style={{ fontSize: 11, fontWeight: 600, color: MCP_COLOR }}>
                      {getMcpToolLabel(s.tool_name)}
                    </span>
                    <span style={{ fontSize: 9.5, color: MCP_COLOR, opacity: 0.6, fontFamily: "monospace" }}>
                      {s.tool_name}()
                    </span>
                  </div>
                  {/* 返回值预览 */}
                  <div style={{
                    fontSize: 10.5, color: CC.muted,
                    overflow: "hidden", textOverflow: "ellipsis",
                    display: "-webkit-box",
                    WebkitLineClamp: 2,
                    WebkitBoxOrient: "vertical" as const,
                    lineHeight: 1.4,
                    fontFamily: "monospace",
                  }}>
                    {s.key_result}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── Markdown 渲染（部门报告正文用）──────────────────────────────────────────

const MD_PREVIEW_LEN = 300; // 折叠时最多显示的字符数

function DeptMd({ text, activeRef, onCitationClick, activeMcpRef, onMcpClick }: {
  text: string;
  activeRef?: number | null;
  onCitationClick?: (ref: number) => void;
  activeMcpRef?: number | null;
  onMcpClick?: (ref: number) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const isLong = text.length > MD_PREVIEW_LEN;
  const shown = (!isLong || expanded) ? text : text.slice(0, MD_PREVIEW_LEN) + "…";

  // Override p/li to inject inline [N] RAG badges and [MN] MCP badges
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const mdComponents: any = (onCitationClick || onMcpClick) ? {
    p: ({ children }: { children?: React.ReactNode }) =>
      <p>{processChildren(children, activeRef ?? null, onCitationClick, activeMcpRef ?? null, onMcpClick)}</p>,
    li: ({ children }: { children?: React.ReactNode }) =>
      <li>{processChildren(children, activeRef ?? null, onCitationClick, activeMcpRef ?? null, onMcpClick)}</li>,
  } : undefined;

  return (
    <div style={{ fontSize: 12.5, lineHeight: 1.7, color: CC.text }}>
      <style>{`
        .dept-md h1,.dept-md h2,.dept-md h3,.dept-md h4 {
          font-size:12.5px; font-weight:700; color:${CC.text};
          margin:6px 0 2px; line-height:1.5;
        }
        .dept-md p  { margin:2px 0; }
        .dept-md ul,.dept-md ol { margin:2px 0; padding-left:16px; }
        .dept-md li { margin:1px 0; }
        .dept-md strong { font-weight:700; color:${CC.text}; }
        .dept-md em { font-style:italic; }
        .dept-md code { font-family:monospace; font-size:11px;
          background:color-mix(in oklab,${CC.line} 60%,transparent);
          padding:0 3px; border-radius:3px; }
        .dept-md hr { border:none; border-top:1px solid ${CC.lineSoft}; margin:6px 0; }
      `}</style>
      <div className="dept-md">
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>{shown}</ReactMarkdown>
      </div>
      {isLong && (
        <button
          onClick={() => setExpanded((v) => !v)}
          style={{
            marginTop: 4, fontSize: 11, color: CC.muted2,
            background: "none", border: "none", cursor: "pointer", padding: 0,
          }}
        >
          {expanded ? "▲ 收起" : "▼ 展开全文"}
        </button>
      )}
    </div>
  );
}

// ── PL 思考中卡（计划生成 / 聚合阶段）──────────────────────────────────────────

function PlThinkingCard({ message }: { message: string }) {
  return (
    <TlRow code="PL" badge="PLANNER">
      <Card stripe={CC.agPl}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{
            width: 12, height: 12, borderRadius: "50%", flexShrink: 0,
            border: `1.5px solid color-mix(in oklab, ${CC.agPl} 28%, transparent)`,
            borderTopColor: CC.agPl,
            animation: "spin 0.75s linear infinite",
            display: "inline-block",
          }} />
          <span style={{ fontSize: 12.5, color: CC.text2, fontStyle: "italic" }}>
            {message}
          </span>
        </div>
        <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      </Card>
    </TlRow>
  );
}

// ── 部门报告卡 ───────────────────────────────────────────────────────────────

function DeptReportCard({
  code, name: _name, task, status, phase, elapsed_ms, summary, kvs, citations, mcp_sources, err_detail,
}: Extract<CommandCard, { type: "dept_report" }>) {
  const toolSteps = phase === "exec"
    ? buildExecSteps(code, task)
    : (DEPT_RESEARCH_STEPS[code] ?? []);
  // 基础时间翻倍；每步再乘以 [0.5, 1.8] 随机系数，让并发部门进度明显错开
  const baseStepMs = phase === "exec" ? 4500 : 5800;
  const [stepIdx, setStepIdx] = useState(0);
  const [toolExpanded, setToolExpanded] = useState(true); // expanded while running
  const cancelRef = useRef(false);

  // Animate tool steps while running — chained setTimeout with per-step jitter
  useEffect(() => {
    if (status !== "running" || toolSteps.length === 0) return;
    cancelRef.current = false;
    setStepIdx(0);

    function scheduleNext(idx: number) {
      if (cancelRef.current || idx >= toolSteps.length - 1) return;
      const jitter = 0.25 + Math.random() * 2.0; // 0.25x ~ 2.25x
      setTimeout(() => {
        if (cancelRef.current) return;
        setStepIdx(idx + 1);
        scheduleNext(idx + 1);
      }, baseStepMs * jitter);
    }

    scheduleNext(0);
    return () => { cancelRef.current = true; };
  }, [status]); // eslint-disable-line react-hooks/exhaustive-deps

  // Collapse tool steps when done/error
  useEffect(() => {
    if (status === "done" || status === "error") {
      cancelRef.current = true;
      setToolExpanded(false);
    }
  }, [status]);

  const [activeRef, setActiveRef] = useState<number | null>(null);
  const [activeMcpRef, setActiveMcpRef] = useState<number | null>(null);

  function handleCitationClick(ref: number) {
    setActiveRef((prev) => (prev === ref ? null : ref));
  }
  function handleMcpClick(ref: number) {
    setActiveMcpRef((prev) => (prev === ref ? null : ref));
  }

  const isDone = status === "done" || status === "error";
  const stripe = status === "done" ? CC.ok : status === "running" ? CC.warn : CC.emerg;
  const badge = status === "done" ? `DONE · ${elapsed_ms ? (elapsed_ms / 1000).toFixed(1) + "s" : "✓"}`
    : status === "running" ? "RUNNING"
      : "ERROR";

  // KV cards: only entries with meaningful labels
  const kvCards = kvs?.filter(({ k }) => k && k !== "·") ?? [];

  return (
    <TlRow code={code} badge={badge} badgeColor={stripe} elapsed={status === "done" ? elapsed_ms : undefined}>
      <Card stripe={stripe}>
        <ReplyTo agent="PL" text={`收到，${task}`} />

        {/* ── 工具调用区域 ────────────────────────────────────────────── */}
        {toolSteps.length > 0 && (
          <div style={{ marginBottom: 8 }}>
            {/* Toggle 标题 */}
            <button
              onClick={() => setToolExpanded((v) => !v)}
              style={{
                display: "flex", alignItems: "center", gap: 7,
                width: "100%", textAlign: "left",
                padding: "5px 9px", borderRadius: 7,
                background: toolExpanded
                  ? `color-mix(in oklab, ${CC.line} 55%, transparent)`
                  : `color-mix(in oklab, ${CC.line} 28%, transparent)`,
                border: `1px solid ${CC.line}`,
                cursor: "pointer", color: CC.text2, fontSize: 12,
              }}
            >
              <span style={{ fontSize: 9, color: CC.muted2 }}>{toolExpanded ? "▼" : "▶"}</span>
              <span style={{ fontWeight: 500 }}>
                {status === "running" ? "工具调用中…" : "查看工具调用过程"}
              </span>
              {status === "running" && (
                <span style={{
                  width: 10, height: 10, borderRadius: "50%",
                  border: `1.5px solid color-mix(in oklab, ${CC.warn} 30%, transparent)`,
                  borderTopColor: CC.warn,
                  animation: "spin 0.75s linear infinite",
                  display: "inline-block", marginLeft: "auto", flexShrink: 0,
                }} />
              )}
            </button>

            {/* 工具步骤列表 */}
            {toolExpanded && (
              <div style={{
                padding: "8px 10px",
                border: `1px solid ${CC.line}`, borderTop: "none",
                borderRadius: "0 0 7px 7px",
                background: `color-mix(in oklab, ${CC.bg2} 60%, transparent)`,
              }}>
                {toolSteps.map((step: WorkflowStep, i: number) => {
                  const s = isDone ? "done"
                    : i < stepIdx ? "done"
                      : i === stepIdx ? "running"
                        : "pending";
                  const sc = s === "done" ? CC.ok : s === "running" ? CC.warn : CC.muted2;
                  const icon = s === "done" ? "✓" : s === "running" ? "○" : "···";

                  return (
                    <div key={i} style={{ marginBottom: i < toolSteps.length - 1 ? 9 : 0 }}>
                      {/* 标题行 */}
                      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 3 }}>
                        <span style={{ fontSize: 10, fontWeight: 700, color: sc, width: 16, textAlign: "center", flexShrink: 0 }}>
                          {icon}
                        </span>
                        <span style={{ fontSize: 11.5, color: CC.text, fontWeight: 500 }}>
                          {step.label}
                        </span>
                        {s === "done" && step.result && (
                          <span style={{
                            marginLeft: "auto", fontSize: 10, fontWeight: 600,
                            color: CC.info, padding: "1px 6px", borderRadius: 4,
                            background: `color-mix(in oklab, ${CC.info} 10%, transparent)`,
                            border: `1px solid color-mix(in oklab, ${CC.info} 20%, transparent)`,
                            flexShrink: 0,
                          }}>
                            📄 {step.result}
                          </span>
                        )}
                      </div>
                      {/* 调用代码 */}
                      <div style={{
                        marginLeft: 22,
                        fontFamily: "monospace", fontSize: 10.5,
                        color: s === "running" ? CC.warn : CC.muted2,
                        padding: "2px 7px", borderRadius: 4,
                        background: `color-mix(in oklab, ${sc} 7%, transparent)`,
                        border: `1px solid color-mix(in oklab, ${sc} 13%, transparent)`,
                      }}>
                        {step.call}
                      </div>
                    </div>
                  );
                })}

                {/* 进度条（仅 running 状态） */}
                {status === "running" && (
                  <div style={{ marginTop: 9, height: 3, borderRadius: 2, background: CC.lineSoft, overflow: "hidden" }}>
                    <div style={{
                      height: "100%",
                      width: `${((stepIdx + 0.6) / toolSteps.length) * 100}%`,
                      background: `linear-gradient(90deg, ${CC.ok}, ${CC.warn})`,
                      borderRadius: 2, transition: "width 0.4s ease",
                    }} />
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* ── 结果区域（done/error 时才展示）────────────────────────── */}
        {isDone && summary && status !== "error" && (
          <DeptMd
            text={summary}
            activeRef={activeRef} onCitationClick={handleCitationClick}
            activeMcpRef={activeMcpRef} onMcpClick={handleMcpClick}
          />
        )}

        {/* KV 卡片网格（有明确标签的） */}
        {isDone && kvCards.length > 0 && (
          <div style={{
            display: "grid",
            gridTemplateColumns: `repeat(${Math.min(kvCards.length, 3)}, 1fr)`,
            gap: 6, marginTop: 8,
          }}>
            {kvCards.map(({ k, v }, i) => (
              <div key={i} style={{
                padding: "5px 8px", borderRadius: 6,
                background: `color-mix(in oklab, ${CC.line} 40%, transparent)`,
                border: `1px solid ${CC.line}`,
              }}>
                <div style={{ fontSize: 9.5, color: CC.muted, marginBottom: 1 }}>{k}</div>
                <div style={{ fontSize: 13, fontWeight: 700, color: CC.text }}>{v}</div>
              </div>
            ))}
          </div>
        )}

        {/* MCP 实时数据来源（青色，区别于 RAG 蓝色引用） */}
        {isDone && <McpSourceList sources={mcp_sources ?? []} activeMcpRef={activeMcpRef} onMcpClick={handleMcpClick} />}

        {/* 引用来源（KB 文献引用卡，与普通对话 CitationList 一致） */}
        {isDone && <DeptCitationList citations={citations ?? []} activeRef={activeRef} onCitationClick={handleCitationClick} />}

        {/* 错误详情 */}
        {status === "error" && err_detail && (
          <div style={{
            marginTop: 8, padding: "6px 9px",
            background: `color-mix(in oklab, ${CC.emerg} 8%, transparent)`,
            border: `1px solid color-mix(in oklab, ${CC.emerg} 22%, transparent)`,
            borderRadius: 5, fontFamily: "monospace", fontSize: 11, color: CC.emerg, lineHeight: 1.5,
          }}>
            {err_detail}
          </div>
        )}
      </Card>
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </TlRow>
  );
}

// ── HITL 锚点卡 ──────────────────────────────────────────────────────────────

function HitlAnchorCard({ message }: { message: string }) {
  return (
    <div style={{ padding: "3px 16px" }}>
      <div style={{
        borderRadius: 8,
        border: `1px dashed color-mix(in oklab, ${CC.warn} 40%, transparent)`,
        background: `color-mix(in oklab, ${CC.warn} 6%, ${CC.panel})`,
        padding: "8px 12px",
        display: "flex", alignItems: "center", gap: 10,
      }}>
        <span style={{
          width: 20, height: 20, borderRadius: 5, flexShrink: 0,
          background: `color-mix(in oklab, ${CC.warn} 20%, transparent)`,
          color: CC.warn, display: "flex", alignItems: "center", justifyContent: "center",
          fontWeight: 700, fontSize: 12,
        }}>!</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 10, color: CC.muted, fontFamily: "monospace", marginBottom: 2 }}>
            HITL · 人工审批
          </div>
          <div style={{ fontSize: 12, color: CC.text, fontWeight: 500 }}>{message}</div>
        </div>
        <a href="#hitl-bar" style={{
          fontSize: 10, color: CC.warn, textDecoration: "none",
          padding: "2px 7px", borderRadius: 4,
          border: `1px solid color-mix(in oklab, ${CC.warn} 30%, transparent)`,
          flexShrink: 0,
        }}>↑ 回到决策条</a>
      </div>
    </div>
  );
}

// ── 主导出 ───────────────────────────────────────────────────────────────────

export function CardRenderer({ card }: { card: CommandCard }) {
  switch (card.type) {
    case "timestamp": return <TimestampCard label={card.label} />;
    case "user_msg": return <UserMsgCard content={card.content} operator={card.operator} />;
    case "pl_thinking": return <PlThinkingCard message={card.message} />;
    case "orch_reasoning": return <OrchReasoningCard think_lines={card.think_lines} summary={card.summary} incident={card.incident} dept_tasks={card.dept_tasks} />;
    case "handoff": return <HandoffCard from={card.from} to={card.to} label={card.label} payload={card.payload} />;
    case "dispatch_plan": return <DispatchPlanCard agents={card.agents} progress={card.progress} />;
    case "dept_report": return <DeptReportCard  {...card} />;
    case "hitl_anchor": return <HitlAnchorCard message={card.message} />;
    default: return null;
  }
}
