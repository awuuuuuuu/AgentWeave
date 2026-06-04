"use client";

import { useEffect, useState } from "react";
import {
  IconDatabase,
  IconCheck,
  IconUsers,
  IconMessageCircle,
  IconSitemap,
} from "@tabler/icons-react";
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
import { fetchWeaveDepts, type WeaveDept } from "@/lib/agent-api";

interface NewSessionDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: (
    kbIds: string[],
    sessionType: "chat" | "weave",
    deptOrgIds: string[]
  ) => Promise<void>;
  /** 是否显示 Weave 指挥台选项（仅 command 类型机构可用） */
  showWeave?: boolean;
  /** 打开时默认激活的会话类型 */
  defaultType?: "chat" | "weave";
}

export function NewSessionDialog({
  open,
  onOpenChange,
  onConfirm,
  showWeave = false,
  defaultType,
}: NewSessionDialogProps) {
  const [sessionType, setSessionType] = useState<"chat" | "weave">(defaultType ?? "chat");

  // chat mode
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [selectedKbIds, setSelectedKbIds] = useState<Set<string>>(new Set());
  const [kbLoading, setKbLoading] = useState(false);

  // weave mode
  const [depts, setDepts] = useState<WeaveDept[]>([]);
  const [selectedDeptCodes, setSelectedDeptCodes] = useState<Set<string>>(new Set());
  const [deptsLoading, setDeptsLoading] = useState(false);

  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    if (!open) return;
    // 打开时重置为指定默认类型
    setSessionType(defaultType ?? (showWeave ? "weave" : "chat"));
    // Load KB list for chat mode
    setKbLoading(true);
    apiListKBs()
      .then((list) => {
        setKbs(list);
        setSelectedKbIds(list.length > 0 ? new Set([list[0].id]) : new Set());
      })
      .catch(() => { setKbs([]); setSelectedKbIds(new Set()); })
      .finally(() => setKbLoading(false));

    // Load department list for weave mode
    setDeptsLoading(true);
    fetchWeaveDepts()
      .then((list) => {
        setDepts(list);
        setSelectedDeptCodes(new Set(list.map((d: WeaveDept) => d.dept_code)));
      })
      .catch(() => { setDepts([]); setSelectedDeptCodes(new Set()); })
      .finally(() => setDeptsLoading(false));
  }, [open]);

  function toggleKb(id: string) {
    setSelectedKbIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function toggleDept(id: string) {
    setSelectedDeptCodes((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  async function handleConfirm() {
    setConfirming(true);
    try {
      await onConfirm(
        Array.from(selectedKbIds),
        sessionType,
        Array.from(selectedDeptCodes)
      );
      onOpenChange(false);
    } finally {
      setConfirming(false);
    }
  }

  const isWeave = sessionType === "weave";
  const loading = isWeave ? deptsLoading : kbLoading;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>新建会话</DialogTitle>
          <DialogDescription>
            {isWeave
              ? "Weave 模式：多 Agent 协作指挥台，选择参与的部门"
              : "普通对话：选择知识库，Agent 将从中检索相关内容"}
          </DialogDescription>
        </DialogHeader>

        {/* ── 会话类型 Toggle（仅 command 机构可见）──────────────── */}
        {showWeave && (
          <div
            style={{
              display: "flex",
              borderRadius: 8,
              border: "0.5px solid rgba(255,255,255,0.1)",
              background: "rgba(255,255,255,0.03)",
              padding: 3,
              gap: 3,
            }}
          >
            {(["chat", "weave"] as const).map((type) => {
              const active = sessionType === type;
              return (
                <button
                  key={type}
                  onClick={() => setSessionType(type)}
                  style={{
                    flex: 1,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    gap: 6,
                    padding: "7px 0",
                    borderRadius: 6,
                    border: "none",
                    background: active ? "rgba(24, 95, 165, 0.85)" : "transparent",
                    color: active ? "#fff" : "var(--color-text-secondary)",
                    fontSize: 12,
                    fontWeight: active ? 600 : 400,
                    cursor: "pointer",
                    transition: "all 0.15s",
                  }}
                >
                  {type === "chat"
                    ? <IconMessageCircle size={13} />
                    : <IconSitemap size={13} />}
                  {type === "chat" ? "普通对话" : "Weave 指挥台"}
                </button>
              );
            })}
          </div>
        )}

        {/* ── 内容区：KB 列表 或 部门列表 ──────────────────────────── */}
        <div style={{ display: "flex", flexDirection: "column", gap: 6, margin: "4px 0", minHeight: 120 }}>
          {loading ? (
            <>
              <Skeleton style={{ height: 44, borderRadius: 8 }} />
              <Skeleton style={{ height: 44, borderRadius: 8 }} />
              <Skeleton style={{ height: 44, borderRadius: 8 }} />
            </>
          ) : isWeave ? (
            /* ── Weave：部门 checkbox ── */
            depts.length === 0 ? (
              <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8, padding: "24px 0", color: "var(--color-text-tertiary)" }}>
                <IconUsers size={28} style={{ opacity: 0.4 }} />
                <div style={{ fontSize: 13 }}>暂无可用部门</div>
                <div style={{ fontSize: 12, opacity: 0.7 }}>请先在系统中创建部门机构</div>
              </div>
            ) : (
              depts.map((d) => {
                const checked = selectedDeptCodes.has(d.dept_code);
                return (
                  <button
                    key={d.dept_code}
                    onClick={() => toggleDept(d.dept_code)}
                    style={{
                      display: "flex", alignItems: "center", gap: 10,
                      padding: "10px 12px", borderRadius: 8, width: "100%",
                      border: checked ? "1px solid rgba(24, 95, 165, 0.6)" : "1px solid var(--color-border-tertiary)",
                      background: checked ? "rgba(24, 95, 165, 0.12)" : "var(--color-background-secondary)",
                      cursor: "pointer", textAlign: "left", transition: "all 0.15s",
                    }}
                  >
                    <div style={{
                      width: 16, height: 16, borderRadius: 4, flexShrink: 0,
                      border: checked ? "none" : "1.5px solid var(--color-border-secondary)",
                      background: checked ? "#185FA5" : "transparent",
                      display: "flex", alignItems: "center", justifyContent: "center",
                    }}>
                      {checked && <IconCheck size={10} color="#fff" strokeWidth={3} />}
                    </div>
                    <IconUsers size={15} style={{ color: "var(--color-text-tertiary)", flexShrink: 0 }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 13, fontWeight: 500, color: "var(--color-text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {d.name}
                      </div>
                      <div style={{ fontSize: 11, color: "var(--color-text-tertiary)", marginTop: 1 }}>
                        {d.dept_code} · A2A :{d.a2a_port}
                      </div>
                    </div>
                  </button>
                );
              })
            )
          ) : (
            /* ── Chat：知识库 checkbox ── */
            kbs.length === 0 ? (
              <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8, padding: "24px 0", color: "var(--color-text-tertiary)" }}>
                <IconDatabase size={28} style={{ opacity: 0.4 }} />
                <div style={{ fontSize: 13 }}>还没有知识库</div>
                <div style={{ fontSize: 12, opacity: 0.7 }}>可在知识库页面创建后再开始对话</div>
              </div>
            ) : (
              kbs.map((kb) => {
                const checked = selectedKbIds.has(kb.id);
                return (
                  <button
                    key={kb.id}
                    onClick={() => toggleKb(kb.id)}
                    style={{
                      display: "flex", alignItems: "center", gap: 10,
                      padding: "10px 12px", borderRadius: 8, width: "100%",
                      border: checked ? "1px solid rgba(24, 95, 165, 0.6)" : "1px solid var(--color-border-tertiary)",
                      background: checked ? "rgba(24, 95, 165, 0.12)" : "var(--color-background-secondary)",
                      cursor: "pointer", textAlign: "left", transition: "all 0.15s",
                    }}
                  >
                    <div style={{
                      width: 16, height: 16, borderRadius: 4, flexShrink: 0,
                      border: checked ? "none" : "1.5px solid var(--color-border-secondary)",
                      background: checked ? "#185FA5" : "transparent",
                      display: "flex", alignItems: "center", justifyContent: "center",
                    }}>
                      {checked && <IconCheck size={10} color="#fff" strokeWidth={3} />}
                    </div>
                    <IconDatabase size={15} style={{ color: "var(--color-text-tertiary)", flexShrink: 0 }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 13, fontWeight: 500, color: "var(--color-text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {kb.name}
                      </div>
                      {kb.description && (
                        <div style={{ fontSize: 11, color: "var(--color-text-tertiary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", marginTop: 1 }}>
                          {kb.description}
                        </div>
                      )}
                    </div>
                  </button>
                );
              })
            )
          )}
        </div>

        {/* 提示文案 */}
        {!loading && (
          <p style={{ fontSize: 12, color: "var(--color-text-tertiary)", margin: "0 0 4px" }}>
            {isWeave
              ? selectedDeptCodes.size === 0
                ? "未选择部门时将使用系统默认配置"
                : `已选 ${selectedDeptCodes.size} 个部门参与协作`
              : selectedKbIds.size === 0
                ? "未选择知识库时，Agent 仅依赖自身知识回答"
                : `已选 ${selectedKbIds.size} 个知识库`}
          </p>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={confirming}>
            取消
          </Button>
          <Button onClick={handleConfirm} disabled={confirming || loading}>
            {confirming ? "创建中…" : isWeave ? "启动 Weave" : "开始对话"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
