"use client";

import { useRef, useEffect, useState, useMemo } from "react";
import { CC } from "./tokens";
import { HITLBar } from "./HITLBar";
import { CardRenderer } from "./CardRenderer";
import { SopStrip } from "./SopStrip";
import type { CommandCard, HITLNotification, SopStage } from "./types";

import type { LocationCandidate } from "./types";

interface CommandCenterPanelProps {
  sessionId: string;
  sessionTitle?: string;
  cards?: CommandCard[];
  hitl?: HITLNotification | null;
  sopStages?: SopStage[];
  isRunning?: boolean;
  onSubmit?: (query: string) => void;
  onOpenHitlModal?: () => void;
  activeStepId?: string | null;
  onStepSelect?: (stepId: string) => void;
  onSelectLocation?: (loc: LocationCandidate) => void;
  onRetryLocation?: (searchQuery: string) => void;
}

// ── conv-head ───────────────────────────────────────────────────────────────

function ConvHead({
  title,
  sessionId,
  isRunning,
  hitlCount,
}: {
  title?: string;
  sessionId: string;
  isRunning?: boolean;
  hitlCount: number;
}) {
  return (
    <div
      style={{
        padding: "10px 16px 9px",
        borderBottom: `1px solid ${CC.line}`,
        background: CC.bg2,
        flexShrink: 0,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <div
          style={{
            width: 8,
            height: 8,
            borderRadius: "50%",
            background: isRunning ? CC.emerg : CC.muted2,
            animation: isRunning ? "conv-pulse 1.5s ease-in-out infinite" : "none",
            flexShrink: 0,
          }}
        />
        <span style={{ fontSize: 13, fontWeight: 600, color: CC.text }}>
          {title ?? "新 Weave 会话"}
        </span>
        {isRunning && (
          <span
            style={{
              fontSize: 9,
              fontWeight: 700,
              letterSpacing: "0.08em",
              color: CC.emerg,
              background: `color-mix(in oklab, ${CC.emerg} 15%, transparent)`,
              border: `1px solid color-mix(in oklab, ${CC.emerg} 30%, transparent)`,
              padding: "1px 6px",
              borderRadius: 4,
            }}
          >
            LIVE
          </span>
        )}
        {hitlCount > 0 && (
          <span
            style={{
              marginLeft: "auto",
              fontSize: 9,
              fontWeight: 700,
              color: CC.warn,
              background: `color-mix(in oklab, ${CC.warn} 15%, transparent)`,
              border: `1px solid color-mix(in oklab, ${CC.warn} 35%, transparent)`,
              padding: "1px 6px",
              borderRadius: 4,
            }}
          >
            等待审批 · {hitlCount}
          </span>
        )}
      </div>
      <div style={{
        fontSize: 11, color: CC.muted, marginTop: 3,
        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
      }}>
        会话 #{sessionIdShort()} · Weave 多 Agent 协作
      </div>
    </div>
  );

  function sessionIdShort() {
    return sessionId ? sessionId.slice(-6) : "……";
  }
}

// ── 部门名 → agent 颜色映射 ──────────────────────────────────────────────────

const DEPT_ALIASES: { name: string; code: string; color: string }[] = [
  { name: "指挥中心", code: "PL", color: CC.agPl },
  { name: "环保局",   code: "EN", color: CC.agEn },
  { name: "医疗急救", code: "ME", color: CC.agMe },
  { name: "交通管控", code: "TR", color: CC.agTr },
  { name: "应急物资", code: "LG", color: CC.agLg },
  { name: "消防救援", code: "FF", color: CC.agFf },
];

/** 从文本里提取最后一个 @部门名，没有则返回 null */
function parseMention(text: string): typeof DEPT_ALIASES[0] | null {
  const matches = text.match(/@([\u4e00-\u9fa5A-Za-z]+)/g);
  if (!matches || matches.length === 0) return null;
  const last = matches[matches.length - 1].slice(1); // 去掉 @
  return DEPT_ALIASES.find((d) => d.name === last || d.code === last) ?? null;
}

// 可 @定向 的部门（自动补全菜单，排除默认占位项"指挥中心"）
const ROUTABLE_DEPTS: { name: string; code: string; color: string; icon: string; desc: string }[] = [
  { name: "医疗急救", code: "ME", color: CC.agMe, icon: "🏥", desc: "救护车调派 / 召回" },
  { name: "消防救援", code: "FF", color: CC.agFf, icon: "🚒", desc: "消防车调派 / 撤回" },
  { name: "交通管控", code: "TR", color: CC.agTr, icon: "🚦", desc: "信号控制 / 疏散方案" },
  { name: "应急物资", code: "LG", color: CC.agLg, icon: "📦", desc: "物资调拨 / 配发" },
  { name: "环保局",   code: "EN", color: CC.agEn, icon: "🌿", desc: "污染监测 / 扩散预警" },
];

/** 检测光标前是否有正在输入中的 @mention（尾部无空白 = 还未完成） */
function getAtQuery(text: string): { query: string; atIndex: number } | null {
  const lastAt = text.lastIndexOf("@");
  if (lastAt === -1) return null;
  const after = text.slice(lastAt + 1);
  if (/\s/.test(after)) return null; // 已补全，不弹菜单
  return { query: after, atIndex: lastAt };
}

// ── Composer ─────────────────────────────────────────────────────────────────

function Composer({
  onSubmit,
  isRunning,
}: {
  onSubmit?: (q: string) => void;
  isRunning?: boolean;
}) {
  const [value, setValue] = useState("");
  const [activeIdx, setActiveIdx] = useState(0);
  const [dismissed, setDismissed] = useState<number | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const mention = parseMention(value);
  const target = mention ?? DEPT_ALIASES[0];

  const atQueryInfo = useMemo(() => getAtQuery(value), [value]);

  const autocompleteItems = useMemo(() => {
    if (!atQueryInfo) return [];
    const q = atQueryInfo.query;
    if (q === "") return ROUTABLE_DEPTS;
    return ROUTABLE_DEPTS.filter(
      (d) => d.name.startsWith(q) || d.code.toLowerCase().startsWith(q.toLowerCase())
    );
  }, [atQueryInfo?.query]);

  const showMenu =
    atQueryInfo !== null &&
    dismissed !== atQueryInfo.atIndex &&
    autocompleteItems.length > 0;

  useEffect(() => {
    setActiveIdx(0);
  }, [atQueryInfo?.atIndex]);

  function insertMention(dept: (typeof ROUTABLE_DEPTS)[0]) {
    if (!atQueryInfo) return;
    const before = value.slice(0, atQueryInfo.atIndex);
    const after = value.slice(atQueryInfo.atIndex + 1 + atQueryInfo.query.length);
    const newVal = before + "@" + dept.name + " " + after;
    setValue(newVal);
    setDismissed(null);
    requestAnimationFrame(() => {
      if (textareaRef.current) {
        const pos = before.length + dept.name.length + 2;
        textareaRef.current.focus();
        textareaRef.current.setSelectionRange(pos, pos);
      }
    });
  }

  function handleKey(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (showMenu) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveIdx((i) => (i + 1) % autocompleteItems.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveIdx((i) => (i - 1 + autocompleteItems.length) % autocompleteItems.length);
        return;
      }
      if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
        e.preventDefault();
        insertMention(autocompleteItems[activeIdx]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setDismissed(atQueryInfo!.atIndex);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (value.trim() && onSubmit) {
        onSubmit(value.trim());
        setValue("");
      }
    }
  }

  return (
    <div
      style={{
        padding: "8px 16px 10px",
        borderTop: "1px solid " + CC.line,
        background: CC.bg2,
        flexShrink: 0,
        position: "relative",
      }}
    >
      {showMenu && (
        <div
          style={{
            position: "absolute",
            bottom: "calc(100% - 4px)",
            left: 16,
            right: 16,
            background: CC.bg,
            border: "1px solid " + CC.line,
            borderRadius: 8,
            boxShadow: "0 -4px 20px rgba(0,0,0,0.07)",
            overflow: "hidden",
            zIndex: 100,
          }}
        >
          <div
            style={{
              padding: "6px 12px 4px",
              fontSize: 10,
              color: CC.muted,
              letterSpacing: "0.06em",
              fontWeight: 600,
              textTransform: "uppercase",
            }}
          >
            定向部门
          </div>
          {autocompleteItems.map((dept, i) => (
            <div
              key={dept.code}
              onMouseDown={(e) => { e.preventDefault(); insertMention(dept); }}
              onMouseEnter={() => setActiveIdx(i)}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: "7px 12px",
                cursor: "pointer",
                background: i === activeIdx ? "color-mix(in oklab, " + dept.color + " 9%, transparent)" : "transparent",
                borderLeft: i === activeIdx ? "2px solid " + dept.color : "2px solid transparent",
                transition: "background 0.1s",
              }}
            >
              <span style={{ fontSize: 15, flexShrink: 0 }}>{dept.icon}</span>
              <span style={{ fontSize: 13, fontWeight: 600, color: dept.color, flexShrink: 0 }}>{dept.name}</span>
              <span style={{ fontSize: 11, color: CC.muted }}>{dept.desc}</span>
              {i === activeIdx && (
                <span style={{ marginLeft: "auto", fontSize: 10, color: CC.muted2, flexShrink: 0 }}>
                  ↵
                </span>
              )}
            </div>
          ))}
          <div
            style={{
              padding: "4px 12px 6px",
              fontSize: 10,
              color: CC.muted2,
              borderTop: "1px solid " + CC.lineSoft,
            }}
          >
            ↑↓ 选择 · Tab / ↵ 确认 · Esc 关闭
          </div>
        </div>
      )}
      <div
        style={{
          display: "flex",
          alignItems: "flex-end",
          gap: 8,
          background: CC.panel,
          border: "1px solid " + (mention ? "color-mix(in oklab, " + target.color + " 35%, " + CC.line + ")" : CC.line),
          borderRadius: 8,
          padding: "8px 10px",
          transition: "border-color 0.15s",
        }}
      >
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKey}
          placeholder={"→ " + target.name + "　输入应急指令，或 @部门 定向…"}
          rows={1}
          style={{
            flex: 1,
            background: "none",
            border: "none",
            outline: "none",
            resize: "none",
            fontSize: 13,
            color: CC.text,
            lineHeight: 1.5,
            fontFamily: "inherit",
            maxHeight: 120,
            overflowY: "auto",
          }}
        />
        <button
          onClick={() => {
            if (value.trim() && onSubmit) {
              onSubmit(value.trim());
              setValue("");
            }
          }}
          disabled={!value.trim() || isRunning}
          style={{
            width: 28,
            height: 28,
            borderRadius: 6,
            border: "none",
            background: value.trim() && !isRunning ? target.color : CC.lineSoft,
            color: value.trim() && !isRunning ? "#fff" : CC.muted2,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            cursor: value.trim() && !isRunning ? "pointer" : "not-allowed",
            fontSize: 14,
            flexShrink: 0,
            transition: "background 0.15s",
          }}
        >
          ↑
        </button>
      </div>
    </div>
  );
}
// ── 主组件 ───────────────────────────────────────────────────────────────────

export function CommandCenterPanel({
  sessionId,
  sessionTitle,
  cards = [],
  hitl = null,
  sopStages,
  isRunning = false,
  onSubmit,
  onOpenHitlModal,
  activeStepId = null,
  onStepSelect,
  onSelectLocation,
  onRetryLocation,
}: CommandCenterPanelProps) {
  const streamRef = useRef<HTMLDivElement>(null);

  // 自动滚到底部
  useEffect(() => {
    if (streamRef.current) {
      streamRef.current.scrollTop = streamRef.current.scrollHeight;
    }
  }, [cards]);

  const hitlCount = hitl ? 1 : 0;

  return (
    <div
      id="hitl-bar"
      style={{
        position: "absolute",
        inset: 0,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        background: CC.bg,
      }}
    >
      {/* Row 1: conv-head */}
      <ConvHead
        title={sessionTitle}
        sessionId={sessionId}
        isRunning={isRunning}
        hitlCount={hitlCount}
      />

      {/* Row 2: SOP progress strip */}
      <SopStrip stages={sopStages} />

      {/* Row 3: stream */}
      <div
        ref={streamRef}
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
          padding: "8px 0",
          display: "flex",
          flexDirection: "column",
          gap: 4,
        }}
      >
        {cards.length === 0 ? (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              height: "100%",
              color: CC.muted,
              gap: 8,
            }}
          >
            <div style={{ fontSize: 28, opacity: 0.3 }}>⚡</div>
            <div style={{ fontSize: 13 }}>等待指令</div>
            <div style={{ fontSize: 11, opacity: 0.7 }}>在下方输入框发送第一条消息，启动 Weave</div>
          </div>
        ) : (
          cards.map((card, i) => (
            <CardRenderer
              key={i}
              card={card}
              activeStepId={activeStepId}
              onStepSelect={onStepSelect}
              onSelectLocation={onSelectLocation}
              onRetryLocation={onRetryLocation}
            />
          ))
        )}
      </div>

      {/* Row 4: hitl-bar — above composer so it's always visible when approval needed */}
      <HITLBar
        hitl={hitl}
        onOpenModal={onOpenHitlModal ?? (() => {})}
      />

      {/* Row 5: composer */}
      <Composer onSubmit={onSubmit} isRunning={isRunning} />

      <style>{`
        @keyframes conv-pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
      `}</style>
    </div>
  );
}
