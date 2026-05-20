"use client";

import { CC } from "./tokens";
import type { HITLNotification } from "./types";

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
        position: "sticky",
        top: 0,
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
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 2 }}>
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

      {/* 按钮组 */}
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
    </div>
  );
}
