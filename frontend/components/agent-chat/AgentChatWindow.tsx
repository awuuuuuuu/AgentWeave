"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  IconDatabase,
  IconChevronDown,
  IconChevronUp,
  IconSend,
  IconSquare,
  IconCheck,
  IconX,
  IconMessageQuestion,
  IconLock,
} from "@tabler/icons-react";
import { toast } from "sonner";

import {
  streamAgent,
  resumeAgent,
  updateSession,
  closeSession,
  listMessages,
  saveMessages,
  msgTobubble,
  bubbleToMsg,
  REPLY_TO,
  RESEARCHER_STEP_TEXT,
  resolveToolStatusText,
  type AgentBubble,
  type AgentName,
  type Citation,
  type McpSource,
} from "@/lib/agent-api";
import { apiListKBs, type KnowledgeBase } from "@/lib/api";
import { AgentMessage } from "./AgentMessage";
import { AgentSidebar } from "./AgentSidebar";
import { HITLCard } from "./HITLCard";
import { MapBubble } from "./MapBubble";

// ── 知识库颜色配置 ────────────────────────────────────────────────────────────

const KB_COLORS = [
  { bg: "#E1F5EE", color: "#085041", border: "#5DCAA5" },
  { bg: "#EEEDFE", color: "#3C3489", border: "#AFA9EC" },
  { bg: "#FAEEDA", color: "#633806", border: "#FAC775" },
  { bg: "#FAECE7", color: "#712B13", border: "#F0997B" },
  { bg: "#F1EFE8", color: "#444441", border: "#B4B2A9" },
];

//── 工具函数 ──────────────────────────────────────────────────────────────────

function makeBubbleId() {
  return Math.random().toString(36).slice(2, 9);
}

// ── Props ─────────────────────────────────────────────────────────────────────

interface AgentChatWindowProps {
  sessionId: string;
  sessionMessageCount?: number;  // 来自 DB 的当前 message_count（父级传入）
  initialKbIds?: string[];       // 新建会话时选中的知识库 IDs
  onSessionUpdated?: () => void; // 流结束后通知父级刷新会话列表
}

// ── 主组件 ────────────────────────────────────────────────────────────────────

export function AgentChatWindow({
  sessionId,
  sessionMessageCount = 0,
  initialKbIds,
  onSessionUpdated,
}: AgentChatWindowProps) {
  const [bubbles, setBubbles] = useState<AgentBubble[]>([]);
  const [input, setInput] = useState("");
  const [isRunning, setIsRunning] = useState(false);
  const [hitlPending, setHitlPending] = useState(false);
  const [activeAgent, setActiveAgent] = useState<AgentName | null>(null);

  // 知识库
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [selectedKbIds, setSelectedKbIds] = useState<Set<string>>(new Set());
  const [kbPopOpen, setKbPopOpen] = useState(false);
  const [kbsLoading, setKbsLoading] = useState(true);

  // 追踪首条用户消息（用于生成 session title）
  const firstUserMsgRef = useRef<string | null>(null);
  // 最近一次发送的用户消息（用于错误时提供重试）
  const lastQueryRef = useRef<string>("");
  // 镜像 selectedKbIds，供重试回调读取最新值（避免 stale closure）
  const selectedKbIdsRef = useRef<Set<string>>(new Set());

  const abortRef = useRef<AbortController | null>(null);
  const msgsRef = useRef<HTMLDivElement>(null);
  const atBottomRef = useRef(true);
  const kbPopRef = useRef<HTMLDivElement>(null);
  // 镜像 bubbles，供异步回调读取最新值（避免 stale closure）
  const bubblesRef = useRef<AgentBubble[]>([]);
  // 已持久化到 DB 的气泡数量（用于 afterStreamDone 只保存增量）
  const savedCountRef = useRef(0);

  // 切换 sessionId 时：重置状态、加载历史消息；离开时触发 memory_save
  useEffect(() => {
    setBubbles([]);
    bubblesRef.current = [];
    savedCountRef.current = 0;
    setInput("");
    setIsRunning(false);
    setHitlPending(false);
    setActiveAgent(null);
    firstUserMsgRef.current = null;

    listMessages(sessionId)
      .then((msgs) => {
        const loaded = msgs.map(msgTobubble);
        setBubbles(loaded);
        bubblesRef.current = loaded;
        savedCountRef.current = loaded.length;
      })
      .catch(() => {}); // 静默失败，从空白开始

    return () => {
      if (!firstUserMsgRef.current) return;
      closeSession(sessionId).catch(() => {});
    };
  }, [sessionId]);

  // 同步 selectedKbIds → ref，供重试回调读取最新值
  useEffect(() => { selectedKbIdsRef.current = selectedKbIds; }, [selectedKbIds]);

  // 点击 KB 弹层外部时关闭
  useEffect(() => {
    if (!kbPopOpen) return;
    function handleMouseDown(e: MouseEvent) {
      if (kbPopRef.current && !kbPopRef.current.contains(e.target as Node)) {
        setKbPopOpen(false);
      }
    }
    document.addEventListener("mousedown", handleMouseDown);
    return () => document.removeEventListener("mousedown", handleMouseDown);
  }, [kbPopOpen]);

  // 加载知识库列表；initialKbIds 有值时优先使用，否则默认选第一个
  useEffect(() => {
    apiListKBs()
      .then((list) => {
        setKbs(list);
        if (initialKbIds && initialKbIds.length > 0) {
          setSelectedKbIds(new Set(initialKbIds));
        } else if (list.length > 0) {
          setSelectedKbIds(new Set([list[0].id]));
        }
      })
      .catch(() => {})
      .finally(() => setKbsLoading(false));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  // bubblesRef 跟随 bubbles 状态同步
  useEffect(() => {
    bubblesRef.current = bubbles;
  }, [bubbles]);

  // 自动滚动
  useEffect(() => {
    if (atBottomRef.current && msgsRef.current) {
      msgsRef.current.scrollTop = msgsRef.current.scrollHeight;
    }
  }, [bubbles]);

  function handleScroll() {
    const el = msgsRef.current;
    if (!el) return;
    atBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  }

  // ── 流结束后更新 session 元数据 ─────────────────────────────────────────────

  async function afterStreamDone() {
    if (!sessionId) return;
    const totalCount = bubblesRef.current.length;
    const title = firstUserMsgRef.current
      ? firstUserMsgRef.current.slice(0, 45) +
        (firstUserMsgRef.current.length > 45 ? "…" : "")
      : undefined;

    // 将本轮新增的已完成气泡持久化到 DB
    const allBubbles = bubblesRef.current;
    const newBubbles = allBubbles
      .slice(savedCountRef.current)
      .filter((b) => b.status === "done");
    if (newBubbles.length > 0) {
      const msgs = newBubbles.map((b, i) =>
        bubbleToMsg(b, savedCountRef.current + i)
      );
      await saveMessages(sessionId, msgs).catch(() => {});
      savedCountRef.current += newBubbles.length;
    }

    try {
      await updateSession(sessionId, {
        message_count: totalCount,
        ...(sessionMessageCount === 0 && title ? { title } : {}),
      });
      onSessionUpdated?.();
    } catch {
      // 非关键路径，静默失败
    }
  }

  // ── SSE 状态机 ──────────────────────────────────────────────────────────────

  async function runStream(
    gen: AsyncGenerator<import("@/lib/agent-api").AgentSSEEvent>
  ) {
    function upsertBubble(
      id: string,
      updater: (prev: AgentBubble | undefined) => AgentBubble
    ) {
      setBubbles((prev) => {
        const idx = prev.findIndex((b) => b.id === id);
        if (idx === -1) return [...prev, updater(undefined)];
        const next = [...prev];
        next[idx] = updater(prev[idx]);
        return next;
      });
    }

    const nodeBubbleId: Partial<Record<AgentName, string>> = {};
    // 记录本轮 analyst 收集到的 MCP 数据源，供 final_answer（reporter）一并展示
    let lastMcpSources: McpSource[] | undefined;

    for await (const event of gen) {
      if (event.type === "node_start") {
        const node = event.node;
        setActiveAgent(node);

        const id = makeBubbleId();
        nodeBubbleId[node] = id;

        upsertBubble(id, () => ({
          id,
          agent: node,
          content: "",
          status: "thinking",
          citations: [],
          replyTo: REPLY_TO[node],
        }));
      }

      else if (event.type === "token") {
        const node = event.node;
        const id = nodeBubbleId[node];
        if (!id) continue;
        const chunk = event.data.content;

        upsertBubble(id, (prev) => ({
          ...(prev ?? { id, agent: node, citations: [], replyTo: REPLY_TO[node] }),
          // 若 bubble 还在 thinking 状态，content 是状态提示文字，首个 token 到来时清空
          content: (prev?.status === "thinking" ? "" : (prev?.content ?? "")) + chunk,
          status: "streaming",
        }));

        atBottomRef.current = true;
      }

      else if (event.type === "status") {
        const node = event.node;
        const id = nodeBubbleId[node];
        if (!id) continue;
        const { step, tool_name } = event.data;
        const text =
          node === "analyst" && tool_name
            ? resolveToolStatusText(tool_name)
            : (RESEARCHER_STEP_TEXT[step] ?? step);
        upsertBubble(id, (prev) => ({
          ...(prev ?? { id, agent: node, citations: [], replyTo: REPLY_TO[node] }),
          content: text,
          status: "thinking",
        }));
      }

      else if (event.type === "tool_result") {
        const node = event.node;
        const id = nodeBubbleId[node];
        if (!id) continue;
        upsertBubble(id, (prev) => {
          const base = prev ?? { id, agent: node as AgentName, content: "", status: "thinking" as const, citations: [], replyTo: REPLY_TO[node] };
          const existing = base.mcpSources ?? [];
          const alreadyExists = existing.some((s) => s.idx === event.data.idx);
          return {
            ...base,
            mcpSources: alreadyExists ? existing : [...existing, event.data],
          };
        });
      }

      else if (event.type === "node_end") {
        const node = event.node;
        const id = nodeBubbleId[node];
        if (!id) continue;
        const citations: Citation[] = event.data.citations ?? [];
        const mcpSources = event.data.mcp_sources;
        const messageToUser = event.data.message_to_user;
        const answerText = event.data.answer_text;

        if (node === "supervisor" && !messageToUser) {
          setBubbles((prev) => prev.filter((b) => b.id !== id));
          setActiveAgent(null);
          continue;
        }

        // 记录 analyst MCP 来源，reporter/final_answer 气泡一并展示
        if (node === "analyst" && mcpSources && mcpSources.length > 0) {
          lastMcpSources = mcpSources;
        }

        upsertBubble(id, (prev) => ({
          ...(prev ?? { id, agent: node, content: "", replyTo: REPLY_TO[node] }),
          status: "done",
          citations,
          ...(mcpSources ? { mcpSources } : {}),
          ...(messageToUser ? { content: messageToUser } : {}),
          ...(answerText ? { content: answerText } : {}),
        }));

        setActiveAgent(null);
      }

      else if (event.type === "map_update") {
        const mapId = makeBubbleId();
        const mapAgent = (event.node ?? "analyst") as AgentName;
        setBubbles((prev) => [
          ...prev,
          {
            id: mapId,
            agent: mapAgent,
            content: "",
            status: "done",
            citations: [],
            mapData: event.data,
          },
        ]);
      }

      else if (event.type === "interrupt") {
        const id = nodeBubbleId["hitl"] ?? makeBubbleId();
        nodeBubbleId["hitl"] = id;

        setBubbles((prev) => {
          const idx = prev.findIndex((b) => b.id === id);
          if (idx === -1) {
            return [
              ...prev,
              {
                id,
                agent: "hitl" as AgentName,
                content: "",
                status: "done",
                citations: [],
                hitlData: event.data,
              },
            ];
          }
          const next = [...prev];
          next[idx] = { ...next[idx], hitlData: event.data, status: "done" };
          return next;
        });

        setHitlPending(true);
        setIsRunning(false);
        setActiveAgent(null);
        return;
      }

      else if (event.type === "final_answer") {
        const reporterBubbleId = nodeBubbleId["reporter"];
        if (reporterBubbleId) {
          upsertBubble(reporterBubbleId, (prev) => ({
            ...(prev ?? {
              id: reporterBubbleId,
              agent: "reporter" as AgentName,
              citations: [],
              replyTo: REPLY_TO["reporter"],
            }),
            content: event.data.content,
            status: "done",
            citations: event.data.citations ?? [],
            isFinalAnswer: true,
            // 将本轮 analyst 的 MCP 数据源一并带入 reporter 最终答案气泡
            ...(lastMcpSources && lastMcpSources.length > 0 ? { mcpSources: lastMcpSources } : {}),
          }));
        } else {
          const finalId = makeBubbleId();
          setBubbles((prev) => [
            ...prev,
            {
              id: finalId,
              agent: "supervisor" as AgentName,
              content: event.data.content,
              status: "done",
              citations: event.data.citations ?? [],
              isFinalAnswer: true,
              ...(lastMcpSources && lastMcpSources.length > 0 ? { mcpSources: lastMcpSources } : {}),
            },
          ]);
        }
      }

      else if (event.type === "done") {
        setIsRunning(false);
        setActiveAgent(null);
        await afterStreamDone();
      }

      else if (event.type === "error") {
        toast.error(event.data.message || "Agent 执行出错", {
          action: { label: "重试", onClick: () => retryLast() },
        });
        setIsRunning(false);
        setActiveAgent(null);
      }
    }

    setIsRunning(false);
    setActiveAgent(null);
  }

  // ── 重试上一条 ──────────────────────────────────────────────────────────────

  const retryLast = useCallback(async () => {
    const q = lastQueryRef.current;
    if (!q || isRunning) return;
    setIsRunning(true);
    abortRef.current = new AbortController();
    try {
      const gen = streamAgent(q, sessionId, Array.from(selectedKbIdsRef.current), abortRef.current.signal);
      await runStream(gen);
    } catch (e: unknown) {
      if (e instanceof Error && e.name !== "AbortError") {
        toast.error(e.message || "连接失败");
      }
      setIsRunning(false);
      setActiveAgent(null);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isRunning, sessionId]);

  // ── 发送消息 ────────────────────────────────────────────────────────────────

  const send = useCallback(async () => {
    const q = input.trim();
    if (!q || isRunning || hitlPending || kbsLoading || !sessionId) return;
    setInput("");
    setIsRunning(true);

    // 记录首条消息（用于生成 title）
    if (!firstUserMsgRef.current) {
      firstUserMsgRef.current = q;
    }
    lastQueryRef.current = q;


    setBubbles((prev) => [
      ...prev,
      {
        id: makeBubbleId(),
        agent: "user",
        content: q,
        status: "done",
        citations: [],
      },
    ]);

    abortRef.current = new AbortController();

    try {
      const gen = streamAgent(
        q,
        sessionId,
        Array.from(selectedKbIds),
        abortRef.current.signal
      );
      await runStream(gen);
    } catch (e: unknown) {
      if (e instanceof Error && e.name !== "AbortError") {
        toast.error(e.message || "连接失败", {
          action: { label: "重试", onClick: () => retryLast() },
        });
      }
      setIsRunning(false);
      setActiveAgent(null);
    }
  }, [input, isRunning, hitlPending, kbsLoading, sessionId, selectedKbIds, retryLast]);

  // ── HITL 审批 ───────────────────────────────────────────────────────────────

  const handleHITLDecision = useCallback(
    async (decision: "approve" | "reject") => {
      setHitlPending(false);

      if (decision === "reject") return;

      setIsRunning(true);
      abortRef.current = new AbortController();
      try {
        const gen = resumeAgent(sessionId, decision, abortRef.current.signal);
        await runStream(gen);
      } catch (e: unknown) {
        if (e instanceof Error && e.name !== "AbortError") {
          toast.error(e.message || "恢复执行失败");
        }
        setIsRunning(false);
        setActiveAgent(null);
      }
    },
    [sessionId]
  );

  // ── 中止 ────────────────────────────────────────────────────────────────────

  function abort() {
    abortRef.current?.abort();
    setIsRunning(false);
    setActiveAgent(null);
  }

  // ── 知识库多选 ──────────────────────────────────────────────────────────────

  function toggleKb(id: string) {
    setSelectedKbIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const shortId = sessionId.slice(-4).toUpperCase();
  const hasMessages = bubbles.length > 0;

  return (
    <div style={{ display: "flex", flex: 1, minWidth: 0, height: "100%", overflow: "hidden" }}>
      {/* Agent 状态侧边栏 */}
      <AgentSidebar
        activeAgent={activeAgent}
        sessionId={sessionId}
        isRunning={isRunning}
      />

      {/* 主聊天区 */}
      <div
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          minWidth: 0,
          background: "var(--color-background-primary)",
        }}
      >
        {/* Topbar */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "12px 18px",
            borderBottom: "0.5px solid var(--color-border-tertiary)",
            flexShrink: 0,
          }}
        >
          <span style={{ fontSize: 14, fontWeight: 500, color: "var(--color-text-primary)" }}>
            Agent 协同
          </span>
          <span style={{ fontSize: 12, color: "var(--color-text-tertiary)" }} suppressHydrationWarning>
            · #{shortId}
          </span>

          <span
            style={{
              marginLeft: "auto",
              fontSize: 11,
              padding: "3px 10px",
              borderRadius: 100,
              fontWeight: 500,
              background: hitlPending
                ? "#FCEBEB"
                : isRunning
                ? "#E1F5EE"
                : "var(--color-background-secondary)",
              color: hitlPending
                ? "#791F1F"
                : isRunning
                ? "#085041"
                : "var(--color-text-tertiary)",
            }}
          >
            {hitlPending ? "等待审批" : isRunning ? "运行中" : "待命"}
          </span>
        </div>

        {/* 消息区 */}
        <div
          ref={msgsRef}
          onScroll={handleScroll}
          style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column" }}
        >
            {!hasMessages ? (
              /* 空状态：flex:1 直接撑满滚动容器，justifyContent 居中 */
              <div
                style={{
                  flex: 1,
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: 16,
                  padding: 32,
                }}
              >
                <div
                  style={{
                    width: 48,
                    height: 48,
                    borderRadius: 12,
                    background: "var(--color-background-secondary)",
                    border: "0.5px solid var(--color-border-tertiary)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    color: "var(--color-text-tertiary)",
                  }}
                >
                  <IconMessageQuestion size={22} />
                </div>
                <div style={{ fontSize: 15, fontWeight: 500, color: "var(--color-text-primary)", textAlign: "center" }}>
                  向 Agent 协同提问
                </div>
                <div style={{ fontSize: 13, color: "var(--color-text-tertiary)", textAlign: "center", lineHeight: 1.6, maxWidth: 320 }}>
                  Supervisor 会自动拆解任务，调度 Researcher、Analyst、Reporter 协同完成。
                </div>
              </div>
            ) : (
              /* 有消息时：正常流布局，加内边距和最大宽度 */
              <div
                style={{
                  maxWidth: 800,
                  width: "100%",
                  margin: "0 auto",
                  padding: "18px 22px",
                  display: "flex",
                  flexDirection: "column",
                  gap: 14,
                }}
              >
                {bubbles.map((bubble) =>
                  bubble.hitlData ? (
                    <HITLCard
                      key={bubble.id}
                      data={bubble.hitlData}
                      onDecision={handleHITLDecision}
                      disabled={!hitlPending}
                    />
                  ) : bubble.mapData ? (
                    <MapBubble key={bubble.id} bubble={bubble} />
                  ) : (
                    <AgentMessage key={bubble.id} bubble={bubble} />
                  )
                )}
              </div>
            )}
        </div>

        {/* 输入区 */}
        <div
          style={{
            borderTop: "0.5px solid var(--color-border-tertiary)",
            padding: "12px 0 16px",
            background: "var(--color-background-primary)",
            flexShrink: 0,
          }}
        >
          <div style={{ maxWidth: 800, width: "100%", margin: "0 auto", padding: "0 22px" }}>
            {/* 知识库选择器 */}
            <div style={{ position: "relative" }}>
              {kbPopOpen && (
                <div
                  ref={kbPopRef}
                  style={{
                    position: "absolute",
                    bottom: "calc(100% + 6px)",
                    left: 0,
                    background: "var(--color-background-primary)",
                    border: "0.5px solid var(--color-border-secondary)",
                    borderRadius: 10,
                    padding: 5,
                    width: 240,
                    zIndex: 50,
                    boxShadow: "0 4px 16px rgba(0,0,0,0.08)",
                  }}
                >
                  <div
                    style={{
                      padding: "5px 7px 7px",
                      borderBottom: "0.5px solid var(--color-border-tertiary)",
                      marginBottom: 3,
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                    }}
                  >
                    <span style={{ fontSize: 10, fontWeight: 500, color: "var(--color-text-tertiary)", textTransform: "uppercase", letterSpacing: "0.04em" }}>
                      选择知识库
                    </span>
                    <button
                      onClick={() => setSelectedKbIds(new Set())}
                      style={{ fontSize: 11, color: "var(--color-text-tertiary)", background: "none", border: "none", cursor: "pointer" }}
                    >
                      清空
                    </button>
                  </div>
                  {kbs.map((kb, i) => {
                    const c = KB_COLORS[i % KB_COLORS.length];
                    const checked = selectedKbIds.has(kb.id);
                    return (
                      <div
                        key={kb.id}
                        onClick={() => toggleKb(kb.id)}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 8,
                          padding: "6px 7px",
                          borderRadius: 6,
                          cursor: "pointer",
                        }}
                      >
                        <div
                          style={{
                            width: 22,
                            height: 22,
                            borderRadius: 4,
                            background: c.bg,
                            color: c.color,
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                            fontSize: 12,
                            flexShrink: 0,
                          }}
                        >
                          <IconDatabase size={12} />
                        </div>
                        <div style={{ flex: 1 }}>
                          <div style={{ fontSize: 12, fontWeight: 500, color: "var(--color-text-primary)" }}>
                            {kb.name}
                          </div>
                        </div>
                        <div
                          style={{
                            width: 15,
                            height: 15,
                            borderRadius: 3,
                            border: `0.5px solid ${checked ? "#185FA5" : "var(--color-border-secondary)"}`,
                            background: checked ? "#185FA5" : "transparent",
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                            flexShrink: 0,
                            color: "#fff",
                          }}
                        >
                          {checked && <IconCheck size={10} />}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}

              {/* KB 标签行 */}
              <div style={{ display: "flex", flexWrap: "wrap", gap: 5, marginBottom: 8, alignItems: "center", minHeight: 22 }}>
                {Array.from(selectedKbIds).map((id, i) => {
                  const kb = kbs.find((k) => k.id === id);
                  if (!kb) return null;
                  const c = KB_COLORS[i % KB_COLORS.length];
                  return (
                    <span
                      key={id}
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 4,
                        padding: "3px 7px",
                        borderRadius: 100,
                        fontSize: 11,
                        fontWeight: 500,
                        background: c.bg,
                        color: c.color,
                        border: `0.5px solid ${c.border}`,
                      }}
                    >
                      {kb.name}
                      <button
                        onClick={() => toggleKb(id)}
                        style={{ background: "none", border: "none", cursor: "pointer", color: c.color, opacity: 0.6, display: "flex", padding: 0 }}
                      >
                        <IconX size={10} />
                      </button>
                    </span>
                  );
                })}
                <button
                  onClick={() => setKbPopOpen((o) => !o)}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 4,
                    padding: "3px 8px",
                    borderRadius: 100,
                    border: "0.5px dashed var(--color-border-secondary)",
                    fontSize: 11,
                    color: "var(--color-text-tertiary)",
                    cursor: "pointer",
                    background: "transparent",
                  }}
                >
                  <IconDatabase size={11} />
                  {selectedKbIds.size > 0 ? "添加更多" : "添加知识库"}
                  {kbPopOpen ? <IconChevronUp size={10} /> : <IconChevronDown size={10} />}
                </button>
              </div>
            </div>

            {/* 输入行 */}
            <div style={{ display: "flex", gap: 7, alignItems: "center" }}>
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    send();
                  }
                }}
                disabled={hitlPending || kbsLoading}
                placeholder={
                  kbsLoading
                    ? "知识库加载中，请稍候…"
                    : hitlPending
                    ? "等待人工审批，输入已暂停…"
                    : isRunning
                    ? "Agent 运行中，请稍候…"
                    : "提出你的研究问题，Agent 将协同回答…"
                }
                style={{
                  flex: 1,
                  border: "1px solid var(--color-border-secondary)",
                  borderRadius: 8,
                  padding: "7px 11px",
                  fontSize: 13,
                  color: "var(--color-text-primary)",
                  background:
                    hitlPending || kbsLoading
                      ? "var(--color-background-secondary)"
                      : "var(--color-background-primary)",
                  cursor: hitlPending || kbsLoading ? "not-allowed" : "text",
                  outline: "none",
                  boxShadow: "0 0 0 0 transparent",
                  transition: "border-color 0.15s, box-shadow 0.15s",
                }}
                onFocus={(e) => {
                  e.currentTarget.style.borderColor = "#185FA5";
                  e.currentTarget.style.boxShadow = "0 0 0 3px rgba(24,95,165,0.12)";
                }}
                onBlur={(e) => {
                  e.currentTarget.style.borderColor = "var(--color-border-secondary)";
                  e.currentTarget.style.boxShadow = "0 0 0 0 transparent";
                }}
              />
              <button
                onClick={isRunning ? abort : send}
                disabled={hitlPending || kbsLoading || (!isRunning && !input.trim())}
                style={{
                  width: 32,
                  height: 32,
                  borderRadius: 7,
                  background:
                    hitlPending || kbsLoading || (!isRunning && !input.trim())
                      ? "var(--color-background-secondary)"
                      : "#185FA5",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  cursor:
                    hitlPending || kbsLoading || (!isRunning && !input.trim())
                      ? "not-allowed"
                      : "pointer",
                  flexShrink: 0,
                  border: "none",
                  color:
                    hitlPending || kbsLoading || (!isRunning && !input.trim())
                      ? "var(--color-text-tertiary)"
                      : "#fff",
                }}
              >
                {isRunning ? <IconSquare size={14} /> : <IconSend size={15} />}
              </button>
            </div>

            {/* 提示行 */}
            <div
              style={{
                fontSize: 11,
                color: "var(--color-text-tertiary)",
                marginTop: 6,
                display: "flex",
                alignItems: "center",
                gap: 4,
              }}
            >
              {hitlPending ? (
                <>
                  <IconLock size={12} />
                  HITL 审批通过后，Agent 将自动继续任务流
                </>
              ) : (
                <>已选 <strong>{selectedKbIds.size}</strong> 个知识库</>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
