"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import { CC } from "./tokens";
import { CommandCenterPanel } from "./CommandCenterPanel";
import { AMapPanel } from "./AMapPanel";
import { KanbanDrawer } from "./KanbanDrawer";
import { PlanEditModal } from "./PlanEditModal";
import type {
  CommandCard,
  HITLNotification,
  HITLQueueItem,
  KanbanKpis,
  TaskEntry,
  SopStage,
} from "./types";
import {
  streamWeave,
  resumeWeave,
  type WeaveSSEEvent,
  type MapPayload,
  type PlanStep,
  type Session,
} from "@/lib/agent-api";

const DEPT_META: Record<string, { code: string; name: string }> = {
  env_agency:         { code: "EN", name: "环保局" },
  medical_ems:        { code: "ME", name: "医疗急救" },
  traffic_control:    { code: "TR", name: "交通管控" },
  emergency_supplies: { code: "LG", name: "应急物资" },
  fire_brigade:       { code: "FF", name: "消防救援" },
};

// 研判阶段固定顺序（与 weave_supervisor.py 路由顺序一致）
const RESEARCH_DEPTS = [
  { dept_code: "env_agency",         code: "EN", name: "环保局" },
  { dept_code: "medical_ems",        code: "ME", name: "医疗急救" },
  { dept_code: "traffic_control",    code: "TR", name: "交通管控" },
  { dept_code: "emergency_supplies", code: "LG", name: "应急物资" },
  { dept_code: "fire_brigade",       code: "FF", name: "消防救援" },
];

function deptMeta(dept_code: string) {
  return DEPT_META[dept_code] ?? { code: dept_code.slice(0, 2).toUpperCase(), name: dept_code };
}

const INITIAL_SOP: SopStage[] = [
  { id: "s1", label: "研判阶段",  status: "pending" },
  { id: "s2", label: "计划生成",  status: "pending" },
  { id: "s3", label: "HITL 审批", status: "pending" },
  { id: "s4", label: "执行阶段",  status: "pending" },
  { id: "s5", label: "综合报告",  status: "pending" },
];

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
  weaveSessions?: Session[];
  hitlQueue?: HITLQueueItem[];
  selectedDeptCodes?: string[];
}

export function CommandCenterLayout({
  sessionId,
  sessionTitle,
  weaveSessions = [],
  hitlQueue = [],
  selectedDeptCodes = [],
}: CommandCenterLayoutProps) {
  const [cards, setCards] = useState<CommandCard[]>([]);
  const [mapEvents, setMapEvents] = useState<MapPayload[]>([]);
  const [incidentCenter, setIncidentCenter] = useState<[number, number] | null>(null);
  const [hitl, setHitl] = useState<HITLNotification | null>(null);
  const [sopStages, setSopStages] = useState<SopStage[]>(INITIAL_SOP);
  const [tasks, setTasks] = useState<TaskEntry[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [pendingInterrupt, setPendingInterrupt] = useState<{
    type: string; plan?: PlanStep[]; step_id?: string; title?: string;
  } | null>(null);
  const [showPlanEdit, setShowPlanEdit] = useState(false);
  const [title, setTitle] = useState(sessionTitle ?? "新 Weave 会话");
  const [activeStepId, setActiveStepId] = useState<string | null>(null);  // A5：地图↔执行卡联动

  const abortRef               = useRef<AbortController | null>(null);
  const streamSettleRef        = useRef<Promise<void>>(Promise.resolve()); // 当前流结束后 resolve
  const hitlTimeoutRef         = useRef<ReturnType<typeof setTimeout> | null>(null);
  // _resumeRef 使每次渲染后的最新 _resume 可在 timeout callback 中安全调用
  const _resumeRef             = useRef<((decision: unknown) => Promise<void>) | undefined>(undefined);
  const deptCardIdxRef         = useRef<Record<string, number>>({});   // dept_code → card idx
  const plThinkingCardIdxRef   = useRef<number>(-1);                   // 初始思考卡位置
  const deptTotalRef           = useRef<number>(0);                    // 研判部门总数
  const deptDoneRef            = useRef<number>(0);                    // 已完成部门数
  const aggThinkingCardIdxRef  = useRef<number>(-1);                   // 聚合思考卡位置
  const planStepsRef           = useRef<PlanStep[]>([]);               // 执行计划步骤
  const dispatchPlanCardIdxRef = useRef<number>(-1);                   // 甘特卡位置
  const planStepIdxMapRef      = useRef<Record<string, number>>({});   // step_id→甘特索引
  const execCardIdxRef         = useRef<Record<string, number>>({});   // step_id→执行卡位置
  const hitlPendingRef         = useRef<boolean>(false);               // 阻止 done 清除 hitl

  // Reset state when switching sessions
  useEffect(() => {
    abortRef.current?.abort();
    deptCardIdxRef.current        = {};
    plThinkingCardIdxRef.current  = -1;
    deptTotalRef.current          = 0;
    deptDoneRef.current           = 0;
    aggThinkingCardIdxRef.current = -1;
    planStepsRef.current          = [];
    dispatchPlanCardIdxRef.current = -1;
    planStepIdxMapRef.current     = {};
    execCardIdxRef.current        = {};
    hitlPendingRef.current        = false;
    setCards([]);
    setMapEvents([]);
    setIncidentCenter(null);
    setHitl(null);
    setSopStages(INITIAL_SOP);
    setTasks([]);
    setIsRunning(false);
    setPendingInterrupt(null);
    setActiveStepId(null);
    setTitle(sessionTitle ?? "新 Weave 会话");
  }, [sessionId, sessionTitle]);

  // ── Event processor (uses only functional setState, no stale closure issues) ─

  function processEvent(event: WeaveSSEEvent) {
    switch (event.type) {
      case "research_dispatch": {
        // weave_supervisor 在 A2A 调用前推送，包含每个部门的实际任务文本
        const tasks = event.data.tasks;

        deptCardIdxRef.current = {};

        // 从任务文本提取可读摘要，兼容两种格式：
        //   LLM 格式：  "【研判阶段】结合知识库规程，使用 xxx 工具…"（无换行）
        //   模板格式：  "事故：xxx\n请调用 yyy 工具…"（换行分隔）
        function extractTaskBody(full: string): string {
          const bracketMatch = full.match(/^【[^\]]+】\s*([\s\S]*)/);
          if (bracketMatch) return bracketMatch[1].trim();
          if (full.includes("\n")) {
            return full.split("\n").slice(1).join("").replace(/^请/, "").trim();
          }
          return full.trim();
        }

        function displayTask(full: string): string {
          const body = extractTaskBody(full);
          return body.length > 30 ? body.slice(0, 30) + "…" : body;
        }

        // 事故名称：优先使用后端直接传递的 incident 字段，兼容旧格式（模板第一行 "事故：xxx"）
        const incident = event.data.incident
          ?? (() => {
            const firstLine = tasks[0]?.task.split("\n")[0] ?? "";
            return firstLine.replace(/^事故[：:]\s*/, "").trim();
          })();

        // 结构化部门任务（供 OrchReasoningCard 新设计使用）
        const deptTasksForCard = tasks.map((t) => {
          const m = deptMeta(t.dept_code);
          return { code: m.code, name: m.name, task: extractTaskBody(t.task) };
        });

        deptTotalRef.current = tasks.length;
        deptDoneRef.current  = 0;

        setCards((prev) => {
          // 按内容过滤移除初始 pl_thinking 卡（不依赖 index，规避 async 时序问题）
          const prefix = prev.filter(
            (c) => !(c.type === "pl_thinking" && c.message === "正在分析事故，制定应急响应计划...")
          );
          const base   = prefix.length; // orch=base, handoff=base+1, depts=base+2..

          tasks.forEach((t, i) => {
            deptCardIdxRef.current[t.dept_code] = base + 2 + i;
          });

          const insertCards: CommandCard[] = [
            {
              type: "orch_reasoning",
              think_lines: [],
              summary: `已分析事故，指派 ${tasks.length} 路子任务并行展开`,
              incident,
              dept_tasks: deptTasksForCard,
            },
            {
              type: "handoff",
              from: ["PL"],
              to: tasks.map((t) => deptMeta(t.dept_code).code),
              label: "parallel dispatch",
              payload: `${tasks.length} 路并行`,
            },
            // 各部门 running 占位卡（research workflow 动画从这里开始）
            ...tasks.map((t): CommandCard => {
              const m = deptMeta(t.dept_code);
              return {
                type: "dept_report",
                code: m.code,
                name: m.name,
                task: displayTask(t.task),
                status: "running",
                phase: "research",
              };
            }),
          ];
          return [...prefix, ...insertCards];
        });

        // 初始化看板（研判阶段，全部 running）
        setTasks(tasks.map((t) => {
          const m = deptMeta(t.dept_code);
          return {
            id: t.dept_code,
            name: displayTask(t.task),
            dept_code: m.code,
            dept_name: m.name,
            status: "running" as const,
          };
        }));

        break;
      }

      case "dept_report": {
        const d = event.data;
        const meta = deptMeta(d.dept_code);
        const isErr = d.status === "failed" || d.status === "timeout";
        const cardStatus: "done" | "error" = isErr ? "error" : "done";

        setCards((prev) => {
          const updated = [...prev];
          // Update the running dept card in-place → done
          const di = deptCardIdxRef.current[d.dept_code];
          if (di != null && updated[di]?.type === "dept_report") {
            const prev = updated[di] as Extract<CommandCard, { type: "dept_report" }>;
            updated[di] = {
              ...prev,
              status: cardStatus,
              summary: d.summary,
              facts: (d.key_facts ?? []).slice(0, 6),
              metrics: d.metrics ?? [],
              citations: d.citations ?? [],
              mcp_sources: d.mcp_sources ?? [],
              kvs: [],
              ...(isErr ? { err_detail: d.summary } : {}),
            };
          }
          return updated;
        });

        setTasks((prev) => {
          const entry: TaskEntry = {
            id: d.dept_code, name: meta.name + " 初始研判",
            dept_code: meta.code, dept_name: meta.name,
            status: cardStatus, summary: d.summary,
          };
          const idx = prev.findIndex((t) => t.id === d.dept_code);
          return idx >= 0 ? prev.map((t, i) => (i === idx ? entry : t)) : [...prev, entry];
        });

        // 将部门研判阶段产生的地图事件追加到地图面板
        if (d.map_events?.length) {
          setMapEvents((prev) => [...prev, ...d.map_events]);
          // 首次收到 ERPG 圆圈时，以圆心作为事故坐标（由 calculate_plume 结果派生，无需硬编码）
          setIncidentCenter((prev) => {
            if (prev) return prev;
            for (const me of d.map_events) {
              if (me.circles?.length) {
                return me.circles[0].center as [number, number];
              }
            }
            return prev;
          });
        }

        // 全部部门完成后插入 PL 聚合思考卡
        deptDoneRef.current += 1;
        if (deptDoneRef.current >= deptTotalRef.current && deptTotalRef.current > 0) {
          setCards((prev) => {
            const next: CommandCard[] = [
              ...prev,
              { type: "pl_thinking" as const, message: "汇总研判数据，制定执行计划..." },
            ];
            aggThinkingCardIdxRef.current = next.length - 1;
            return next;
          });
        }
        break;
      }

      case "dispatch_plan": {
        // All dept_reports received; LLM has generated the execution plan
        const steps = event.data.steps;
        planStepsRef.current = steps;
        steps.forEach((s, i) => { planStepIdxMapRef.current[s.step_id] = i; });

        setSopStages((prev) =>
          prev.map((s) =>
            s.id === "s1" ? { ...s, status: "done" }
            : s.id === "s2" ? { ...s, status: "done" }
            : s.id === "s3" ? { ...s, status: "active" }
            : s
          )
        );
        setCards((prev) => {
          // 按内容过滤移除聚合 pl_thinking 卡
          const prefix = prev.filter(
            (c) => !(c.type === "pl_thinking" && c.message === "汇总研判数据，制定执行计划...")
          );
          const base   = prefix.length; // handoff=base, dispatch_plan=base+1

          dispatchPlanCardIdxRef.current = base + 1;

          return [
            ...prefix,
            // Aggregate handoff: all depts → PL
            {
              type: "handoff" as const,
              from: RESEARCH_DEPTS.map((d) => d.code),
              to: ["PL"],
              label: "aggregate",
              payload: "↑ 汇总研判",
            },
            // Execution plan
            {
              type: "dispatch_plan" as const,
              agents: steps.map((s) => {
                const m = deptMeta(s.dept_code);
                return { code: m.code, name: m.name, task: s.task || s.title, step_id: s.step_id };
              }),
              progress: steps.map(() => "idle" as const),
            },
          ];
        });
        setTasks(
          steps.map((s) => {
            const m = deptMeta(s.dept_code);
            return { id: s.step_id, name: s.title, dept_code: m.code, dept_name: m.name, status: "pending" as const };
          })
        );
        break;
      }

      case "plan_step": {
        const { step_id, status, summary } = event.data;
        const taskStatus: TaskEntry["status"] =
          status === "done" || status === "approved" ? "done"
          : status === "failed" ? "error"
          : status === "running" ? "running"
          : status === "skipped" ? "done"
          : "pending";

        setTasks((prev) =>
          prev.map((t) => (t.id === step_id ? { ...t, status: taskStatus, summary: summary ?? t.summary } : t))
        );

        if (status === "running") {
          setSopStages((prev) =>
            prev.map((s) =>
              s.id === "s3" ? { ...s, status: "done" }
              : s.id === "s4" ? { ...s, status: "active" }
              : s
            )
          );
          // 执行已启动，清除审批条（approval 已处理完，spinner 不应继续显示）
          setHitl(null);
          // Insert handoff + exec dept card, update Gantt to running
          const step = planStepsRef.current.find((s) => s.step_id === step_id);
          if (step) {
            const m = deptMeta(step.dept_code);
            setCards((prev) => {
              const next: CommandCard[] = [
                ...prev,
                { type: "handoff" as const, from: ["PL"], to: [m.code], label: "execute", payload: step.title },
                { type: "dept_report" as const, code: m.code, name: m.name, task: step.title, status: "running", phase: "exec" },
              ];
              execCardIdxRef.current[step_id] = next.length - 1;

              const ganttIdx = planStepIdxMapRef.current[step_id];
              const planIdx  = dispatchPlanCardIdxRef.current;
              if (planIdx >= 0 && ganttIdx != null && next[planIdx]?.type === "dispatch_plan") {
                const pc = next[planIdx] as Extract<CommandCard, { type: "dispatch_plan" }>;
                const newProgress = [...pc.progress];
                newProgress[ganttIdx] = "running";
                next[planIdx] = { ...pc, progress: newProgress };
              }
              return next;
            });
          }
        } else {
          // done / failed / skipped — update exec card in-place + Gantt
          const cardStatus = taskStatus === "error" ? "error" : "done";
          setCards((prev) => {
            const updated = [...prev];
            const cardIdx = execCardIdxRef.current[step_id];
            if (cardIdx != null && updated[cardIdx]?.type === "dept_report") {
              updated[cardIdx] = { ...updated[cardIdx], status: cardStatus, summary: summary ?? undefined } as CommandCard;
            }
            const ganttIdx = planStepIdxMapRef.current[step_id];
            const planIdx  = dispatchPlanCardIdxRef.current;
            if (planIdx >= 0 && ganttIdx != null && updated[planIdx]?.type === "dispatch_plan") {
              const pc = updated[planIdx] as Extract<CommandCard, { type: "dispatch_plan" }>;
              const newProgress = [...pc.progress];
              newProgress[ganttIdx] = cardStatus === "error" ? "error" : "done";
              updated[planIdx] = { ...pc, progress: newProgress };
            }
            return updated;
          });
        }
        break;
      }

      case "map_update": {
        setMapEvents((prev) => [...prev, event.data]);
        // 警戒圈（计划生成时推送）→ 以其圆心确定事故中心，保证计划生成后即渲染事故标志
        if (event.data.layer === "cordon" && event.data.circles?.length) {
          setIncidentCenter((prev) => prev ?? (event.data.circles![0].center as [number, number]));
        }
        break;
      }

      case "hitl_required":
        // Pre-interrupt push; actual pause handled by "interrupt" event
        break;

      case "interrupt": {
        const payload = event.data;
        hitlPendingRef.current = true;
        setPendingInterrupt(payload);
        setIsRunning(false);
        // 单次综合 HITL：自动弹出审批弹窗
        setHitl({
          id: "hitl-plan",
          message: "请审批执行计划",
          detail: `共 ${(payload.plan ?? []).length} 步 · 可编辑、部分批准或拒绝`,
        });
        setSopStages((prev) => prev.map((s) => (s.id === "s3" ? { ...s, status: "active" } : s)));
        setCards((prev) => [
          ...prev,
          { type: "hitl_anchor" as const, message: "等待指挥长批准执行计划" },
        ]);
        setShowPlanEdit(true);
        break;
      }

      case "final_answer":
        setSopStages((prev) =>
          prev.map((s) => (s.id === "s5" ? { ...s, status: "active" } : s.id === "s4" ? { ...s, status: "done" } : s))
        );
        setCards((prev) => [
          ...prev,
          { type: "timestamp" as const, label: "综合报告：" + event.data.content.slice(0, 120) },
        ]);
        break;

      case "done":
        setIsRunning(false);
        // If an interrupt is pending, keep hitl visible — approval/rejection clears it
        if (!hitlPendingRef.current) {
          setHitl(null);
          setPendingInterrupt(null);
          setSopStages((prev) => prev.map((s) => ({ ...s, status: "done" as const })));
        }
        break;

      case "error":
        setIsRunning(false);
        setCards((prev) => [
          ...prev,
          { type: "timestamp" as const, label: `错误：${event.data.message}` },
        ]);
        break;
    }
  }

  async function runStream(gen: AsyncGenerator<WeaveSSEEvent>, ac: AbortController) {
    try {
      for await (const event of gen) {
        if (ac.signal.aborted) break;
        processEvent(event);
      }
    } catch (err) {
      if (!(err instanceof DOMException && err.name === "AbortError")) {
        console.error("[weave] stream error", err);
        setCards((prev) => [
          ...prev,
          { type: "timestamp" as const, label: `连接错误：${String(err)}` },
        ]);
        setIsRunning(false);
        // 恢复 HITL 按钮（审批请求失败时 isProcessing 留在 true，按钮消失，用户无法重试）
        setHitl((prev) => prev ? { ...prev, isProcessing: false } : null);
      }
    }
  }

  // ── Handlers ─────────────────────────────────────────────────────────────────

  const handleSubmit = useCallback(
    async (query: string) => {
      if (!query.trim() || isRunning) return;

      abortRef.current?.abort();
      const ac = new AbortController();
      abortRef.current = ac;

      deptCardIdxRef.current        = {};
      plThinkingCardIdxRef.current  = -1;
      deptTotalRef.current          = 0;
      deptDoneRef.current           = 0;
      aggThinkingCardIdxRef.current = -1;
      planStepsRef.current          = [];
      dispatchPlanCardIdxRef.current = -1;
      planStepIdxMapRef.current     = {};
      execCardIdxRef.current        = {};

      setIsRunning(true);
      setTitle(query.slice(0, 60));
      setSopStages(INITIAL_SOP.map((s) => (s.id === "s1" ? { ...s, status: "active" } : s)));

      // 立即插入用户消息 + PL 思考卡（研判计划生成中）
      setCards((prev) => {
        const next: CommandCard[] = [
          ...prev,
          { type: "user_msg" as const, content: query },
          { type: "pl_thinking" as const, message: "正在分析事故，制定应急响应计划..." },
        ];
        plThinkingCardIdxRef.current = next.length - 1;
        return next;
      });

      streamSettleRef.current = runStream(streamWeave(query, sessionId, selectedDeptCodes, ac.signal), ac);
      await streamSettleRef.current;
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sessionId, isRunning]
  );

  async function _resume(decision: unknown) {
    // 清除 HITL 超时定时器（用户已主动审批，不再需要自动拒绝）
    if (hitlTimeoutRef.current !== null) {
      clearTimeout(hitlTimeoutRef.current);
      hitlTimeoutRef.current = null;
    }
    hitlPendingRef.current = false;
    setHitl((prev) => prev ? { ...prev, isProcessing: true } : null);
    setPendingInterrupt(null);
    setShowPlanEdit(false);
    setIsRunning(true);
    // 等旧流自然结束后再发 resume：interrupt 事件到达时服务端仍在排水（drain）以完成
    // checkpoint 落盘，若此时立即调用 /agent/resume 会读到空 state.next → 409。
    await streamSettleRef.current;
    const ac = new AbortController();
    abortRef.current = ac;
    streamSettleRef.current = runStream(resumeWeave(sessionId, decision, ac.signal), ac);
    await streamSettleRef.current;
  }
  // 每次渲染后同步最新的 _resume，供 timeout callback 调用（不进 deps 避免无限重建）
  _resumeRef.current = _resume;

  // HITL 超时自动拒绝：pendingInterrupt 出现时启动倒计时
  useEffect(() => {
    if (!pendingInterrupt) {
      if (hitlTimeoutRef.current !== null) {
        clearTimeout(hitlTimeoutRef.current);
        hitlTimeoutRef.current = null;
      }
      return;
    }
    const timeoutSec = (pendingInterrupt as { timeout_sec?: number }).timeout_sec ?? 300;
    hitlTimeoutRef.current = setTimeout(() => {
      if (hitlPendingRef.current) {
        _resumeRef.current?.("reject");
      }
    }, timeoutSec * 1000);
    return () => {
      if (hitlTimeoutRef.current !== null) {
        clearTimeout(hitlTimeoutRef.current);
        hitlTimeoutRef.current = null;
      }
    };
  // pendingInterrupt 变更时重新绑定（sessionId 用于区分不同会话的 interrupt）
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingInterrupt, sessionId]);

  const handlePlanConfirm = useCallback(
    (steps: PlanStep[]) => _resume(steps),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sessionId],
  );

  const handlePlanReject = useCallback(
    () => _resume("reject"),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sessionId],
  );

  const handleOpenHitlModal = useCallback(() => setShowPlanEdit(true), []);

  // ── Render ────────────────────────────────────────────────────────────────────

  const kpis = buildKpis(weaveSessions, hitlQueue);

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
        <div
          style={{
            width: "54%",
            flexShrink: 0,
            position: "relative",
            borderRight: `1px solid ${CC.line}`,
            overflow: "hidden",
          }}
        >
          <CommandCenterPanel
            sessionId={sessionId}
            sessionTitle={title}
            cards={cards}
            hitl={hitl}
            sopStages={sopStages}
            isRunning={isRunning}
            onSubmit={handleSubmit}
            onOpenHitlModal={handleOpenHitlModal}
            activeStepId={activeStepId}
            onStepSelect={(id) => setActiveStepId((prev) => (prev === id ? null : id))}
          />
          {showPlanEdit && pendingInterrupt?.plan && (
            <PlanEditModal
              steps={pendingInterrupt.plan}
              onConfirm={handlePlanConfirm}
              onReject={handlePlanReject}
              onClose={() => setShowPlanEdit(false)}
            />
          )}
        </div>

        {/* 右列：46% — 地图 */}
        <div style={{ flex: 1, minWidth: 0, position: "relative", overflow: "hidden" }}>
          <AMapPanel
            mapEvents={mapEvents}
            incidentCenter={incidentCenter}
            activeStepId={activeStepId}
            onStepSelect={(id) => setActiveStepId((prev) => (prev === id ? null : id))}
          />
        </div>
      </div>

      {/* Row 2: 底部协同看板 */}
      <KanbanDrawer
        tasks={tasks}
        kpis={kpis}
        hitlQueue={hitlQueue}
        currentSessionId={sessionId}
        onApproveHitl={(_id) => {}}
        onRejectHitl={(_id) => {}}
      />
    </div>
  );
}
