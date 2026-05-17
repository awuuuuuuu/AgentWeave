"use client";

import React, { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  IconCrown,
  IconSearch,
  IconChartBar,

  IconShieldCheck,
  IconBrain,
  IconArchive,
  IconClipboardList,
  IconUser,
  IconChevronDown,
  IconChevronUp,
  IconCheckbox,

} from "@tabler/icons-react";
import type { AgentBubble, Citation } from "@/lib/agent-api";

// ── Agent 视觉配置 ────────────────────────────────────────────────────────────

const AGENT_META: Record<
  string,
  {
    label: string;
    icon: React.ReactNode;
    avatarBg: string;
    avatarColor: string;
    dotColor: string;
    nameColor: string;
  }
> = {
  supervisor: {
    label: "Supervisor",
    icon: <IconCrown size={14} />,
    avatarBg: "#E6F1FB",
    avatarColor: "#0C447C",
    dotColor: "#378ADD",
    nameColor: "#185FA5",
  },
  researcher: {
    label: "Researcher",
    icon: <IconSearch size={13} />,
    avatarBg: "#E1F5EE",
    avatarColor: "#085041",
    dotColor: "#1D9E75",
    nameColor: "#0F6E56",
  },
  analyst: {
    label: "Analyst",
    icon: <IconChartBar size={13} />,
    avatarBg: "#EEEDFE",
    avatarColor: "#3C3489",
    dotColor: "#7F77DD",
    nameColor: "#534AB7",
  },
  reporter: {
    label: "Reporter",
    icon: <IconClipboardList size={13} />,
    avatarBg: "#E0F7F6",
    avatarColor: "#0E6B65",
    dotColor: "#3ABFB4",
    nameColor: "#0E8C84",
  },
  hitl: {
    label: "HITL",
    icon: <IconShieldCheck size={13} />,
    avatarBg: "#FCEBEB",
    avatarColor: "#791F1F",
    dotColor: "#E24B4A",
    nameColor: "#A32D2D",
  },
  memory_inject: {
    label: "memory_inject",
    icon: <IconBrain size={13} />,
    avatarBg: "transparent",
    avatarColor: "#5F5E5A",
    dotColor: "#5F5E5A",
    nameColor: "#5F5E5A",
  },
  memory_save: {
    label: "memory_save",
    icon: <IconArchive size={13} />,
    avatarBg: "transparent",
    avatarColor: "#5F5E5A",
    dotColor: "#5F5E5A",
    nameColor: "#5F5E5A",
  },
};

// @mention chip 配置（key 统一小写）
const MENTION_META: Record<
  string,
  { bg: string; color: string; icon: React.ReactNode; label: string }
> = {
  supervisor: { bg: "#E6F1FB", color: "#185FA5", icon: <IconCrown size={11} />,          label: "Supervisor" },
  researcher: { bg: "#E1F5EE", color: "#0F6E56", icon: <IconSearch size={11} />,         label: "Researcher" },
  analyst:    { bg: "#EEEDFE", color: "#534AB7", icon: <IconChartBar size={11} />,       label: "Analyst"    },
  reporter:   { bg: "#E0F7F6", color: "#1A7A74", icon: <IconClipboardList size={11} />, label: "Reporter"   },
  hitl:       { bg: "#FCEBEB", color: "#791F1F", icon: <IconShieldCheck size={11} />,   label: "HITL"       },
};

// ── 引用角标 [N] ──────────────────────────────────────────────────────────────
//
// remark 把 [5] 解析成 linkReference，拆成三个节点，正则无法匹配。
// 解法：preprocess 把 [N]/【N】 → ⟦N⟧，remark 不解析 ⟦⟧，文本保持完整。

function preprocess(text: string): string {
  return text
    .replace(/【(\d+)】/g, (_, n) => `⟦${n}⟧`)
    .replace(/\[(\d+)\]/g, (_, n) => `⟦${n}⟧`);
}

/** 从原始正文中提取实际出现的引用编号，过滤幽灵引用 */
function extractReferencedRefs(text: string): Set<number> {
  const set = new Set<number>();
  const re = /\[(\d+)\]|【(\d+)】/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    set.add(parseInt(m[1] ?? m[2], 10));
  }
  return set;
}

// ── react-markdown components ─────────────────────────────────────────────────
//
// makeMarkdownComponents 把 refMap 和所有渲染逻辑封装在一起：
// - refMap 用 string key，避免 JSON number/string 隐式转换
// - CITE_RE 每次用 new RegExp 实例，避免 /g 全局状态在 React 18
//   Strict Mode 双重渲染时被污染（lastIndex 竞态）

function makeMarkdownComponents(
  citations: Citation[],
  onCiteClick: (n: number) => void
) {
  // string key 防止 refMap.get(1) vs refMap.get("1") 不匹配
  const refMap = new Map<string, Citation>();
  for (const c of citations) {
    if (c.ref != null) refMap.set(String(c.ref), c);
  }

  /** 把一段纯文本中的 @mention 和 ⟦N⟧ 都转为 React 节点 */
  function processStr(text: string): React.ReactNode[] {
    const result: React.ReactNode[] = [];
    // split on @Mention（大小写不敏感）
    const mentionRe = /(@(?:Supervisor|Researcher|Analyst|Reporter|HITL))/gi;
    const parts = text.split(mentionRe);

    for (let pi = 0; pi < parts.length; pi++) {
      const part = parts[pi];
      const lk = part.startsWith("@") ? part.slice(1).toLowerCase() : "";
      const meta = MENTION_META[lk];

      if (meta) {
        result.push(
          <span key={`m${pi}`} style={{
            display: "inline-flex", alignItems: "center", gap: 3,
            padding: "1px 6px", borderRadius: 4,
            background: meta.bg, color: meta.color,
            fontWeight: 500, fontSize: 13, whiteSpace: "nowrap",
          }}>
            {meta.icon}@{meta.label}
          </span>
        );
      } else {
        // 每次 new RegExp，避免 /g 的 lastIndex 全局竞态
        const citeRe = new RegExp("⟦(\\d+)⟧", "g");
        let last = 0;
        let m: RegExpExecArray | null;
        while ((m = citeRe.exec(part)) !== null) {
          if (m.index > last) result.push(part.slice(last, m.index));
          const key = m[1];
          const cit = refMap.get(key);
          const valid = !!cit;
          result.push(
            <button
              key={`c${pi}-${m.index}`}
              onClick={() => valid && onCiteClick(parseInt(key, 10))}
              title={cit?.source_file}
              style={{
                display: "inline-flex", alignItems: "center", justifyContent: "center",
                minWidth: 18, height: 18, padding: "0 4px",
                borderRadius: 4, fontSize: 11, fontWeight: 600, lineHeight: 1,
                background: valid ? "#EEF5FD" : "var(--color-background-secondary)",
                color: valid ? "#185FA5" : "var(--color-text-tertiary)",
                border: `1px solid ${valid ? "#B5D4F4" : "var(--color-border-tertiary)"}`,
                cursor: valid ? "pointer" : "default",
                verticalAlign: "middle", position: "relative", top: -1,
              }}
            >
              {key}
            </button>
          );
          last = m.index + m[0].length;
        }
        if (last < part.length) result.push(part.slice(last));
      }
    }
    return result;
  }

  /** 遍历 children，对字符串节点调用 processStr，其余原样保留 */
  function rc(children: React.ReactNode): React.ReactNode {
    return React.Children.map(children, (child, i) => {
      if (typeof child !== "string") return child;
      const nodes = processStr(child);
      if (nodes.length === 1 && typeof nodes[0] === "string") return nodes[0];
      return <React.Fragment key={i}>{nodes}</React.Fragment>;
    });
  }

  return {
    // 行内代码
    code({ children, className, ...props }: React.ComponentPropsWithoutRef<"code"> & { className?: string }) {
      // 代码块由 pre > code 包裹，inline code 没有 className
      if (!className) {
        return (
          <code style={{ fontSize: 11, background: "var(--color-background-secondary)", padding: "1px 5px", borderRadius: 3 }} {...props}>
            {children}
          </code>
        );
      }
      return (
        <pre style={{ margin: "8px 0", padding: "10px 12px", borderRadius: 6, background: "var(--color-background-secondary)", overflowX: "auto" }}>
          <code style={{ fontSize: 11, fontFamily: "monospace" }} className={className} {...props}>{children}</code>
        </pre>
      );
    },
    // 段落
    p({ children }: { children?: React.ReactNode }) {
      return <p style={{ margin: "4px 0", lineHeight: 1.7 }}>{rc(children)}</p>;
    },
    // 加粗 / 斜体（引用角标可能出现在 strong 内）
    strong({ children }: { children?: React.ReactNode }) {
      return <strong style={{ fontWeight: 600, color: "var(--color-text-primary)" }}>{rc(children)}</strong>;
    },
    em({ children }: { children?: React.ReactNode }) {
      return <em style={{ fontStyle: "italic" }}>{rc(children)}</em>;
    },
    // 标题
    h1({ children }: { children?: React.ReactNode }) {
      return <h1 style={{ fontSize: 16, fontWeight: 700, margin: "12px 0 4px", lineHeight: 1.4 }}>{rc(children)}</h1>;
    },
    h2({ children }: { children?: React.ReactNode }) {
      return <h2 style={{ fontSize: 14, fontWeight: 700, margin: "10px 0 3px", lineHeight: 1.4 }}>{rc(children)}</h2>;
    },
    h3({ children }: { children?: React.ReactNode }) {
      return <h3 style={{ fontSize: 13, fontWeight: 600, margin: "8px 0 2px", lineHeight: 1.4 }}>{rc(children)}</h3>;
    },
    // 列表
    ul({ children }: { children?: React.ReactNode }) {
      return <ul style={{ paddingLeft: 18, margin: "4px 0", listStyleType: "disc" }}>{children}</ul>;
    },
    ol({ children }: { children?: React.ReactNode }) {
      return <ol style={{ paddingLeft: 18, margin: "4px 0", listStyleType: "decimal" }}>{children}</ol>;
    },
    li({ children }: { children?: React.ReactNode }) {
      return <li style={{ margin: "2px 0", lineHeight: 1.7 }}>{rc(children)}</li>;
    },
    // 引用块
    blockquote({ children }: { children?: React.ReactNode }) {
      return (
        <blockquote style={{ borderLeft: "3px solid var(--color-border-secondary)", paddingLeft: 10, margin: "6px 0", color: "var(--color-text-secondary)" }}>
          {children}
        </blockquote>
      );
    },
    // ── 表格 ──────────────────────────────────────────────────────────────────
    table({ children }: { children?: React.ReactNode }) {
      return (
        <div style={{ overflowX: "auto", margin: "10px 0" }}>
          <table style={{ borderCollapse: "collapse", fontSize: 12, width: "100%", minWidth: 360 }}>
            {children}
          </table>
        </div>
      );
    },
    thead({ children }: { children?: React.ReactNode }) {
      return <thead style={{ background: "var(--color-background-secondary)", borderBottom: "1.5px solid var(--color-border-secondary)" }}>{children}</thead>;
    },
    tbody({ children }: { children?: React.ReactNode }) {
      return <tbody>{children}</tbody>;
    },
    tr({ children }: { children?: React.ReactNode }) {
      return <tr style={{ borderBottom: "0.5px solid var(--color-border-tertiary)" }}>{children}</tr>;
    },
    th({ children }: { children?: React.ReactNode }) {
      return <th style={{ padding: "6px 10px", textAlign: "left", fontWeight: 600, fontSize: 11, color: "var(--color-text-secondary)", whiteSpace: "nowrap" }}>{rc(children)}</th>;
    },
    td({ children }: { children?: React.ReactNode }) {
      return <td style={{ padding: "5px 10px", color: "var(--color-text-primary)", verticalAlign: "top", lineHeight: 1.5 }}>{rc(children)}</td>;
    },
  };
}


// ── 引用来源面板（对齐参考设计）──────────────────────────────────────────────

function CitationPanel({
  citations,
  highlightedRef,
  bubbleId,
  onCiteClick,
}: {
  citations: Citation[];
  highlightedRef: number | null;
  bubbleId: string;
  onCiteClick: (n: number) => void;
}) {
  if (!citations.length) return null;

  return (
    <div style={{ marginTop: 14 }}>
      {/* 分隔线 */}
      <div
        style={{
          height: 1,
          background: "var(--color-border-tertiary)",
          marginBottom: 10,
        }}
      />
      {/* 标题 */}
      <div
        style={{
          fontSize: 11,
          color: "var(--color-text-tertiary)",
          fontWeight: 500,
          marginBottom: 6,
          letterSpacing: "0.01em",
        }}
      >
        引用来源
      </div>
      {/* 来源列表 */}
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {citations.map((c, i) => {
          // 用 ref 字段作为显示编号，与答案中 [N] 一一对应
          const n = c.ref ?? i + 1;
          const hl = highlightedRef === n;
          return (
            <CitationRow
              key={c.chunk_id ?? String(i)}
              id={`cite-${bubbleId}-${n}`}
              c={c}
              n={n}
              highlighted={hl}
              onClick={() => onCiteClick(n)}
            />
          );
        })}
      </div>
    </div>
  );
}

function CitationRow({
  id,
  c,
  n,
  highlighted,
  onClick,
}: {
  id: string;
  c: Citation;
  n: number;
  highlighted: boolean;
  onClick: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const hasDetail = !!(c.snippet || c.section_path);
  const isOpen = highlighted || expanded;

  return (
    <div
      id={id}
      style={{
        borderRadius: 7,
        border: `1px solid ${highlighted ? "#B5D4F4" : "var(--color-border-tertiary)"}`,
        background: highlighted ? "#F4F9FF" : "transparent",
        overflow: "hidden",
        transition: "border-color 0.15s, background 0.15s",
      }}
    >
      <div
        onClick={() => {
          onClick();
          if (hasDetail) setExpanded((e) => !e);
        }}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "6px 10px",
          cursor: "pointer",
        }}
      >
        {/* 编号徽章 */}
        <span
          style={{
            flexShrink: 0,
            width: 20,
            height: 20,
            borderRadius: 5,
            background: highlighted ? "#185FA5" : "#EEF5FD",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 11,
            fontWeight: 700,
            color: highlighted ? "#fff" : "#5591CC",
            transition: "background 0.15s, color 0.15s",
          }}
        >
          {n}
        </span>

        {/* 文件名 */}
        <span
          style={{
            flex: 1,
            fontSize: 12,
            fontWeight: highlighted ? 500 : 400,
            color: highlighted ? "#185FA5" : "var(--color-text-secondary)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {c.source_file}
        </span>

        {/* 展开箭头 */}
        {hasDetail && (
          <span style={{ flexShrink: 0, color: "var(--color-text-tertiary)" }}>
            {isOpen ? <IconChevronUp size={12} /> : <IconChevronDown size={12} />}
          </span>
        )}
      </div>

      {/* 展开内容 */}
      {isOpen && hasDetail && (
        <div
          style={{
            borderTop: "0.5px solid var(--color-border-tertiary)",
            padding: "7px 10px 7px 38px",
            display: "flex",
            flexDirection: "column",
            gap: 4,
          }}
        >
          {c.section_path && (
            <div style={{ fontSize: 10, color: "var(--color-text-tertiary)" }}>
              {c.section_path}
            </div>
          )}
          {c.snippet && (
            <div
              style={{
                fontSize: 12,
                color: "var(--color-text-secondary)",
                lineHeight: 1.65,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
              }}
            >
              {c.snippet}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── 主组件 ────────────────────────────────────────────────────────────────────

interface AgentMessageProps {
  bubble: AgentBubble;
}

export function AgentMessage({ bubble }: AgentMessageProps) {
  const {
    agent, content, status, citations, replyTo,
    isFinalAnswer,
  } = bubble;
  const [highlightedRef, setHighlightedRef] = useState<number | null>(null);

  // 只展示正文中实际出现 [N] 的引用，过滤幽灵引用
  const referencedRefs = extractReferencedRefs(content);
  const visibleCitations = citations.filter((c) => referencedRefs.has(c.ref));

  function handleCiteClick(n: number) {
    setHighlightedRef((prev) => (prev === n ? null : n));
    const el = document.getElementById(`cite-${bubble.id}-${n}`);
    el?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  // 用户消息
  if (agent === "user") {
    return (
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <div style={{ display: "flex", gap: 8, alignItems: "flex-start", flexDirection: "row-reverse", maxWidth: "72%" }}>
          <div
            style={{
              width: 26, height: 26, borderRadius: 7,
              background: "#1e1e26", color: "#c5cad8",
              display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0,
            }}
          >
            <IconUser size={13} />
          </div>
          <div
            style={{
              padding: "9px 13px", borderRadius: 10, fontSize: 13,
              lineHeight: 1.65, background: "#185FA5", color: "#fff",
            }}
          >
            {content}
          </div>
        </div>
      </div>
    );
  }

  // 系统条（memory_inject / memory_save）
  if (agent === "memory_inject" || agent === "memory_save") {
    const meta = AGENT_META[agent];
    return (
      <div
        style={{
          display: "flex", alignItems: "center", justifyContent: "center",
          gap: 5, fontSize: 11, color: "var(--color-text-tertiary)", padding: "2px 0",
        }}
      >
        <span style={{ color: meta.avatarColor }}>{meta.icon}</span>
        <span>
          {meta.label} ·{" "}
          {status === "thinking"
            ? "处理中…"
            : content || (agent === "memory_inject" ? "记忆已注入" : "结果已归档")}
        </span>
      </div>
    );
  }

  // 普通 Agent 气泡
  const meta = AGENT_META[agent] ?? AGENT_META.supervisor;

  return (
    <div style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
      {/* 头像 */}
      <div
        style={{
          width: 28, height: 28, borderRadius: 8,
          background: meta.avatarBg, color: meta.avatarColor,
          display: "flex", alignItems: "center", justifyContent: "center",
          flexShrink: 0, border: "0.5px solid var(--color-border-tertiary)",
        }}
      >
        {meta.icon}
      </div>

      {/* 内容区 */}
      <div style={{ flex: 1, minWidth: 0 }}>
        {/* Agent 名 */}
        <div style={{ fontSize: 11, marginBottom: 5, display: "flex", alignItems: "center", gap: 5 }}>
          <span style={{ width: 5, height: 5, borderRadius: "50%", background: meta.dotColor, display: "inline-block", flexShrink: 0 }} />
          <span style={{ fontWeight: 600, color: meta.nameColor }}>{meta.label}</span>
        </div>

        {/* 气泡卡片 */}
        <div
          style={{
            padding: "12px 14px",
            borderRadius: 10,
            fontSize: 13,
            lineHeight: 1.7,
            color: "var(--color-text-primary)",
            border: `1px solid ${isFinalAnswer ? "#5DCAA5" : "var(--color-border-secondary)"}`,
            background: isFinalAnswer ? "#F4FBF8" : "var(--color-background-primary)",
            boxShadow: "0 1px 4px rgba(0,0,0,0.07)",
          }}
        >
          {/* 最终答案标识 */}
          {isFinalAnswer && (
            <div
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                padding: "2px 8px",
                borderRadius: 100,
                fontSize: 11,
                fontWeight: 600,
                background: "#E1F5EE",
                color: "#085041",
                border: "0.5px solid #5DCAA5",
                marginBottom: 10,
              }}
            >
              <IconCheckbox size={12} />
              最终回复
            </div>
          )}

          {/* reply-to */}
          {replyTo && (
            <div
              style={{
                borderLeft: "2px solid #B5D4F4", paddingLeft: 8,
                marginBottom: 8, fontSize: 11, color: "var(--color-text-tertiary)",
              }}
            >
              <span style={{ fontWeight: 500, color: "#185FA5" }}>@{replyTo.agentName}</span>
              {" · "}{replyTo.text}
            </div>
          )}

          {/* 正文 */}
          {status === "thinking" ? (
            <span style={{ display: "inline-flex", alignItems: "center", gap: 8, color: "var(--color-text-tertiary)" }}>
              <span
                style={{
                  width: 14, height: 14, borderRadius: "50%",
                  border: "2px solid var(--color-border-secondary)",
                  borderTopColor: "var(--color-text-tertiary)",
                  flexShrink: 0, animation: "spin 0.75s linear infinite",
                }}
              />
              思考中…
            </span>
          ) : (
            <>
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={makeMarkdownComponents(visibleCitations, handleCiteClick)}>
                {preprocess(content)}
              </ReactMarkdown>
              {status === "streaming" && (
                <span
                  style={{
                    display: "inline-block", width: 2, height: 13,
                    background: "var(--color-text-secondary)", verticalAlign: "-2px",
                    marginLeft: 1, animation: "agent-cursor-blink 0.8s step-end infinite",
                  }}
                />
              )}
            </>
          )}

          {/* 引用来源（只展示正文中实际出现的 [N]，过滤幽灵引用） */}
          {status === "done" && visibleCitations.length > 0 && (
            <CitationPanel
              citations={visibleCitations}
              highlightedRef={highlightedRef}
              bubbleId={bubble.id}
              onCiteClick={handleCiteClick}
            />
          )}
        </div>
      </div>
    </div>
  );
}
