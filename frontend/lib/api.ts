/**
 * API 客户端
 * - Auth：注册/登录/token 存储/自动 refresh
 * - SSE 流式问答：fetch + ReadableStream
 * - 知识库 CRUD + 文档上传
 */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

// ── Token 存储 ─────────────────────────────────────────────────────────────

export const tokenStorage = {
  getAccess: (): string | null =>
    typeof window !== "undefined" ? localStorage.getItem("access_token") : null,
  getRefresh: (): string | null =>
    typeof window !== "undefined" ? localStorage.getItem("refresh_token") : null,
  set: (access: string, refresh: string) => {
    localStorage.setItem("access_token", access);
    localStorage.setItem("refresh_token", refresh);
    // middleware 鉴权用 cookie（SameSite=Lax，不设 HttpOnly 以便 JS 读取）
    document.cookie = `access_token=${access}; path=/; SameSite=Lax`;
  },
  clear: () => {
    localStorage.removeItem("access_token");
    localStorage.removeItem("refresh_token");
    document.cookie = "access_token=; path=/; max-age=0";
  },
};

// ── 带鉴权的 fetch，401 时自动续期 ────────────────────────────────────────

let _isRefreshing = false;
let _refreshQueue: Array<(token: string | null) => void> = [];

async function _tryRefresh(): Promise<string | null> {
  const refresh = tokenStorage.getRefresh();
  if (!refresh) return null;

  const res = await fetch(`${API_BASE}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refresh }),
  });
  if (!res.ok) { tokenStorage.clear(); return null; }
  const data = await res.json();
  localStorage.setItem("access_token", data.access_token);
  document.cookie = `access_token=${data.access_token}; path=/; SameSite=Lax`;
  return data.access_token as string;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

const ERROR_DICT: Record<string, string> = {
  "Invalid email or password": "邮箱或密码错误",
  "Email already registered": "该邮箱已注册",
  "User not found": "用户不存在",
};

async function _parseError(res: Response, fallback: string): Promise<string> {
  const data = await res.json().catch(() => ({}));
  const detail = data.detail ?? fallback;
  return ERROR_DICT[detail] ?? detail;
}

export async function apiFetch(
  path: string,
  init: RequestInit = {},
  { expectJson = true }: { expectJson?: boolean } = {}
): Promise<Response> {
  const token = tokenStorage.getAccess();
  const headers = new Headers(init.headers);
  if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);

  let res = await fetch(`${API_BASE}${path}`, { ...init, headers });

  if (res.status !== 401) {
    if (!res.ok && expectJson) {
      const msg = await _parseError(res, `请求失败 (${res.status})`);
      throw new ApiError(res.status, msg);
    }
    return res;
  }

  // 并发请求共用同一次 refresh
  if (_isRefreshing) {
    const newToken = await new Promise<string | null>((resolve) => {
      _refreshQueue.push(resolve);
    });
    if (!newToken) return res;
    headers.set("Authorization", `Bearer ${newToken}`);
    const retried = await fetch(`${API_BASE}${path}`, { ...init, headers });
    if (!retried.ok && expectJson) {
      const msg = await _parseError(retried, `请求失败 (${retried.status})`);
      throw new ApiError(retried.status, msg);
    }
    return retried;
  }

  _isRefreshing = true;
  const newToken = await _tryRefresh();
  _isRefreshing = false;
  _refreshQueue.forEach((cb) => cb(newToken));
  _refreshQueue = [];

  if (!newToken) { window.location.href = "/login"; return res; }
  headers.set("Authorization", `Bearer ${newToken}`);
  res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!res.ok && expectJson) {
    const msg = await _parseError(res, `请求失败 (${res.status})`);
    throw new ApiError(res.status, msg);
  }
  return res;
}

// ── Auth API ───────────────────────────────────────────────────────────────

export interface AuthTokens {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export async function apiRegister(email: string, password: string): Promise<AuthTokens> {
  return apiFetch("/auth/register", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  }).then((r) => r.json());
}

export async function apiLogin(email: string, password: string): Promise<AuthTokens> {
  return apiFetch("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  }).then((r) => r.json());
}

// ── Knowledge Base API ─────────────────────────────────────────────────────

export interface KnowledgeBase {
  id: string;
  name: string;
  description: string | null;
  user_id: string;
  created_at: string;
  updated_at: string;
}

export async function apiListKBs(): Promise<KnowledgeBase[]> {
  return apiFetch("/kb").then((r) => r.json());
}

export async function apiCreateKB(name: string, description?: string): Promise<KnowledgeBase> {
  return apiFetch("/kb", { method: "POST", body: JSON.stringify({ name, description }) }).then((r) => r.json());
}

export async function apiDeleteKB(id: string): Promise<void> {
  await apiFetch(`/kb/${id}`, { method: "DELETE" }, { expectJson: false });
}

export interface KBDocument {
  id: string;
  kb_id: string;
  filename: string;
  status: "pending" | "processing" | "ready" | "error";
  error_message: string | null;
  task_id: string | null;
  created_at: string;
  updated_at: string;
}

export async function apiListDocuments(kbId: string): Promise<KBDocument[]> {
  return apiFetch(`/kb/${kbId}/documents`).then((r) => r.json());
}

export async function apiDeleteDocument(kbId: string, docId: string): Promise<void> {
  await apiFetch(`/kb/${kbId}/documents/${docId}`, { method: "DELETE" }, { expectJson: false });
}

export async function apiUploadDocument(
  kbId: string,
  file: File
): Promise<{ document_id: string; task_id: string; filename: string; status: string }> {
  const token = tokenStorage.getAccess();
  const form = new FormData();
  form.append("file", file);
  // 上传不设 Content-Type，让浏览器自动填 multipart/form-data boundary
  const res = await fetch(`${API_BASE}/kb/${kbId}/documents/upload`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: form,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail ?? "上传失败");
  }
  return res.json();
}

// ── SSE 流式问答 ───────────────────────────────────────────────────────────

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
