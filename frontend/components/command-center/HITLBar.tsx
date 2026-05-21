"use client";

import { CC, DEPT_ICONS, agentColor } from "./tokens";
import type { HITLNotification } from "./types";

const DEPT_NAMES: Record<string, string> = {
  EN: "环保局", ME: "医疗急救", TR: "交通管控", LG: "应急物资", SF: "企业安全",
};

interface HITLBarProps {
  hitl: HITLNotification | null;
  onApprove: () => void;
  onReject: () => void;
}

export function HITLBar({ hitl, onApprove, onReject }: HITLBarProps) {
  if (!hitl) return null;

  return (
    <div
      style={{
        zIndex: 4,
        display: "flex",
        alignItems: "flex-start",
        gap: 12,
        padding: "10px 16px",
        background: `linear-gradient(90deg,
          color-mix(in oklab, ${CC.warn} 22%, ${CC.panel}) 0%,
          color-mix(in oklab, ${CC.warn} 10%, ${CC.panel}) 100%)`,
        borderBottom: `1px solid color-mix(in oklab, ${CC.warn} 45%, transparent)`,
        flexShrink: 0,
      }}
    >
      {/* 警告图标 */}
      <div
        style={{
          width: 28,
          height: 28,
          borderRadius: 6,
          background: `color-mix(in oklab, ${CC.warn} 25%, transparent)`,
          border: `1px solid color-mix(in oklab, ${CC.warn} 50%, transparent)`,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
          fontSize: 13,
          color: CC.warn,
          fontWeight: 700,
          marginTop: 1,
        }}
      >
        !
      </div>

      {/* 内容 */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 2, flexWrap: "wrap" }}>
          {/* 部门标签（step_review 时显示） */}
          {hitl.dept_code && (() => {
            const code = hitl.dept_code.toUpperCase();
            const deptColor = agentColor(code);
            return (
              <span style={{
                display: "inline-flex", alignItems: "center", gap: 4,
                padding: "1px 7px 1px 4px", borderRadius: 5,
                background: `color-mix(in oklab, ${deptColor} 14%, transparent)`,
                border: `1px solid color-mix(in oklab, ${deptColor} 30%, transparent)`,
                fontSize: 11, fontWeight: 600, color: deptColor, flexShrink: 0,
              }}>
                <span style={{ fontSize: 13 }}>{DEPT_ICONS[code] ?? "🏢"}</span>
                {DEPT_NAMES[code] ?? code}
              </span>
            );
          })()}
          <span style={{ fontSize: 12, fontWeight: 600, color: CC.text }}>
            等待审批：{hitl.message}
          </span>
          <span
            style={{
              fontSize: 9,
              fontWeight: 700,
              letterSpacing: "0.08em",
              color: CC.warn,
              background: `color-mix(in oklab, ${CC.warn} 15%, transparent)`,
              border: `1px solid color-mix(in oklab, ${CC.warn} 35%, transparent)`,
              padding: "1px 5px",
              borderRadius: 4,
            }}
          >
            HITL · BLOCKING
          </span>
        </div>
        {hitl.detail && (
          <div style={{ fontSize: 11, color: CC.text2, lineHeight: 1.5 }}>
            {hitl.detail}
          </div>
        )}
      </div>

      {/* 按钮组 / 执行中状态 */}
      {hitl.isProcessing ? (
        <div style={{
          display: "flex", alignItems: "center", gap: 7,
          flexShrink: 0, paddingTop: 2,
          fontSize: 11, color: CC.warn,
        }}>
          <span style={{
            width: 12, height: 12, borderRadius: "50%", flexShrink: 0,
            border: `1.5px solid color-mix(in oklab, ${CC.warn} 30%, transparent)`,
            borderTopColor: CC.warn,
            animation: "hitl-spin 0.75s linear infinite",
            display: "inline-block",
          }} />
          执行中…
          <style>{`@keyframes hitl-spin { to { transform: rotate(360deg); } }`}</style>
        </div>
      ) : (
        <div style={{ display: "flex", gap: 6, flexShrink: 0, alignItems: "center", paddingTop: 2 }}>
          <button
            onClick={onReject}
            style={{
              padding: "5px 12px",
              borderRadius: 5,
              border: `1px solid ${CC.line}`,
              background: "transparent",
              color: CC.text2,
              fontSize: 11,
              fontWeight: 500,
              cursor: "pointer",
            }}
          >
            驳回修改
          </button>
          <button
            onClick={onApprove}
            style={{
              padding: "5px 12px",
              borderRadius: 5,
              border: `1px solid color-mix(in oklab, ${CC.ok} 50%, transparent)`,
              background: `color-mix(in oklab, ${CC.ok} 20%, transparent)`,
              color: CC.ok,
              fontSize: 11,
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            批准执行
          </button>
        </div>
      )}
    </div>
  );
}
