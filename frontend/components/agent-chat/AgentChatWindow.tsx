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
  IconCar,
  IconBrain,
  IconCode,
  IconLock,
  IconRefresh,
} from "@tabler/icons-react";
import { v4 as uuidv4 } from "uuid";
import { toast } from "sonner";

import {
  streamAgent,
  resumeAgent,
  REPLY_TO,
  type AgentBubble,
  type AgentName,
  type Citation,
} from "@/lib/agent-api";
import { apiListKBs, type KnowledgeBase } from "@/lib/api";
import { AgentMessage } from "./AgentMessage";
import { AgentSidebar } from "./AgentSidebar";
import { HITLCard } from "./HITLCard";

// ── 知识库颜色配置 ────────────────────────────────────────────────────────────

const KB_COLORS = [
  { bg: "#E1F5EE", color: "#085041", border: "#5DCAA5" },
  { bg: "#EEEDFE", color: "#3C3489", border: "#AFA9EC" },
  { bg: "#FAEEDA", color: "#633806", border: "#FAC775" },
  { bg: "#FAECE7", color: "#712B13", border: "#F0997B" },
  { bg: "#F1EFE8", color: "#444441", border: "#B4B2A9" },
];

// ── 建议提问 ──────────────────────────────────────────────────────────────────

const SUGGESTIONS = [
  { icon: <IconCar size={14} />, text: "分析 2024 年新能源汽车市场竞争格局" },
  { icon: <IconBrain size={14} />, text: "梳理 AI 大模型赛道头部玩家的融资情况" },
  { icon: <IconCode size={14} />, text: "对比 GPT-4o 与 Claude 在代码生成场景的能力差异" },
];

// ── 工具函数 ──────────────────────────────────────────────────────────────────

function makeBubbleId() {
  return Math.random().toString(36).slice(2, 9);
}

// ── 主组件 ────────────────────────────────────────────────────────────────────

export function AgentChatWindow() {
  // sessionId 和 bubbles 必须在 useEffect 里从 sessionStorage 初始化
  // 不能在 useState 懒初始化里读 sessionStorage（SSR/客户端不一致会导致 hydration 报错）
  const [sessionId, setSessionId] = useState<string>("");
  const [bubbles, setBubbles] = useState<AgentBubble[]>([]);
  const [input, setInput] = useState("");
  const [isRunning, setIsRunning] = useState(false);
  const [hitlPending, setHitlPending] = useState(false);
  const [activeAgent, setActiveAgent] = useState<AgentName | null>(null);

  // 知识库
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [selectedKbIds, setSelectedKbIds] = useState<Set<string>>(new Set());
  const [kbPopOpen, setKbPopOpen] = useState(false);
  const [kbsLoading, setKbsLoading] = useState(true);   // 加载完成前禁止发送

  const abortRef = useRef<AbortController | null>(null);
  const msgsRef = useRef<HTMLDivElement>(null);
  const atBottomRef = useRef(true);
  const kbPopRef = useRef<HTMLDivElement>(null);

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

  // 客户端挂载后从 sessionStorage 恢复 sessionId 和聊天记录（避免 SSR/hydration 不一致）
  useEffect(() => {
    // sessionId
    const storedId = sessionStorage.getItem("agent_session_id");
    if (storedId) {
      setSessionId(storedId);
    } else {
      const id = uuidv4();
      sessionStorage.setItem("agent_session_id", id);
      setSessionId(id);
    }

    // 聊天记录
    try {
      const raw = sessionStorage.getItem("agent_bubbles");
      if (raw) setBubbles(JSON.parse(raw) as AgentBubble[]);
    } catch { }
  }, []);

  // 加载知识库列表（完成前阻止发送，避免 kb_ids=[] 的空请求）
  useEffect(() => {
    apiListKBs()
      .then((list) => {
        setKbs(list);
        if (list.length > 0) setSelectedKbIds(new Set([list[0].id]));
      })
      .catch(() => { })
      .finally(() => setKbsLoading(false));
  }, []);

  // 聊天记录持久化（仅保存 done 状态的气泡，避免保存半途截断的 streaming 状态）
  useEffect(() => {
    const stable = bubbles.filter((b) => b.status === "done" || b.status === "error");
    sessionStorage.setItem("agent_bubbles", JSON.stringify(stable));
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

    // 每个节点对应一个 bubbleId
    const nodeBubbleId: Partial<Record<AgentName, string>> = {};

    for await (const event of gen) {
      if (event.type === "node_start") {
        const node = event.node;
        setActiveAgent(node);

        // memory 节点不创建大气泡，只会在 node_end 时显示系统条
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
          content: (prev?.content ?? "") + chunk,
          status: "streaming",
        }));

        atBottomRef.current = true; // 流式时强制滚到底
      }

      else if (event.type === "node_end") {
        const node = event.node;
        const id = nodeBubbleId[node];
        if (!id) continue;
        const citations: Citation[] = event.data.citations ?? [];
        const messageToUser = event.data.message_to_user;
        const answerText = event.data.answer_text;
        const criticScore = event.data.critic_score;
        const criticFeedback = event.data.critic_feedback;
        const criticApproved = event.data.critic_approved;

        // Supervisor 二次路由（纯内部决策）时无内容，直接删掉气泡避免重复展示
        if (node === "supervisor" && !messageToUser) {
          setBubbles((prev) => prev.filter((b) => b.id !== id));
          setActiveAgent(null);
          continue;
        }

        upsertBubble(id, (prev) => ({
          ...(prev ?? { id, agent: node, content: "", replyTo: REPLY_TO[node] }),
          status: "done",
          citations,
          // supervisor 没有 token 流，用 message_to_user 作为气泡内容
          ...(messageToUser ? { content: messageToUser } : {}),
          // researcher 使用 ainvoke，无 token 流，答案从 node_end 的 answer_text 取
          ...(answerText ? { content: answerText } : {}),
          ...(criticScore !== undefined ? { criticScore } : {}),
          ...(criticFeedback ? { criticFeedback } : {}),
          ...(criticApproved !== undefined ? { criticApproved } : {}),
        }));

        setActiveAgent(null);
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
        return; // SSE 在 interrupt 时自然结束，等待 resume
      }

      else if (event.type === "final_answer") {
        // Reporter 整合后输出最终答案气泡（超纲降级时由 supervisor message_to_user 触发）
        // 若 reporter 的 node_start/node_end 已创建气泡，更新它；否则新建
        const reporterBubbleId = nodeBubbleId["reporter"];
        if (reporterBubbleId) {
          upsertBubble(reporterBubbleId, (prev) => ({
            ...(prev ?? { id: reporterBubbleId, agent: "reporter" as AgentName, citations: [], replyTo: REPLY_TO["reporter"] }),
            content: event.data.content,
            status: "done",
            citations: event.data.citations ?? [],
            isFinalAnswer: true,
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
            },
          ]);
        }
      }

      else if (event.type === "done") {
        setIsRunning(false);
        setActiveAgent(null);
      }

      else if (event.type === "error") {
        toast.error(event.data.message || "Agent 执行出错");
        setIsRunning(false);
        setActiveAgent(null);
      }
    }

    setIsRunning(false);
    setActiveAgent(null);
  }

  // ── 发送消息 ────────────────────────────────────────────────────────────────

  const send = useCallback(async () => {
    const q = input.trim();
    if (!q || isRunning || hitlPending || kbsLoading) return;
    setInput("");
    setIsRunning(true);

    // 追加用户消息气泡
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
        toast.error(e.message || "连接失败");
      }
      setIsRunning(false);
      setActiveAgent(null);
    }
  }, [input, isRunning, hitlPending, kbsLoading, sessionId, selectedKbIds]);

  // ── HITL 审批 ───────────────────────────────────────────────────────────────

  const handleHITLDecision = useCallback(
    async (decision: "approve" | "reject") => {
      setHitlPending(false);

      if (decision === "reject") {
        return;
      }

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
    <div style={{ display: "flex", height: "100%", overflow: "hidden" }}>
      {/* 侧边栏 */}
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
            Agent 群组
          </span>
          <span style={{ fontSize: 12, color: "var(--color-text-tertiary)" }} suppressHydrationWarning>
            · #{shortId}
          </span>

          {/* 新对话按钮 */}
          {!isRunning && (
            <button
              onClick={() => {
                const id = uuidv4();
                sessionStorage.setItem("agent_session_id", id);
                sessionStorage.removeItem("agent_bubbles");
                setSessionId(id);
                setBubbles([]);
                setInput("");
                setHitlPending(false);
                setActiveAgent(null);
              }}
              title="开启新对话"
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                padding: "3px 8px",
                borderRadius: 6,
                border: "0.5px solid var(--color-border-secondary)",
                background: "transparent",
                fontSize: 11,
                color: "var(--color-text-tertiary)",
                cursor: "pointer",
              }}
            >
              <IconRefresh size={11} />
              新对话
            </button>
          )}

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
          style={{ flex: 1, overflowY: "auto" }}
        >
          <div
            style={{
              maxWidth: 800,
              width: "100%",
              margin: "0 auto",
              padding: "18px 22px",
              display: "flex",
              flexDirection: "column",
              gap: 14,
              minHeight: "100%",
            }}
          >
            {!hasMessages ? (
              /* 空状态 */
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
                  向 Agent 群组提问
                </div>
                <div style={{ fontSize: 13, color: "var(--color-text-tertiary)", textAlign: "center", lineHeight: 1.6, maxWidth: 320 }}>
                  Supervisor 会自动拆解任务，调度 Researcher、Analyst、Critic 协同完成。
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 6, width: "100%", maxWidth: 360 }}>
                  {SUGGESTIONS.map((s, i) => (
                    <button
                      key={i}
                      onClick={() => setInput(s.text)}
                      style={{
                        background: "var(--color-background-secondary)",
                        border: "0.5px solid var(--color-border-secondary)",
                        borderRadius: 8,
                        padding: "8px 12px",
                        fontSize: 12,
                        color: "var(--color-text-secondary)",
                        cursor: "pointer",
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        textAlign: "left",
                      }}
                    >
                      <span style={{ color: "var(--color-text-tertiary)", flexShrink: 0 }}>
                        {s.icon}
                      </span>
                      {s.text}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              bubbles.map((bubble) =>
                bubble.hitlData ? (
                  /* HITL 审批卡片 */
                  <HITLCard
                    key={bubble.id}
                    data={bubble.hitlData}
                    onDecision={handleHITLDecision}
                    disabled={!hitlPending}
                  />
                ) : (
                  <AgentMessage key={bubble.id} bubble={bubble} />
                )
              )
            )}
          </div>
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
                        : "提出你的研究问题，Agent 群组将协同回答…"
                }
                style={{
                  flex: 1,
                  border: "0.5px solid var(--color-border-secondary)",
                  borderRadius: 8,
                  padding: "7px 11px",
                  fontSize: 13,
                  color: "var(--color-text-primary)",
                  background: (hitlPending || kbsLoading) ? "var(--color-background-secondary)" : "var(--color-background-primary)",
                  cursor: (hitlPending || kbsLoading) ? "not-allowed" : "text",
                  outline: "none",
                }}
              />
              <button
                onClick={isRunning ? abort : send}
                disabled={hitlPending || kbsLoading || (!isRunning && !input.trim())}
                style={{
                  width: 32,
                  height: 32,
                  borderRadius: 7,
                  background: (hitlPending || kbsLoading || (!isRunning && !input.trim())) ? "var(--color-background-secondary)" : "#185FA5",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  cursor: (hitlPending || kbsLoading || (!isRunning && !input.trim())) ? "not-allowed" : "pointer",
                  flexShrink: 0,
                  border: "none",
                  color: (hitlPending || kbsLoading || (!isRunning && !input.trim())) ? "var(--color-text-tertiary)" : "#fff",
                }}
              >
                {isRunning ? <IconSquare size={14} /> : <IconSend size={15} />}
              </button>
            </div>

            {/* 提示行 */}
            <div style={{ fontSize: 11, color: "var(--color-text-tertiary)", marginTop: 6, display: "flex", alignItems: "center", gap: 4 }}>
              {hitlPending ? (
                <>
                  <IconLock size={12} />
                  HITL 审批通过后，Agent 将自动继续任务流
                </>
              ) : (
                <>
                  已选 <strong>{selectedKbIds.size}</strong> 个知识库
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
