"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import type { Citation } from "@/lib/api";
import CitationList from "./CitationList";

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  streaming: boolean;
}

interface Props {
  message: Message;
}

/**
 * 处理 ReactMarkdown 段落/列表子节点，把 `[N]` 转为可点击的上角标。
 * 点击后设置 activeRef，联动高亮对应引用卡。
 */
function processChildren(
  children: React.ReactNode,
  onRefClick: (ref: number) => void
): React.ReactNode {
  return Array.isArray(children)
    ? children.map((child, i) =>
        typeof child === "string"
          ? inlineRefs(child, i, onRefClick)
          : child
      )
    : typeof children === "string"
    ? inlineRefs(children, 0, onRefClick)
    : children;
}

function inlineRefs(
  text: string,
  keyBase: number,
  onRefClick: (ref: number) => void
): React.ReactNode {
  const parts = text.split(/(\[\d+\])/g);
  if (parts.length === 1) return text;

  return parts.map((part, i) => {
    const m = part.match(/^\[(\d+)\]$/);
    if (m) {
      const ref = Number(m[1]);
      return (
        <sup key={`${keyBase}-${i}`}>
          <button
            onClick={() => onRefClick(ref)}
            className="mx-0.5 rounded bg-blue-100 px-1 py-0.5 text-xs font-bold text-blue-700 hover:bg-blue-200"
          >
            {part}
          </button>
        </sup>
      );
    }
    return <span key={`${keyBase}-${i}`}>{part}</span>;
  });
}

export default function MessageItem({ message }: Props) {
  const isUser = message.role === "user";
  const [activeRef, setActiveRef] = useState<number | null>(null);

  const handleRefClick = (ref: number) => {
    setActiveRef((prev) => (prev === ref ? null : ref)); // 再次点击取消高亮
  };

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[80%] rounded-2xl px-4 py-3 ${
          isUser
            ? "bg-blue-600 text-white"
            : "bg-white text-gray-800 shadow-sm ring-1 ring-gray-100"
        }`}
      >
        {isUser ? (
          <p className="whitespace-pre-wrap text-sm">{message.content}</p>
        ) : (
          <>
            <div className="prose prose-sm max-w-none prose-p:leading-relaxed prose-pre:bg-gray-100">
              <ReactMarkdown
                components={{
                  // 把段落和列表项内的 [N] 转为可点击角标
                  p: ({ children }) => (
                    <p>{processChildren(children, handleRefClick)}</p>
                  ),
                  li: ({ children }) => (
                    <li>{processChildren(children, handleRefClick)}</li>
                  ),
                }}
              >
                {message.content}
              </ReactMarkdown>
            </div>

            {/* 流式光标 */}
            {message.streaming && (
              <span className="ml-0.5 inline-block h-4 w-0.5 animate-pulse bg-gray-400" />
            )}

            <CitationList
              citations={message.citations}
              activeRef={activeRef}
              onRefClick={handleRefClick}
            />
          </>
        )}
      </div>
    </div>
  );
}
