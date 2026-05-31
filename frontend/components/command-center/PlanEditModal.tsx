"use client";

import { useState, useEffect } from "react";
import { CC, DEPT_ICONS, DEPT_NAMES, agentColor, normalizeDeptCode } from "./tokens";
import type { PlanStep } from "@/lib/agent-api";

interface PlanEditModalProps {
  steps: PlanStep[];
  /** 批准（全部或部分）— steps 中 status 字段已由组件设置 */
  onConfirm: (steps: PlanStep[]) => void;
  /** 拒绝方案，跳过全部步骤 */
  onReject: () => void;
  /** 关闭弹窗但不提交（放弃编辑，保持 HITL 挂起） */
  onClose?: () => void;
}

export function PlanEditModal({ steps, onConfirm, onReject, onClose }: PlanEditModalProps) {
  const [draft, setDraft]     = useState<PlanStep[]>(() => steps.map((s) => ({ ...s })));
  const [enabled, setEnabled] = useState<Record<string, boolean>>(
    () => Object.fromEntries(steps.map((s) => [s.step_id, true]))
  );
  const [showRejectConfirm, setShowRejectConfirm] = useState(false);

  useEffect(() => {
    if (!onClose) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  function updateTitle(idx: number, title: string) {
    setDraft((prev) => prev.map((s, i) => (i === idx ? { ...s, title } : s)));
  }
  function updateTask(idx: number, task: string) {
    setDraft((prev) => prev.map((s, i) => (i === idx ? { ...s, task } : s)));
  }
  function toggleRisk(idx: number) {
    setDraft((prev) => prev.map((s, i) => (i === idx ? { ...s, is_high_risk: !s.is_high_risk } : s)));
  }
  function toggleEnabled(stepId: string) {
    setEnabled((prev) => ({ ...prev, [stepId]: !prev[stepId] }));
  }

  const enabledCount = Object.values(enabled).filter(Boolean).length;
  const allEnabled   = enabledCount === draft.length;

  function handleApproveSelected() {
    onConfirm(draft.map((s) => ({ ...s, status: enabled[s.step_id] ? "pending" : "skipped" })));
  }
  function handleApproveAll() {
    onConfirm(draft.map((s) => ({ ...s, status: "pending" })));
  }

  return (
    <div
      style={{
        position: "fixed", inset: 0, zIndex: 200,
        display: "flex", alignItems: "center", justifyContent: "center",
        background: "rgba(0,0,0,0.55)", backdropFilter: "blur(4px)",
      }}
    >
      <div
        style={{
          width: "min(720px, 94vw)", maxHeight: "86vh",
          display: "flex", flexDirection: "column",
          background: CC.bg, border: `1px solid ${CC.line}`,
          borderRadius: 12, boxShadow: "0 20px 60px rgba(0,0,0,0.22)",
          overflow: "hidden",
        }}
      >
        {/* ── 头部 ── */}
        <div style={{
          display: "flex", alignItems: "center", gap: 10,
          padding: "14px 20px", borderBottom: `1px solid ${CC.line}`,
          flexShrink: 0, background: CC.bg2,
        }}>
          <span style={{ fontSize: 14, fontWeight: 700, color: CC.warn }}>⚠</span>
          <span style={{ fontSize: 14, fontWeight: 700, color: CC.text, flex: 1 }}>
            审批执行计划
          </span>
          <span style={{ fontSize: 11, color: CC.muted }}>{draft.length} 个步骤</span>
          <button
            onClick={() => setEnabled(Object.fromEntries(draft.map((s) => [s.step_id, !allEnabled])))}
            style={{
              padding: "3px 10px", borderRadius: 5, fontSize: 10, fontWeight: 600,
              border: `1px solid ${CC.line}`, background: "transparent",
              color: CC.muted2, cursor: "pointer",
            }}
          >
            {allEnabled ? "取消全选" : "全选"}
          </button>
          {onClose && (
            <button
              onClick={onClose}
              title="关闭（保持审批挂起）"
              style={{
                width: 22, height: 22, borderRadius: 4, display: "flex",
                alignItems: "center", justifyContent: "center",
                border: `1px solid ${CC.line}`, background: "transparent",
                color: CC.muted2, fontSize: 13, cursor: "pointer", lineHeight: 1,
              }}
            >
              ×
            </button>
          )}
        </div>

        {/* ── 步骤列表 ── */}
        <div style={{ flex: 1, overflowY: "auto", padding: "12px 20px", display: "flex", flexDirection: "column", gap: 10 }}>
          {draft.map((step, idx) => {
            const code      = normalizeDeptCode(step.dept_code);
            const deptColor = agentColor(code);
            const isOn      = enabled[step.step_id] ?? true;
            return (
              <div
                key={step.step_id}
                style={{
                  border: `1px solid ${
                    !isOn ? CC.line
                    : step.is_high_risk
                      ? `color-mix(in oklab, ${CC.warn} 40%, ${CC.line})`
                      : CC.line
                  }`,
                  borderRadius: 8, padding: "10px 12px",
                  background: !isOn
                    ? `color-mix(in oklab, ${CC.bg} 70%, ${CC.bg2})`
                    : step.is_high_risk ? `color-mix(in oklab, ${CC.warn} 5%, ${CC.bg})`
                    : CC.panel,
                  opacity: isOn ? 1 : 0.48,
                  transition: "opacity 0.15s, border-color 0.15s",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                  {/* 启用复选框 */}
                  <button
                    onClick={() => toggleEnabled(step.step_id)}
                    title={isOn ? "点击跳过此步骤" : "点击包含此步骤"}
                    style={{
                      width: 18, height: 18, borderRadius: 4, flexShrink: 0, cursor: "pointer",
                      border: `1.5px solid ${isOn ? CC.ok : CC.line}`,
                      background: isOn ? `color-mix(in oklab, ${CC.ok} 20%, transparent)` : "transparent",
                      display: "flex", alignItems: "center", justifyContent: "center",
                      fontSize: 11, color: CC.ok,
                    }}
                  >
                    {isOn && "✓"}
                  </button>

                  <span style={{ fontSize: 10, fontWeight: 700, color: CC.muted2, minWidth: 20 }}>{idx + 1}</span>

                  <span style={{
                    display: "inline-flex", alignItems: "center", gap: 4,
                    padding: "1px 7px 1px 4px", borderRadius: 5, flexShrink: 0,
                    background: `color-mix(in oklab, ${deptColor} 12%, transparent)`,
                    border: `1px solid color-mix(in oklab, ${deptColor} 28%, transparent)`,
                    fontSize: 11, fontWeight: 600, color: deptColor,
                  }}>
                    <span style={{ fontSize: 12 }}>{DEPT_ICONS[code] ?? "🏢"}</span>
                    {DEPT_NAMES[code] ?? code}
                  </span>

                  <button
                    onClick={() => toggleRisk(idx)}
                    title={step.is_high_risk ? "取消高风险标记" : "标记为高风险"}
                    style={{
                      padding: "2px 8px", borderRadius: 4, fontSize: 10, fontWeight: 600,
                      cursor: "pointer", marginLeft: "auto", flexShrink: 0, transition: "all 0.15s",
                      border: `1px solid ${step.is_high_risk
                        ? `color-mix(in oklab, ${CC.warn} 50%, transparent)` : CC.line}`,
                      background: step.is_high_risk
                        ? `color-mix(in oklab, ${CC.warn} 15%, transparent)` : "transparent",
                      color: step.is_high_risk ? CC.warn : CC.muted,
                    }}
                  >
                    {step.is_high_risk ? "⚠ 高风险" : "普通"}
                  </button>
                </div>

                {/* 标题（可编辑）*/}
                <input
                  value={step.title}
                  onChange={(e) => updateTitle(idx, e.target.value)}
                  disabled={!isOn}
                  placeholder="步骤标题"
                  style={{
                    width: "100%", marginBottom: 6,
                    border: `1px solid ${CC.line}`, borderRadius: 5,
                    padding: "5px 8px", fontSize: 12, fontWeight: 600,
                    color: CC.text, background: CC.bg, fontFamily: "inherit",
                    outline: "none", boxSizing: "border-box",
                  }}
                  onFocus={(e) => { if (isOn) e.currentTarget.style.borderColor = CC.info; }}
                  onBlur={(e) => { e.currentTarget.style.borderColor = CC.line; }}
                />

                {/* 任务说明（可编辑） */}
                <textarea
                  value={step.task}
                  onChange={(e) => updateTask(idx, e.target.value)}
                  disabled={!isOn}
                  rows={2}
                  style={{
                    width: "100%", resize: "vertical",
                    border: `1px solid ${CC.line}`, borderRadius: 5,
                    padding: "6px 8px", fontSize: 11.5, color: CC.text2,
                    background: CC.bg, fontFamily: "inherit", lineHeight: 1.5,
                    outline: "none", boxSizing: "border-box",
                  }}
                  onFocus={(e) => { if (isOn) e.currentTarget.style.borderColor = CC.info; }}
                  onBlur={(e) => { e.currentTarget.style.borderColor = CC.line; }}
                />
              </div>
            );
          })}
        </div>

        {/* ── 底部按钮 ── */}
        <div style={{
          padding: "12px 20px", borderTop: `1px solid ${CC.line}`,
          background: CC.bg2, flexShrink: 0,
          display: "flex", alignItems: "center", gap: 8,
        }}>
          {showRejectConfirm ? (
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontSize: 11, color: CC.text2 }}>确认拒绝整个方案？</span>
              <button
                onClick={onReject}
                style={{
                  padding: "5px 12px", borderRadius: 5, fontSize: 11, fontWeight: 600, cursor: "pointer",
                  border: `1px solid color-mix(in oklab, ${CC.emerg} 50%, transparent)`,
                  background: `color-mix(in oklab, ${CC.emerg} 15%, transparent)`,
                  color: CC.emerg,
                }}
              >
                确认拒绝
              </button>
              <button
                onClick={() => setShowRejectConfirm(false)}
                style={{
                  padding: "5px 12px", borderRadius: 5, fontSize: 11, fontWeight: 500, cursor: "pointer",
                  border: `1px solid ${CC.line}`, background: "transparent", color: CC.muted,
                }}
              >
                取消
              </button>
            </div>
          ) : (
            <button
              onClick={() => setShowRejectConfirm(true)}
              style={{
                padding: "5px 12px", borderRadius: 5, fontSize: 11, fontWeight: 500, cursor: "pointer",
                border: `1px solid ${CC.line}`, background: "transparent", color: CC.text2,
              }}
            >
              拒绝方案
            </button>
          )}

          <div style={{ flex: 1 }} />

          {!allEnabled && enabledCount > 0 ? (
            <button
              onClick={handleApproveSelected}
              style={{
                padding: "5px 14px", borderRadius: 5, fontSize: 11, fontWeight: 600, cursor: "pointer",
                border: `1px solid color-mix(in oklab, ${CC.warn} 50%, transparent)`,
                background: `color-mix(in oklab, ${CC.warn} 12%, transparent)`,
                color: CC.warn,
              }}
            >
              批准选中 {enabledCount} 步
            </button>
          ) : (
            <button
              onClick={handleApproveAll}
              disabled={enabledCount === 0}
              style={{
                padding: "5px 16px", borderRadius: 5, fontSize: 11, fontWeight: 700,
                cursor: enabledCount === 0 ? "not-allowed" : "pointer",
                border: `1px solid color-mix(in oklab, ${CC.ok} 50%, transparent)`,
                background: `color-mix(in oklab, ${CC.ok} 18%, transparent)`,
                color: CC.ok, opacity: enabledCount === 0 ? 0.4 : 1,
              }}
            >
              批准全部方案
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
