/**
 * SSE 流式问答 API 封装。
 *
 * 使用 fetch + ReadableStream 而非 EventSource：
 * - EventSource 仅支持 GET，不能携带 JSON body
 * - fetch 可完整控制请求头和 body
 */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export interface Citation {
  ref: number;
  source_file: string;
  section_path: string;
  chunk_id: string;
}

export type StreamEvent =
  | { type: "token"; content: string }
  | { type: "citations"; data: Citation[] }
  | { type: "error"; message: string }
  | { type: "done" };

export interface ChatRequest {
  query: string;
  knowledge_base_id: string;
  top_k?: number;
}

/**
 * 发起 SSE 流式问答，返回 AsyncGenerator。
 * 调用方负责捕获 AbortError（用户主动取消时）。
 *
 * @example
 * const ctrl = new AbortController();
 * for await (const event of streamChat(req, ctrl.signal)) { ... }
 */
export async function* streamChat(
  req: ChatRequest,
  signal?: AbortSignal
): AsyncGenerator<StreamEvent> {
  const resp = await fetch(`${API_BASE}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
    signal,
  });

  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(detail.detail ?? `HTTP ${resp.status}`);
  }

  const reader = resp.body!.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buf += decoder.decode(value, { stream: true });

      // SSE 每个事件以 \n\n 结尾
      const parts = buf.split("\n\n");
      buf = parts.pop() ?? "";

      for (const part of parts) {
        for (const line of part.split("\n")) {
          if (!line.startsWith("data: ")) continue;
          const payload = line.slice(6).trim();

          if (payload === "[DONE]") {
            yield { type: "done" };
            return;
          }

          try {
            yield JSON.parse(payload) as StreamEvent;
          } catch {
            // 记录原始 payload 摘要，并向调用方暴露 error 事件而非静默丢弃
            console.warn("[SSE] malformed event:", payload.slice(0, 120));
            yield {
              type: "error",
              message: `SSE 数据格式异常：${payload.slice(0, 60)}`,
            };
          }
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}
