/**
 * Agent 群组 API
 * - GET  /agent/members        → Agent 成员列表
 * - POST /agent/stream         → 启动对话 SSE
 * - POST /agent/resume         → HITL 审批后恢复 SSE
 *
 * 会话管理 API
 * - GET    /sessions           → 列出会话
 * - POST   /sessions           → 创建会话
 * - PATCH  /sessions/{id}      → 更新会话
 * - DELETE /sessions/{id}      → 删除会话
 *
 * SSE 事件格式（与 backend/api/routes/agent.py 对齐）：
 *   {type: "node_start", node: AgentName}
 *   {type: "token",      node: AgentName, data: {content: string}}
 *   {type: "node_end",   node: AgentName, data: {citations?: Citation[]}}
 *   {type: "interrupt",  node: "hitl",    data: HITLData}
 *   {type: "done",       data: {citations: Citation[]}}
 *   {type: "error",      data: {message: string}}
 */

import { tokenStorage } from "./api";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

// ── 类型定义 ─────────────────────────────────────────────────────────────────

export type AgentName =
  | "user"
  | "memory_inject"
  | "supervisor"
  | "researcher"
  | "analyst"
  | "reporter"
  | "hitl"
  | "memory_save";

export interface Citation {
  ref: number;
  source_file: string;
  section_path: string;
  chunk_id: string;
  snippet?: string;
  score?: number;
}

export interface HITLData {
  tool_name: string;
  description: string;
  message: string;
}

export interface AgentCard {
  name: string;
  description: string;
  capabilities: string[];
}

export type AgentSSEEvent =
  | { type: "node_start"; node: AgentName }
  | { type: "token"; node: AgentName; data: { content: string } }
  | { type: "node_end"; node: AgentName; data: { citations?: Citation[]; message_to_user?: string; answer_text?: string } }
  | { type: "interrupt"; node: "hitl"; data: HITLData }
  | { type: "final_answer"; data: { content: string; citations: Citation[] } }
  | { type: "done"; data: { citations: Citation[] } }
  | { type: "error"; data: { message: string } };

// ── 消息气泡状态（供 AgentChatWindow 使用）────────────────────────────────────

export interface AgentBubble {
  id: string;
  agent: AgentName;
  content: string;
  status: "thinking" | "streaming" | "done" | "error";
  citations: Citation[];
  hitlData?: HITLData;           // interrupt 时才有
  replyTo?: { agentName: string; text: string };
  isFinalAnswer?: boolean;       // Reporter 完成后的最终汇总答案
}

// reply-to 硬推断：按节点在图中的依赖关系固定
export const REPLY_TO: Partial<Record<AgentName, { agentName: string; text: string }>> = {
  researcher: { agentName: "Supervisor", text: "收到，开始检索" },
  analyst:    { agentName: "Supervisor", text: "收到指令，开始建模" },
  reporter:   { agentName: "Supervisor", text: "收到，整合最终答案" },
  hitl:       { agentName: "Supervisor", text: "需要人工确认" },
};

// ── 带鉴权的 fetch（复用 api.ts 的 tokenStorage）────────────────────────────

async function authFetch(url: string, init?: RequestInit): Promise<Response> {
  const token = tokenStorage.getAccess();
  return fetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers ?? {}),
    },
  });
}

// ── SSE 解析器（复用 api.ts 的模式）─────────────────────────────────────────

async function* parseSSE(
  response: Response,
  signal?: AbortSignal
): AsyncGenerator<AgentSSEEvent> {
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  try {
    while (true) {
      if (signal?.aborted) break;
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });

      const lines = buf.split("\n");
      buf = lines.pop() ?? "";

      for (const line of lines) {
        if (!line.startsWith("data:")) continue;
        const raw = line.slice(5).trim();
        if (raw === "[DONE]") return;
        try {
          yield JSON.parse(raw) as AgentSSEEvent;
        } catch {
          console.warn("[agent-api] malformed SSE line:", raw);
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

// ── 会话类型 ─────────────────────────────────────────────────────────────────

export interface Session {
  id: string;
  title: string | null;
  status: string;
  message_count: number;
  kb_ids: string[] | null;
  created_at: string;
  updated_at: string | null;
}

// ── 公共 API ──────────────────────────────────────────────────────────────────

/** 获取 Agent 成员列表（侧边栏展示用） */
export async function listAgentMembers(): Promise<AgentCard[]> {
  const res = await authFetch(`${API_BASE}/agent/members`);
  if (!res.ok) throw new Error("获取 Agent 列表失败");
  return res.json();
}

/** 启动 Agent 对话，返回 SSE 事件流 */
export async function* streamAgent(
  query: string,
  sessionId: string,
  kbIds: string[],
  signal?: AbortSignal
): AsyncGenerator<AgentSSEEvent> {
  console.log("[agent-api] streamAgent kb_ids=", kbIds);
  const res = await authFetch(`${API_BASE}/agent/stream`, {
    method: "POST",
    body: JSON.stringify({ query, session_id: sessionId, kb_ids: kbIds }),
    signal,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "请求失败" }));
    throw new Error(err.detail ?? "Agent 请求失败");
  }
  yield* parseSSE(res, signal);
}

// ── 会话 CRUD ─────────────────────────────────────────────────────────────────

export async function listSessions(): Promise<Session[]> {
  const res = await authFetch(`${API_BASE}/sessions`);
  if (!res.ok) throw new Error("获取会话列表失败");
  return res.json();
}

export async function createSession(kbIds: string[] = []): Promise<Session> {
  const res = await authFetch(`${API_BASE}/sessions`, {
    method: "POST",
    body: JSON.stringify({ kb_ids: kbIds }),
  });
  if (!res.ok) throw new Error("创建会话失败");
  return res.json();
}

export async function updateSession(
  sessionId: string,
  data: { title?: string; status?: string; message_count?: number }
): Promise<Session> {
  const res = await authFetch(`${API_BASE}/sessions/${sessionId}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("更新会话失败");
  return res.json();
}

export async function deleteSession(sessionId: string): Promise<void> {
  const res = await authFetch(`${API_BASE}/sessions/${sessionId}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error("删除会话失败");
}

// ── 消息持久化 ────────────────────────────────────────────────────────────────

export interface ChatMessageIn {
  seq: number;
  agent: string;
  content: string;
  citations?: Citation[] | null;
  hitl_data?: HITLData | null;
  reply_to?: { agentName: string; text: string } | null;
  is_final_answer?: boolean;
}

export interface ChatMessageOut {
  id: string;
  seq: number;
  agent: AgentName;
  content: string;
  citations: Citation[] | null;
  hitl_data: HITLData | null;
  reply_to: { agentName: string; text: string } | null;
  is_final_answer: boolean;
}

export async function listMessages(sessionId: string): Promise<ChatMessageOut[]> {
  const res = await authFetch(`${API_BASE}/sessions/${sessionId}/messages`);
  if (!res.ok) throw new Error("获取消息列表失败");
  return res.json();
}

export async function saveMessages(
  sessionId: string,
  messages: ChatMessageIn[]
): Promise<void> {
  if (messages.length === 0) return;
  const res = await authFetch(`${API_BASE}/sessions/${sessionId}/messages`, {
    method: "POST",
    body: JSON.stringify(messages),
  });
  if (!res.ok) throw new Error("保存消息失败");
}

/** 将 DB 返回的消息转换为前端气泡 */
export function msgTobubble(msg: ChatMessageOut): AgentBubble {
  return {
    id: msg.id,
    agent: msg.agent,
    content: msg.content,
    status: "done",
    citations: msg.citations ?? [],
    hitlData: msg.hitl_data ?? undefined,
    replyTo: msg.reply_to ?? undefined,
    isFinalAnswer: msg.is_final_answer,
  };
}

/** 将前端气泡转换为 DB 写入格式 */
export function bubbleToMsg(bubble: AgentBubble, seq: number): ChatMessageIn {
  return {
    seq,
    agent: bubble.agent,
    content: bubble.content,
    citations: bubble.citations.length > 0 ? bubble.citations : null,
    hitl_data: bubble.hitlData ?? null,
    reply_to: bubble.replyTo ?? null,
    is_final_answer: bubble.isFinalAnswer ?? false,
  };
}

/** 关闭会话，触发后端 memory_save（fire-and-forget，忽略错误） */
export async function closeSession(sessionId: string): Promise<void> {
  try {
    await authFetch(`${API_BASE}/agent/sessions/${sessionId}/close`, {
      method: "POST",
    });
  } catch {
    // 非关键路径，静默失败
  }
}

/** HITL 审批后恢复执行，返回 SSE 事件流 */
export async function* resumeAgent(
  sessionId: string,
  decision: "approve" | "reject",
  signal?: AbortSignal
): AsyncGenerator<AgentSSEEvent> {
  const res = await authFetch(`${API_BASE}/agent/resume`, {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, decision }),
    signal,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "审批请求失败" }));
    throw new Error(err.detail ?? "审批失败");
  }
  yield* parseSSE(res, signal);
}
