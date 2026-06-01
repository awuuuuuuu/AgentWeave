import type { Citation, DeptMetric, MapPayload } from "@/lib/agent-api";
export type { Citation, DeptMetric, MapPayload };

export interface LocationCandidate {
  name: string; address: string; lat: number; lng: number; type?: string;
}

export interface McpSource {
  idx: number;
  tool_name: string;
  key_result: string;
}

// ── Agent Fleet ────────────────────────────────────────────────────────────

export interface AgentFleetEntry {
  id: string;           // 'env' | 'med' | 'traf' | 'supply' | 'fire' | 'orch'
  code: string;         // 'EN' | 'ME' | 'TR' | 'LG' | 'FF' | 'PL'
  name: string;         // 显示名称，如 '环保局'
  status: "idle" | "running" | "done" | "error";
  elapsed_ms?: number;
}

// ── Card Stream ────────────────────────────────────────────────────────────

export type CommandCard =
  | { type: "user_msg";        content: string; operator?: string }
  | { type: "pl_thinking";     message: string }
  | { type: "location_picker"; candidates: LocationCandidate[]; query: string; confirmed?: LocationCandidate }
  | { type: "orch_reasoning";  think_lines: string[]; summary: string;
      incident?: string;
      dept_tasks?: Array<{ code: string; name: string; task: string }> }
  | { type: "handoff";         from: string[]; to: string[]; label?: string; payload?: string }
  | { type: "dispatch_plan";   agents: { code: string; name: string; task: string; step_id?: string }[];
                                progress: ("done" | "running" | "error" | "idle")[];
                                hitl_message?: string }
  | { type: "dept_report";     code: string; name: string; task: string;
                                status: "done" | "running" | "error";
                                phase?: "research" | "exec";
                                direct?: boolean;
                                elapsed_ms?: number;
                                summary?: string;
                                facts?: string[];
                                metrics?: DeptMetric[];
                                map_events?: MapPayload[];
                                kvs?: { k: string; v: string }[];
                                citations?: Citation[];
                                mcp_sources?: McpSource[];
                                err_detail?: string }
  | { type: "timestamp";      label: string };

// ── HITL Bar（流外固定区域）─────────────────────────────────────────────────

export interface HITLNotification {
  id: string;
  message: string;
  detail?: string;
  dept_code?: string;   // step_review 时指明执行部门
  isProcessing?: boolean; // approve/reject 后等待后端响应期间
}

// ── Map ────────────────────────────────────────────────────────────────────

export interface MapLayer {
  id: string;
  name: string;
  color: string;
  enabled: boolean;
}

// ── Kanban Drawer ───────────────────────────────────────────────────────────

export type SessionSeverity = 1 | 2 | 3; // 1=critical(red), 2=warning(amber), 3=ok(green)

/** 用于 Kanban 抽屉的会话摘要（一行 = 一个 Weave 会话） */
export interface SessionKanbanEntry {
  id: string;
  title: string | null;
  severity: SessionSeverity;
  elapsed_ms: number;             // 从创建到当前的毫秒数
  agents: Record<string, "ok" | "running" | "error" | "idle">; // code → status
  hitl_message?: string;          // 有 HITL 时显示文字（非null=有待审）
  hitl_countdown_s?: number;      // 倒计时秒数
  error_agent?: string;           // 出错的 agent code
}

/** 跨会话 HITL 队列条目 */
export interface HITLQueueItem {
  id: string;
  session_id: string;
  session_title: string | null;
  message: string;
  detail?: string;
  countdown_s: number;
  urgent: boolean;                 // true = 倒计时 < 3 min
}

/** SOP 流程阶段 */
export interface SopStage {
  id: string;
  label: string;
  status: "done" | "active" | "pending";
}

/** 分配任务条目（Kanban 把手条 + 任务列表） */
export interface TaskEntry {
  id: string;
  name: string;                                          // "大气扩散评估"
  dept_code: string;                                     // "EN"
  dept_name: string;                                     // "环保局"
  status: "pending" | "running" | "done" | "error";
  elapsed_ms?: number;
  summary?: string;                                      // 完成摘要
}

/** 看板 KPI */
export interface KanbanKpis {
  active_sessions: number;
  hitl_pending: number;
  agents_running: number;
  errors: number;
  completed_today: number;
}
