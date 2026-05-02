"use client";

import { useEffect, useRef, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { Upload, FileText, RefreshCw, Files, Settings, Trash2, MoreHorizontal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
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
import { apiListDocuments, apiUploadDocument, apiDeleteDocument, type KBDocument } from "@/lib/api";

const STATUS_MAP: Record<KBDocument["status"], { label: string; variant: "default" | "secondary" | "destructive" | "outline" }> = {
  pending:    { label: "等待中",  variant: "outline" },
  processing: { label: "处理中",  variant: "secondary" },
  ready:      { label: "已就绪",  variant: "default" },
  error:      { label: "失败",    variant: "destructive" },
};

const sideNavItems = [
  { key: "docs",     label: "文档",     icon: Files },
  { key: "settings", label: "设置",     icon: Settings },
];

export default function KnowledgeDetailPage() {
  const { id } = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const kbName = searchParams.get("name") ?? "知识库";

  const [activeTab] = useState("docs");
  const [docs, setDocs] = useState<KBDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<KBDocument | null>(null);
  const [deleting, setDeleting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    loadDocs();
    return () => stopPoll();
  }, [id]);

  useEffect(() => {
    const hasActive = docs.some((d) => d.status === "pending" || d.status === "processing");
    if (hasActive) {
      schedulePoll();
    } else {
      stopPoll();
    }
  }, [docs]);

  function stopPoll() {
    if (pollRef.current) { clearTimeout(pollRef.current); pollRef.current = null; }
  }

  function schedulePoll() {
    if (pollRef.current) return; // 已有计划中的轮询，不重复调度
    pollRef.current = setTimeout(async () => {
      pollRef.current = null;
      await loadDocs(); // 等请求彻底完成后，useEffect 再决定是否继续调度
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

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = "";
    setUploading(true);
    try {
      await apiUploadDocument(id, file);
      await loadDocs();
    } catch (err) {
      alert(err instanceof Error ? err.message : "上传失败");
    } finally {
      setUploading(false);
    }
  }

  return (
    <>
    <div className="flex h-full">
      {/* Left sidebar */}
      <aside className="w-52 border-r flex flex-col shrink-0 bg-background">
        {/* KB info */}
        <div className="px-4 py-5 border-b">
          <div className="w-10 h-10 rounded-xl bg-primary/10 flex items-center justify-center mb-3">
            <Files size={18} className="text-primary" />
          </div>
          <p className="text-sm font-semibold leading-snug truncate" title={kbName}>{kbName}</p>
          <p className="text-xs text-muted-foreground mt-0.5">知识库</p>
        </div>

        {/* Nav items */}
        <nav className="flex-1 py-2 px-2">
          {sideNavItems.map(({ key, label, icon: Icon }) => (
            <div
              key={key}
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
            <Button size="sm" onClick={() => fileInputRef.current?.click()} disabled={uploading}>
              <Upload size={14} className="mr-1.5" />
              {uploading ? "上传中…" : "添加文件"}
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

        {/* Body */}
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
                    <TableRow key={doc.id}>
                      <TableCell className="flex items-center gap-2 font-medium">
                        <FileText size={14} className="text-muted-foreground shrink-0" />
                        <span className="truncate max-w-sm" title={doc.filename}>{doc.filename}</span>
                      </TableCell>
                      <TableCell>
                        <Badge variant={s.variant} className="text.xs">{s.label}</Badge>
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        {new Date(doc.created_at).toLocaleString("zh-CN", { dateStyle: "short", timeStyle: "short" })}
                      </TableCell>
                      <TableCell className="text-center">
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
      </div>
    </div>

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
    </>
  );
}
