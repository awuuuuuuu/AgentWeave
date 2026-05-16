"use client";

import {
  IconTournament,
  IconCrown,
  IconSearch,
  IconChartBar,
  IconShieldCheck,
  IconBrain,
  IconArchive,
  IconClipboardList,
} from "@tabler/icons-react";
import type { AgentName } from "@/lib/agent-api";

// ── Agent 视觉配置 ────────────────────────────────────────────────────────────

const AGENT_CONFIG: Record<
  string,
  { icon: React.ReactNode; dotColor: string; iconColor: string; desc: string }
> = {
  supervisor: {
    icon: <IconCrown size={15} />,
    dotColor: "#378ADD",
    iconColor: "#378ADD",
    desc: "调度与决策",
  },
  researcher: {
    icon: <IconSearch size={15} />,
    dotColor: "#1D9E75",
    iconColor: "#1D9E75",
    desc: "知识库检索 + 自校正",
  },
  analyst: {
    icon: <IconChartBar size={15} />,
    dotColor: "#7F77DD",
    iconColor: "#7F77DD",
    desc: "数据建模分析",
  },
  reporter: {
    icon: <IconClipboardList size={15} />,
    dotColor: "#3ABFB4",
    iconColor: "#3ABFB4",
    desc: "整合结论 + 引用溯源",
  },
  hitl: {
    icon: <IconShieldCheck size={15} />,
    dotColor: "#E24B4A",
    iconColor: "#E24B4A",
    desc: "人工审批节点",
  },
  memory_inject: {
    icon: <IconBrain size={15} />,
    dotColor: "#5F5E5A",
    iconColor: "#5F5E5A",
    desc: "上下文注入",
  },
  memory_save: {
    icon: <IconArchive size={15} />,
    dotColor: "#5F5E5A",
    iconColor: "#5F5E5A",
    desc: "结果归档",
  },
};

// 固定展示顺序
const AGENTS_ORDER: AgentName[] = [
  "supervisor",
  "researcher",
  "analyst",
  "reporter",
  "hitl",
];
const MEMORY_ORDER: AgentName[] = ["memory_inject", "memory_save"];

// ── Props ─────────────────────────────────────────────────────────────────────

interface AgentSidebarProps {
  activeAgent: AgentName | null;   // 当前正在运行的 agent
  sessionId: string;
  isRunning: boolean;
}

// ── 组件 ──────────────────────────────────────────────────────────────────────

export function AgentSidebar({ activeAgent, sessionId, isRunning }: AgentSidebarProps) {
  const shortId = sessionId.slice(-4).toUpperCase();

  return (
    <aside
      style={{
        width: 220,
        background: "#0d1117",
        flexShrink: 0,
        display: "flex",
        flexDirection: "column",
        borderRight: "0.5px solid rgba(255,255,255,0.07)",
      }}
    >
      {/* 头部 */}
      <div
        style={{
          padding: "16px 15px 12px",
          borderBottom: "0.5px solid rgba(255,255,255,0.07)",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 7,
            fontSize: 14,
            fontWeight: 500,
            color: "#e2e6f0",
          }}
        >
          <IconTournament size={16} color="#85B7EB" />
          Agent Studio
        </div>
        <div style={{ fontSize: 11, color: "#4a5568", marginTop: 3 }} suppressHydrationWarning>
          会话 #{shortId} · {isRunning ? "运行中" : "待命"}
        </div>
      </div>

      {/* Agents 列表 */}
      <SidebarSection label="Agents">
        {AGENTS_ORDER.map((name) => (
          <AgentRow
            key={name}
            name={name}
            active={activeAgent === name}
            config={AGENT_CONFIG[name]}
          />
        ))}
      </SidebarSection>

      {/* Memory 列表 */}
      <SidebarSection label="Memory">
        {MEMORY_ORDER.map((name) => (
          <AgentRow
            key={name}
            name={name}
            active={activeAgent === name}
            config={AGENT_CONFIG[name]}
          />
        ))}
      </SidebarSection>

      {/* 页脚状态 */}
      <div
        style={{
          marginTop: "auto",
          padding: "10px 15px",
          borderTop: "0.5px solid rgba(255,255,255,0.07)",
          fontSize: 11,
          color: "#4a5568",
          display: "flex",
          alignItems: "center",
          gap: 6,
        }}
      >
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: isRunning ? "#EF9F27" : "#1D9E75",
            display: "inline-block",
            flexShrink: 0,
          }}
        />
        {isRunning ? "Agent 运行中…" : "待命中，发送问题启动"}
      </div>
    </aside>
  );
}

// ── 子组件 ────────────────────────────────────────────────────────────────────

function SidebarSection({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <>
      <div
        style={{
          padding: "12px 13px 6px",
          fontSize: 10,
          color: "#4a5568",
          letterSpacing: "0.06em",
          fontWeight: 500,
          textTransform: "uppercase",
        }}
      >
        {label}
      </div>
      {children}
    </>
  );
}

function AgentRow({
  name,
  active,
  config,
}: {
  name: string;
  active: boolean;
  config: (typeof AGENT_CONFIG)[string];
}) {
  const label =
    name === "memory_inject"
      ? "memory_inject"
      : name === "memory_save"
      ? "memory_save"
      : name.charAt(0).toUpperCase() + name.slice(1);

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "8px 11px",
        borderRadius: 6,
        margin: "1px 6px",
        background: active ? "rgba(255,255,255,0.06)" : "transparent",
        transition: "background 0.15s",
      }}
    >
      {/* 状态点 */}
      <span
        style={{
          width: 8,
          height: 8,
          borderRadius: "50%",
          background: config.dotColor,
          flexShrink: 0,
          opacity: active ? 1 : 0.35,
          animation: active ? "sidebar-pulse 1.4s ease-in-out infinite" : "none",
        }}
      />

      {/* 文字 */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontSize: 13,
            color: active ? "#e2e6f0" : "#8a8f9a",
            fontWeight: 500,
            transition: "color 0.15s",
          }}
        >
          {label}
        </div>
        <div
          style={{
            fontSize: 11,
            color: "#4a5568",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {config.desc}
        </div>
      </div>

      {/* 图标 */}
      <span style={{ color: config.iconColor, flexShrink: 0, opacity: active ? 1 : 0.4 }}>
        {config.icon}
      </span>
    </div>
  );
}
