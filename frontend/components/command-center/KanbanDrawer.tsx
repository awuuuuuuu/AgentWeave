"use client";

import { useState } from "react";
import { CC, agentColor, DEPT_ICONS } from "./tokens";
import type {
  TaskEntry,
  HITLQueueItem,
  KanbanKpis,
} from "./types";

// ── 工具函数 ──────────────────────────────────────────────────────────────────

function fmtElapsed(ms: number): string {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  if (h > 0) return `${h}:${String(m % 60).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  return `${String(m).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

function fmtCountdown(s: number): string {
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

// ── 任务 Chip（把手条）──────────────────────────────────────────────────────

function TaskChip({ task }: { task: TaskEntry }) {
  const color = agentColor(task.dept_code);
  const isRunning = task.status === "running";
  const isDone    = task.status === "done";
  const isError   = task.status === "error";

  const statusColor =
    isDone    ? CC.ok :
    isRunning ? CC.warn :
    isError   ? CC.emerg :
                CC.muted2;

  const statusLabel =
    isDone    ? (task.elapsed_ms ? `${(task.elapsed_ms / 1000).toFixed(1)}s` : "完成") :
    isRunning ? "执行中" :
    isError   ? "错误" :
                "待分配";

  return (
    <div style={{
      display: "flex",
      alignItems: "center",
      gap: 7,
      padding: "5px 11px 5px 5px",
      borderRadius: 6,
      minWidth: 130,
      background: isRunning
        ? `color-mix(in oklab, ${color} 8%, ${CC.bg})`
        : CC.bg,
      border: `1px solid ${
        isRunning ? `color-mix(in oklab, ${color} 35%, ${CC.line})` :
        isError   ? `color-mix(in oklab, ${CC.emerg} 30%, ${CC.line})` :
                    CC.line
      }`,
      boxShadow: "0 1px 3px rgba(0,0,0,0.06)",
      flexShrink: 0,
      animation: isRunning ? "kd-pulse 2s ease-in-out infinite" : "none",
    }}>
      {/* 部门 emoji 图标 */}
      <div style={{
        width: 28, height: 28, borderRadius: 6, flexShrink: 0,
        background: `color-mix(in oklab, ${color} 14%, ${CC.panel2})`,
        border: `1px solid color-mix(in oklab, ${color} 24%, ${CC.line})`,
        display: "flex", alignItems: "center", justifyContent: "center",
        fontSize: 14,
      }}>
        {DEPT_ICONS[task.dept_code] ?? task.dept_code.slice(0, 2)}
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 1, minWidth: 0 }}>
        {/* 任务名 */}
        <span style={{
          fontSize: 11.5, fontWeight: 600, color: CC.text,
          whiteSpace: "nowrap", lineHeight: 1.2,
        }}>
          {task.name}
        </span>
        {/* 部门 + 状态 */}
        <span style={{
          fontSize: 9.5, display: "flex", alignItems: "center", gap: 4,
          lineHeight: 1.1,
        }}>
          <span style={{ color }}>{task.dept_name}</span>
          <span style={{ color: CC.muted2 }}>·</span>
          <span style={{
            color: statusColor,
            display: "flex", alignItems: "center", gap: 2,
          }}>
            {isRunning && (
              <span style={{
                width: 4, height: 4, borderRadius: "50%",
                background: CC.warn, display: "inline-block",
                animation: "kd-pulse 1.4s ease-in-out infinite",
              }} />
            )}
            {statusLabel}
          </span>
        </span>
      </div>
    </div>
  );
}

// ── 任务看板（展开后正文左侧）───────────────────────────────────────────────

function TaskBoard({ tasks }: { tasks: TaskEntry[] }) {
  const cols: { key: TaskEntry["status"]; label: string; color: string }[] = [
    { key: "pending", label: "待分配", color: CC.muted },
    { key: "running", label: "执行中", color: CC.warn },
    { key: "done",    label: "已完成", color: CC.ok },
    { key: "error",   label: "异常",   color: CC.emerg },
  ];

  return (
    <div style={{ overflow: "auto", flex: 1, padding: "10px 12px 12px" }}>
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
        gap: 6,
        height: "100%",
      }}>
        {cols.map((col) => {
          const colTasks = tasks.filter((t) => t.status === col.key);
          return (
            <div key={col.key} style={{
              display: "flex", flexDirection: "column", gap: 5,
              background: `color-mix(in oklab, ${col.color} 5%, ${CC.panel})`,
              borderRadius: 6,
              padding: "6px 6px 8px",
              borderTop: `2.5px solid color-mix(in oklab, ${col.color} 45%, transparent)`,
            }}>
              {/* 列标题 */}
              <div style={{
                display: "flex", alignItems: "center", gap: 5,
                padding: "2px 4px",
                marginBottom: 2,
              }}>
                <span style={{ fontSize: 10, fontWeight: 600, color: col.color }}>
                  {col.label}
                </span>
                <span style={{
                  marginLeft: "auto", fontSize: 9, fontFamily: "monospace",
                  color: colTasks.length > 0 ? col.color : CC.muted2,
                  fontWeight: colTasks.length > 0 ? 700 : 400,
                }}>
                  {colTasks.length}
                </span>
              </div>

              {/* 任务卡 */}
              {colTasks.map((task) => {
                const color = agentColor(task.dept_code);
                return (
                  <div key={task.id} style={{
                    background: CC.bg,
                    border: `1px solid ${CC.line}`,
                    borderLeft: `2.5px solid ${color}`,
                    borderRadius: 5,
                    padding: "6px 8px",
                    boxShadow: "0 1px 3px rgba(0,0,0,0.05)",
                  }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 5, marginBottom: 3 }}>
                      <span style={{ fontSize: 13 }}>{DEPT_ICONS[task.dept_code] ?? "🏢"}</span>
                      <span style={{ fontSize: 11.5, fontWeight: 500, color: CC.text }}>
                        {task.name}
                      </span>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
                      <span style={{
                        fontSize: 9, padding: "1px 5px", borderRadius: 3,
                        background: `color-mix(in oklab, ${color} 12%, transparent)`,
                        color,
                      }}>
                        {task.dept_name}
                      </span>
                      {task.elapsed_ms != null && (
                        <span style={{ fontSize: 9, color: CC.muted2, fontFamily: "monospace" }}>
                          {fmtElapsed(task.elapsed_ms)}
                        </span>
                      )}
                    </div>
                    {task.summary && (
                      <div style={{ fontSize: 10, color: CC.muted, marginTop: 4, lineHeight: 1.4 }}>
                        {task.summary}
                      </div>
                    )}
                  </div>
                );
              })}

              {colTasks.length === 0 && (
                <div style={{
                  fontSize: 10, color: CC.muted2, textAlign: "center",
                  padding: "12px 0",
                }}>
                  —
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── HITL 队列 ─────────────────────────────────────────────────────────────────

function HitlQueue({
  items,
  onApprove,
  onReject,
}: {
  items: HITLQueueItem[];
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
}) {
  return (
    <div style={{ overflowY: "auto", padding: "10px 14px", flex: 1 }}>
      <div style={{
        fontSize: 10, color: CC.muted,
        display: "flex", alignItems: "center", gap: 6, marginBottom: 7,
      }}>
        HITL · 跨会话
        <span style={{
          background: `color-mix(in oklab, ${CC.warn} 15%, transparent)`,
          color: CC.warn, padding: "1px 5px", borderRadius: 3, fontSize: 9,
        }}>
          {items.length} 待批
        </span>
        <div style={{ flex: 1, height: 1, background: CC.line }} />
      </div>

      {items.length === 0 ? (
        <div style={{ fontSize: 12, color: CC.muted, padding: "16px 0", textAlign: "center" }}>
          暂无待审批
        </div>
      ) : (
        items.map((item) => {
          const isUrgent = item.urgent;
          const stripeColor = isUrgent ? CC.emerg : CC.warn;
          return (
            <div key={item.id} style={{
              background: CC.bg,
              border: `1px solid color-mix(in oklab, ${stripeColor} 25%, ${CC.line})`,
              borderLeft: `2.5px solid ${stripeColor}`,
              borderRadius: 6, marginBottom: 7, overflow: "hidden",
              boxShadow: "0 1px 3px rgba(0,0,0,0.06)",
            }}>
              <div style={{ padding: "7px 10px 5px", display: "flex", alignItems: "flex-start", gap: 6 }}>
                <div style={{
                  width: 18, height: 18, borderRadius: 4, flexShrink: 0, marginTop: 1,
                  background: `color-mix(in oklab, ${stripeColor} 18%, transparent)`,
                  color: stripeColor,
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontWeight: 700, fontSize: 11,
                }}>!</div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 9, color: CC.muted, marginBottom: 2 }}>
                    {item.session_title ?? "Weave 会话"}
                  </div>
                  <div style={{ fontSize: 11.5, fontWeight: 600, color: CC.text, lineHeight: 1.3 }}>
                    {item.message}
                  </div>
                </div>
                <div style={{
                  fontSize: 12, fontWeight: 600, fontFamily: "monospace",
                  color: stripeColor, flexShrink: 0,
                }}>
                  {fmtCountdown(item.countdown_s)}
                </div>
              </div>
              {item.detail && (
                <div style={{ padding: "0 10px 4px", fontSize: 10, color: CC.muted }}>
                  {item.detail}
                </div>
              )}
              <div style={{ padding: "5px 10px 7px", display: "flex", gap: 4 }}>
                <button onClick={() => onApprove(item.id)} style={{
                  padding: "3px 8px", fontSize: 10.5, borderRadius: 5,
                  background: CC.ok, border: "none",
                  color: "rgba(11,14,20,0.9)", fontWeight: 600, cursor: "pointer",
                }}>批准</button>
                <button onClick={() => onReject(item.id)} style={{
                  padding: "3px 8px", fontSize: 10.5, borderRadius: 5,
                  background: "transparent",
                  border: `1px solid color-mix(in oklab, ${CC.emerg} 30%, ${CC.line})`,
                  color: CC.emerg, cursor: "pointer",
                }}>驳回</button>
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}

// ── 主组件 ────────────────────────────────────────────────────────────────────

interface KanbanDrawerProps {
  tasks?: TaskEntry[];
  kpis: KanbanKpis;
  hitlQueue: HITLQueueItem[];
  currentSessionId: string | null;
  onApproveHitl?: (hitlId: string) => void;
  onRejectHitl?: (hitlId: string) => void;
}

export function KanbanDrawer({
  tasks = [],
  kpis,
  hitlQueue,
  onApproveHitl,
  onRejectHitl,
}: KanbanDrawerProps) {
  const [expanded, setExpanded] = useState(false);

  const runningCount = tasks.filter((t) => t.status === "running").length;
  const errorCount   = tasks.filter((t) => t.status === "error").length;

  return (
    <div style={{
      background: CC.panel,
      borderTop: `1px solid ${CC.line}`,
      display: "flex",
      flexDirection: "column",
      overflow: "hidden",
      flexShrink: 0,
      height: expanded ? 340 : "auto",
      transition: "height 0.2s ease",
    }}>
      {/* ── 把手 ── */}
      <div style={{
        minHeight: 68,
        display: "flex",
        alignItems: "center",
        padding: "0 12px",
        gap: 10,
        background: CC.panel,
        borderBottom: expanded ? `1px solid ${CC.line}` : "1px solid transparent",
      }}>
        {/* 展开按钮 */}
        <div
          onClick={() => setExpanded((p) => !p)}
          style={{
            display: "flex", alignItems: "center", gap: 8,
            padding: "6px 10px", borderRadius: 5, cursor: "pointer",
            color: CC.text2, transition: "background 0.1s", flexShrink: 0,
            userSelect: "none",
          }}
          onMouseEnter={(e) => (e.currentTarget.style.background = CC.line)}
          onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
        >
          <span style={{ fontSize: 13, fontWeight: 600, color: CC.text, display: "flex", alignItems: "center", gap: 6 }}>
            🤖 协同看板
            {runningCount > 0 && (
              <span style={{ background: `color-mix(in oklab, ${CC.warn} 15%, transparent)`, color: CC.warn, padding: "1px 5px", borderRadius: 3, fontSize: 10 }}>
                {runningCount} 运行
              </span>
            )}
            {errorCount > 0 && (
              <span style={{ background: `color-mix(in oklab, ${CC.emerg} 12%, transparent)`, color: CC.emerg, padding: "1px 5px", borderRadius: 3, fontSize: 10 }}>
                {errorCount} 异常
              </span>
            )}
          </span>
          <span style={{ fontSize: 13, color: CC.muted }}>
            {expanded ? "收起 ▴" : "展开 ▾"}
          </span>
        </div>

        {/* 任务 Chip 条 */}
        <div style={{
          flex: 1,
          display: "flex",
          alignItems: "center",
          gap: 6,
          overflowX: "auto",
          padding: "4px 4px",
          scrollbarWidth: "none",
        }}>
          {tasks.map((task) => (
            <TaskChip key={task.id} task={task} />
          ))}
          {tasks.length === 0 && (
            <span style={{ fontSize: 11, color: CC.muted }}>
              暂无分配任务
            </span>
          )}
        </div>

        {/* HITL 徽章 */}
        {(kpis.hitl_pending > 0 || kpis.errors > 0) && (
          <div
            onClick={() => setExpanded(true)}
            style={{
              display: "flex", alignItems: "center", gap: 8,
              fontSize: 11, fontFamily: "monospace", padding: "3px 10px",
              borderRadius: 10, background: CC.bg, border: `1px solid ${CC.line}`,
              color: CC.text2, cursor: "pointer", flexShrink: 0,
            }}
          >
            {kpis.hitl_pending > 0 && <span style={{ color: CC.warn }}>HITL <strong style={{ color: CC.warn }}>{kpis.hitl_pending}</strong></span>}
            {kpis.hitl_pending > 0 && kpis.errors > 0 && <span style={{ color: CC.muted }}>·</span>}
            {kpis.errors > 0 && <span style={{ color: CC.emerg }}>错误 <strong style={{ color: CC.emerg }}>{kpis.errors}</strong></span>}
          </div>
        )}
      </div>

      {/* ── 展开正文 ── */}
      {expanded && (
        <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0, overflow: "hidden" }}>
          <div style={{
            flex: 1,
            display: "grid",
            gridTemplateColumns: hitlQueue.length > 0
              ? "minmax(0, 1.6fr) minmax(0, 1fr)"
              : "minmax(0, 1fr)",
            minHeight: 0,
            overflow: "hidden",
          }}>
            {/* 左：任务看板（始终显示，无 HITL 时撑满全宽） */}
            <div style={{ display: "flex", flexDirection: "column", minHeight: 0, overflow: "hidden", borderRight: hitlQueue.length > 0 ? `1px solid ${CC.line}` : "none" }}>
              <div style={{
                padding: "8px 14px", borderBottom: `1px solid ${CC.line}`,
                display: "flex", alignItems: "center", gap: 8, flexShrink: 0,
              }}>
                <span style={{ fontSize: 11.5, fontWeight: 600, color: CC.text }}>📋 任务分配看板</span>
                <span style={{ fontSize: 10, color: CC.muted2, marginLeft: "auto" }}>
                  {tasks.length} 个任务
                </span>
              </div>
              <TaskBoard tasks={tasks} />
            </div>

            {/* 右：HITL 队列（仅有待批项时渲染） */}
            {hitlQueue.length > 0 && (
              <div style={{ display: "flex", flexDirection: "column", minHeight: 0, overflow: "hidden" }}>
                <div style={{
                  padding: "8px 14px", borderBottom: `1px solid ${CC.line}`,
                  display: "flex", alignItems: "center", gap: 8, flexShrink: 0,
                }}>
                  <span style={{ fontSize: 11.5, fontWeight: 600, color: CC.text }}>⚡ 待审批队列</span>
                  <span style={{
                    background: `color-mix(in oklab, ${CC.warn} 12%, transparent)`,
                    color: CC.warn, padding: "1px 6px", borderRadius: 4, fontSize: 10,
                    marginLeft: "auto",
                  }}>
                    {hitlQueue.length} 待批
                  </span>
                </div>
                <HitlQueue
                  items={hitlQueue}
                  onApprove={onApproveHitl ?? (() => {})}
                  onReject={onRejectHitl ?? (() => {})}
                />
              </div>
            )}
          </div>
        </div>
      )}

      <style>{`
        @keyframes kd-pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.55; }
        }
      `}</style>
    </div>
  );
}
