"use client";

import React, { useEffect, useState, useCallback } from "react";
import { useParams, useSearchParams, useRouter } from "next/navigation";
import { ArrowLeft, ChevronRight, Plus, X, FileText, Files, Settings, Search } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  apiGetKB, apiListChunks, apiGetDocMetadata, apiUpdateDocMetadata,
  type KnowledgeBase, type ChunkItem, type ChunkListResponse,
} from "@/lib/api";

const CONTENT_TYPE_LABELS: Record<string, string> = {
  text: "正文", title: "标题", table: "表格", figure: "图片", error: "错误",
};

const PAGE_SIZE_OPTIONS = [10, 25, 50];

export default function DocumentDetailPage() {
  const { id: kbId, docId } = useParams<{ id: string; docId: string }>();
  const searchParams = useSearchParams();
  const router = useRouter();
  const kbName = searchParams.get("name") ?? "知识库";
  const docName = searchParams.get("doc") ?? "文档";

  const [kb, setKb] = useState<KnowledgeBase | null>(null);
  const [chunkData, setChunkData] = useState<ChunkListResponse | null>(null);
  const [chunksLoading, setChunksLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [selectedChunk, setSelectedChunk] = useState<ChunkItem | null>(null);

  // metadata
  const [metadata, setMetadata] = useState<Record<string, string>>({});
  const [metaLoading, setMetaLoading] = useState(true);
  const [metaSaving, setMetaSaving] = useState(false);
  const [newKey, setNewKey] = useState("");
  const [newVal, setNewVal] = useState("");
  const [addingMeta, setAddingMeta] = useState(false);

  const loadChunks = useCallback(async (p: number, ps: number) => {
    setChunksLoading(true);
    try {
      const data = await apiListChunks(kbId, docId, p, ps);
      setChunkData(data);
    } finally {
      setChunksLoading(false);
    }
  }, [kbId, docId]);

  useEffect(() => {
    apiGetKB(kbId).then(setKb).catch(() => null);
    apiGetDocMetadata(kbId, docId).then(setMetadata).catch(() => null).finally(() => setMetaLoading(false));
    loadChunks(1, pageSize);
  }, [kbId, docId, loadChunks]);

  async function handlePageChange(p: number) {
    setPage(p);
    await loadChunks(p, pageSize);
  }

  async function handlePageSizeChange(ps: number) {
    setPageSize(ps);
    setPage(1);
    await loadChunks(1, ps);
  }

  async function saveMetadata(next: Record<string, string>) {
    setMetaSaving(true);
    try {
      const saved = await apiUpdateDocMetadata(kbId, docId, next);
      setMetadata(saved);
    } finally {
      setMetaSaving(false);
    }
  }

  async function addMeta() {
    if (!newKey.trim()) return;
    const next = { ...metadata, [newKey.trim()]: newVal };
    try {
      await saveMetadata(next);
      setNewKey(""); setNewVal(""); setAddingMeta(false);
    } catch {
      // 保存失败时保留输入框内容，saveMetadata 内部已处理 setMetaSaving
    }
  }

  function removeMeta(key: string) {
    const next = { ...metadata };
    delete next[key];
    saveMetadata(next);
  }

  const totalPages = chunkData ? Math.ceil(chunkData.total / pageSize) : 1;

  const sideNavItems = [
    { key: "docs",        label: "文档",     icon: Files,    href: `/knowledge/${kbId}?name=${encodeURIComponent(kbName)}` },
    { key: "hit-testing", label: "召回测试",  icon: Search,   href: `/knowledge/${kbId}/hit-testing?name=${encodeURIComponent(kbName)}` },
    { key: "settings",    label: "设置",     icon: Settings, href: `/knowledge/${kbId}?name=${encodeURIComponent(kbName)}&tab=settings` },
  ];

  return (
    <div className="flex h-full">
      {/* ── Left sidebar ─── */}
      <aside className="w-52 border-r flex flex-col shrink-0 bg-background">
        <div className="px-4 py-5 border-b">
          <div className="w-10 h-10 rounded-xl bg-primary/10 flex items-center justify-center mb-3">
            <Files size={18} className="text-primary" />
          </div>
          <p className="text-sm font-semibold leading-snug truncate" title={kbName}>{kbName}</p>
          <p className="text-xs text-muted-foreground mt-0.5">知识库</p>
        </div>
        <nav className="flex-1 py-2 px-2">
          {sideNavItems.map(({ key, label, icon: Icon, href }) => (
            <div
              key={key}
              onClick={() => router.push(href)}
              className={`flex items-center gap-2.5 px-3 py-2 rounded-md text-sm cursor-pointer transition-colors ${
                key === "docs"
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

      {/* ── Left: chunk list ─── */}
      <div className="flex-1 min-w-0 border-r flex flex-col overflow-hidden">
        {/* Header */}
        <div className="px-6 py-4 border-b flex items-center gap-3 shrink-0">
          <button
            onClick={() => router.push(`/knowledge/${kbId}?name=${encodeURIComponent(kbName)}`)}
            className="p-1 rounded-md hover:bg-accent transition-colors text-muted-foreground"
          >
            <ArrowLeft size={16} />
          </button>
          <div className="min-w-0">
            <p className="text-xs text-muted-foreground truncate">
              {kbName} <ChevronRight size={10} className="inline" /> {docName}
            </p>
            {chunkData && (
              <p className="text-sm font-semibold mt-0.5">
                共 <span className="text-primary">{chunkData.total}</span> 个分段
              </p>
            )}
          </div>
        </div>

        {/* Chunk list */}
        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-2">
          {chunksLoading ? (
            [...Array(5)].map((_, i) => <Skeleton key={i} className="h-24 rounded-xl" />)
          ) : !chunkData?.items.length ? (
            <div className="flex flex-col items-center justify-center h-full gap-2 text-muted-foreground">
              <FileText size={36} className="opacity-20" />
              <p className="text-sm">暂无分段数据</p>
            </div>
          ) : (
            chunkData.items.map((chunk) => (
              <div
                key={chunk.chunk_id}
                onClick={() => setSelectedChunk(chunk.chunk_id === selectedChunk?.chunk_id ? null : chunk)}
                className={`rounded-xl border px-4 py-3 cursor-pointer transition-all ${
                  selectedChunk?.chunk_id === chunk.chunk_id
                    ? "border-primary bg-primary/5"
                    : "hover:border-primary/40"
                }`}
              >
                <div className="flex items-center gap-2 mb-1.5 text-xs text-muted-foreground">
                  <span className="font-mono font-medium text-foreground">#{chunk.chunk_index + 1}</span>
                  <span className="text-muted-foreground/40">·</span>
                  <Badge variant="outline" className="text-xs py-0 px-1.5 h-4">
                    {CONTENT_TYPE_LABELS[chunk.content_type] ?? chunk.content_type}
                  </Badge>
                  {chunk.section_path && (
                    <>
                      <span className="text-muted-foreground/40">·</span>
                      <span className="truncate max-w-[200px]">{chunk.section_path}</span>
                    </>
                  )}
                  <span className="ml-auto tabular-nums">{chunk.text.length} 字符</span>
                </div>
                <p className="text-sm text-foreground/80 line-clamp-2 leading-relaxed whitespace-pre-wrap">
                  {chunk.text}
                </p>
              </div>
            ))
          )}
        </div>

        {/* Pagination */}
        {chunkData && chunkData.total > 0 && (
          <div className="px-6 py-3 border-t shrink-0 flex items-center justify-between">
            <div className="flex items-center gap-1">
              {PAGE_SIZE_OPTIONS.map((ps) => (
                <button
                  key={ps}
                  onClick={() => handlePageSizeChange(ps)}
                  className={`px-2.5 py-1 rounded text-xs transition-colors ${
                    pageSize === ps ? "bg-primary text-primary-foreground" : "hover:bg-accent text-muted-foreground"
                  }`}
                >
                  {ps}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-1">
              <button
                disabled={page <= 1}
                onClick={() => handlePageChange(page - 1)}
                className="px-2.5 py-1 rounded text-xs hover:bg-accent disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                上一页
              </button>
              {[...Array(totalPages)].map((_, i) => (
                <button
                  key={i}
                  onClick={() => handlePageChange(i + 1)}
                  className={`w-7 h-7 rounded text-xs transition-colors ${
                    page === i + 1 ? "bg-primary text-primary-foreground" : "hover:bg-accent text-muted-foreground"
                  }`}
                >
                  {i + 1}
                </button>
              ))}
              <button
                disabled={page >= totalPages}
                onClick={() => handlePageChange(page + 1)}
                className="px-2.5 py-1 rounded text-xs hover:bg-accent disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                下一页
              </button>
            </div>
          </div>
        )}
      </div>

      {/* ── Right: detail / info ─── */}
      <div className="w-80 shrink-0 flex flex-col overflow-hidden border-l bg-muted/20">
        {selectedChunk ? (
          /* 分段全文 */
          <div className="flex flex-col h-full">
            <div className="px-5 py-4 border-b flex items-center justify-between shrink-0">
              <span className="text-sm font-semibold">分段 #{selectedChunk.chunk_index + 1}</span>
              <button onClick={() => setSelectedChunk(null)} className="text-muted-foreground hover:text-foreground">
                <X size={14} />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto px-5 py-4">
              <p className="text-sm leading-relaxed whitespace-pre-wrap text-foreground/85">
                {selectedChunk.text}
              </p>
            </div>
          </div>
        ) : (
          /* 元数据 + 文档信息 */
          <div className="flex-1 overflow-y-auto">
            {/* 元数据 */}
            <div className="px-5 py-4 border-b">
              <div className="flex items-center justify-between mb-3">
                <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">元数据</p>
                <button
                  onClick={() => setAddingMeta(true)}
                  className="inline-flex items-center gap-0.5 px-2 py-0.5 rounded-md border border-dashed text-xs text-muted-foreground hover:text-foreground hover:border-foreground/40 transition-colors"
                >
                  <Plus size={10} />添加
                </button>
              </div>

              {metaLoading ? (
                <Skeleton className="h-16 rounded-lg" />
              ) : (
                <div className="space-y-0">
                  {Object.entries(metadata).map(([k, v]) => (
                    <div key={k} className="flex items-center gap-3 py-1.5 group border-b border-border/50 last:border-0">
                      <span className="text-sm text-muted-foreground w-24 shrink-0 truncate">{k}</span>
                      <span className="text-sm flex-1 truncate">{v}</span>
                      <button
                        onClick={() => removeMeta(k)}
                        className="opacity-0 group-hover:opacity-100 text-muted-foreground hover:text-destructive transition-all shrink-0"
                      >
                        <X size={12} />
                      </button>
                    </div>
                  ))}

                  {addingMeta && (
                    <div className="space-y-1.5 pt-1">
                      <Input
                        autoFocus
                        placeholder="键名"
                        value={newKey}
                        onChange={(e) => setNewKey(e.target.value)}
                        className="h-8 text-sm"
                      />
                      <Input
                        placeholder="值"
                        value={newVal}
                        onChange={(e) => setNewVal(e.target.value)}
                        onKeyDown={(e) => { if (e.key === "Enter") addMeta(); if (e.key === "Escape") { setAddingMeta(false); setNewKey(""); setNewVal(""); }}}
                        className="h-8 text-sm"
                      />
                      <div className="flex gap-1.5">
                        <Button size="sm" className="flex-1 h-7 text-xs" onClick={addMeta} disabled={metaSaving || !newKey.trim()}>
                          {metaSaving ? "保存中…" : "保存"}
                        </Button>
                        <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => { setAddingMeta(false); setNewKey(""); setNewVal(""); }}>
                          取消
                        </Button>
                      </div>
                    </div>
                  )}

                  {!Object.keys(metadata).length && !addingMeta && (
                    <p className="text-xs text-muted-foreground text-center py-4">暂无元数据</p>
                  )}
                </div>
              )}
            </div>

            {/* 文档信息 */}
            <div className="px-5 py-4">
              <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-3">文档信息</p>
              <div className="space-y-0 text-sm">
                {[
                  { label: "文件名", value: docName },
                  kb && { label: "检索模式", value: kb.retrieval_mode },
                  kb && { label: "Top K", value: String(kb.top_k) },
                  chunkData && { label: "分段数", value: String(chunkData.total) },
                ].filter(Boolean).map((item) => {
                  const { label, value } = item as { label: string; value: string };
                  return (
                    <div key={label} className="flex items-center gap-3 py-1.5 border-b border-border/50 last:border-0">
                      <span className="text-muted-foreground w-20 shrink-0">{label}</span>
                      <span className="flex-1 truncate font-medium" title={value}>{value}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
