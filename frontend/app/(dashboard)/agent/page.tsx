"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { IconMessageQuestion, IconPlus } from "@tabler/icons-react";
import { toast } from "sonner";

import { AgentChatWindow } from "@/components/agent-chat/AgentChatWindow";
import { NewSessionDialog } from "@/components/agent-chat/NewSessionDialog";
import { SessionListSidebar } from "@/components/agent-chat/SessionListSidebar";
import { CommandCenterLayout } from "@/components/command-center/CommandCenterLayout";
import {
  listSessions,
  createSession,
  deleteSession,
  type Session,
} from "@/lib/agent-api";
import { apiGetMyOrg } from "@/lib/api";

// ── 空状态（未选会话）────────────────────────────────────────────────────────

function NoSessionState({ onNew, isCreating }: { onNew: () => void; isCreating: boolean }) {
  return (
    <div
      style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 16,
        background: "var(--color-background-primary)",
        color: "var(--color-text-tertiary)",
      }}
    >
      <div
        style={{
          width: 52,
          height: 52,
          borderRadius: 12,
          background: "var(--color-background-secondary)",
          border: "0.5px solid var(--color-border-tertiary)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <IconMessageQuestion size={24} />
      </div>
      <div style={{ textAlign: "center" }}>
        <div style={{ fontSize: 15, fontWeight: 500, color: "var(--color-text-primary)", marginBottom: 6 }}>
          选择或新建会话
        </div>
        <div style={{ fontSize: 13, lineHeight: 1.6, maxWidth: 280 }}>
          从左侧选择历史会话继续，
          <br />
          或新建一个会话开始提问。
        </div>
      </div>
      <button
        onClick={onNew}
        disabled={isCreating}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          padding: "8px 18px",
          borderRadius: 8,
          background: "#185FA5",
          border: "none",
          color: "#fff",
          fontSize: 13,
          fontWeight: 500,
          cursor: isCreating ? "not-allowed" : "pointer",
          opacity: isCreating ? 0.6 : 1,
        }}
      >
        <IconPlus size={14} />
        新建会话
      </button>
    </div>
  );
}

// ── 页面主体 ──────────────────────────────────────────────────────────────────

export default function AgentPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const sessionId = searchParams.get("session");

  // null = 加载中，[] = 已加载但为空，[...] = 有会话
  const [sessions, setSessions] = useState<Session[] | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [isCommandOrg, setIsCommandOrg] = useState(false);

  const loadSessions = useCallback(async () => {
    try {
      const list = await listSessions();
      setSessions(list); // 单次 setState，无中间帧
    } catch {
      setSessions([]); // 失败时显示空状态
    }
  }, []);

  useEffect(() => {
    loadSessions();
    apiGetMyOrg()
      .then((org) => setIsCommandOrg(org.type === "command"))
      .catch(() => {});
  }, [loadSessions]);

  function handleNew() {
    if (isCreating) return;
    setDialogOpen(true);
  }

  async function handleDialogConfirm(
    kbIds: string[],
    sessionType: "chat" | "weave" = "chat",
    _deptOrgIds: string[] = []
  ) {
    setIsCreating(true);
    try {
      const s = await createSession(kbIds, sessionType);
      setSessions((prev) => [s, ...(prev ?? [])]);
      router.push(`/agent?session=${s.id}`);
    } catch {
      toast.error("创建会话失败");
      throw new Error("创建会话失败"); // 让 Dialog 保持打开
    } finally {
      setIsCreating(false);
    }
  }

  function handleSelect(id: string) {
    router.push(`/agent?session=${id}`);
  }

  async function handleDelete(id: string) {
    try {
      await deleteSession(id);
      setSessions((prev) => (prev ?? []).filter((s) => s.id !== id));
      // 如果删的是当前会话，跳回无选中状态
      if (id === sessionId) {
        router.push("/agent");
      }
    } catch {
      toast.error("删除会话失败");
    }
  }

  const sessionList = sessions ?? [];
  const currentSession = sessionList.find((s) => s.id === sessionId) ?? null;

  return (
    <div style={{ display: "flex", height: "100%", overflow: "hidden" }}>
      {/* 左栏：会话历史 */}
      <SessionListSidebar
        sessions={sessionList}
        currentSessionId={sessionId}
        isCreating={isCreating}
        isLoading={sessions === null}
        onNew={handleNew}
        onSelect={handleSelect}
        onDelete={handleDelete}
      />

      {/* 右侧：按会话类型路由 */}
      {sessionId ? (
        currentSession?.session_type === "weave" ? (
          <CommandCenterLayout
            sessionId={sessionId}
            sessionTitle={currentSession?.title ?? undefined}
            onSessionUpdated={loadSessions}
            weaveSessions={sessionList.filter((s) => s.session_type === "weave")}
          />
        ) : (
          <AgentChatWindow
            sessionId={sessionId}
            sessionMessageCount={currentSession?.message_count ?? 0}
            initialKbIds={currentSession?.kb_ids ?? undefined}
            onSessionUpdated={loadSessions}
          />
        )
      ) : (
        <NoSessionState onNew={handleNew} isCreating={isCreating} />
      )}

      {/* 新建会话弹窗 */}
      <NewSessionDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        onConfirm={handleDialogConfirm}
        showWeave={isCommandOrg}
      />
    </div>
  );
}
