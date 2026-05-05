"use client";

import React, { useState, useEffect } from "react";
import { useParams, useSearchParams, useRouter } from "next/navigation";
import { Files, Settings, Search, FileText } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { apiHitTesting, apiGetHitTestingHistory, type HitTestingRecord, type HitTestingLogItem } from "@/lib/api";

const CONTENT_TYPE_LABELS: Record<string, string> = {
  text: "正文", title: "标题", table: "表格", figure: "图片", error: "错误",
};

function ScoreBadge({ score }: { score: number }) {
  const color =
    score >= 0.8 ? "bg-green-500" :
    score >= 0.5 ? "bg-yellow-500" :
    "bg-orange-400";
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-bold text-white ${color}`}>
      SCORE {score.toFixed(2)}
    </span>
  );
}

function formatTime(s: string) {
  const d = new Date(s);
  return d.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export default function HitTestingPage() {
  const { id: kbId } = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const router = useRouter();
  const kbName = searchParams.get("name") ?? "知识库";

  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [records, setRecords] = useState<HitTestingRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<HitTestingLogItem[]>([]);

  useEffect(() => {
    apiGetHitTestingHistory(kbId).then(setHistory).catch(() => null);
  }, [kbId]);

  async function runTest(q: string) {
    if (!q.trim()) return;
    setLoading(true);
    setError(null);
    setRecords(null);
    try {
      const res = await apiHitTesting(kbId, q.trim());
      setRecords(res.records);
      // 直接用响应里的 log_item 做乐观更新，避免重拉历史接口的竞态
      setHistory((prev) => [res.log_item, ...prev.filter((h) => h.id !== res.log_item.id)]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "测试失败");
    } finally {
      setLoading(false);
    }
  }

  function handleTest() {
    runTest(query);
  }

  const sideNavItems = [
    { key: "docs", label: "文档", icon: Files, href: `/knowledge/${kbId}?name=${encodeURIComponent(kbName)}` },
    { key: "hit-testing", label: "召回测试", icon: Search, href: `/knowledge/${kbId}/hit-testing?name=${encodeURIComponent(kbName)}` },
    { key: "settings", label: "设置", icon: Settings, href: `/knowledge/${kbId}?name=${encodeURIComponent(kbName)}&tab=settings` },
  ];

  return (
    <div className="flex h-full">
      {/* Sidebar */}
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
                key === "hit-testing"
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

      {/* Main: 40/60 proportional split, fills full width */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left: input + history — 40% */}
        <div className="w-1/2 min-w-[360px] shrink-0 border-r flex flex-col overflow-hidden">
          {/* Header */}
          <div className="px-6 py-5 border-b shrink-0">
            <h2 className="text-sm font-semibold">召回测试</h2>
            <p className="text-xs text-muted-foreground mt-0.5">根据当前检索设置测试召回效果</p>
          </div>

          {/* Input */}
          <div className="px-6 py-5 border-b shrink-0 space-y-3">
            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <label className="text-xs font-medium text-muted-foreground">源文本</label>
                <span className="text-xs text-muted-foreground">{query.length} / 2000</span>
              </div>
              <textarea
                placeholder="输入查询内容…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) runTest(query); }}
                className="w-full min-h-[120px] resize-none text-sm rounded-md border border-input bg-background px-3 py-2.5 placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              />
            </div>
            <Button onClick={handleTest} disabled={loading || !query.trim()} className="w-full">
              <Search size={14} className="mr-1.5" />
              {loading ? "测试中…" : "测试"}
            </Button>
          </div>

          {/* History */}
          <div className="flex-1 overflow-y-auto">
            <div className="px-6 py-3 border-b">
              <p className="text-xs font-semibold text-muted-foreground">记录</p>
            </div>
            {history.length === 0 ? (
              <div className="px-6 py-8 text-center text-xs text-muted-foreground">暂无记录</div>
            ) : (
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b">
                    <th className="px-6 py-2 text-left font-medium text-muted-foreground">查询内容</th>
                    <th className="px-3 py-2 text-right font-medium text-muted-foreground whitespace-nowrap">段落数</th>
                    <th className="px-4 py-2 text-right font-medium text-muted-foreground whitespace-nowrap">时间</th>
                  </tr>
                </thead>
                <tbody>
                  {history.map((h) => (
                    <tr
                      key={h.id}
                      onClick={() => { setQuery(h.query); runTest(h.query); }}
                      className="border-b last:border-0 cursor-pointer hover:bg-accent/40 transition-colors"
                    >
                      <td className="px-6 py-2.5 max-w-[160px] truncate">{h.query}</td>
                      <td className="px-3 py-2.5 text-right tabular-nums text-muted-foreground">{h.result_count}</td>
                      <td className="px-4 py-2.5 text-right tabular-nums text-muted-foreground whitespace-nowrap">{formatTime(h.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>

        {/* Right: results — flex-1 takes remaining 60% */}
        <div className="flex-1 flex flex-col overflow-hidden">
          <div className="px-8 py-5 border-b bg-background shrink-0">
            {records !== null ? (
              <p className="text-sm font-semibold">
                <span className="text-primary">{records.length}</span> 个召回段落
              </p>
            ) : (
              <p className="text-sm font-semibold text-muted-foreground">召回结果</p>
            )}
          </div>

          <div className="flex-1 overflow-y-auto px-8 py-6">
            {loading ? (
              <div className="space-y-3">
                {[...Array(3)].map((_, i) => <Skeleton key={i} className="h-36 rounded-xl" />)}
              </div>
            ) : error ? (
              <div className="text-sm text-destructive bg-destructive/10 rounded-lg px-4 py-3">{error}</div>
            ) : records === null ? (
              <div className="flex flex-col items-center justify-center h-full gap-3 text-muted-foreground">
                <Search size={40} className="opacity-15" />
                <p className="text-sm">输入查询文本，点击「测试」查看召回结果</p>
              </div>
            ) : records.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full gap-3 text-muted-foreground">
                <FileText size={40} className="opacity-15" />
                <p className="text-sm">没有找到相关段落</p>
              </div>
            ) : (
              <div className="space-y-3">
                {records.map((rec, i) => (
                  <div key={rec.chunk_id} className="bg-background rounded-xl border px-5 py-4">
                    <div className="flex items-start justify-between gap-2 mb-2.5">
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="text-xs font-mono font-medium text-muted-foreground shrink-0">#{i + 1}</span>
                        <Badge variant="outline" className="text-xs py-0 px-1.5 h-4 shrink-0">
                          {CONTENT_TYPE_LABELS[rec.content_type] ?? rec.content_type}
                        </Badge>
                        {rec.section_path && (
                          <span className="text-xs text-muted-foreground truncate">{rec.section_path}</span>
                        )}
                      </div>
                      <ScoreBadge score={rec.score} />
                    </div>
                    <p className="text-sm leading-relaxed text-foreground/85 line-clamp-5 whitespace-pre-wrap">
                      {rec.text}
                    </p>
                    {rec.source_file && (
                      <div className="mt-3 pt-2.5 border-t border-border/50 flex items-center gap-1.5 min-w-0">
                        <FileText size={11} className="text-muted-foreground/60 shrink-0" />
                        <span className="text-xs text-muted-foreground truncate" title={rec.source_file}>
                          {rec.source_file}
                        </span>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
