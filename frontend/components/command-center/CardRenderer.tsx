"use client";

import { CC, agentColor } from "./tokens";
import type { CommandCard } from "./types";

// ── Agent 元信息 ─────────────────────────────────────────────────────────────

const AGENT_META: Record<string, { name: string; short: string }> = {
  PL: { name: "指挥中心", short: "PL" },
  EN: { name: "环保局",   short: "EN" },
  ME: { name: "医疗急救", short: "ME" },
  TR: { name: "交通管控", short: "TR" },
  LG: { name: "应急物资", short: "LG" },
  SF: { name: "企业安全", short: "SF" },
};

// ── 小工具 ───────────────────────────────────────────────────────────────────

function Avatar({ code }: { code: string }) {
  const color = agentColor(code);
  return (
    <div style={{
      width: 28, height: 28, borderRadius: 8, flexShrink: 0,
      background: `color-mix(in oklab, ${color} 18%, ${CC.panel2})`,
      border: `1px solid color-mix(in oklab, ${color} 30%, ${CC.line})`,
      color,
      display: "flex", alignItems: "center", justifyContent: "center",
      fontFamily: "monospace", fontSize: 9, fontWeight: 700, letterSpacing: "0.04em",
    }}>
      {(AGENT_META[code]?.short ?? code).slice(0, 2)}
    </div>
  );
}

function AgentDot({ code }: { code: string }) {
  return (
    <span style={{
      width: 5, height: 5, borderRadius: "50%",
      background: agentColor(code), display: "inline-block", flexShrink: 0,
    }} />
  );
}

function Badge({ label, color }: { label: string; color?: string }) {
  const c = color ?? CC.muted2;
  return (
    <span style={{
      fontSize: 9, fontWeight: 700, letterSpacing: "0.06em",
      padding: "1px 6px", borderRadius: 4,
      background: `color-mix(in oklab, ${c} 14%, transparent)`,
      border: `1px solid color-mix(in oklab, ${c} 28%, transparent)`,
      color: c,
    }}>
      {label}
    </span>
  );
}

function ReplyTo({ agent, text }: { agent: string; text: string }) {
  const color = agentColor(agent);
  return (
    <div style={{
      borderLeft: `2px solid color-mix(in oklab, ${color} 50%, transparent)`,
      paddingLeft: 8, marginBottom: 8,
      fontSize: 11, color: CC.muted,
    }}>
      <span style={{ fontWeight: 600, color }}>@{AGENT_META[agent]?.name ?? agent}</span>
      {" · "}{text}
    </div>
  );
}

/** Timeline row 包装：头像 + 右侧内容 */
function TlRow({ code, badge, badgeColor, elapsed, children }: {
  code: string;
  badge?: string;
  badgeColor?: string;
  elapsed?: number;
  children: React.ReactNode;
}) {
  const color = agentColor(code);
  const meta = AGENT_META[code] ?? { name: code };
  return (
    <div style={{ display: "flex", gap: 10, padding: "3px 16px", alignItems: "flex-start" }}>
      <Avatar code={code} />
      <div style={{ flex: 1, minWidth: 0 }}>
        {/* 头部：点 + 名 + badge + 耗时 */}
        <div style={{
          fontSize: 11, marginBottom: 5,
          display: "flex", alignItems: "center", gap: 5,
        }}>
          <AgentDot code={code} />
          <span style={{ fontWeight: 600, color }}>{meta.name}</span>
          {badge && <Badge label={badge} color={badgeColor ?? color} />}
          {elapsed != null && (
            <span style={{ fontSize: 10, color: CC.muted, fontFamily: "monospace", marginLeft: "auto" }}>
              {(elapsed / 1000).toFixed(1)}s
            </span>
          )}
        </div>
        {children}
      </div>
    </div>
  );
}

/** 通用卡片容器 */
function Card({ stripe, children }: { stripe?: string; children: React.ReactNode }) {
  return (
    <div style={{
      padding: "10px 12px",
      borderRadius: "0 8px 8px 0",
      fontSize: 13, lineHeight: 1.7,
      color: CC.text2,
      background: CC.panel,
      border: `1px solid ${CC.line}`,
      borderLeft: stripe ? `2.5px solid ${stripe}` : `1px solid ${CC.line}`,
    }}>
      {children}
    </div>
  );
}

// ── 时间戳分隔线 ─────────────────────────────────────────────────────────────

function TimestampCard({ label }: { label: string }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 10,
      padding: "4px 16px", margin: "4px 0", opacity: 0.55,
    }}>
      <div style={{ flex: 1, height: 1, background: CC.lineSoft }} />
      <span style={{ fontSize: 10, color: CC.muted, whiteSpace: "nowrap",
        fontFamily: "monospace", padding: "1px 8px",
        background: CC.bg2, border: `1px solid ${CC.line}`, borderRadius: 8,
      }}>{label}</span>
      <div style={{ flex: 1, height: 1, background: CC.lineSoft }} />
    </div>
  );
}

// ── 用户消息 ─────────────────────────────────────────────────────────────────

function UserMsgCard({ content, operator }: { content: string; operator?: string }) {
  return (
    <div style={{ padding: "3px 16px", display: "flex", justifyContent: "flex-end" }}>
      <div style={{ maxWidth: "72%" }}>
        {operator && (
          <div style={{
            fontSize: 10, color: CC.muted, textAlign: "right",
            marginBottom: 4, fontFamily: "monospace",
          }}>
            {operator}
          </div>
        )}
        <div style={{
          padding: "9px 13px", borderRadius: "10px 10px 3px 10px",
          background: CC.info,
          fontSize: 13, lineHeight: 1.65, color: "#fff",
        }}>
          {content}
        </div>
      </div>
    </div>
  );
}

// ── Orchestrator 推理卡 ──────────────────────────────────────────────────────

function OrchReasoningCard({ think_lines, summary }: { think_lines: string[]; summary: string }) {
  return (
    <TlRow code="PL" badge="PLANNER" elapsed={2100}>
      <Card stripe={CC.agPl}>
        {think_lines.length > 0 && (
          <div style={{
            fontFamily: "monospace", fontSize: 11, color: CC.muted, lineHeight: 1.7,
            background: `color-mix(in oklab, ${CC.agPl} 6%, ${CC.bg})`,
            border: `1px solid color-mix(in oklab, ${CC.agPl} 15%, ${CC.line})`,
            borderRadius: 5, padding: "8px 10px", marginBottom: 8,
          }}>
            {think_lines.map((line, i) => (
              <div key={i} style={{ color: line.startsWith("//") ? CC.muted2 : CC.text2 }}>
                {line}
              </div>
            ))}
          </div>
        )}
        <div style={{ color: CC.text }}>{summary}</div>
      </Card>
    </TlRow>
  );
}

// ── Handoff 连接线 ───────────────────────────────────────────────────────────

function HandoffCard({ from, to, label, payload }: {
  from: string[]; to: string[]; label?: string; payload?: string;
}) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 6,
      padding: "0 28px", margin: "0",
      opacity: 0.55,
    }}>
      <div style={{ display: "flex", gap: 2 }}>
        {from.map((code) => (
          <span key={code} style={{
            fontSize: 8, fontFamily: "monospace", fontWeight: 600,
            color: agentColor(code),
          }}>{AGENT_META[code]?.short ?? code}</span>
        ))}
      </div>

      <div style={{ flex: 1, display: "flex", alignItems: "center", gap: 4 }}>
        <div style={{ flex: 1, height: 1, background: CC.lineSoft }} />
        {label && <span style={{ fontSize: 8, color: CC.muted2, whiteSpace: "nowrap" }}>{label}</span>}
        {payload && (
          <span style={{ fontSize: 8, color: CC.muted, whiteSpace: "nowrap" }}>{payload}</span>
        )}
        <div style={{ flex: 1, height: 1, background: CC.lineSoft }} />
        <div style={{
          width: 0, height: 0,
          borderTop: "2px solid transparent", borderBottom: "2px solid transparent",
          borderLeft: `3px solid ${CC.muted2}`,
        }} />
      </div>

      <div style={{ display: "flex", gap: 2 }}>
        {to.map((code) => (
          <span key={code} style={{
            fontSize: 8, fontFamily: "monospace", fontWeight: 600,
            color: agentColor(code),
          }}>{AGENT_META[code]?.short ?? code}</span>
        ))}
      </div>
    </div>
  );
}

// ── 任务分派计划（Gantt）─────────────────────────────────────────────────────

function DispatchPlanCard({ agents, progress }: {
  agents: { code: string; name: string; task: string }[];
  progress: ("done" | "running" | "error" | "idle")[];
}) {
  const done = progress.filter((s) => s === "done").length;
  const total = agents.length;

  return (
    <TlRow code="PL" badge="DISPATCH" badgeColor={CC.info}>
      <Card stripe={CC.info}>
        <ReplyTo agent="PL" text={`5 路并行 · ${done}/${total} 完成`} />
        <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
          {agents.map((agent, i) => {
            const st = progress[i] ?? "idle";
            const color = agentColor(agent.code);
            const barColor =
              st === "done"    ? CC.ok :
              st === "running" ? CC.warn :
              st === "error"   ? CC.emerg :
                                 CC.lineSoft;
            const metaColor =
              st === "done"    ? CC.ok :
              st === "running" ? CC.warn :
              st === "error"   ? CC.emerg :
                                 CC.muted2;
            const metaLabel =
              st === "done"    ? "✓ 完成" :
              st === "running" ? "⟳ 执行中" :
              st === "error"   ? "✕ 错误" :
                                 "待命";

            return (
              <div key={agent.code} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                {/* 部门标识 */}
                <div style={{
                  width: 18, height: 18, borderRadius: 4, flexShrink: 0,
                  background: `color-mix(in oklab, ${color} 18%, ${CC.panel2})`,
                  border: `1px solid color-mix(in oklab, ${color} 30%, ${CC.line})`,
                  color, display: "flex", alignItems: "center", justifyContent: "center",
                  fontFamily: "monospace", fontSize: 8, fontWeight: 700,
                }}>
                  {agent.code.slice(0, 2)}
                </div>
                {/* 部门名 */}
                <span style={{ flex: "0 0 58px", fontSize: 11.5, color: CC.text, fontWeight: 500, whiteSpace: "nowrap" }}>
                  {agent.name}
                </span>
                {/* 任务 */}
                <span style={{ flex: "0 0 72px", fontSize: 11, color: CC.muted, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {agent.task}
                </span>
                {/* 进度条 */}
                <div style={{ flex: 1, height: 6, background: CC.lineSoft, borderRadius: 3, overflow: "hidden", minWidth: 60 }}>
                  <div style={{
                    height: "100%", borderRadius: 3,
                    width: st === "done" ? "100%" : st === "running" ? "60%" : st === "error" ? "40%" : "0%",
                    background: barColor,
                    animation: st === "running" ? "gantt-slide 1.5s linear infinite" : "none",
                    backgroundSize: st === "running" ? "200% 100%" : "auto",
                    transition: "width 0.4s ease",
                  }} />
                </div>
                {/* 状态标注 */}
                <span style={{ flex: "0 0 52px", textAlign: "right", fontSize: 10, fontFamily: "monospace", color: metaColor }}>
                  {metaLabel}
                </span>
              </div>
            );
          })}
        </div>
        {/* 总进度条 */}
        <div style={{ marginTop: 10, height: 3, borderRadius: 2, background: CC.lineSoft, overflow: "hidden" }}>
          <div style={{
            height: "100%", width: `${(done / total) * 100}%`,
            background: CC.ok, borderRadius: 2, transition: "width 0.4s ease",
          }} />
        </div>
        <div style={{ fontSize: 9, color: CC.muted, marginTop: 3, fontFamily: "monospace" }}>
          {done}/{total} done
        </div>
      </Card>
      <style>{`
        @keyframes gantt-slide {
          0%   { background-position: 200% 0; }
          100% { background-position: -200% 0; }
        }
      `}</style>
    </TlRow>
  );
}

// ── 部门报告卡 ───────────────────────────────────────────────────────────────

function DeptReportCard({
  code, name, task, status, elapsed_ms, summary, kvs, err_detail,
}: Extract<CommandCard, { type: "dept_report" }>) {
  const stripe =
    status === "done"    ? CC.ok :
    status === "running" ? CC.warn :
                           CC.emerg;
  const badge =
    status === "done"    ? `DONE · ${elapsed_ms ? (elapsed_ms / 1000).toFixed(1) + "s" : "✓"}` :
    status === "running" ? "RUNNING" :
                           "ERROR";

  return (
    <TlRow code={code} badge={badge} badgeColor={stripe} elapsed={status === "done" ? elapsed_ms : undefined}>
      <Card stripe={stripe}>
        <ReplyTo agent="PL" text={`收到，${task}`} />

        {status === "running" && (
          <div style={{ display: "inline-flex", alignItems: "center", gap: 6, color: CC.warn, fontSize: 12, marginBottom: 6 }}>
            <span style={{
              width: 12, height: 12, borderRadius: "50%",
              border: `2px solid color-mix(in oklab, ${CC.warn} 30%, transparent)`,
              borderTopColor: CC.warn,
              animation: "spin 0.75s linear infinite", flexShrink: 0,
              display: "inline-block",
            }} />
            执行中…
          </div>
        )}

        {summary && (
          <div style={{ color: CC.text, fontSize: 13, marginBottom: kvs && kvs.length ? 8 : 0 }}>
            {summary}
          </div>
        )}

        {kvs && kvs.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 16px", marginTop: 4 }}>
            {kvs.map(({ k, v }) => (
              <div key={k} style={{ fontSize: 11.5 }}>
                <span style={{ color: CC.muted }}>{k}</span>
                {"  "}
                <span style={{ color: CC.text, fontWeight: 600, fontFamily: "monospace" }}>{v}</span>
              </div>
            ))}
          </div>
        )}

        {status === "error" && err_detail && (
          <div style={{
            marginTop: 8, padding: "6px 9px",
            background: `color-mix(in oklab, ${CC.emerg} 8%, transparent)`,
            border: `1px solid color-mix(in oklab, ${CC.emerg} 22%, transparent)`,
            borderRadius: 5, fontFamily: "monospace", fontSize: 11, color: CC.emerg, lineHeight: 1.5,
          }}>
            {err_detail}
          </div>
        )}

        {status === "error" && (
          <div style={{ display: "flex", gap: 5, marginTop: 8 }}>
            {["重试", "切换至人工核查", "查看日志"].map((label) => (
              <button key={label} style={{
                padding: "3px 8px", fontSize: 10, borderRadius: 4,
                border: `1px solid ${CC.line}`, background: "transparent",
                color: CC.text2, cursor: "pointer",
              }}>{label}</button>
            ))}
          </div>
        )}
      </Card>
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </TlRow>
  );
}

// ── HITL 锚点卡 ──────────────────────────────────────────────────────────────

function HitlAnchorCard({ message }: { message: string }) {
  return (
    <div style={{ padding: "3px 16px" }}>
      <div style={{
        borderRadius: 8,
        border: `1px dashed color-mix(in oklab, ${CC.warn} 40%, transparent)`,
        background: `color-mix(in oklab, ${CC.warn} 6%, ${CC.panel})`,
        padding: "8px 12px",
        display: "flex", alignItems: "center", gap: 10,
      }}>
        <span style={{
          width: 20, height: 20, borderRadius: 5, flexShrink: 0,
          background: `color-mix(in oklab, ${CC.warn} 20%, transparent)`,
          color: CC.warn, display: "flex", alignItems: "center", justifyContent: "center",
          fontWeight: 700, fontSize: 12,
        }}>!</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 10, color: CC.muted, fontFamily: "monospace", marginBottom: 2 }}>
            HITL · 已上浮，等待操作员决策
          </div>
          <div style={{ fontSize: 12, color: CC.text, fontWeight: 500 }}>{message}</div>
        </div>
        <a href="#hitl-bar" style={{
          fontSize: 10, color: CC.warn, textDecoration: "none",
          padding: "2px 7px", borderRadius: 4,
          border: `1px solid color-mix(in oklab, ${CC.warn} 30%, transparent)`,
          flexShrink: 0,
        }}>↑ 回到决策条</a>
      </div>
    </div>
  );
}

// ── 主导出 ───────────────────────────────────────────────────────────────────

export function CardRenderer({ card }: { card: CommandCard }) {
  switch (card.type) {
    case "timestamp":      return <TimestampCard   label={card.label} />;
    case "user_msg":       return <UserMsgCard     content={card.content} operator={card.operator} />;
    case "orch_reasoning": return <OrchReasoningCard think_lines={card.think_lines} summary={card.summary} />;
    case "handoff":        return <HandoffCard     from={card.from} to={card.to} label={card.label} payload={card.payload} />;
    case "dispatch_plan":  return <DispatchPlanCard agents={card.agents} progress={card.progress} />;
    case "dept_report":    return <DeptReportCard  {...card} />;
    case "hitl_anchor":    return <HitlAnchorCard  message={card.message} />;
    default:               return null;
  }
}
