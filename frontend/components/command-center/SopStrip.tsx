"use client";

import { CC } from "./tokens";
import type { SopStage } from "./types";

interface SopStripProps {
  stages?: SopStage[];
}

export const DEFAULT_SOP_STAGES: SopStage[] = [
  { id: "init",     label: "A2A 初始化", status: "pending" },
  { id: "assess",   label: "态势研判",   status: "pending" },
  { id: "dispatch", label: "并发处置",   status: "pending" },
  { id: "hitl",     label: "HITL 审批", status: "pending" },
  { id: "execute",  label: "统一执行",   status: "pending" },
];

export function SopStrip({ stages = DEFAULT_SOP_STAGES }: SopStripProps) {
  return (
    <div style={{
      display: "flex",
      alignItems: "center",
      padding: "7px 18px",
      borderBottom: `1px solid ${CC.line}`,
      background: CC.bg2,
      gap: 0,
      overflowX: "auto",
      flexShrink: 0,
      scrollbarWidth: "none",
    }}>
      <span style={{
        fontSize: 10, letterSpacing: "0.1em", color: CC.muted,
        textTransform: "uppercase", fontFamily: "monospace",
        marginRight: 12, flexShrink: 0,
      }}>
        SOP
      </span>

      {stages.map((stage, i) => {
        const isDone = stage.status === "done";
        const isActive = stage.status === "active";

        return (
          <div key={stage.id} style={{ display: "flex", alignItems: "center", flexShrink: 0 }}>
            {/* 连接线 */}
            {i > 0 && (
              <div style={{
                width: 18, height: 1, flexShrink: 0, margin: "0 8px",
                background: stages[i - 1].status === "done" ? CC.ok : CC.line,
              }} />
            )}

            {/* 阶段 pill */}
            <div style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              fontSize: 11,
              color: isActive ? CC.text : isDone ? CC.text2 : CC.muted,
              fontWeight: isActive ? 600 : 400,
              padding: "2px 0",
            }}>
              {/* 圆形序号 */}
              <span style={{
                width: 16, height: 16, borderRadius: "50%",
                display: "inline-flex", alignItems: "center", justifyContent: "center",
                fontFamily: "monospace", fontSize: 9, fontWeight: 700, flexShrink: 0,
                background: isDone ? CC.ok : isActive ? CC.warn : CC.panel,
                border: `1px solid ${isDone ? CC.ok : isActive ? CC.warn : CC.lineSoft}`,
                color: isDone || isActive ? "#0b0e14" : CC.muted,
                animation: isActive ? "sop-pulse 1.8s ease-in-out infinite" : "none",
              }}>
                {isDone ? "✓" : i + 1}
              </span>
              {stage.label}
            </div>
          </div>
        );
      })}

      <style>{`
        @keyframes sop-pulse {
          0%, 100% { box-shadow: 0 0 0 2px rgba(229,146,14,0.2); }
          50% { box-shadow: 0 0 0 5px rgba(229,146,14,0.05); }
        }
      `}</style>
    </div>
  );
}
