# RAGent 前端

企业级 RAG 问答界面，基于 Next.js + Tailwind CSS，支持 SSE 流式逐字渲染与引用溯源。

## 快速启动

```bash
cd frontend
npm install
npm run dev   # http://localhost:3000
```

后端同步启动（在 `backend/` 目录）：

```bash
uv run uvicorn api.main:app --reload   # http://localhost:8000
```

## 环境变量（`.env.local`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `NEXT_PUBLIC_API_BASE` | `http://localhost:8000` | 后端 API 地址 |
| `NEXT_PUBLIC_KB_ID` | `default` | 知识库 ID（需与 Milvus 中写入时一致） |

## SSE 流式事件协议

前端消费 `POST /chat/stream` 的 SSE 事件流：

```
data: {"type": "token",     "content": "..."}   # 逐 token 渲染
data: {"type": "citations", "data": [...]}        # 答案完成后发送引用列表
data: {"type": "error",     "message": "..."}     # 错误事件（含 SSE 解析异常）
data: [DONE]                                       # 流结束
```

## 组件结构

```
components/chat/
├── ChatWindow.tsx    # 消息状态管理、流控制、智能自动滚动
├── MessageItem.tsx   # ReactMarkdown 渲染、[N] 可点击引用角标
└── CitationList.tsx  # 引用卡联动高亮

lib/
└── api.ts            # streamChat() SSE 封装（fetch + ReadableStream）
```

## 关键设计说明

- **SSE 连接**：使用 `fetch + ReadableStream` 而非 `EventSource`（后者不支持 POST + JSON body）
- **流式渲染性能**：token 先累积到 `ref`，用 `requestAnimationFrame` 批量合并后再 `setState`，避免每个字符触发 re-render
- **智能自动滚动**：距底部 > 120px 时停止跟随，显示"回到底部"按钮；流式阶段用 `instant` 避免平滑动画持续触发
- **引用联动**：正文中的 `[N]` 渲染为可点击上角标，点击高亮对应引用卡片；再次点击取消
- **SSE 解析异常**：malformed event 不静默丢弃，输出 console.warn 并向 UI 发 `error` 事件
