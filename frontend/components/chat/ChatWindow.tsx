"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { streamChat, apiListKBs } from "@/lib/api";
import type { Citation, KnowledgeBase } from "@/lib/api";
import MessageItem from "./MessageItem";
import type { Message } from "./MessageItem";
import { ChevronDown } from "lucide-react";

let _idCounter = 0;
const nextId = () => String(++_idCounter);

/** 距底部小于此距离时视为"贴底"，自动跟随滚动 */
const NEAR_BOTTOM_PX = 120;

export default function ChatWindow() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [isNearBottom, setIsNearBottom] = useState(true);
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [selectedKbId, setSelectedKbId] = useState<string>("");

  const abortRef = useRef<AbortController | null>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const tokenBufRef = useRef("");
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    apiListKBs().then((list) => {
      setKbs(list);
      if (list.length > 0) setSelectedKbId(list[0].id);
    }).catch(() => null);
  }, []);

  // 监听用户手动滚动，判断是否贴近底部
  const handleScroll = useCallback(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
    setIsNearBottom(dist < NEAR_BOTTOM_PX);
  }, []);

  // 只在用户贴底时自动跟随；流式阶段用 "instant" 避免平滑滚动持续触发
  useEffect(() => {
    if (!isNearBottom) return;
    bottomRef.current?.scrollIntoView({ behavior: streaming ? "instant" : "smooth" });
  }, [messages, isNearBottom, streaming]);

  const scrollToBottom = useCallback(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    setIsNearBottom(true);
  }, []);

  const flushTokenBuf = useCallback((assistantId: string) => {
    const text = tokenBufRef.current;
    if (!text) return;
    tokenBufRef.current = "";
    setMessages((prev) =>
      prev.map((m) =>
        m.id === assistantId ? { ...m, content: m.content + text } : m
      )
    );
  }, []);

  const scheduleFlush = useCallback(
    (assistantId: string) => {
      if (rafRef.current !== null) return;
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = null;
        flushTokenBuf(assistantId);
      });
    },
    [flushTokenBuf]
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const send = useCallback(async () => {
    const query = input.trim();
    if (!query || streaming || !selectedKbId) return;

    setInput("");
    setStreaming(true);
    setIsNearBottom(true);
    tokenBufRef.current = "";

    const userMsg: Message = {
      id: nextId(),
      role: "user",
      content: query,
      citations: [],
      streaming: false,
    };
    const assistantId = nextId();
    const assistantMsg: Message = {
      id: assistantId,
      role: "assistant",
      content: "",
      citations: [],
      streaming: true,
    };
    setMessages((prev) => [...prev, userMsg, assistantMsg]);

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    try {
      for await (const event of streamChat(
        { query, knowledge_base_id: selectedKbId, top_k: 5 },
        ctrl.signal
      )) {
        if (event.type === "token") {
          tokenBufRef.current += event.content;
          scheduleFlush(assistantId);
        } else if (event.type === "citations") {
          if (rafRef.current !== null) {
            cancelAnimationFrame(rafRef.current);
            rafRef.current = null;
          }
          flushTokenBuf(assistantId);
          setCitations(assistantId, event.data);
        } else if (event.type === "error") {
          flushTokenBuf(assistantId);
          appendError(assistantId, event.message);
        }
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name !== "AbortError") {
        appendError(assistantId, err.message);
      }
    } finally {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      flushTokenBuf(assistantId);
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId ? { ...m, streaming: false } : m
        )
      );
      setStreaming(false);
      abortRef.current = null;
    }
  }, [input, streaming, selectedKbId, scheduleFlush, flushTokenBuf]);

  const setCitations = (id: string, citations: Citation[]) => {
    setMessages((prev) =>
      prev.map((m) => (m.id === id ? { ...m, citations } : m))
    );
  };

  const appendError = (id: string, msg: string) => {
    setMessages((prev) =>
      prev.map((m) =>
        m.id === id
          ? { ...m, content: m.content + `\n\n⚠️ 错误：${msg}` }
          : m
      )
    );
  };

  const handleKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  const selectedKb = kbs.find((kb) => kb.id === selectedKbId);

  return (
    <div className="flex h-full flex-col bg-gray-50">
      {/* KB 选择器 */}
      <div className="shrink-0 border-b bg-white px-4 py-2.5 flex items-center gap-2">
        <span className="text-xs text-muted-foreground shrink-0">知识库</span>
        <div className="relative">
          <select
            value={selectedKbId}
            onChange={(e) => setSelectedKbId(e.target.value)}
            disabled={streaming || kbs.length === 0}
            className="appearance-none h-7 pl-3 pr-7 rounded-md border bg-background text-sm font-medium focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50 disabled:cursor-not-allowed min-w-[180px]"
          >
            {kbs.length === 0 ? (
              <option value="">暂无知识库</option>
            ) : (
              kbs.map((kb) => (
                <option key={kb.id} value={kb.id}>{kb.name}</option>
              ))
            )}
          </select>
          <ChevronDown size={12} className="absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none text-muted-foreground" />
        </div>
        {selectedKb?.description && (
          <span className="text-xs text-muted-foreground truncate max-w-[300px]" title={selectedKb.description}>
            {selectedKb.description}
          </span>
        )}
      </div>
      {/* 消息列表 */}
      <div
        ref={scrollContainerRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto px-4 py-6"
      >
        <div className="mx-auto flex max-w-2xl flex-col gap-4">
          {messages.length === 0 && (
            <div className="mt-20 text-center text-gray-400">
              <p className="text-lg font-medium">AgentWeave</p>
              <p className="mt-1 text-sm">输入问题，从知识库中获取答案</p>
            </div>
          )}
          {messages.map((m) => (
            <MessageItem key={m.id} message={m} />
          ))}
          <div ref={bottomRef} />
        </div>
      </div>

      {/* 回到底部按钮：用户上滑后显示 */}
      {!isNearBottom && (
        <div className="absolute bottom-24 left-1/2 -translate-x-1/2">
          <button
            onClick={scrollToBottom}
            className="rounded-full bg-white px-4 py-2 text-sm font-medium text-gray-600 shadow-md ring-1 ring-gray-200 hover:bg-gray-50"
          >
            ↓ 回到底部
          </button>
        </div>
      )}

      {/* 输入区 */}
      <div className="border-t border-gray-200 bg-white px-4 py-4">
        <div className="mx-auto flex max-w-2xl gap-2">
          <textarea
            className="flex-1 resize-none rounded-xl border border-gray-200 px-4 py-3 text-sm shadow-sm outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100 disabled:opacity-50"
            rows={1}
            placeholder="输入问题… (Enter 发送，Shift+Enter 换行)"
            value={input}
            onChange={(e) => {
              setInput(e.target.value);
              e.target.style.height = "auto";
              e.target.style.height = `${Math.min(e.target.scrollHeight, 150)}px`;
            }}
            onKeyDown={handleKey}
            disabled={streaming}
          />
          {streaming ? (
            <button
              onClick={stop}
              className="rounded-xl bg-red-500 px-4 py-3 text-sm font-medium text-white hover:bg-red-600"
            >
              停止
            </button>
          ) : (
            <button
              onClick={send}
              disabled={!input.trim() || !selectedKbId}
              className="rounded-xl bg-blue-600 px-4 py-3 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-40"
            >
              发送
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
