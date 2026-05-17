"use client";

import { useState } from "react";
import {
  IconPlus,
  IconMessageCircle,
  IconTrash,
  IconLoader2,
  IconChevronsLeft,
  IconChevronsRight,
} from "@tabler/icons-react";
import type { Session } from "@/lib/agent-api";

interface SessionListSidebarProps {
  sessions: Session[];
  currentSessionId: string | null;
  isCreating: boolean;
  isLoading?: boolean;
  onNew: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => Promise<void>;
}

function formatTime(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const now = new Date();
  const diffDays = Math.floor((now.getTime() - d.getTime()) / 86400000);
  if (diffDays === 0) return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  if (diffDays === 1) return "昨天";
  if (diffDays < 7) return `${diffDays}天前`;
  return d.toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
}

function sessionLabel(s: Session): string {
  return s.title || "新对话";
}

// ── 折叠态 ────────────────────────────────────────────────────────────────────

function CollapsedSidebar({
  sessions,
  currentSessionId,
  isCreating,
  onNew,
  onExpand,
  onSelect,
}: {
  sessions: Session[];
  currentSessionId: string | null;
  isCreating: boolean;
  onNew: () => void;
  onExpand: () => void;
  onSelect: (id: string) => void;
}) {
  return (
    <aside
      style={{
        width: 44,
        flexShrink: 0,
        background: "#0a0e15",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        borderRight: "0.5px solid rgba(255,255,255,0.06)",
        overflow: "hidden",
      }}
    >
      {/* 展开按钮 */}
      <button
        onClick={onExpand}
        title="展开会话列表"
        style={{
          width: "100%",
          padding: "11px 0 10px",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "none",
          border: "none",
          borderBottom: "0.5px solid rgba(255,255,255,0.06)",
          cursor: "pointer",
          color: "#8a9ab5",
          marginBottom: 6,
        }}
      >
        <IconChevronsRight size={14} />
      </button>

      {/* 新建按钮 */}
      <button
        onClick={onNew}
        disabled={isCreating}
        title="新建会话"
        style={{
          width: 28,
          height: 28,
          borderRadius: 7,
          border: "0.5px solid rgba(255,255,255,0.1)",
          background: "transparent",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          cursor: isCreating ? "not-allowed" : "pointer",
          color: "#9aa3b2",
          marginBottom: 8,
        }}
      >
        {isCreating
          ? <IconLoader2 size={13} style={{ animation: "spin 1s linear infinite" }} />
          : <IconPlus size={13} />}
      </button>

      {/* 会话图标列 */}
      <div style={{ display: "flex", flexDirection: "column", gap: 3, width: "100%", alignItems: "center" }}>
        {sessions.slice(0, 8).map((s) => {
          const isActive = s.id === currentSessionId;
          return (
            <button
              key={s.id}
              onClick={() => onSelect(s.id)}
              title={sessionLabel(s)}
              style={{
                width: 28,
                height: 28,
                borderRadius: 7,
                border: isActive ? "0.5px solid rgba(56,122,221,0.35)" : "0.5px solid transparent",
                background: isActive ? "rgba(56,122,221,0.12)" : "transparent",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                cursor: "pointer",
                color: isActive ? "#85B7EB" : "#3a4252",
                flexShrink: 0,
              }}
            >
              <IconMessageCircle size={13} />
            </button>
          );
        })}
      </div>

      {/* 会话数 */}
      {sessions.length > 0 && (
        <div
          style={{
            marginTop: "auto",
            padding: "8px 0",
            fontSize: 10,
            color: "#6b7787",
            textAlign: "center",
          }}
        >
          {sessions.length}
        </div>
      )}

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </aside>
  );
}

// ── 展开态 ────────────────────────────────────────────────────────────────────

export function SessionListSidebar({
  sessions,
  currentSessionId,
  isCreating,
  isLoading = false,
  onNew,
  onSelect,
  onDelete,
}: SessionListSidebarProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  // 等待二次确认的会话 id
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  // 正在删除中的会话 id
  const [deletingId, setDeletingId] = useState<string | null>(null);

  if (collapsed) {
    return (
      <CollapsedSidebar
        sessions={sessions}
        currentSessionId={currentSessionId}
        isCreating={isCreating}
        onNew={onNew}
        onExpand={() => setCollapsed(false)}
        onSelect={onSelect}
      />
    );
  }

  async function handleDeleteConfirmed(id: string) {
    setConfirmDeleteId(null);
    setDeletingId(id);
    try {
      await onDelete(id);
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <aside
      style={{
        width: 188,
        flexShrink: 0,
        background: "#0a0e15",
        display: "flex",
        flexDirection: "column",
        borderRight: "0.5px solid rgba(255,255,255,0.06)",
        overflow: "hidden",
      }}
    >
      {/* 头部 */}
      <div
        style={{
          padding: "11px 10px 9px",
          borderBottom: "0.5px solid rgba(255,255,255,0.06)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 6,
        }}
      >
        <span style={{ fontSize: 11, fontWeight: 500, color: "#9aa3b2", letterSpacing: "0.06em", textTransform: "uppercase" }}>
          会话历史
        </span>
        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <button
            onClick={() => { onNew(); setCollapsed(true); }}
            disabled={isCreating}
            title="新建会话"
            style={{
              width: 22, height: 22, borderRadius: 5,
              border: "0.5px solid rgba(255,255,255,0.15)",
              background: "transparent", display: "flex", alignItems: "center",
              justifyContent: "center", cursor: isCreating ? "not-allowed" : "pointer",
              color: "#9aa3b2", flexShrink: 0,
            }}
          >
            {isCreating
              ? <IconLoader2 size={11} style={{ animation: "spin 1s linear infinite" }} />
              : <IconPlus size={11} />}
          </button>
          <button
            onClick={() => setCollapsed(true)}
            title="收起"
            style={{
              width: 22, height: 22, borderRadius: 5, border: "none",
              background: "transparent", display: "flex", alignItems: "center",
              justifyContent: "center", cursor: "pointer", color: "#9aa3b2",
            }}
          >
            <IconChevronsLeft size={13} />
          </button>
        </div>
      </div>

      {/* 会话列表 */}
      <div style={{ flex: 1, overflowY: "auto", padding: "4px 5px" }}>
        {isLoading ? (
          /* 骨架屏 */
          <div style={{ padding: "6px 4px", display: "flex", flexDirection: "column", gap: 3 }}>
            {[72, 88, 60, 80].map((w, i) => (
              <div key={i} style={{ padding: "7px 7px", borderRadius: 5, display: "flex", alignItems: "center", gap: 7 }}>
                <div style={{ width: 12, height: 12, borderRadius: 3, background: "rgba(255,255,255,0.05)", flexShrink: 0 }} />
                <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 4 }}>
                  <div style={{ height: 9, borderRadius: 3, background: "rgba(255,255,255,0.06)", width: `${w}%`, animation: "skeleton-pulse 1.4s ease-in-out infinite" }} />
                  <div style={{ height: 7, borderRadius: 3, background: "rgba(255,255,255,0.04)", width: "45%", animation: "skeleton-pulse 1.4s ease-in-out infinite 0.2s" }} />
                </div>
              </div>
            ))}
          </div>
        ) : sessions.length === 0 ? (
          <div style={{ padding: "24px 10px", textAlign: "center", fontSize: 12, color: "#6b7787", lineHeight: 1.6 }}>
            点击 + 新建<br />第一个会话
          </div>
        ) : (
          sessions.map((s) => {
            const isActive = s.id === currentSessionId;
            const isHovered = hoveredId === s.id;
            const isDeleting = deletingId === s.id;
            const isConfirming = confirmDeleteId === s.id;

            return (
              <div key={s.id}>
                <div
                  onClick={() => {
                    if (isDeleting || isConfirming) return;
                    setConfirmDeleteId(null);
                    onSelect(s.id);
                    setCollapsed(true);
                  }}
                  onMouseEnter={() => setHoveredId(s.id)}
                  onMouseLeave={() => setHoveredId(null)}
                  style={{
                    display: "flex", alignItems: "center", gap: 7,
                    padding: "6px 7px", borderRadius: 5, margin: "1px 0",
                    cursor: isDeleting ? "not-allowed" : "pointer",
                    opacity: isDeleting ? 0.45 : 1,
                    background: isActive ? "rgba(56,122,221,0.12)" : isHovered ? "rgba(255,255,255,0.03)" : "transparent",
                    border: isActive ? "0.5px solid rgba(56,122,221,0.25)" : "0.5px solid transparent",
                    transition: "background 0.1s, opacity 0.15s",
                  }}
                >
                  <span style={{ color: isActive ? "#85B7EB" : "#6b7787", flexShrink: 0 }}>
                    {isDeleting
                      ? <IconLoader2 size={12} style={{ animation: "spin 1s linear infinite" }} />
                      : <IconMessageCircle size={12} />}
                  </span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{
                      fontSize: 12, fontWeight: isActive ? 500 : 400,
                      color: isActive ? "#d8dde8" : "#9aa3b2",
                      whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", lineHeight: 1.3,
                    }}>
                      {sessionLabel(s)}
                    </div>
                    <div style={{ fontSize: 10, color: "#6b7787", marginTop: 1, display: "flex", gap: 3, alignItems: "center" }}>
                      <span>{isDeleting ? "删除中…" : formatTime(s.updated_at ?? s.created_at)}</span>
                      {!isDeleting && s.message_count > 0 && <><span>·</span><span>{s.message_count}条</span></>}
                    </div>
                  </div>
                  {isHovered && !isDeleting && (
                    <button
                      onClick={(e) => { e.stopPropagation(); setConfirmDeleteId(isConfirming ? null : s.id); }}
                      title="删除"
                      style={{
                        background: isConfirming ? "rgba(220,50,50,0.12)" : "transparent",
                        border: "none", cursor: "pointer",
                        color: isConfirming ? "#e05555" : "#7a8494",
                        display: "flex", padding: 2, borderRadius: 3, flexShrink: 0,
                      }}
                    >
                      <IconTrash size={11} />
                    </button>
                  )}
                </div>

                {/* 二次确认行 */}
                {isConfirming && !isDeleting && (
                  <div
                    style={{
                      display: "flex", alignItems: "center", justifyContent: "space-between",
                      padding: "5px 9px 5px 26px", margin: "0 0 2px",
                      borderRadius: 5, background: "rgba(220,50,50,0.08)",
                      border: "0.5px solid rgba(220,50,50,0.2)",
                    }}
                  >
                    <span style={{ fontSize: 11, color: "#e05555" }}>确认删除？</span>
                    <div style={{ display: "flex", gap: 4 }}>
                      <button
                        onClick={() => setConfirmDeleteId(null)}
                        style={{
                          fontSize: 11, padding: "2px 7px", borderRadius: 4,
                          border: "0.5px solid rgba(255,255,255,0.1)",
                          background: "transparent", color: "#9aa3b2", cursor: "pointer",
                        }}
                      >
                        取消
                      </button>
                      <button
                        onClick={() => handleDeleteConfirmed(s.id)}
                        style={{
                          fontSize: 11, padding: "2px 7px", borderRadius: 4,
                          border: "none", background: "#c0392b", color: "#fff", cursor: "pointer",
                        }}
                      >
                        删除
                      </button>
                    </div>
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>

      {/* 页脚 */}
      <div style={{ padding: "7px 10px", borderTop: "0.5px solid rgba(255,255,255,0.06)", fontSize: 10, color: "#6b7787" }}>
        {isLoading ? "加载中…" : `${sessions.length} 个会话`}
      </div>

      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes skeleton-pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
      `}</style>
    </aside>
  );
}
