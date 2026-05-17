"use client";

import { useEffect, useState } from "react";
import { IconDatabase, IconCheck } from "@tabler/icons-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { apiListKBs, type KnowledgeBase } from "@/lib/api";

interface NewSessionDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 确认后调用，传入选中的 kb_ids，由父级负责实际创建和跳转 */
  onConfirm: (kbIds: string[]) => Promise<void>;
}

export function NewSessionDialog({
  open,
  onOpenChange,
  onConfirm,
}: NewSessionDialogProps) {
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [confirming, setConfirming] = useState(false);

  // 每次打开时重新加载知识库列表，默认选中第一个
  useEffect(() => {
    if (!open) return;
    setLoading(true);
    apiListKBs()
      .then((list) => {
        setKbs(list);
        setSelectedIds(list.length > 0 ? new Set([list[0].id]) : new Set());
      })
      .catch(() => {
        setKbs([]);
        setSelectedIds(new Set());
      })
      .finally(() => setLoading(false));
  }, [open]);

  function toggle(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function handleConfirm() {
    setConfirming(true);
    try {
      await onConfirm(Array.from(selectedIds));
      onOpenChange(false);
    } finally {
      setConfirming(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>新建对话</DialogTitle>
          <DialogDescription>
            选择知识库，Agent 将从中检索相关内容
          </DialogDescription>
        </DialogHeader>

        {/* 知识库列表 */}
        <div style={{ display: "flex", flexDirection: "column", gap: 6, margin: "4px 0" }}>
          {loading ? (
            <>
              <Skeleton style={{ height: 44, borderRadius: 8 }} />
              <Skeleton style={{ height: 44, borderRadius: 8 }} />
              <Skeleton style={{ height: 44, borderRadius: 8 }} />
            </>
          ) : kbs.length === 0 ? (
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: 8,
                padding: "24px 0",
                color: "var(--color-text-tertiary)",
              }}
            >
              <IconDatabase size={28} style={{ opacity: 0.4 }} />
              <div style={{ fontSize: 13 }}>还没有知识库</div>
              <div style={{ fontSize: 12, opacity: 0.7 }}>
                可在知识库页面创建后再开始对话
              </div>
            </div>
          ) : (
            kbs.map((kb) => {
              const checked = selectedIds.has(kb.id);
              return (
                <button
                  key={kb.id}
                  onClick={() => toggle(kb.id)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    padding: "10px 12px",
                    borderRadius: 8,
                    border: checked
                      ? "1px solid rgba(24, 95, 165, 0.6)"
                      : "1px solid var(--color-border-tertiary)",
                    background: checked
                      ? "rgba(24, 95, 165, 0.12)"
                      : "var(--color-background-secondary)",
                    cursor: "pointer",
                    textAlign: "left",
                    width: "100%",
                    transition: "all 0.15s",
                  }}
                >
                  {/* 自定义 checkbox */}
                  <div
                    style={{
                      width: 16,
                      height: 16,
                      borderRadius: 4,
                      flexShrink: 0,
                      border: checked
                        ? "none"
                        : "1.5px solid var(--color-border-secondary)",
                      background: checked ? "#185FA5" : "transparent",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                  >
                    {checked && <IconCheck size={10} color="#fff" strokeWidth={3} />}
                  </div>

                  <IconDatabase
                    size={15}
                    style={{ color: "var(--color-text-tertiary)", flexShrink: 0 }}
                  />

                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        fontSize: 13,
                        fontWeight: 500,
                        color: "var(--color-text-primary)",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {kb.name}
                    </div>
                    {kb.description && (
                      <div
                        style={{
                          fontSize: 11,
                          color: "var(--color-text-tertiary)",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                          marginTop: 1,
                        }}
                      >
                        {kb.description}
                      </div>
                    )}
                  </div>
                </button>
              );
            })
          )}
        </div>

        {/* 提示文案 */}
        {!loading && kbs.length > 0 && (
          <p style={{ fontSize: 12, color: "var(--color-text-tertiary)", margin: "0 0 4px" }}>
            {selectedIds.size === 0
              ? "未选择知识库时，Agent 仅依赖自身知识回答"
              : `已选 ${selectedIds.size} 个知识库`}
          </p>
        )}

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={confirming}
          >
            取消
          </Button>
          <Button onClick={handleConfirm} disabled={confirming || loading}>
            {confirming ? "创建中…" : "开始对话"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
