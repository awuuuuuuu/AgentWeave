/**
 * Agent 群组 API
 * - GET  /agent/members        → Agent 成员列表
 * - POST /agent/stream         → 启动对话 SSE（chat / weave 共用）
 * - POST /agent/resume         → HITL 审批后恢复 SSE（chat / weave 共用）
 *
 * 会话管理 API
 * - GET    /sessions           → 列出会话
 * - POST   /sessions           → 创建会话
 * - PATCH  /sessions/{id}      → 更新会话
 * - DELETE /sessions/{id}      → 删除会话
 *
 * SSE 事件格式（chat session，与 backend/api/routes/agent.py 对齐）：
 *   {type: "node_start", node: AgentName}
 *   {type: "token",      node: AgentName, data: {content: string}}
 *   {type: "node_end",   node: AgentName, data: {citations?: Citation[]}}
 *   {type: "interrupt",  node: "hitl",    data: HITLData}
 *   {type: "done",       data: {citations: Citation[]}}
 *   {type: "error",      data: {message: string}}
 *
 * SSE 事件格式（weave session，来自 Weave Supervisor）：
 *   {type: "dept_report",   data: {...}}
 *   {type: "dispatch_plan", data: {steps: PlanStep[]}}
 *   {type: "plan_step",     data: {step_id, status, summary?}}
 *   {type: "map_update",    data: MapPayload}
 *   {type: "hitl_required", data: {step_id, title, dept_code, timeout_sec}}
 *   {type: "final_answer",  data: {content: string}}
 *   {type: "interrupt",     data: {type: "plan_review"|"step_review", ...}}
 *   {type: "done",          data: {}}
 *   {type: "error",         data: {message: string}}
 */

import { tokenStorage } from "./api";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

// Token refresh state shared within this module (mirrors api.ts logic for SSE calls)
let _agentRefreshing = false;
let _agentRefreshQueue: Array<(token: string | null) => void> = [];

async function _tryAgentRefresh(): Promise<string | null> {
  const refresh = tokenStorage.getRefresh();
  if (!refresh) return null;
  const res = await fetch(`${API_BASE}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refresh }),
  });
  if (!res.ok) { tokenStorage.clear(); return null; }
  const data = await res.json();
  const newAccess: string = data.access_token;
  localStorage.setItem("access_token", newAccess);
  document.cookie = `access_token=${newAccess}; path=/; SameSite=Lax`;
  return newAccess;
}

// ── 类型定义 ─────────────────────────────────────────────────────────────────

export type AgentName =
  | "user"
  | "memory_inject"
  | "supervisor"
  | "researcher"
  | "analyst"
  | "executor"
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

export interface MapMarker {
  position: [number, number];  // [lng, lat]
  label?: string;
  icon?: string;    // 如 "🚑" "🏥" "🚦" "💨" "📦"
  meta?: string;    // 附加说明，如 "ICU:5 急诊:12"
  color?: string;   // 路口/自定义着色覆盖
}

export interface MapRoute {
  from?: { lat: number; lng: number };
  to?:   { lat: number; lng: number };
  polyline: [number, number][];
  distance_m?: number;
  duration_seconds?: number;
  dept_code?: string;   // 用于路线着色
  color?: string;       // 十六进制，覆盖 dept_code 映射
}

export interface MapCircle {
  center: [number, number];   // [lng, lat]
  radius: number;             // 米
  color: string;              // 十六进制，如 "#ef4444"
  label?: string;             // 如 "ERPG-3 致命区"
}

export interface MapPayload {
  title?: string;
  center: [number, number];  // [lng, lat]
  zoom?: number;
  markers?: MapMarker[];
  route?: MapRoute;
  circles?: MapCircle[];
  layer?: string;       // 图层 ID（plume/resources/signals/routes/sensors/warehouse/cordon/incident_source）
  step_id?: string;     // 执行步骤 ID（marker↔执行卡联动）
  dept_code?: string;   // 来源部门代码
  unit_count?: number;  // 路线动画车辆数
  clear_routes_for_dept?: string;  // 召回操作：清除该部门的已渲染路线
}

export interface AgentCard {
  name: string;
  description: string;
  capabilities: string[];
}

export interface McpSource {
  idx: number;
  tool_name: string;
  key_result: string;
}

export interface DeptMetric {
  label: string;
  value?: string;
  unit?: string;
  severity?: string;       // critical | warn | ok | info
  source_idx?: number | null;
}

// Researcher 子图步骤 → 提示文字
export const RESEARCHER_STEP_TEXT: Record<string, string> = {
  retrieve: "正在检索知识库…",
  grade:    "正在评估文档质量…",
  rewrite:  "正在优化查询词…",
  generate: "正在生成答案…",
};

// Analyst MCP 工具名 → 提示文字（前缀匹配）
export const ANALYST_TOOL_TEXT: Array<[string, string]> = [
  ["geocode",               "正在解析地址坐标…"],
  ["plan_driving_route",    "正在规划驾车路线…"],
  ["get_hospital_capacity", "正在查询医院 ICU 容量…"],
  ["list_ambulances",       "正在查询救护车状态…"],
  ["dispatch_ambulance",    "正在调度救护车…"],
  ["calculate_plume",       "正在计算气体扩散范围…"],
  ["get_sensor_readings",   "正在读取传感器数据…"],
  ["get_critical_alarms",   "正在获取高风险传感器告警…"],
  ["get_incident_timeline", "正在检索事故时间线…"],
  ["list_intersections",    "正在查询路口信号状态…"],
  ["set_intersection_mode", "正在设置路口信号模式…"],
  ["batch_set_intersections","正在批量设置路口信号…"],
  ["get_inventory",         "正在查询应急物资库存…"],
  ["check_alerts",          "正在检查库存告警…"],
  ["dispatch_materials",    "正在调拨应急物资…"],
  ["get_equipment_status",  "正在查询设备状态…"],
];

export function resolveToolStatusText(toolName: string): string {
  const match = ANALYST_TOOL_TEXT.find(([key]) => toolName.includes(key));
  return match ? match[1] : `正在调用工具 ${toolName}…`;
}

export type AgentSSEEvent =
  | { type: "node_start"; node: AgentName }
  | { type: "token"; node: AgentName; data: { content: string } }
  | { type: "node_end"; node: AgentName; data: { citations?: Citation[]; mcp_sources?: McpSource[]; message_to_user?: string; answer_text?: string } }
  | { type: "status"; node: AgentName; data: { step: string; tool_name?: string } }
  | { type: "tool_result"; node: AgentName; data: McpSource }
  | { type: "interrupt"; node: "hitl"; data: HITLData }
  | { type: "map_update"; node?: AgentName; data: MapPayload }
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
  mcpSources?: McpSource[];      // analyst MCP 工具调用结果
  hitlData?: HITLData;           // interrupt 时才有
  mapData?: MapPayload;          // map_update 时才有
  replyTo?: { agentName: string; text: string };
  isFinalAnswer?: boolean;       // Reporter 完成后的最终汇总答案
}

// reply-to 硬推断：按节点在图中的依赖关系固定
export const REPLY_TO: Partial<Record<AgentName, { agentName: string; text: string }>> = {
  researcher: { agentName: "Supervisor", text: "收到，开始检索" },
  analyst:    { agentName: "Supervisor", text: "收到，调用工具分析" },
  executor:   { agentName: "Supervisor", text: "收到，执行写操作" },
  reporter:   { agentName: "Supervisor", text: "收到，整合最终答案" },
  hitl:       { agentName: "Supervisor", text: "需要人工确认" },
};

// ── Weave 类型 ─────────────────────────────────────────────────────────────────

export interface PlanStep {
  step_id: string;
  title: string;
  dept_code: string;
  task: string;
  is_high_risk: boolean;
  status: string;
  map_layer: string | null;
  result_summary: string;
  execution_tool?: string | null;
  execution_params?: Record<string, unknown> | null;
}

export type WeaveSSEEvent =
  | { type: "research_dispatch"; data: { incident?: string; tasks: Array<{ dept_code: string; task: string }> } }
  | { type: "dept_report";   data: { dept_code: string; status: string; summary: string; key_facts: string[]; metrics?: DeptMetric[]; map_events: MapPayload[]; citations?: Citation[]; mcp_sources?: McpSource[] } }
  | { type: "dispatch_plan"; data: { steps: PlanStep[] } }
  | { type: "direct_dispatch"; data: { dept_code: string; step_id: string; task: string; title: string } }
  | { type: "plan_step";     data: { step_id: string; status: string; summary?: string } }
  | { type: "map_update";    data: MapPayload }
  | { type: "hitl_required"; data: { step_id: string; title: string; dept_code: string; timeout_sec: number } }
  | { type: "location_candidates"; data: { candidates: Array<{ name: string; address: string; lat: number; lng: number; type?: string }>; query: string } }
  | { type: "final_answer";  data: { content: string } }
  | { type: "interrupt";     data: { type: "plan_review" | "step_review" | "location_select"; plan?: PlanStep[]; step_id?: string; title?: string; dept_code?: string; timeout_sec?: number } }
  | { type: "done";          data: Record<string, never> }
  | { type: "error";         data: { message: string } };

// ── Weave 部门 ────────────────────────────────────────────────────────────────

export interface WeaveDept {
  dept_code: string;   // "EN" | "ME" | "TR" | "LG" | "SF"
  name: string;
  org_id_key: string;
  a2a_port: number;
}

// ── 带鉴权的 fetch（含 401 自动续期，供 SSE 流式接口使用）────────────────────

async function authFetch(url: string, init?: RequestInit): Promise<Response> {
  const makeHeaders = (token: string | null) => ({
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(init?.headers ?? {}),
  });

  const res = await fetch(url, { ...init, headers: makeHeaders(tokenStorage.getAccess()) });
  if (res.status !== 401) return res;

  // 并发请求共用同一次 refresh
  if (_agentRefreshing) {
    const newToken = await new Promise<string | null>((resolve) => {
      _agentRefreshQueue.push(resolve);
    });
    if (!newToken) return res;
    return fetch(url, { ...init, headers: makeHeaders(newToken) });
  }

  _agentRefreshing = true;
  const newToken = await _tryAgentRefresh();
  _agentRefreshing = false;
  _agentRefreshQueue.forEach((cb) => cb(newToken));
  _agentRefreshQueue = [];

  if (!newToken) { if (typeof window !== "undefined") window.location.href = "/login"; return res; }
  return fetch(url, { ...init, headers: makeHeaders(newToken) });
}

// ── SSE 解析器（泛型，chat 和 weave 共用）─────────────────────────────────────

async function* parseSSE<T>(
  response: Response,
  signal?: AbortSignal
): AsyncGenerator<T> {
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
          yield JSON.parse(raw) as T;
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
  session_type: "chat" | "weave";
  message_count: number;
  kb_ids: string[] | null;
  created_at: string;
  updated_at: string | null;
}

export interface OrgDept {
  id: string;
  name: string;
  dept_code: string | null;
}

// ── 公共 API ──────────────────────────────────────────────────────────────────

/** 获取 Agent 成员列表（侧边栏展示用） */
export async function listAgentMembers(): Promise<AgentCard[]> {
  const res = await authFetch(`${API_BASE}/agent/members`);
  if (!res.ok) throw new Error("获取 Agent 列表失败");
  return res.json();
}

/** 启动 Agent 对话（chat session），返回 SSE 事件流 */
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
  yield* parseSSE<AgentSSEEvent>(res, signal);
}

/** 启动 Weave 应急会话，返回 SSE 事件流 */
export async function* streamWeave(
  incident: string,
  sessionId: string,
  selectedDeptCodes: string[] = [],
  signal?: AbortSignal
): AsyncGenerator<WeaveSSEEvent> {
  const res = await authFetch(`${API_BASE}/agent/stream`, {
    method: "POST",
    body: JSON.stringify({ query: incident, session_id: sessionId, selected_dept_codes: selectedDeptCodes }),
    signal,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "请求失败" }));
    throw new Error(err.detail ?? "Weave 请求失败");
  }
  yield* parseSSE<WeaveSSEEvent>(res, signal);
}

/** HITL 审批后恢复 Weave 执行，返回 SSE 事件流 */
export async function* resumeWeave(
  sessionId: string,
  decision: string | unknown[] | Record<string, unknown>,  // "approve" | "reject" | PlanStep[] | LocationCandidate
  signal?: AbortSignal
): AsyncGenerator<WeaveSSEEvent> {
  const res = await authFetch(`${API_BASE}/agent/resume`, {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, decision }),
    signal,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "审批请求失败" }));
    throw new Error(err.detail ?? "Weave 审批失败");
  }
  yield* parseSSE<WeaveSSEEvent>(res, signal);
}

// ── 会话 CRUD ─────────────────────────────────────────────────────────────────

export async function listSessions(): Promise<Session[]> {
  const res = await authFetch(`${API_BASE}/sessions`);
  if (!res.ok) throw new Error("获取会话列表失败");
  return res.json();
}

export async function createSession(
  kbIds: string[] = [],
  sessionType: "chat" | "weave" = "chat"
): Promise<Session> {
  const res = await authFetch(`${API_BASE}/sessions`, {
    method: "POST",
    body: JSON.stringify({ kb_ids: kbIds, session_type: sessionType }),
  });
  if (!res.ok) throw new Error("创建会话失败");
  return res.json();
}

export async function listDepartments(): Promise<OrgDept[]> {
  const res = await authFetch(`${API_BASE}/orgs/departments`);
  if (!res.ok) throw new Error("获取部门列表失败");
  return res.json();
}

export async function fetchWeaveDepts(): Promise<WeaveDept[]> {
  const res = await authFetch(`${API_BASE}/agent/weave/depts`);
  if (!res.ok) throw new Error("获取部门列表失败");
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
  yield* parseSSE<AgentSSEEvent>(res, signal);
}
