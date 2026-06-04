"use client";

import { CC } from "./tokens";
import type { HITLNotification } from "./types";

interface HITLBarProps {
  hitl: HITLNotification | null;
  onOpenModal: () => void;
  onApprove?: () => void;
  onReject?: () => void;
}

export function HITLBar({ hitl, onOpenModal, onApprove, onReject }: HITLBarProps) {
  if (!hitl) return null;

  return (
    <div
      style={{
        zIndex: 4,
        display: "flex",
        alignItems: "center",
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
          width: 28, height: 28, borderRadius: 6, flexShrink: 0,
          background: `color-mix(in oklab, ${CC.warn} 25%, transparent)`,
          border: `1px solid color-mix(in oklab, ${CC.warn} 50%, transparent)`,
          display: "flex", alignItems: "center", justifyContent: "center",
          fontSize: 13, color: CC.warn, fontWeight: 700,
        }}
      >
        !
      </div>

      {/* 内容 */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 2, flexWrap: "wrap" }}>
          <span style={{ fontSize: 12, fontWeight: 600, color: CC.text }}>
            等待审批：{hitl.message}
          </span>
          <span style={{
            fontSize: 9, fontWeight: 700, letterSpacing: "0.08em", color: CC.warn,
            background: `color-mix(in oklab, ${CC.warn} 15%, transparent)`,
            border: `1px solid color-mix(in oklab, ${CC.warn} 35%, transparent)`,
            padding: "1px 5px", borderRadius: 4,
          }}>
            HITL · BLOCKING
          </span>
        </div>
        {hitl.detail && (
          <div style={{ fontSize: 11, color: CC.text2, lineHeight: 1.5 }}>{hitl.detail}</div>
        )}
      </div>

      {/* 审批按钮 */}
      {hitl.isProcessing ? (
        <div style={{
          display: "flex", alignItems: "center", gap: 7, flexShrink: 0,
          fontSize: 11, color: CC.warn,
        }}>
          <span style={{
            width: 12, height: 12, borderRadius: "50%", flexShrink: 0,
            border: `1.5px solid color-mix(in oklab, ${CC.warn} 30%, transparent)`,
            borderTopColor: CC.warn,
            animation: "hitl-spin 0.75s linear infinite", display: "inline-block",
          }} />
          执行中…
          <style>{`@keyframes hitl-spin { to { transform: rotate(360deg); } }`}</style>
        </div>
      ) : hitl.isInline ? (
        <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
          <button
            onClick={onReject}
            style={{
              padding: "6px 14px", borderRadius: 5,
              border: `1px solid color-mix(in oklab, ${CC.muted} 40%, transparent)`,
              background: "transparent",
              color: CC.muted2, fontSize: 12, fontWeight: 600, cursor: "pointer",
            }}
          >
            {hitl.rejectLabel ?? "中止"}
          </button>
          <button
            onClick={onApprove}
            style={{
              padding: "6px 14px", borderRadius: 5,
              border: `1px solid color-mix(in oklab, ${CC.warn} 55%, transparent)`,
              background: `color-mix(in oklab, ${CC.warn} 18%, transparent)`,
              color: CC.warn, fontSize: 12, fontWeight: 700, cursor: "pointer",
            }}
          >
            {hitl.confirmLabel ?? "继续"}
          </button>
        </div>
      ) : (
        <button
          onClick={onOpenModal}
          style={{
            padding: "6px 16px", borderRadius: 5, flexShrink: 0,
            border: `1px solid color-mix(in oklab, ${CC.warn} 55%, transparent)`,
            background: `color-mix(in oklab, ${CC.warn} 18%, transparent)`,
            color: CC.warn, fontSize: 12, fontWeight: 700, cursor: "pointer",
          }}
        >
          审批计划
        </button>
      )}
    </div>
  );
}
