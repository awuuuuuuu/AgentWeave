"use client";

import { useState } from "react";
import { IconShieldCheck, IconCheck, IconX } from "@tabler/icons-react";
import type { HITLData } from "@/lib/agent-api";

interface HITLCardProps {
  data: HITLData;
  onDecision: (decision: "approve" | "reject") => void;
  disabled?: boolean;  // 审批完成后禁用按钮
}

export function HITLCard({ data, onDecision, disabled = false }: HITLCardProps) {
  const [decided, setDecided] = useState<"approve" | "reject" | null>(null);

  function handleClick(decision: "approve" | "reject") {
    if (decided || disabled) return;
    setDecided(decision);
    onDecision(decision);
  }

  const approved = decided === "approve";
  const rejected = decided === "reject";
  const isDone = !!decided || disabled;

  return (
    <div>
      <div
        style={{
          border: `0.5px solid ${isDone ? (approved ? "#5DCAA5" : rejected ? "#F09595" : "var(--color-border-secondary)") : "#F09595"}`,
          borderLeft: `3px solid ${isDone ? (approved ? "#5DCAA5" : rejected ? "#E24B4A" : "var(--color-border-secondary)") : "#E24B4A"}`,
          borderRadius: "0 8px 8px 0",
          background: isDone
            ? approved ? "#F4FBF8" : rejected ? "#FDF4F4" : "var(--color-background-secondary)"
            : "var(--color-background-primary)",
          padding: "10px 12px",
          transition: "all 0.2s",
        }}
      >
        {/* 决策结果 badge（醒目大标） */}
        {isDone && (
          <div style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            padding: "5px 12px", borderRadius: 8, marginBottom: 8,
            background: approved ? "#E1F5EE" : "#FCEBEB",
            border: `0.5px solid ${approved ? "#5DCAA5" : "#F09595"}`,
          }}>
            <IconShieldCheck size={15} color={approved ? "#085041" : "#791F1F"} />
            <span style={{ fontSize: 14, fontWeight: 700, color: approved ? "#085041" : "#791F1F" }}>
              {approved ? "已批准" : "已拒绝"}
            </span>
          </div>
        )}

        {/* 状态标签（待审批时显示） */}
        {!isDone && (
          <div style={{
            fontSize: 10, fontWeight: 500, color: "#A32D2D",
            letterSpacing: "0.04em", textTransform: "uppercase",
            marginBottom: 4, display: "flex", alignItems: "center", gap: 4,
          }}>
            <IconShieldCheck size={11} />
            人工审批请求
          </div>
        )}

        {/* 描述 */}
        <div style={{ fontSize: 12, color: "var(--color-text-secondary)", lineHeight: 1.5, marginBottom: isDone ? 0 : 8 }}>
          {data.message || data.description}
        </div>

        {/* 操作按钮（仅在未决时显示） */}
        {!isDone && (
          <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
            <button
              onClick={() => handleClick("approve")}
              style={{
                padding: "4px 12px",
                borderRadius: 6,
                fontSize: 12,
                fontWeight: 500,
                background: "#E1F5EE",
                color: "#085041",
                border: "0.5px solid #5DCAA5",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                gap: 4,
              }}
            >
              <IconCheck size={12} />
              批准
            </button>
            <button
              onClick={() => handleClick("reject")}
              style={{
                padding: "4px 12px",
                borderRadius: 6,
                fontSize: 12,
                fontWeight: 500,
                background: "#FCEBEB",
                color: "#791F1F",
                border: "0.5px solid #F09595",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                gap: 4,
              }}
            >
              <IconX size={12} />
              拒绝
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
