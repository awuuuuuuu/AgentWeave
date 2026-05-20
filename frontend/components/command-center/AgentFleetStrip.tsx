"use client";

import { CC, agentColor } from "./tokens";
import { AgentAvatar } from "./AgentAvatar";
import type { AgentFleetEntry } from "./types";

interface AgentFleetStripProps {
  agents: AgentFleetEntry[];
}

function statusLabel(status: AgentFleetEntry["status"], elapsed_ms?: number): string {
  if (status === "done")    return elapsed_ms ? `${(elapsed_ms / 1000).toFixed(1)}s` : "完成";
  if (status === "running") return "执行中";
  if (status === "error")   return "错误";
  return "空闲";
}

function statusColor(status: AgentFleetEntry["status"]): string {
  if (status === "done")    return CC.ok;
  if (status === "running") return CC.warn;
  if (status === "error")   return CC.err;
  return CC.muted2;
}

export function AgentFleetStrip({ agents }: AgentFleetStripProps) {
  return (
    <div
      style={{
        padding: "8px 16px",
        borderBottom: `1px solid ${CC.line}`,
        background: CC.bg2,
        overflowX: "auto",
        flexShrink: 0,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 4,
          minWidth: "max-content",
        }}
      >
        <span
          style={{
            fontSize: 9,
            fontWeight: 700,
            letterSpacing: "0.1em",
            color: CC.muted,
            marginRight: 6,
            textTransform: "uppercase",
            flexShrink: 0,
          }}
        >
          编队状态
        </span>

        {agents.map((agent, idx) => {
          const color = agentColor(agent.code);
          const isRunning = agent.status === "running";

          return (
            <div key={agent.id} style={{ display: "flex", alignItems: "center", gap: 2 }}>
              {/* Arrow connector (skip before first) */}
              {idx > 0 && (
                <div
                  style={{
                    width: 16,
                    height: 1,
                    background: CC.lineSoft,
                    position: "relative",
                    flexShrink: 0,
                  }}
                >
                  <div style={{
                    position: "absolute",
                    right: -3,
                    top: -3,
                    width: 0,
                    height: 0,
                    borderTop: "3px solid transparent",
                    borderBottom: "3px solid transparent",
                    borderLeft: `4px solid ${CC.lineSoft}`,
                  }} />
                </div>
              )}

              {/* Chip */}
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 5,
                  padding: "4px 8px",
                  borderRadius: 6,
                  border: `1px solid color-mix(in oklab, ${color} ${isRunning ? "45" : "25"}%, ${CC.line})`,
                  background: `color-mix(in oklab, ${color} ${isRunning ? "14" : "8"}%, ${CC.panel})`,
                  flexShrink: 0,
                  animation: isRunning ? "chip-pulse 2s ease-in-out infinite" : "none",
                }}
              >
                <AgentAvatar code={agent.code} status={agent.status} size="sm" />
                <div>
                  <div style={{
                    fontSize: 10,
                    fontWeight: 600,
                    color,
                    lineHeight: 1.1,
                  }}>
                    {agent.name}
                  </div>
                  <div style={{
                    fontSize: 9,
                    color: statusColor(agent.status),
                    lineHeight: 1.1,
                    marginTop: 1,
                  }}>
                    {statusLabel(agent.status, agent.elapsed_ms)}
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <style>{`
        @keyframes chip-pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.75; }
        }
      `}</style>
    </div>
  );
}
