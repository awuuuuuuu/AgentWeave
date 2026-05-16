/**
 * Agent 群组 API
 * - GET  /agent/members        → Agent 成员列表
 * - POST /agent/stream         → 启动对话 SSE
 * - POST /agent/resume         → HITL 审批后恢复 SSE
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
