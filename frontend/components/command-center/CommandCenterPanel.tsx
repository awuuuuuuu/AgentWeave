"use client";

import { useRef, useEffect, useState } from "react";
import { CC } from "./tokens";
import { HITLBar } from "./HITLBar";
import { CardRenderer } from "./CardRenderer";
import { SopStrip } from "./SopStrip";
import type { CommandCard, HITLNotification, SopStage } from "./types";

interface CommandCenterPanelProps {
  sessionId: string;
  sessionTitle?: string;
  cards?: CommandCard[];
  hitl?: HITLNotification | null;
  sopStages?: SopStage[];
  isRunning?: boolean;
  onSubmit?: (query: string) => void;
  onApprove?: () => void;
  onReject?: () => void;
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
  { name: "企业安全", code: "SF", color: CC.agSf },
];

/** 从文本里提取最后一个 @部门名，没有则返回 null */
function parseMention(text: string): typeof DEPT_ALIASES[0] | null {
  const matches = text.match(/@([\u4e00-\u9fa5A-Za-z]+)/g);
  if (!matches || matches.length === 0) return null;
  const last = matches[matches.length - 1].slice(1); // 去掉 @
  return DEPT_ALIASES.find((d) => d.name === last || d.code === last) ?? null;
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

  const mention = parseMention(value);
  const target = mention ?? DEPT_ALIASES[0]; // 默认 → 指挥中心

  function handleKey(e: React.KeyboardEvent<HTMLTextAreaElement>) {
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
        borderTop: `1px solid ${CC.line}`,
        background: CC.bg2,
        flexShrink: 0,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "flex-end",
          gap: 8,
          background: CC.panel,
          border: `1px solid ${mention ? `color-mix(in oklab, ${target.color} 35%, ${CC.line})` : CC.line}`,
          borderRadius: 8,
          padding: "8px 10px",
          transition: "border-color 0.15s",
        }}
      >
        <textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKey}
          placeholder={`→ ${target.name}　输入应急指令，或 @部门 定向…`}
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
  onApprove,
  onReject,
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
          cards.map((card, i) => <CardRenderer key={i} card={card} />)
        )}
      </div>

      {/* Row 4: hitl-bar — above composer so it's always visible when approval needed */}
      <HITLBar
        hitl={hitl}
        onApprove={onApprove ?? (() => {})}
        onReject={onReject ?? (() => {})}
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
