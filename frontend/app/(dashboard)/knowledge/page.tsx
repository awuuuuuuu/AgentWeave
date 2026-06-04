"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Plus, Trash2, BookOpen } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { apiListKBs, apiCreateKB, apiDeleteKB, apiGetMyOrg, type KnowledgeBase } from "@/lib/api";
import Link from "next/link";

// 根据名称生成固定颜色
const COLORS = [
  "bg-blue-100 text-blue-600",
  "bg-violet-100 text-violet-600",
  "bg-emerald-100 text-emerald-600",
  "bg-orange-100 text-orange-600",
  "bg-pink-100 text-pink-600",
  "bg-cyan-100 text-cyan-600",
];
function getColor(name: string) {
  const i = name.charCodeAt(0) % COLORS.length;
  return COLORS[i];
}

export default function KnowledgePage() {
  const router = useRouter();
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [creating, setCreating] = useState(false);

  const [deleteTarget, setDeleteTarget] = useState<KnowledgeBase | null>(null);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    // 指挥中心 org 无需知识库，重定向到指挥台
    apiGetMyOrg()
      .then((org) => { if (org.type === "command") router.replace("/agent"); })
      .catch(() => {});
    loadKBs();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function loadKBs() {
    setLoading(true);
    setError(null);
    try {
      setKbs(await apiListKBs());
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!newName.trim()) return;
    setCreating(true);
    try {
      const kb = await apiCreateKB(newName.trim(), newDesc.trim() || undefined);
      setKbs((prev) => [kb, ...prev]);
      setCreateOpen(false);
      setNewName("");
      setNewDesc("");
    } catch (e) {
      alert(e instanceof Error ? e.message : "创建失败");
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await apiDeleteKB(deleteTarget.id);
      setKbs((prev) => prev.filter((kb) => kb.id !== deleteTarget.id));
      setDeleteTarget(null);
    } catch (e) {
      alert(e instanceof Error ? e.message : "删除失败");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="h-full overflow-y-auto">
    <div className="px-8 py-8">
      {/* Page header */}
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-lg font-semibold">知识库</h1>
        <Button size="sm" onClick={() => setCreateOpen(true)}>
          <Plus size={15} className="mr-1.5" />
          新建知识库
        </Button>
      </div>

      {/* Error */}
      {error && (
        <div className="text-sm text-destructive bg-destructive/10 rounded-md px-4 py-3 flex items-center justify-between mb-6">
          {error}
          <Button variant="ghost" size="sm" onClick={loadKBs}>重试</Button>
        </div>
      )}

      {/* Card grid */}
      <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-4">
        {/* Create card */}
        <button
          onClick={() => setCreateOpen(true)}
          className="flex flex-col items-center justify-center gap-2.5 h-40 rounded-xl border-2 border-dashed border-border text-muted-foreground hover:border-primary/40 hover:text-primary hover:bg-accent/30 transition-all group"
        >
          <div className="w-10 h-10 rounded-full border-2 border-dashed border-current flex items-center justify-center group-hover:border-primary/60 transition-colors">
            <Plus size={20} />
          </div>
          <span className="text-sm font-medium">新建知识库</span>
        </button>

        {/* Loading skeletons */}
        {loading &&
          [...Array(3)].map((_, i) => (
            <Skeleton key={i} className="h-40 rounded-xl" />
          ))}

        {/* KB cards */}
        {!loading &&
          kbs.map((kb) => (
            <KBCard
              key={kb.id}
              kb={kb}
              onDelete={() => setDeleteTarget(kb)}
            />
          ))}
      </div>

      {/* Empty state (no loading, no error, no kbs) */}
      {!loading && !error && kbs.length === 0 && (
        <div className="flex flex-col items-center justify-center py-20 text-center gap-2 col-span-full">
          <BookOpen size={40} className="text-muted-foreground/30 mb-2" />
          <p className="font-medium text-sm">还没有知识库</p>
          <p className="text-xs text-muted-foreground">创建知识库后上传文档，即可开始问答</p>
        </div>
      )}

      {/* 新建对话框 */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>新建知识库</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleCreate}>
            <div className="space-y-4 py-4">
              <div className="space-y-2">
                <Label htmlFor="kb-name">名称</Label>
                <Input
                  id="kb-name"
                  placeholder="例如：产品手册"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  required
                  autoFocus
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="kb-desc">描述（可选）</Label>
                <Input
                  id="kb-desc"
                  placeholder="简短描述这个知识库的用途"
                  value={newDesc}
                  onChange={(e) => setNewDesc(e.target.value)}
                />
              </div>
            </div>
            <DialogFooter>
              <Button type="button" variant="ghost" onClick={() => setCreateOpen(false)}>取消</Button>
              <Button type="submit" disabled={creating}>
                {creating ? "创建中…" : "创建"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* 删除确认 */}
      <AlertDialog open={!!deleteTarget} onOpenChange={(o) => !o && setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确定删除知识库？</AlertDialogTitle>
            <AlertDialogDescription>
              将删除知识库「{deleteTarget?.name}」及其所有文档，此操作不可撤销。
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
    </div>
    </div>
  );
}

function KBCard({ kb, onDelete }: { kb: KnowledgeBase; onDelete: () => void }) {
  const color = getColor(kb.name);
  const initial = kb.name.charAt(0).toUpperCase();

  return (
    <div className="group relative flex flex-col h-40 rounded-xl border bg-card hover:shadow-sm transition-all overflow-hidden">
      <Link href={`/knowledge/${kb.id}?name=${encodeURIComponent(kb.name)}`} className="flex flex-col flex-1 p-4 gap-3">
        {/* Icon */}
        <div className={`w-10 h-10 rounded-lg flex items-center justify-center text-sm font-semibold ${color}`}>
          {initial}
        </div>

        {/* Name + desc */}
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium truncate">{kb.name}</p>
          {kb.description ? (
            <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{kb.description}</p>
          ) : (
            <p className="text-xs text-muted-foreground/50 mt-0.5">暂无描述</p>
          )}
        </div>
      </Link>

      {/* Footer */}
      <div className="px-4 pb-3 flex items-center justify-between">
        <span className="text-xs text-muted-foreground">
          {new Date(kb.created_at).toLocaleDateString("zh-CN")}
        </span>
        <button
          onClick={(e) => { e.preventDefault(); e.stopPropagation(); onDelete(); }}
          className="opacity-0 group-hover:opacity-100 text-muted-foreground hover:text-destructive transition-all p-1 rounded"
        >
          <Trash2 size={13} />
        </button>
      </div>
    </div>
  );
}
