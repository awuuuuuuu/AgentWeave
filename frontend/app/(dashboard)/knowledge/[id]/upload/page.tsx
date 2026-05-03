"use client";

import React, { useEffect, useRef, useState } from "react";
import { useParams, useSearchParams, useRouter } from "next/navigation";
import { ArrowLeft, Eye, Upload, ChevronRight, X, Plus, Zap, Search, Layers } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  apiUploadDocument, apiPreviewChunks, apiGetKB, apiUpdateKBRetrievalSettings, apiListDocuments,
  type UploadSettings, type ChunkPreviewItem, type KnowledgeBase, type KBRetrievalSettings,
} from "@/lib/api";
import { uploadState } from "@/lib/upload-state";

// 默认分隔符：存真实字符（不是转义字面量），发给后端 JSON.stringify 后能正确还原
const DEFAULT_SEPARATORS = ["\n\n", "\n", "。", ".", "；", " "];

const DEFAULT_SETTINGS: UploadSettings = {
  splitter_type: "recursive",
  chunk_size: 512,
  chunk_overlap: 64,
  separators: DEFAULT_SEPARATORS,
};

/** 真实字符 → 可读显示，如 "\n\n" → "\\n\\n" */
function displaySep(sep: string): string {
  if (sep === "" || sep === " ") return "空格";
  return sep.replace(/\n/g, "\\n").replace(/\r/g, "\\r").replace(/\t/g, "\\t");
}

/** 用户输入（如 \\n\\n）→ 真实字符（"\n\n"），方便 split 生效 */
function parseSepInput(val: string): string {
  return val.replace(/\\n/g, "\n").replace(/\\r/g, "\r").replace(/\\t/g, "\t");
}

const CONTENT_TYPE_LABELS: Record<string, string> = {
  text: "正文",
  title: "标题",
  table: "表格",
  figure: "图片",
  error: "错误",
};

const RETRIEVAL_MODE_LABELS: Record<string, string> = {
  vector: "向量检索",
  fulltext: "全文检索",
  hybrid: "混合检索",
};

const RETRIEVAL_MODE_DESCS: Record<string, string> = {
  vector: "生成查询嵌入，召回语义最相似的文本分段",
  fulltext: "基于关键词的精确匹配，适合专有名词查询",
  hybrid: "向量 + 全文联合检索，兼顾语义理解与精确匹配",
};

const RETRIEVAL_MODE_ICONS: Record<string, React.ReactNode> = {
  vector: <Zap size={14} className="text-primary" />,
  fulltext: <Search size={14} className="text-primary" />,
  hybrid: <Layers size={14} className="text-primary" />,
};

export default function UploadPage() {
  const { id } = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const router = useRouter();
  const kbName = searchParams.get("name") ?? "知识库";

  const [file, setFile] = useState<File | null>(null);
  const [kb, setKb] = useState<KnowledgeBase | null>(null);
  const [settings, setSettings] = useState<UploadSettings>(DEFAULT_SETTINGS);
  const [retrieval, setRetrieval] = useState<KBRetrievalSettings>({
    retrieval_mode: "hybrid",
    use_rerank: true,
    top_k: 5,
    score_threshold: 0.0,
    hybrid_mode: "weighted",
    vector_weight: 0.7,
  });
  const [chunks, setChunks] = useState<ChunkPreviewItem[]>([]);
  const [previewing, setPreviewing] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  const [isRetrievalLocked, setIsRetrievalLocked] = useState(false);

  // separator input state
  const [sepInput, setSepInput] = useState("");
  const [sepVisible, setSepVisible] = useState(false);

  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const f = uploadState.get();
    if (!f) {
      router.replace(`/knowledge/${id}?name=${encodeURIComponent(kbName)}`);
      return;
    }
    setFile(f);
    Promise.all([
      apiGetKB(id),
      apiListDocuments(id),
    ]).then(([data, docs]) => {
      setKb(data);
      setRetrieval({
        retrieval_mode: data.retrieval_mode,
        use_rerank: data.use_rerank,
        top_k: data.top_k,
        score_threshold: data.score_threshold,
        hybrid_mode: data.hybrid_mode,
        vector_weight: data.vector_weight,
      });
      setIsRetrievalLocked(docs.length > 0);
    }).catch(() => null);
  }, []);

  async function handlePreview() {
    if (!file) return;
    abortRef.current?.abort();
    setPreviewing(true);
    setPreviewError(null);
    setChunks([]);
    try {
      const result = await apiPreviewChunks(id, file, settings);
      setChunks(result);
    } catch (e) {
      setPreviewError(e instanceof Error ? e.message : "预览失败");
    } finally {
      setPreviewing(false);
    }
  }

  async function handleUpload() {
    if (!file) return;
    setUploading(true);
    try {
      await Promise.all([
        isRetrievalLocked ? Promise.resolve() : apiUpdateKBRetrievalSettings(id, retrieval),
        apiUploadDocument(id, file, settings),
      ]);
      uploadState.clear();
      router.push(`/knowledge/${id}?name=${encodeURIComponent(kbName)}`);
    } catch (e) {
      alert(e instanceof Error ? e.message : "上传失败");
      setUploading(false);
    }
  }

  function handleBack() {
    uploadState.clear();
    router.push(`/knowledge/${id}?name=${encodeURIComponent(kbName)}`);
  }

  function addSeparator() {
    const raw = sepInput.trim();
    if (!raw) return;
    const val = parseSepInput(raw);
    if (settings.separators.includes(val)) return;
    setSettings((s) => ({ ...s, separators: [...s.separators, val] }));
    setSepInput("");
  }

  function removeSeparator(sep: string) {
    setSettings((s) => ({ ...s, separators: s.separators.filter((x) => x !== sep) }));
  }

  return (
    <div className="flex h-full">
      {/* ── Left: settings ─────────────────────────────────────── */}
      <div className="flex-1 min-w-0 border-r flex flex-col overflow-y-auto">
        {/* Header */}
        <div className="px-6 py-5 border-b flex items-center gap-3 shrink-0">
          <button
            onClick={handleBack}
            className="p-1 rounded-md hover:bg-accent transition-colors text-muted-foreground"
          >
            <ArrowLeft size={16} />
          </button>
          <div>
            <p className="text-xs text-muted-foreground">
              {kbName} <ChevronRight size={10} className="inline" /> 上传文档
            </p>
            <p className="text-sm font-semibold mt-0.5 truncate max-w-xs" title={file?.name}>
              {file?.name ?? "—"}
            </p>
          </div>
        </div>

        <div className="flex-1 px-6 py-6 space-y-6">

          {/* ── 分段设置 ── */}
          <section>
            <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-3">
              分段设置
            </h3>
            <div className="space-y-2">

              {/* ── 递归分割卡片 ── */}
              <div
                className={`rounded-lg border-2 transition-all ${
                  settings.splitter_type === "recursive"
                    ? "border-primary"
                    : "border-border cursor-pointer hover:border-primary/50"
                }`}
              >
                {/* 卡片头：始终可见，点击切换 */}
                <div
                  className="flex items-start gap-3 px-4 py-3"
                  onClick={() => setSettings((s) => ({ ...s, splitter_type: "recursive" }))}
                >
                  <div className={`mt-0.5 w-4 h-4 rounded-full border-2 shrink-0 flex items-center justify-center transition-colors ${
                    settings.splitter_type === "recursive" ? "border-primary" : "border-muted-foreground/40"
                  }`}>
                    {settings.splitter_type === "recursive" && (
                      <div className="w-2 h-2 rounded-full bg-primary" />
                    )}
                  </div>
                  <div>
                    <p className="text-sm font-medium">递归分割</p>
                    <p className="text-xs text-muted-foreground mt-0.5">通用场景，按分隔符递归切分</p>
                  </div>
                </div>

                {/* 展开的设置区（选中时显示） */}
                {settings.splitter_type === "recursive" && (
                  <div className="px-4 pb-4 pt-1 border-t border-primary/20 space-y-4">
                    {/* Separators */}
                    <div className="space-y-2">
                      <div className="flex items-center justify-between">
                        <Label className="text-sm font-medium">分段标识符</Label>
                        <div className="flex items-center gap-1.5">
                          <button
                            onClick={() => { setSepInput(""); setSepVisible(true); }}
                            className="inline-flex items-center gap-0.5 px-2 py-0.5 rounded-md border border-dashed text-xs text-muted-foreground hover:text-foreground hover:border-foreground/40 transition-colors"
                          >
                            <Plus size={10} />
                            添加
                          </button>
                          <button
                            onClick={() => setSettings((s) => ({ ...s, separators: DEFAULT_SEPARATORS }))}
                            className="inline-flex items-center px-2 py-0.5 rounded-md border border-dashed text-xs text-muted-foreground hover:text-foreground hover:border-foreground/40 transition-colors"
                          >
                            重置默认
                          </button>
                        </div>
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {settings.separators.map((sep) => (
                          <span
                            key={sep}
                            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-muted text-xs font-mono border"
                          >
                            {displaySep(sep)}
                            <button
                              onClick={() => removeSeparator(sep)}
                              className="text-muted-foreground hover:text-foreground"
                            >
                              <X size={10} />
                            </button>
                          </span>
                        ))}
                      </div>
                      {sepVisible && (
                        <div className="flex gap-2">
                          <Input
                            autoFocus
                            value={sepInput}
                            placeholder="输入标识符，如 \n\n，回车添加"
                            className="h-8 text-sm font-mono"
                            onChange={(e) => setSepInput(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") { e.preventDefault(); addSeparator(); setSepVisible(false); }
                              if (e.key === "Escape") { setSepVisible(false); setSepInput(""); }
                            }}
                          />
                          <Button
                            size="sm" variant="outline" className="h-8 px-2.5 shrink-0"
                            onClick={() => { addSeparator(); setSepVisible(false); }}
                          >
                            <Plus size={14} />
                          </Button>
                        </div>
                      )}
                    </div>

                    {/* Chunk size + overlap 两列 */}
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1.5">
                        <div className="flex items-center justify-between">
                          <Label htmlFor="chunk-size" className="text-sm font-medium">分段最大长度</Label>
                          <span className="text-xs text-muted-foreground tabular-nums">{settings.chunk_size} tokens</span>
                        </div>
                        <Input
                          id="chunk-size"
                          type="number"
                          min={64}
                          max={4096}
                          step={64}
                          value={settings.chunk_size}
                          onChange={(e) =>
                            setSettings((s) => ({ ...s, chunk_size: Math.max(64, Math.min(4096, Number(e.target.value))) }))
                          }
                          className="h-9"
                        />
                        <p className="text-xs text-muted-foreground">建议 256–1024</p>
                      </div>
                      <div className="space-y-1.5">
                        <div className="flex items-center justify-between">
                          <Label htmlFor="chunk-overlap" className="text-sm font-medium">重叠长度</Label>
                          <span className="text-xs text-muted-foreground tabular-nums">{settings.chunk_overlap} tokens</span>
                        </div>
                        <Input
                          id="chunk-overlap"
                          type="number"
                          min={0}
                          max={Math.floor(settings.chunk_size / 2)}
                          step={16}
                          value={settings.chunk_overlap}
                          onChange={(e) =>
                            setSettings((s) => ({ ...s, chunk_overlap: Math.max(0, Number(e.target.value)) }))
                          }
                          className="h-9"
                        />
                        <p className="text-xs text-muted-foreground">相邻段共享 token</p>
                      </div>
                    </div>
                  </div>
                )}
              </div>

              {/* ── 父子分段卡片 ── */}
              <div
                className={`rounded-lg border-2 transition-all ${
                  settings.splitter_type === "parent_child"
                    ? "border-primary"
                    : "border-border cursor-pointer hover:border-primary/50"
                }`}
              >
                <div
                  className="flex items-start gap-3 px-4 py-3"
                  onClick={() => setSettings((s) => ({ ...s, splitter_type: "parent_child" }))}
                >
                  <div className={`mt-0.5 w-4 h-4 rounded-full border-2 shrink-0 flex items-center justify-center transition-colors ${
                    settings.splitter_type === "parent_child" ? "border-primary" : "border-muted-foreground/40"
                  }`}>
                    {settings.splitter_type === "parent_child" && (
                      <div className="w-2 h-2 rounded-full bg-primary" />
                    )}
                  </div>
                  <div>
                    <p className="text-sm font-medium">父子分段</p>
                    <p className="text-xs text-muted-foreground mt-0.5">保留大段上下文，子块用于检索</p>
                  </div>
                </div>

                {settings.splitter_type === "parent_child" && (
                  <div className="px-4 pb-4 pt-1 border-t border-primary/20 space-y-4">
                    {/* Parent */}
                    <div className="space-y-2">
                      <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">父块用作上下文</p>
                      <div className="space-y-1.5">
                        <div className="flex items-center justify-between">
                          <Label htmlFor="parent-size" className="text-sm font-medium">分段最大长度</Label>
                          <span className="text-xs text-muted-foreground tabular-nums">{settings.chunk_size} tokens</span>
                        </div>
                        <Input
                          id="parent-size"
                          type="number"
                          min={128}
                          max={4096}
                          step={64}
                          value={settings.chunk_size}
                          onChange={(e) =>
                            setSettings((s) => ({ ...s, chunk_size: Math.max(128, Math.min(4096, Number(e.target.value))) }))
                          }
                          className="h-9"
                        />
                        <p className="text-xs text-muted-foreground">建议 512–2048，提供充足上下文</p>
                      </div>
                    </div>

                    {/* Child */}
                    <div className="space-y-2">
                      <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">子块用于检索</p>
                      <div className="grid grid-cols-2 gap-3">
                        <div className="space-y-1.5">
                          <Label className="text-sm font-medium">分段最大长度</Label>
                          <div className="h-9 flex items-center px-3 rounded-md border bg-muted/50 text-sm tabular-nums text-muted-foreground">
                            {Math.max(Math.floor(settings.chunk_size / 4), 64)} tokens
                          </div>
                          <p className="text-xs text-muted-foreground">父块 ÷ 4，自动</p>
                        </div>
                        <div className="space-y-1.5">
                          <div className="flex items-center justify-between">
                            <Label htmlFor="child-overlap" className="text-sm font-medium">重叠长度</Label>
                            <span className="text-xs text-muted-foreground tabular-nums">{settings.chunk_overlap} tokens</span>
                          </div>
                          <Input
                            id="child-overlap"
                            type="number"
                            min={0}
                            max={Math.floor(settings.chunk_size / 8)}
                            step={8}
                            value={settings.chunk_overlap}
                            onChange={(e) =>
                              setSettings((s) => ({ ...s, chunk_overlap: Math.max(0, Number(e.target.value)) }))
                            }
                            className="h-9"
                          />
                          <p className="text-xs text-muted-foreground">子块间重叠</p>
                        </div>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </section>

          {/* ── Embedding 模型 ── */}
          <section>
            <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">
              Embedding 模型
            </h3>
            <div className="flex items-center gap-2 px-3 py-2.5 rounded-lg border bg-muted/40 text-sm">
              <span className="w-2 h-2 rounded-full bg-green-500 shrink-0" />
              <span className="font-medium">text-embedding-3-small</span>
            </div>
          </section>

          {/* ── 检索设置 ── */}
          <section>
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                检索设置
              </h3>
              {isRetrievalLocked && (
                <button
                  className="text-xs text-primary hover:underline"
                  onClick={() => router.push(`/knowledge/${id}?name=${encodeURIComponent(kbName)}&tab=settings`)}
                >
                  在设置中修改
                </button>
              )}
            </div>
            {!kb ? (
              <Skeleton className="h-[220px] rounded-xl" />
            ) : (
              <div className="space-y-1.5">
                {(["vector", "fulltext", "hybrid"] as const).map((mode) => {
                  const isSelected = retrieval.retrieval_mode === mode;
                  return (
                    <div
                      key={mode}
                      className={`rounded-xl border-2 overflow-hidden transition-all ${
                        isSelected
                          ? "border-primary"
                          : isRetrievalLocked
                          ? "border-border opacity-50"
                          : "border-border hover:border-primary/40 cursor-pointer"
                      }`}
                      onClick={() => !isRetrievalLocked && !isSelected && setRetrieval((r) => ({ ...r, retrieval_mode: mode }))}
                    >
                      {/* 卡片头 */}
                      <div className="flex items-center gap-3 px-4 py-3">
                        <span className={isSelected ? "" : "text-muted-foreground opacity-60"}>
                          {RETRIEVAL_MODE_ICONS[mode]}
                        </span>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className={`text-sm font-medium ${!isSelected ? "text-muted-foreground" : ""}`}>
                              {RETRIEVAL_MODE_LABELS[mode]}
                            </span>
                            {mode === "hybrid" && (
                              <span className="text-[10px] font-semibold bg-primary/10 text-primary px-1.5 py-0.5 rounded-md">推荐</span>
                            )}
                          </div>
                          <p className="text-xs text-muted-foreground mt-0.5 truncate">{RETRIEVAL_MODE_DESCS[mode]}</p>
                        </div>
                        <div className={`w-4 h-4 rounded-full border-2 shrink-0 flex items-center justify-center transition-colors ${
                          isSelected ? "border-primary" : "border-muted-foreground/30"
                        }`}>
                          {isSelected && <div className="w-2 h-2 rounded-full bg-primary" />}
                        </div>
                      </div>

                      {/* 展开的参数区（选中时） */}
                      {isSelected && (
                        <div className="border-t border-primary/20 bg-muted/20 px-4 pb-4 pt-3 space-y-4">

                          {/* hybrid 子模式选择 */}
                          {mode === "hybrid" && (
                            <div className="grid grid-cols-2 gap-2">
                              {(["weighted", "rerank"] as const).map((hm) => (
                                <button
                                  key={hm}
                                  onClick={(e) => { e.stopPropagation(); if (!isRetrievalLocked) setRetrieval((r) => ({ ...r, hybrid_mode: hm })); }}
                                  className={`flex items-center gap-2 rounded-lg border px-3 py-2.5 text-left transition-all ${
                                    retrieval.hybrid_mode === hm
                                      ? "border-primary bg-primary/5"
                                      : `border-border ${isRetrievalLocked ? "opacity-40 cursor-not-allowed" : "hover:border-primary/40 cursor-pointer"}`
                                  }`}
                                >
                                  <div className={`w-3.5 h-3.5 rounded-full border-2 shrink-0 flex items-center justify-center ${retrieval.hybrid_mode === hm ? "border-primary" : "border-muted-foreground/40"}`}>
                                    {retrieval.hybrid_mode === hm && <div className="w-1.5 h-1.5 rounded-full bg-primary" />}
                                  </div>
                                  <div>
                                    <p className="text-xs font-medium">{hm === "weighted" ? "权重设置" : "Rerank 模型"}</p>
                                    <p className="text-[10px] text-muted-foreground leading-snug mt-0.5">
                                      {hm === "weighted" ? "调整语义与关键词权重" : "精排模型重新排序"}
                                    </p>
                                  </div>
                                </button>
                              ))}
                            </div>
                          )}

                          {/* hybrid weighted: 语义/关键词权重滑动条 */}
                          {mode === "hybrid" && retrieval.hybrid_mode === "weighted" && (
                            <div className={`space-y-2 ${isRetrievalLocked ? "opacity-40" : ""}`}>
                              <div className="flex items-center justify-between text-xs">
                                <span className="text-primary font-medium">语义 {(retrieval.vector_weight * 100).toFixed(0)}%</span>
                                <span className="text-muted-foreground font-medium">{((1 - retrieval.vector_weight) * 100).toFixed(0)}% 关键词</span>
                              </div>
                              <input
                                type="range" min={0} max={1} step={0.05}
                                value={retrieval.vector_weight}
                                disabled={isRetrievalLocked}
                                onChange={(e) => setRetrieval((r) => ({ ...r, vector_weight: Number(e.target.value) }))}
                                className="w-full h-1.5 rounded-full cursor-pointer disabled:cursor-not-allowed"
                                style={{ accentColor: "hsl(var(--primary))" }}
                              />
                            </div>
                          )}

                          {/* non-hybrid 或 hybrid rerank 子模式: Rerank 开关 */}
                          {(mode !== "hybrid" || retrieval.hybrid_mode === "rerank") && (
                            <div className="flex items-center justify-between">
                              <Label className="text-sm font-medium">Rerank 模型</Label>
                              <button
                                role="switch"
                                aria-checked={retrieval.use_rerank}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  if (!isRetrievalLocked) setRetrieval((r) => ({ ...r, use_rerank: !r.use_rerank }));
                                }}
                                className={`relative inline-flex h-5 w-9 shrink-0 rounded-full transition-colors ${
                                  retrieval.use_rerank ? "bg-primary" : "bg-input"
                                } ${isRetrievalLocked ? "opacity-40 cursor-not-allowed" : "cursor-pointer"}`}
                              >
                                <span className={`pointer-events-none block h-4 w-4 rounded-full bg-white shadow-sm transition-transform my-0.5 ${
                                  retrieval.use_rerank ? "translate-x-4" : "translate-x-0.5"
                                }`} />
                              </button>
                            </div>
                          )}

                          {/* Top K + Score 阈值 */}
                          <div className="grid grid-cols-2 gap-4">
                            <div className={`space-y-2 ${isRetrievalLocked ? "opacity-40" : ""}`}>
                              <div className="flex items-center justify-between">
                                <Label className="text-sm font-medium">Top K</Label>
                                <Input type="number" min={1} max={20} step={1} value={retrieval.top_k} disabled={isRetrievalLocked}
                                  onChange={(e) => setRetrieval((r) => ({ ...r, top_k: Math.max(1, Math.min(20, Number(e.target.value))) }))}
                                  className="h-7 w-14 text-center text-sm px-1" />
                              </div>
                              <input type="range" min={1} max={20} step={1} value={retrieval.top_k} disabled={isRetrievalLocked}
                                onChange={(e) => setRetrieval((r) => ({ ...r, top_k: Number(e.target.value) }))}
                                className="w-full h-1.5 rounded-full cursor-pointer disabled:cursor-not-allowed"
                                style={{ accentColor: "hsl(var(--primary))" }} />
                            </div>
                            <div className={`space-y-2 ${isRetrievalLocked ? "opacity-40" : ""}`}>
                              <div className="flex items-center justify-between">
                                <div className="flex items-center gap-1.5">
                                  <Label className="text-sm font-medium">Score 阈值</Label>
                                  <button role="switch" aria-checked={retrieval.score_threshold > 0}
                                    onClick={(e) => { e.stopPropagation(); if (!isRetrievalLocked) setRetrieval((r) => ({ ...r, score_threshold: r.score_threshold > 0 ? 0 : 0.5 })); }}
                                    className={`relative inline-flex h-4 w-7 shrink-0 rounded-full transition-colors ${retrieval.score_threshold > 0 ? "bg-primary" : "bg-input"} ${isRetrievalLocked ? "cursor-not-allowed" : "cursor-pointer"}`}>
                                    <span className={`pointer-events-none block h-3 w-3 rounded-full bg-white shadow-sm transition-transform my-0.5 ${retrieval.score_threshold > 0 ? "translate-x-3.5" : "translate-x-0.5"}`} />
                                  </button>
                                </div>
                                <Input type="number" min={0} max={1} step={0.05} value={retrieval.score_threshold}
                                  disabled={isRetrievalLocked || retrieval.score_threshold === 0}
                                  onChange={(e) => setRetrieval((r) => ({ ...r, score_threshold: Math.max(0, Math.min(1, Number(e.target.value))) }))}
                                  className="h-7 w-14 text-center text-sm px-1 disabled:opacity-40" />
                              </div>
                              <input type="range" min={0} max={1} step={0.05} value={retrieval.score_threshold}
                                disabled={isRetrievalLocked || retrieval.score_threshold === 0}
                                onChange={(e) => setRetrieval((r) => ({ ...r, score_threshold: Number(e.target.value) }))}
                                className="w-full h-1.5 rounded-full cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                                style={{ accentColor: "hsl(var(--primary))" }} />
                            </div>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}

                {isRetrievalLocked && (
                  <p className="text-xs text-muted-foreground pt-1">
                    首次文档入库后检索设置已锁定，可
                    <button
                      className="text-primary hover:underline mx-0.5"
                      onClick={() => router.push(`/knowledge/${id}?name=${encodeURIComponent(kbName)}&tab=settings`)}
                    >
                      前往知识库设置
                    </button>
                    修改。
                  </p>
                )}
              </div>
            )}
          </section>
        </div>

        {/* Footer actions */}
        <div className="px-6 py-4 border-t shrink-0 flex items-center gap-3">
          <Button
            variant="outline"
            className="flex-1"
            onClick={handlePreview}
            disabled={previewing || !file}
          >
            <Eye size={14} className="mr-1.5" />
            {previewing ? "生成中…" : "预览块"}
          </Button>
          <Button
            className="flex-1"
            onClick={handleUpload}
            disabled={uploading || !file}
          >
            <Upload size={14} className="mr-1.5" />
            {uploading ? "处理中…" : "保存并处理"}
          </Button>
        </div>
      </div>

      {/* ── Right: preview ─────────────────────────────────────── */}
      <div className="flex-1 min-w-0 flex flex-col overflow-hidden bg-muted/30">
        <div className="px-8 py-5 border-b bg-background shrink-0">
          <h2 className="text-sm font-semibold">预览</h2>
          {chunks.length > 0 && (
            <p className="text-xs text-muted-foreground mt-0.5">
              共 <span className="font-medium text-foreground">{chunks.length}</span> 个分段
            </p>
          )}
        </div>

        <div className="flex-1 overflow-y-auto px-8 py-6">
          {previewing ? (
            <div className="space-y-3">
              {[...Array(5)].map((_, i) => (
                <Skeleton key={i} className="h-28 rounded-xl" />
              ))}
            </div>
          ) : previewError ? (
            <div className="text-sm text-destructive bg-destructive/10 rounded-lg px-4 py-3">
              {previewError}
            </div>
          ) : chunks.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full gap-3 text-center text-muted-foreground">
              <Eye size={36} className="opacity-20" />
              <p className="text-sm font-medium">点击左侧「预览块」按钮生成预览</p>
              <p className="text-xs opacity-60">调整分段参数后可重新预览</p>
            </div>
          ) : (
            <div className="space-y-3">
              {chunks.map((chunk) => (
                <div
                  key={chunk.index}
                  className="bg-background rounded-xl border px-5 py-4 text-sm"
                >
                  <div className="flex items-center gap-2 mb-2.5 text-xs text-muted-foreground">
                    <span className="font-mono font-medium text-foreground">
                      #{chunk.index + 1}
                    </span>
                    <span className="text-muted-foreground/40">·</span>
                    <Badge variant="outline" className="text-xs py-0 px-1.5 h-4">
                      {CONTENT_TYPE_LABELS[chunk.content_type] ?? chunk.content_type}
                    </Badge>
                    {chunk.page_number != null && (
                      <>
                        <span className="text-muted-foreground/40">·</span>
                        <span>第 {chunk.page_number} 页</span>
                      </>
                    )}
                    {chunk.section_path && (
                      <>
                        <span className="text-muted-foreground/40">·</span>
                        <span className="truncate max-w-[200px]" title={chunk.section_path}>
                          {chunk.section_path}
                        </span>
                      </>
                    )}
                    <span className="ml-auto tabular-nums">{chunk.token_count} tokens</span>
                  </div>
                  <p className="text-sm leading-relaxed text-foreground/85 line-clamp-6 whitespace-pre-wrap">
                    {chunk.text}
                  </p>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
