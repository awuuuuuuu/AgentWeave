"use client";

import { CC } from "./tokens";
import { CommandCenterPanel } from "./CommandCenterPanel";
import { AMapPanel } from "./AMapPanel";
import { KanbanDrawer } from "./KanbanDrawer";
import type {
  AgentFleetEntry,
  CommandCard,
  HITLNotification,
  HITLQueueItem,
  KanbanKpis,
  TaskEntry,
  SopStage,
} from "./types";
import type { MapPayload, Session } from "@/lib/agent-api";

const DEMO_FLEET: AgentFleetEntry[] = [
  { id: "env",    code: "EN", name: "环保局",    status: "done",    elapsed_ms: 12400 },
  { id: "med",    code: "ME", name: "医疗急救",  status: "done",    elapsed_ms: 8100  },
  { id: "traf",   code: "TR", name: "交通管控",  status: "running", elapsed_ms: 11000 },
  { id: "supply", code: "LG", name: "应急物资",  status: "done",    elapsed_ms: 5900  },
  { id: "safety", code: "SF", name: "企业安全",  status: "error"                      },
];

const DEMO_TASKS: TaskEntry[] = [
  {
    id: "t-en", name: "大气扩散评估",
    dept_code: "EN", dept_name: "环保局",
    status: "done", elapsed_ms: 12400,
    summary: "下风向边界收敛，R₁ 1.8 km · R₂ 3.2 km，ESE 4.2 m/s",
  },
  {
    id: "t-me", name: "医疗资源预备",
    dept_code: "ME", dept_name: "医疗急救",
    status: "done", elapsed_ms: 8100,
    summary: "急救车 6 辆就位，滨海医院床位预留 40 张",
  },
  {
    id: "t-tr", name: "路口交通管控",
    dept_code: "TR", dept_name: "交通管控",
    status: "running", elapsed_ms: 11000,
  },
  {
    id: "t-lg", name: "应急物资调拨",
    dept_code: "LG", dept_name: "应急物资",
    status: "done", elapsed_ms: 5900,
    summary: "防护服 200 套，正压呼吸器 50 台已出库",
  },
  {
    id: "t-sf", name: "企业安全处置",
    dept_code: "SF", dept_name: "企业安全",
    status: "error",
    summary: "ERR_GATEWAY_TIMEOUT · 企业 IoT 网关无响应，建议切换人工核查",
  },
];

const DEMO_CARDS: CommandCard[] = [
  { type: "timestamp", label: "14:34 · 事件创建" },
  {
    type: "user_msg",
    content: "港城大道 388 号天宇化工储罐区，刚收到群众报警怀疑氨气泄漏，先按一级响应处置。",
    operator: "操作员 OP-073 · 张志远",
  },
  {
    type: "orch_reasoning",
    think_lines: [
      '// SOP 匹配',
      'incident_type = "chemical_leak.ammonia"',
      'severity = 1  // 一级响应',
      'sop = "SOP-A2 / 化学品泄漏"',
      '// 委派 5 路并行子任务',
      'dispatch = parallel([EN, ME, TR, LG, SF])',
    ],
    summary: "已匹配 SOP-A2，指派 5 路子任务并行展开",
  },
  {
    type: "handoff",
    from: ["PL"],
    to: ["EN", "ME", "TR", "LG", "SF"],
    label: "parallel dispatch",
    payload: "5 路并行",
  },
  {
    type: "dispatch_plan",
    agents: [
      { code: "EN", name: "环保局",   task: "大气扩散评估" },
      { code: "ME", name: "医疗急救", task: "医疗资源预备" },
      { code: "TR", name: "交通管控", task: "路口交通管控" },
      { code: "LG", name: "应急物资", task: "应急物资调拨" },
      { code: "SF", name: "企业安全", task: "企业安全处置" },
    ],
    progress: ["done", "done", "running", "done", "error"],
  },
  {
    type: "dept_report",
    code: "EN", name: "环保局", task: "大气扩散评估",
    status: "done", elapsed_ms: 12400,
    summary: "下风向边界收敛，建议疏散半径确认",
    kvs: [
      { k: "风向", v: "ESE 4.2 m/s" },
      { k: "R₁", v: "1.8 km" },
      { k: "R₂", v: "3.2 km" },
    ],
  },
  {
    type: "dept_report",
    code: "ME", name: "医疗急救", task: "医疗资源预备",
    status: "done", elapsed_ms: 8100,
    summary: "急救力量完成前置部署",
    kvs: [
      { k: "急救车", v: "6 辆" },
      { k: "预留床位", v: "40 张" },
      { k: "洗消站", v: "2 处" },
    ],
  },
  {
    type: "dept_report",
    code: "LG", name: "应急物资", task: "应急物资调拨",
    status: "done", elapsed_ms: 5900,
    summary: "物资已完成出库配送",
    kvs: [
      { k: "防护服", v: "200 套" },
      { k: "正压呼吸器", v: "50 台" },
      { k: "应急围堰", v: "4 组" },
    ],
  },
  {
    type: "dept_report",
    code: "TR", name: "交通管控", task: "路口交通管控",
    status: "running", elapsed_ms: 11000,
  },
  {
    type: "dept_report",
    code: "SF", name: "企业安全", task: "企业安全处置",
    status: "error",
    err_detail: "ERR_GATEWAY_TIMEOUT · 企业 IoT 网关无响应，无法远程关闭储罐阀门",
  },
  {
    type: "handoff",
    from: ["EN", "ME", "LG"],
    to: ["PL"],
    label: "aggregate",
    payload: "↑ escalate HITL",
  },
  {
    type: "hitl_anchor",
    message: "启动港城大道沿线居民疏散（约 3,200 户）",
  },
  { type: "timestamp", label: "14:38 · 等待操作员决策" },
];

const DEMO_SOP_STAGES: SopStage[] = [
  { id: "s1", label: "A2A 初始化", status: "done" },
  { id: "s2", label: "态势研判",   status: "done" },
  { id: "s3", label: "并发处置",   status: "active" },
  { id: "s4", label: "HITL 审批", status: "pending" },
  { id: "s5", label: "统一执行",   status: "pending" },
];

const DEMO_HITL: HITLNotification = {
  id: "hitl-001",
  message: "启动港城大道沿线居民疏散",
  detail: "影响约 3,200 户 · 预计 22 min · 决策窗口剩余 07:48",
};

const DEMO_TITLE = "港城大道 388 号 · 液氨泄漏 · 一级响应";

/** 将 fleet 转为任务条目（Stage A 兜底，有 DEMO_TASKS 时优先用） */
function fleetToTasks(fleet: AgentFleetEntry[]): TaskEntry[] {
  return fleet
    .filter((a) => a.code !== "PL")
    .map((a) => ({
      id: a.id,
      name: a.name + "任务",
      dept_code: a.code,
      dept_name: a.name,
      status: a.status === "running" ? "running"
            : a.status === "done"    ? "done"
            : a.status === "error"   ? "error"
            : "pending",
      elapsed_ms: a.elapsed_ms,
    } satisfies TaskEntry));
}

function buildKpis(sessions: Session[], hitlQueue: HITLQueueItem[]): KanbanKpis {
  return {
    active_sessions: sessions.filter((s) => s.status !== "completed" && s.status !== "deleted").length,
    hitl_pending: hitlQueue.length,
    agents_running: 0,
    errors: 0,
    completed_today: sessions.filter((s) => s.status === "completed").length,
  };
}

interface CommandCenterLayoutProps {
  sessionId: string;
  sessionTitle?: string;
  onSessionUpdated?: () => void;
  fleet?: AgentFleetEntry[];
  tasks?: TaskEntry[];           // Stage B SSE 填充；Stage A 由 fleet 推导
  cards?: CommandCard[];
  hitl?: HITLNotification | null;
  sopStages?: SopStage[];
  mapEvents?: MapPayload[];
  isRunning?: boolean;
  onSubmit?: (query: string) => void;
  onApprove?: () => void;
  onReject?: () => void;
  crewSessions?: Session[];
  hitlQueue?: HITLQueueItem[];
}

export function CommandCenterLayout({
  sessionId,
  sessionTitle,
  fleet = DEMO_FLEET,
  tasks,
  cards,
  hitl,
  sopStages,
  mapEvents = [],
  isRunning = true,
  onSubmit,
  onApprove,
  onReject,
  crewSessions = [],
  hitlQueue = [],
}: CommandCenterLayoutProps) {
  const isDemo = fleet === DEMO_FLEET;

  // Stage A demo 数据；Stage B SSE 会通过 props 覆盖
  const resolvedTasks     = tasks      ?? (isDemo ? DEMO_TASKS     : fleetToTasks(fleet));
  const resolvedCards     = cards      ?? (isDemo ? DEMO_CARDS     : []);
  const resolvedSopStages = sopStages  ?? (isDemo ? DEMO_SOP_STAGES : undefined);
  const resolvedHitl      = hitl       ?? (isDemo ? DEMO_HITL       : null);
  const resolvedTitle     = sessionTitle ?? (isDemo ? DEMO_TITLE    : "新 Crew 会话");

  const kpis = buildKpis(crewSessions, hitlQueue);

  return (
    <div
      style={{
        display: "grid",
        gridTemplateRows: "minmax(0, 1fr) auto",
        flex: 1,
        minHeight: 0,
        overflow: "hidden",
        background: CC.bg,
      }}
    >
      {/* Row 1: 54/46 主视图 */}
      <div style={{ display: "flex", minHeight: 0, overflow: "hidden" }}>
        {/* 左列：54% — 对话/卡片流 */}
        <div style={{
          width: "54%",
          flexShrink: 0,
          position: "relative",
          borderRight: `1px solid ${CC.line}`,
          overflow: "hidden",
        }}>
          <CommandCenterPanel
            sessionId={sessionId}
            sessionTitle={resolvedTitle}
            cards={resolvedCards}
            hitl={resolvedHitl}
            sopStages={resolvedSopStages}
            isRunning={isRunning}
            onSubmit={onSubmit}
            onApprove={onApprove}
            onReject={onReject}
          />
        </div>

        {/* 右列：46% — 地图 */}
        <div style={{ flex: 1, minWidth: 0, position: "relative", overflow: "hidden" }}>
          <AMapPanel mapEvents={mapEvents} />
        </div>
      </div>

      {/* Row 2: 底部协同看板 */}
      <KanbanDrawer
        tasks={resolvedTasks}
        kpis={kpis}
        hitlQueue={hitlQueue}
        currentSessionId={sessionId}
        onApproveHitl={(_id) => {}}
        onRejectHitl={(_id) => {}}
      />

    </div>
  );
}
