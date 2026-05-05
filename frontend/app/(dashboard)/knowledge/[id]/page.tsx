"use client";

import React, { useEffect, useRef, useState } from "react";
import { useParams, useSearchParams, useRouter } from "next/navigation";
import { Upload, FileText, RefreshCw, Files, Settings, Trash2, MoreHorizontal, Save, Zap, Search, Layers, Lock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  apiListDocuments, apiDeleteDocument, apiGetKB, apiUpdateKB, apiUpdateKBRetrievalSettings, apiDeleteKB,
  type KBDocument, type KnowledgeBase, type KBRetrievalSettings,
} from "@/lib/api";
import { uploadState } from "@/lib/upload-state";

const RETRIEVAL_MODE_LABELS: Record<string, string> = {
  vector: "向量检索", fulltext: "全文检索", hybrid: "混合检索",
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

const STATUS_MAP: Record<KBDocument["status"], { label: string; variant: "default" | "secondary" | "destructive" | "outline" }> = {
  pending:    { label: "等待中",  variant: "outline" },
  processing: { label: "处理中",  variant: "secondary" },
  ready:      { label: "已就绪",  variant: "default" },
  error:      { label: "失败",    variant: "destructive" },
};

const sideNavItems = [
  { key: "docs",         label: "文档",     icon: Files,     route: false },
  { key: "hit-testing",  label: "召回测试",  icon: Search,    route: true  },
  { key: "settings",     label: "设置",     icon: Settings,  route: false },
];

export default function KnowledgeDetailPage() {
  const { id } = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const kbName = searchParams.get("name") ?? "知识库";
  const initialTab = searchParams.get("tab") === "settings" ? "settings" : "docs";

  const router = useRouter();

  const [activeTab, setActiveTab] = useState(initialTab);
  const [docs, setDocs] = useState<KBDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<KBDocument | null>(null);
  const [deleting, setDeleting] = useState(false);

  // Settings tab state
  const [kb, setKb] = useState<KnowledgeBase | null>(null);
  const [retrieval, setRetrieval] = useState<KBRetrievalSettings>({
    retrieval_mode: "hybrid",
    use_rerank: true,
    top_k: 5,
    score_threshold: 0.0,
    hybrid_mode: "weighted",
    vector_weight: 0.7,
  });
  const [saving, setSaving] = useState(false);
  const [saveOk, setSaveOk] = useState(false);
  // 知识库基本信息编辑
  const [editName, setEditName] = useState(kbName);
  const [kbDesc, setKbDesc] = useState("");
  const [savingInfo, setSavingInfo] = useState(false);
  const [saveInfoOk, setSaveInfoOk] = useState(false);
  // 删除知识库
  const [deletingKB, setDeletingKB] = useState(false);
  const [showDeleteKB, setShowDeleteKB] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    loadDocs();
    apiGetKB(id).then((k) => {
      setKb(k);
      setEditName(k.name);
      setKbDesc(k.description ?? "");
      setRetrieval({
        retrieval_mode: k.retrieval_mode,
        use_rerank: k.use_rerank,
        top_k: k.top_k,
        score_threshold: k.score_threshold,
        hybrid_mode: k.hybrid_mode,
        vector_weight: k.vector_weight,
      });
    }).catch(() => null);
    return () => stopPoll();
  }, [id]);

  useEffect(() => {
    const hasActive = docs.some((d) => d.status === "pending" || d.status === "processing");
    if (hasActive) schedulePoll(); else stopPoll();
  }, [docs]);

  function stopPoll() {
    if (pollRef.current) { clearTimeout(pollRef.current); pollRef.current = null; }
  }

  function schedulePoll() {
    if (pollRef.current) return;
    pollRef.current = setTimeout(async () => {
      pollRef.current = null;
      await loadDocs();
    }, 3000);
  }

  async function loadDocs() {
    try {
      setDocs(await apiListDocuments(id));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await apiDeleteDocument(id, deleteTarget.id);
      setDocs((prev) => prev.filter((d) => d.id !== deleteTarget.id));
      setDeleteTarget(null);
    } catch (e) {
      alert(e instanceof Error ? e.message : "删除失败");
    } finally {
      setDeleting(false);
    }
  }

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = "";
    uploadState.set(file);
    router.push(`/knowledge/${id}/upload?name=${encodeURIComponent(kbName)}`);
  }

  async function handleSaveRetrieval() {
    setSaving(true);
    setSaveOk(false);
    try {
      const updated = await apiUpdateKBRetrievalSettings(id, retrieval);
      setKb(updated);
      setSaveOk(true);
      setTimeout(() => setSaveOk(false), 2000);
    } catch (e) {
      alert(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function handleSaveInfo() {
    if (!editName.trim()) return;
    setSavingInfo(true);
    setSaveInfoOk(false);
    try {
      const updated = await apiUpdateKB(id, editName.trim(), kbDesc.trim() || undefined);
      setKb(updated);
      setSaveInfoOk(true);
      setTimeout(() => setSaveInfoOk(false), 2000);
    } catch (e) {
      alert(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSavingInfo(false);
    }
  }

  async function handleDeleteKB() {
    setDeletingKB(true);
    try {
      await apiDeleteKB(id);
      router.push("/knowledge");
    } catch (e) {
      alert(e instanceof Error ? e.message : "删除失败");
      setDeletingKB(false);
      setShowDeleteKB(false);
    }
  }

  return (
    <>
    <div className="flex h-full">
      {/* Left sidebar */}
      <aside className="w-52 border-r flex flex-col shrink-0 bg-background">
        <div className="px-4 py-5 border-b">
          <div className="w-10 h-10 rounded-xl bg-primary/10 flex items-center justify-center mb-3">
            <Files size={18} className="text-primary" />
          </div>
          <p className="text-sm font-semibold leading-snug truncate" title={kbName}>{kbName}</p>
          <p className="text-xs text-muted-foreground mt-0.5">知识库</p>
        </div>

        <nav className="flex-1 py-2 px-2">
          {sideNavItems.map(({ key, label, icon: Icon, route }) => (
            <div
              key={key}
              onClick={() => route
                ? router.push(`/knowledge/${id}/hit-testing?name=${encodeURIComponent(kbName)}`)
                : setActiveTab(key)
              }
              className={`flex items-center gap-2.5 px-3 py-2 rounded-md text-sm cursor-pointer transition-colors ${
                activeTab === key
                  ? "bg-accent text-accent-foreground font-medium"
                  : "text-muted-foreground hover:text-foreground hover:bg-accent/50"
              }`}
            >
              <Icon size={15} />
              {label}
            </div>
          ))}
        </nav>
      </aside>

      {/* Main content */}
      <div className="flex flex-col flex-1 overflow-auto">
        {activeTab === "docs" ? (
          <>
            {/* Content header */}
            <div className="flex items-center justify-between px-8 py-5 border-b shrink-0">
              <div>
                <h2 className="text-sm font-semibold">文档</h2>
                <p className="text-xs text-muted-foreground mt-0.5">上传后自动处理并加入知识库</p>
              </div>
              <div className="flex items-center gap-2">
                <Button variant="outline" size="sm" onClick={loadDocs} disabled={loading}>
                  <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
                </Button>
                <Button size="sm" onClick={() => fileInputRef.current?.click()}>
                  <Upload size={14} className="mr-1.5" />
                  添加文件
                </Button>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".pdf,.docx,.doc,.md,.txt,.html"
                  className="hidden"
                  onChange={handleFileChange}
                />
              </div>
            </div>

            {/* Doc list body */}
            <div className="flex-1 px-8 py-6">
              {error && (
                <div className="text-sm text-destructive bg-destructive/10 rounded-md px-4 py-3 flex items-center justify-between mb-6">
                  {error}
                  <Button variant="ghost" size="sm" onClick={loadDocs}>重试</Button>
                </div>
              )}

              {loading ? (
                <div className="space-y-2">
                  {[...Array(4)].map((_, i) => <Skeleton key={i} className="h-12 rounded-md" />)}
                </div>
              ) : docs.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-full gap-3 text-center">
                  <FileText size={40} className="text-muted-foreground/30" />
                  <p className="font-medium text-sm">还没有文档</p>
                  <p className="text-xs text-muted-foreground">支持 PDF、Word、Markdown、TXT、HTML</p>
                  <Button size="sm" onClick={() => fileInputRef.current?.click()} className="mt-2">
                    <Upload size={14} className="mr-1.5" />
                    上传第一个文档
                  </Button>
                </div>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>文件名</TableHead>
                      <TableHead className="w-28">状态</TableHead>
                      <TableHead className="w-40">上传时间</TableHead>
                      <TableHead className="w-16 text-center">操作</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {docs.map((doc) => {
                      const s = STATUS_MAP[doc.status];
                      return (
                        <TableRow
                          key={doc.id}
                          className="cursor-pointer hover:bg-muted/50"
                          onClick={(e) => {
                            if ((e.target as HTMLElement).closest("[data-no-row-click]")) return;
                            router.push(`/knowledge/${id}/documents/${doc.id}?name=${encodeURIComponent(kbName)}&doc=${encodeURIComponent(doc.filename)}`);
                          }}
                        >
                          <TableCell className="flex items-center gap-2 font-medium">
                            <FileText size={14} className="text-muted-foreground shrink-0" />
                            <span className="truncate max-w-sm" title={doc.filename}>{doc.filename}</span>
                          </TableCell>
                          <TableCell>
                            <Badge variant={s.variant} className="text-xs">{s.label}</Badge>
                          </TableCell>
                          <TableCell className="text-muted-foreground text-sm">
                            {new Date(doc.created_at).toLocaleString("zh-CN", { dateStyle: "short", timeStyle: "short" })}
                          </TableCell>
                          <TableCell className="text-center" data-no-row-click>
                            <DropdownMenu>
                              <DropdownMenuTrigger className="p-1 rounded hover:bg-accent transition-colors outline-none">
                                <MoreHorizontal size={15} className="text-muted-foreground" />
                              </DropdownMenuTrigger>
                              <DropdownMenuContent align="end" className="w-32">
                                <DropdownMenuItem
                                  className="gap-2 text-destructive focus:text-destructive"
                                  onClick={() => setDeleteTarget(doc)}
                                >
                                  <Trash2 size={14} />
                                  删除
                                </DropdownMenuItem>
                              </DropdownMenuContent>
                            </DropdownMenu>
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              )}
            </div>
          </>
        ) : (
          /* ── 设置 tab ── */
          <div className="flex-1 overflow-y-auto px-8 py-8 max-w-2xl space-y-10">

            {/* ── 基本信息 ── */}
            <section>
              <h2 className="text-sm font-semibold mb-1">基本信息</h2>
              <p className="text-xs text-muted-foreground mb-5">修改知识库名称和描述</p>
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <Label className="text-xs font-medium text-muted-foreground">名称</Label>
                  <Input value={editName} onChange={(e) => setEditName(e.target.value)} placeholder="知识库名称" className="text-sm" />
                </div>
                <div className="space-y-1.5">
                  <Label className="text-xs font-medium text-muted-foreground">描述（可选）</Label>
                  <textarea
                    value={kbDesc}
                    onChange={(e) => setKbDesc(e.target.value)}
                    placeholder="描述此知识库的内容和用途…"
                    rows={3}
                    className="w-full resize-none text-sm rounded-md border border-input bg-background px-3 py-2 placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  />
                </div>
                <Button onClick={handleSaveInfo} disabled={savingInfo || !editName.trim()} className="w-full">
                  <Save size={14} className="mr-1.5" />
                  {savingInfo ? "保存中…" : saveInfoOk ? "已保存 ✓" : "保存信息"}
                </Button>
              </div>
            </section>

            {/* ── 分段模式 ── */}
            <section>
              <h2 className="text-sm font-semibold mb-1">分段模式</h2>
              <p className="text-xs text-muted-foreground mb-5">首次上传文档后自动锁定，后续只能调整参数</p>
              {!kb ? (
                <Skeleton className="h-24 rounded-xl" />
              ) : kb.splitter_type === null ? (
                <div className="rounded-xl border border-dashed px-5 py-4 text-center text-sm text-muted-foreground">
                  暂无（尚未上传文档，首次上传时选择）
                </div>
              ) : (
                <div className="rounded-xl border px-5 py-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium">
                        {kb.splitter_type === "recursive" ? "递归分割" : "父子分段"}
                      </span>
                      <span className="inline-flex items-center gap-1 text-[10px] font-medium text-muted-foreground bg-muted px-1.5 py-0.5 rounded-md">
                        <Lock size={9} />
                        已锁定
                      </span>
                    </div>
                    <span className="text-xs text-muted-foreground">
                      {kb.splitter_type === "recursive" ? "通用场景，按分隔符递归切分" : "保留大段上下文，子块用于检索"}
                    </span>
                  </div>
                  <div className="flex items-center gap-6 text-xs text-muted-foreground">
                    <span>分块大小：<span className="font-medium text-foreground">{kb.chunk_size} tokens</span></span>
                    <span>重叠：<span className="font-medium text-foreground">{kb.chunk_overlap} tokens</span></span>
                  </div>
                  {kb.separators && kb.separators.length > 0 && (
                    <div className="flex items-center gap-2 flex-wrap text-xs">
                      <span className="text-muted-foreground">分隔符：</span>
                      {kb.separators.map((sep) => (
                        <span key={sep} className="font-mono bg-muted px-1.5 py-0.5 rounded text-foreground border">
                          {sep === " " ? "空格" : sep.replace(/\n/g, "\\n").replace(/\r/g, "\\r")}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </section>

            {/* ── 检索设置 ── */}
            <section>
              <h2 className="text-sm font-semibold mb-1">检索设置</h2>
              <p className="text-xs text-muted-foreground mb-5">配置此知识库的检索方式，影响对话时的召回行为</p>

            {!kb ? (
              <Skeleton className="h-[300px] rounded-xl" />
            ) : (
              <div className="space-y-1.5 mb-6">
                {(["vector", "fulltext", "hybrid"] as const).map((mode) => {
                  const isSelected = retrieval.retrieval_mode === mode;
                  return (
                    <div
                      key={mode}
                      className={`rounded-xl border-2 overflow-hidden transition-all ${
                        isSelected ? "border-primary" : "border-border hover:border-primary/40 cursor-pointer"
                      }`}
                      onClick={() => !isSelected && setRetrieval((r) => ({ ...r, retrieval_mode: mode }))}
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
                          <p className="text-xs text-muted-foreground mt-0.5">{RETRIEVAL_MODE_DESCS[mode]}</p>
                        </div>
                        <div className={`w-4 h-4 rounded-full border-2 shrink-0 flex items-center justify-center transition-colors ${
                          isSelected ? "border-primary" : "border-muted-foreground/30"
                        }`}>
                          {isSelected && <div className="w-2 h-2 rounded-full bg-primary" />}
                        </div>
                      </div>

                      {/* 展开的参数区 */}
                      {isSelected && (
                        <div className="border-t border-primary/20 bg-muted/20 px-4 pb-4 pt-3 space-y-4">

                          {/* hybrid 子模式选择 */}
                          {mode === "hybrid" && (
                            <div className="grid grid-cols-2 gap-2">
                              {(["weighted", "rerank"] as const).map((hm) => (
                                <button key={hm}
                                  onClick={(e) => { e.stopPropagation(); setRetrieval((r) => ({ ...r, hybrid_mode: hm })); }}
                                  className={`flex items-center gap-2 rounded-lg border px-3 py-2.5 text-left transition-all cursor-pointer ${
                                    retrieval.hybrid_mode === hm ? "border-primary bg-primary/5" : "border-border hover:border-primary/40"
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

                          {/* hybrid weighted: 权重滑动条 */}
                          {mode === "hybrid" && retrieval.hybrid_mode === "weighted" && (
                            <div className="space-y-2">
                              <div className="flex items-center justify-between text-xs">
                                <span className="text-primary font-medium">语义 {(retrieval.vector_weight * 100).toFixed(0)}%</span>
                                <span className="text-muted-foreground font-medium">{((1 - retrieval.vector_weight) * 100).toFixed(0)}% 关键词</span>
                              </div>
                              <input type="range" min={0} max={1} step={0.05}
                                value={retrieval.vector_weight}
                                onChange={(e) => setRetrieval((r) => ({ ...r, vector_weight: Number(e.target.value) }))}
                                onClick={(e) => e.stopPropagation()}
                                className="w-full h-1.5 rounded-full cursor-pointer"
                                style={{ accentColor: "hsl(var(--primary))" }} />
                            </div>
                          )}

                          {/* non-hybrid 或 hybrid rerank 子模式: Rerank 开关 */}
                          {(mode !== "hybrid" || retrieval.hybrid_mode === "rerank") && (
                            <div className="flex items-center justify-between">
                              <div>
                                <Label className="text-sm font-medium">Rerank 模型</Label>
                                <p className="text-xs text-muted-foreground mt-0.5">对召回结果精排，提升相关性</p>
                              </div>
                              <button role="switch" aria-checked={retrieval.use_rerank}
                                onClick={(e) => { e.stopPropagation(); setRetrieval((r) => ({ ...r, use_rerank: !r.use_rerank })); }}
                                className={`relative inline-flex h-5 w-9 shrink-0 rounded-full transition-colors cursor-pointer ${retrieval.use_rerank ? "bg-primary" : "bg-input"}`}>
                                <span className={`pointer-events-none block h-4 w-4 rounded-full bg-white shadow-sm transition-transform my-0.5 ${retrieval.use_rerank ? "translate-x-4" : "translate-x-0.5"}`} />
                              </button>
                            </div>
                          )}

                          {/* Top K + Score 阈值 */}
                          <div className="grid grid-cols-2 gap-6">
                            <div className="space-y-2">
                              <div className="flex items-center justify-between">
                                <Label className="text-sm font-medium">Top K</Label>
                                <Input type="number" min={1} max={20} step={1} value={retrieval.top_k}
                                  onChange={(e) => setRetrieval((r) => ({ ...r, top_k: Math.max(1, Math.min(20, Number(e.target.value))) }))}
                                  onClick={(e) => e.stopPropagation()} className="h-7 w-14 text-center text-sm px-1" />
                              </div>
                              <input type="range" min={1} max={20} step={1} value={retrieval.top_k}
                                onChange={(e) => setRetrieval((r) => ({ ...r, top_k: Number(e.target.value) }))}
                                onClick={(e) => e.stopPropagation()}
                                className="w-full h-1.5 rounded-full cursor-pointer"
                                style={{ accentColor: "hsl(var(--primary))" }} />
                              <p className="text-xs text-muted-foreground">最多召回分段数</p>
                            </div>
                            <div className="space-y-2">
                              <div className="flex items-center justify-between">
                                <div className="flex items-center gap-1.5">
                                  <Label className="text-sm font-medium">Score 阈值</Label>
                                  <button role="switch" aria-checked={retrieval.score_threshold > 0}
                                    onClick={(e) => { e.stopPropagation(); setRetrieval((r) => ({ ...r, score_threshold: r.score_threshold > 0 ? 0 : 0.5 })); }}
                                    className={`relative inline-flex h-4 w-7 shrink-0 rounded-full transition-colors cursor-pointer ${retrieval.score_threshold > 0 ? "bg-primary" : "bg-input"}`}>
                                    <span className={`pointer-events-none block h-3 w-3 rounded-full bg-white shadow-sm transition-transform my-0.5 ${retrieval.score_threshold > 0 ? "translate-x-3.5" : "translate-x-0.5"}`} />
                                  </button>
                                </div>
                                <Input type="number" min={0} max={1} step={0.05} value={retrieval.score_threshold}
                                  disabled={retrieval.score_threshold === 0}
                                  onChange={(e) => setRetrieval((r) => ({ ...r, score_threshold: Math.max(0, Math.min(1, Number(e.target.value))) }))}
                                  onClick={(e) => e.stopPropagation()} className="h-7 w-14 text-center text-sm px-1 disabled:opacity-40" />
                              </div>
                              <input type="range" min={0} max={1} step={0.05} value={retrieval.score_threshold}
                                disabled={retrieval.score_threshold === 0}
                                onChange={(e) => setRetrieval((r) => ({ ...r, score_threshold: Number(e.target.value) }))}
                                onClick={(e) => e.stopPropagation()}
                                className="w-full h-1.5 rounded-full cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                                style={{ accentColor: "hsl(var(--primary))" }} />
                              <p className="text-xs text-muted-foreground">低于阈值的分段被过滤</p>
                            </div>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}

              <Button onClick={handleSaveRetrieval} disabled={saving || !kb} className="w-full">
                <Save size={14} className="mr-1.5" />
                {saving ? "保存中…" : saveOk ? "已保存 ✓" : "保存设置"}
              </Button>
            </section>

            {/* ── 危险区 ── */}
            <section>
              <h2 className="text-sm font-semibold mb-1 text-destructive">危险区域</h2>
              <p className="text-xs text-muted-foreground mb-5">以下操作不可撤销，请谨慎操作</p>
              <div className="rounded-xl border border-destructive/30 px-5 py-4 flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium">删除知识库</p>
                  <p className="text-xs text-muted-foreground mt-0.5">删除此知识库及其所有文档和向量数据</p>
                </div>
                <Button variant="destructive" size="sm" onClick={() => setShowDeleteKB(true)}>
                  <Trash2 size={13} className="mr-1.5" />
                  删除
                </Button>
              </div>
            </section>
          </div>
        )}
      </div>
    </div>

    {/* Delete confirmation */}
    {/* 删除文档 */}
    <AlertDialog open={!!deleteTarget} onOpenChange={(o) => !o && setDeleteTarget(null)}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>确定删除文档？</AlertDialogTitle>
          <AlertDialogDescription>
            将删除「{deleteTarget?.filename}」，此操作不可撤销。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <AlertDialogAction
            onClick={(e) => { e.preventDefault(); handleDelete(); }}
            disabled={deleting}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
          >
            {deleting ? "删除中…" : "确认删除"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>

    {/* 删除知识库 */}
    <AlertDialog open={showDeleteKB} onOpenChange={(o) => !o && setShowDeleteKB(false)}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>确定删除知识库？</AlertDialogTitle>
          <AlertDialogDescription>
            将永久删除知识库「{kb?.name}」及其所有文档和向量数据，此操作不可撤销。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <AlertDialogAction
            onClick={(e) => { e.preventDefault(); handleDeleteKB(); }}
            disabled={deletingKB}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
          >
            {deletingKB ? "删除中…" : "确认删除"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
    </>
  );
}
