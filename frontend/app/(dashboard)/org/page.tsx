"use client";

import { useEffect, useState } from "react";
import {
  Copy, Check, Building2, Users, Plug, Trash2, Plus, Loader2,
  Server, RefreshCw, Pencil, ChevronDown, ChevronRight,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  apiGetMyOrg,
  apiGetOrgMembers,
  apiListMcpConnections,
  apiAddMcpConnection,
  apiDeleteMcpConnection,
  apiGetMcpStatus,
  apiListDepts,
  apiCreateDept,
  apiUpdateDept,
  apiDeleteDept,
  apiPingDept,
  type OrgInfo,
  type OrgMember,
  type McpConnection,
  type McpStatusItem,
  type DeptFull,
  type DeptA2aStatus,
} from "@/lib/api";

const ORG_TYPE_LABEL: Record<string, string> = {
  department: "部门",
  command: "指挥中心",
};

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString("zh-CN", {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

function parsePort(url: string | null): string {
  if (!url) return "—";
  try {
    const p = new URL(url).port;
    return p ? `:${p}` : "—";
  } catch {
    return "—";
  }
}

// ── MCP form ───────────────────────────────────────────────────────────────

const EMPTY_MCP: McpConnection = { name: "", url: "", description: "" };

// ── Dept forms ─────────────────────────────────────────────────────────────

const EMPTY_ADD_DEPT = {
  name: "", dept_code: "", a2a_url: "",
  supervisor_hints: "", analyst_context: "", invite_code: "",
};

type EditDeptForm = {
  name: string; a2a_url: string;
  supervisor_hints: string; analyst_context: string;
};

// ── Page ───────────────────────────────────────────────────────────────────

export default function OrgPage() {
  const [org, setOrg] = useState<OrgInfo | null>(null);
  const [members, setMembers] = useState<OrgMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [noOrg, setNoOrg] = useState(false);
  const [codeCopied, setCodeCopied] = useState(false);
  const [linkCopied, setLinkCopied] = useState(false);

  // ── MCP state ──────────────────────────────────────────────────────────
  const [mcpConns, setMcpConns] = useState<McpConnection[]>([]);
  const [mcpLoading, setMcpLoading] = useState(false);
  const [showAddMcp, setShowAddMcp] = useState(false);
  const [mcpForm, setMcpForm] = useState<McpConnection>(EMPTY_MCP);
  const [addingMcp, setAddingMcp] = useState(false);
  const [deletingMcpName, setDeletingMcpName] = useState<string | null>(null);
  const [mcpError, setMcpError] = useState<string | null>(null);
  const [mcpStatus, setMcpStatus] = useState<McpStatusItem[]>([]);
  const [statusLoading, setStatusLoading] = useState(false);
  const [mcpStatusError, setMcpStatusError] = useState(false);

  // ── Dept / Agent Registry state ────────────────────────────────────────
  const [depts, setDepts] = useState<DeptFull[]>([]);
  const [deptsLoading, setDeptsLoading] = useState(false);
  const [deptStatuses, setDeptStatuses] = useState<Record<string, DeptA2aStatus>>({});
  const [pingingCode, setPingingCode] = useState<string | null>(null);
  const [pingingAll, setPingingAll] = useState(false);

  const [showAddDept, setShowAddDept] = useState(false);
  const [addDeptForm, setAddDeptForm] = useState(EMPTY_ADD_DEPT);
  const [addDeptExpanded, setAddDeptExpanded] = useState(false);
  const [addingDept, setAddingDept] = useState(false);
  const [deptError, setDeptError] = useState<string | null>(null);

  const [editingCode, setEditingCode] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<EditDeptForm>({ name: "", a2a_url: "", supervisor_hints: "", analyst_context: "" });
  const [savingEdit, setSavingEdit] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  const [confirmDeleteCode, setConfirmDeleteCode] = useState<string | null>(null);
  const [deletingCode, setDeletingCode] = useState<string | null>(null);

  const [expandedCode, setExpandedCode] = useState<string | null>(null);

  // ── Load org + members ─────────────────────────────────────────────────
  useEffect(() => {
    Promise.all([apiGetMyOrg(), apiGetOrgMembers()])
      .then(([orgData, membersData]) => {
        setOrg(orgData);
        setMembers(membersData);
      })
      .catch(() => setNoOrg(true))
      .finally(() => setLoading(false));
  }, []);

  // ── Load MCP + depts once org is known ────────────────────────────────
  useEffect(() => {
    if (!org) return;

    setMcpLoading(true);
    apiListMcpConnections()
      .then((conns) => {
        setMcpConns(conns);
        if (conns.length > 0) {
          setStatusLoading(true);
          setMcpStatusError(false);
          apiGetMcpStatus()
            .then((s) => { setMcpStatus(s); setMcpStatusError(false); })
            .catch(() => setMcpStatusError(true))
            .finally(() => setStatusLoading(false));
        }
      })
      .catch(() => {})
      .finally(() => setMcpLoading(false));

    if (org.type === "command") {
      setDeptsLoading(true);
      apiListDepts()
        .then(setDepts)
        .catch(() => {})
        .finally(() => setDeptsLoading(false));
    }
  }, [org]);

  // ── Copy helpers ────────────────────────────────────────────────────────
  function copyCode() {
    if (!org) return;
    navigator.clipboard.writeText(org.invite_code);
    setCodeCopied(true);
    setTimeout(() => setCodeCopied(false), 2000);
  }

  function copyLink() {
    if (!org) return;
    const url = `${window.location.origin}/register?code=${org.invite_code}`;
    navigator.clipboard.writeText(url);
    setLinkCopied(true);
    setTimeout(() => setLinkCopied(false), 2000);
  }

  // ── MCP handlers ────────────────────────────────────────────────────────
  async function handleAddMcp() {
    if (!mcpForm.name.trim() || !mcpForm.url.trim()) return;
    setAddingMcp(true);
    setMcpError(null);
    try {
      const updated = await apiAddMcpConnection({
        name: mcpForm.name.trim(),
        url: mcpForm.url.trim(),
        description: mcpForm.description.trim(),
      });
      setMcpConns(updated);
      setMcpForm(EMPTY_MCP);
      setShowAddMcp(false);
    } catch (e: unknown) {
      setMcpError(e instanceof Error ? e.message : "添加失败");
    } finally {
      setAddingMcp(false);
    }
  }

  async function handleDeleteMcp(name: string) {
    setDeletingMcpName(name);
    try {
      const updated = await apiDeleteMcpConnection(name);
      setMcpConns(updated);
    } catch {
      // silent
    } finally {
      setDeletingMcpName(null);
    }
  }

  async function refreshMcpStatus() {
    setStatusLoading(true);
    setMcpStatusError(false);
    try {
      setMcpStatus(await apiGetMcpStatus());
    } catch {
      setMcpStatusError(true);
    } finally {
      setStatusLoading(false);
    }
  }

  // ── Dept handlers ────────────────────────────────────────────────────────

  async function pingDept(deptCode: string) {
    setPingingCode(deptCode);
    try {
      const s = await apiPingDept(deptCode);
      setDeptStatuses((prev) => ({ ...prev, [deptCode]: s }));
    } catch {
      setDeptStatuses((prev) => ({ ...prev, [deptCode]: { dept_code: deptCode, online: false, latency_ms: null, error: "请求失败" } }));
    } finally {
      setPingingCode(null);
    }
  }

  async function pingAll() {
    setPingingAll(true);
    const codes = depts.map((d) => d.dept_code).filter(Boolean) as string[];
    const results = await Promise.allSettled(codes.map((c) => apiPingDept(c)));
    const next: Record<string, DeptA2aStatus> = {};
    results.forEach((r, i) => {
      const code = codes[i];
      next[code] = r.status === "fulfilled"
        ? r.value
        : { dept_code: code, online: false, latency_ms: null, error: "请求失败" };
    });
    setDeptStatuses(next);
    setPingingAll(false);
  }

  async function handleAddDept() {
    if (!addDeptForm.name.trim() || !addDeptForm.dept_code.trim()) return;
    setAddingDept(true);
    setDeptError(null);
    try {
      const created = await apiCreateDept({
        name: addDeptForm.name.trim(),
        dept_code: addDeptForm.dept_code.trim(),
        a2a_url: addDeptForm.a2a_url.trim() || undefined,
        supervisor_hints: addDeptForm.supervisor_hints.trim() || undefined,
        analyst_context: addDeptForm.analyst_context.trim() || undefined,
        invite_code: addDeptForm.invite_code.trim() || undefined,
      });
      setDepts((prev) => [...prev, created]);
      setAddDeptForm(EMPTY_ADD_DEPT);
      setAddDeptExpanded(false);
      setShowAddDept(false);
    } catch (e: unknown) {
      setDeptError(e instanceof Error ? e.message : "添加失败");
    } finally {
      setAddingDept(false);
    }
  }

  function startEditDept(dept: DeptFull) {
    setEditingCode(dept.dept_code);
    setEditForm({
      name: dept.name,
      a2a_url: dept.a2a_url ?? "",
      supervisor_hints: dept.dept_prompts?.supervisor_hints ?? "",
      analyst_context: dept.dept_prompts?.analyst_context ?? "",
    });
    setEditError(null);
    setConfirmDeleteCode(null);
    setExpandedCode(null);
  }

  async function handleSaveEdit(deptCode: string) {
    setSavingEdit(true);
    setEditError(null);
    try {
      const updated = await apiUpdateDept(deptCode, {
        name: editForm.name.trim() || undefined,
        a2a_url: editForm.a2a_url.trim() || undefined,
        supervisor_hints: editForm.supervisor_hints.trim() || undefined,
        analyst_context: editForm.analyst_context.trim() || undefined,
      });
      setDepts((prev) => prev.map((d) => d.dept_code === deptCode ? updated : d));
      setEditingCode(null);
    } catch (e: unknown) {
      setEditError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSavingEdit(false);
    }
  }

  async function handleDeleteDept(deptCode: string) {
    setDeletingCode(deptCode);
    try {
      await apiDeleteDept(deptCode);
      setDepts((prev) => prev.filter((d) => d.dept_code !== deptCode));
      setConfirmDeleteCode(null);
      setDeptStatuses((prev) => { const n = { ...prev }; delete n[deptCode]; return n; });
    } catch (e: unknown) {
      // show error in place
      setDeptError(e instanceof Error ? e.message : "删除失败");
      setConfirmDeleteCode(null);
    } finally {
      setDeletingCode(null);
    }
  }

  // ── Sub-components ────────────────────────────────────────────────────────

  function McpStatusDot({ name }: { name: string }) {
    const item = mcpStatus.find((s) => s.name === name);
    if (mcpStatusError) return <span className="w-2 h-2 rounded-full bg-yellow-500 inline-block shrink-0" title="探测失败" />;
    if (!item && !statusLoading) return <span className="w-2 h-2 rounded-full bg-muted-foreground/30 inline-block shrink-0" title="未探测" />;
    if (statusLoading && !item) return <span className="w-2 h-2 rounded-full bg-muted-foreground/30 animate-pulse inline-block shrink-0" />;
    if (!item) return null;
    return (
      <span
        className={`w-2 h-2 rounded-full inline-block shrink-0 ${item.online ? "bg-green-500" : "bg-red-500"}`}
        title={item.online ? `在线 · ${item.latency_ms}ms` : "离线"}
      />
    );
  }

  function DeptStatusDot({ deptCode }: { deptCode: string }) {
    const s = deptStatuses[deptCode];
    const pinging = pingingCode === deptCode || pingingAll;
    if (pinging) return <span className="w-2 h-2 rounded-full bg-muted-foreground/30 animate-pulse inline-block shrink-0" />;
    if (!s) return <span className="w-2 h-2 rounded-full bg-muted-foreground/20 inline-block shrink-0" title="未检测" />;
    return (
      <span
        className={`w-2 h-2 rounded-full inline-block shrink-0 ${s.online ? "bg-green-500" : "bg-red-500"}`}
        title={s.online ? `在线 · ${s.latency_ms}ms` : (s.error ?? "离线")}
      />
    );
  }

  // ── Loading / no-org ───────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="h-full flex items-center justify-center text-muted-foreground text-sm">
        加载中…
      </div>
    );
  }

  if (noOrg || !org) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3 text-center px-6">
        <Building2 size={40} className="text-muted-foreground/40" />
        <p className="text-muted-foreground text-sm">您还未加入任何部门</p>
        <p className="text-muted-foreground/60 text-xs">请联系管理员获取部门邀请码，然后重新注册或绑定</p>
      </div>
    );
  }

  // ── Main render ────────────────────────────────────────────────────────
  return (
    <div className="h-full overflow-auto p-8">
      <div className="max-w-2xl mx-auto space-y-6">

        {/* 部门基本信息 */}
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-start justify-between">
              <div className="space-y-1">
                <CardTitle className="text-xl flex items-center gap-2">
                  <Building2 size={20} className="text-muted-foreground" />
                  {org.name}
                </CardTitle>
                <p className="text-xs text-muted-foreground">创建于 {formatDate(org.created_at)}</p>
              </div>
              <Badge variant="secondary">
                {ORG_TYPE_LABEL[org.type] ?? org.type}
              </Badge>
            </div>
          </CardHeader>
        </Card>

        {/* 邀请码 */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">部门邀请码</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-center gap-2 bg-muted rounded-lg px-4 py-3">
              <span className="flex-1 font-mono text-lg tracking-widest font-semibold">
                {org.invite_code}
              </span>
              <Button size="sm" variant="ghost" onClick={copyCode} className="gap-1.5 shrink-0">
                {codeCopied ? <Check size={14} className="text-green-500" /> : <Copy size={14} />}
                {codeCopied ? "已复制" : "复制"}
              </Button>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground flex-1 truncate">
                {typeof window !== "undefined"
                  ? `${window.location.origin}/register?code=${org.invite_code}`
                  : ""}
              </span>
              <Button size="sm" variant="outline" onClick={copyLink} className="gap-1.5 shrink-0 text-xs">
                {linkCopied ? <Check size={13} className="text-green-500" /> : <Copy size={13} />}
                {linkCopied ? "已复制" : "复制邀请链接"}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">将邀请链接发给同事，他们注册后即可加入本部门</p>
          </CardContent>
        </Card>

        {/* 成员列表 */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base flex items-center gap-2">
              <Users size={16} className="text-muted-foreground" />
              成员
              <span className="text-muted-foreground font-normal text-sm">({members.length})</span>
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {members.length === 0 ? (
              <p className="text-sm text-muted-foreground px-6 pb-4">暂无成员</p>
            ) : (
              <ul className="divide-y">
                {members.map((m) => (
                  <li key={m.id} className="flex items-center justify-between px-6 py-3">
                    <span className="text-sm">{m.email}</span>
                    <span className="text-xs text-muted-foreground">{formatDate(m.joined_at)}</span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        {/* MCP 连接管理 */}
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-base flex items-center gap-2">
                <Plug size={16} className="text-muted-foreground" />
                MCP 连接
                {!mcpLoading && (
                  <span className="text-muted-foreground font-normal text-sm">({mcpConns.length})</span>
                )}
              </CardTitle>
              <div className="flex gap-2">
                {mcpConns.length > 0 && (
                  <Button
                    size="sm" variant="ghost"
                    className="gap-1.5 text-xs text-muted-foreground"
                    disabled={statusLoading}
                    onClick={refreshMcpStatus}
                  >
                    {statusLoading ? <Loader2 size={12} className="animate-spin" /> : "↻"}
                    检测
                  </Button>
                )}
                <Button
                  size="sm" variant="outline"
                  className="gap-1.5 text-xs"
                  onClick={() => { setShowAddMcp((v) => !v); setMcpError(null); setMcpForm(EMPTY_MCP); }}
                >
                  <Plus size={13} />
                  添加连接
                </Button>
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            {showAddMcp && (
              <div className="rounded-lg border bg-muted/30 p-4 space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1">
                    <Label className="text-xs">名称 *</Label>
                    <Input
                      placeholder="如 ambulance_dispatch"
                      value={mcpForm.name}
                      onChange={(e) => setMcpForm((f) => ({ ...f, name: e.target.value }))}
                      className="h-8 text-sm"
                    />
                  </div>
                  <div className="space-y-1">
                    <Label className="text-xs">URL *</Label>
                    <Input
                      placeholder="http://localhost:8102/mcp"
                      value={mcpForm.url}
                      onChange={(e) => setMcpForm((f) => ({ ...f, url: e.target.value }))}
                      className="h-8 text-sm"
                    />
                  </div>
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">描述</Label>
                  <Input
                    placeholder="救护车调度 MCP Server"
                    value={mcpForm.description}
                    onChange={(e) => setMcpForm((f) => ({ ...f, description: e.target.value }))}
                    className="h-8 text-sm"
                  />
                </div>
                {mcpError && <p className="text-xs text-destructive">{mcpError}</p>}
                <div className="flex gap-2 justify-end">
                  <Button size="sm" variant="ghost" className="text-xs"
                    onClick={() => { setShowAddMcp(false); setMcpError(null); }}>
                    取消
                  </Button>
                  <Button size="sm" className="text-xs gap-1.5"
                    disabled={addingMcp || !mcpForm.name.trim() || !mcpForm.url.trim()}
                    onClick={handleAddMcp}>
                    {addingMcp && <Loader2 size={12} className="animate-spin" />}
                    确认添加
                  </Button>
                </div>
              </div>
            )}

            {mcpLoading ? (
              <p className="text-sm text-muted-foreground py-1">加载中…</p>
            ) : mcpConns.length === 0 && !showAddMcp ? (
              <p className="text-sm text-muted-foreground py-1">
                暂无 MCP 连接。添加后 Agent 将在回答问题时调用对应工具。
              </p>
            ) : (
              <ul className="divide-y border rounded-lg overflow-hidden">
                {mcpConns.map((conn) => (
                  <li key={conn.name} className="flex items-start gap-3 px-4 py-3 bg-background">
                    <div className="flex items-center gap-1.5 mt-0.5 shrink-0">
                      <McpStatusDot name={conn.name} />
                      <Plug size={14} className="text-muted-foreground" />
                    </div>
                    <div className="flex-1 min-w-0 space-y-0.5">
                      <p className="text-sm font-medium truncate">
                        {conn.description?.match(/^([一-龥·]+)/)?.[1] ?? conn.name}
                        <span className="ml-2 text-xs font-normal font-mono text-muted-foreground">{conn.name}</span>
                      </p>
                      <p className="text-xs text-muted-foreground font-mono truncate" title={conn.url}>{conn.url}</p>
                      {conn.description && (
                        <p className="text-xs text-muted-foreground">{conn.description}</p>
                      )}
                    </div>
                    <Button
                      size="icon" variant="ghost"
                      className="h-7 w-7 shrink-0 text-muted-foreground hover:text-destructive"
                      disabled={deletingMcpName === conn.name}
                      onClick={() => handleDeleteMcp(conn.name)}
                    >
                      {deletingMcpName === conn.name
                        ? <Loader2 size={13} className="animate-spin" />
                        : <Trash2 size={13} />}
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        {/* Agent 注册表（仅 command 账号可见） */}
        {org.type === "command" && (
          <Card>
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <CardTitle className="text-base flex items-center gap-2">
                  <Server size={16} className="text-muted-foreground" />
                  Agent 注册表
                  {!deptsLoading && (
                    <span className="text-muted-foreground font-normal text-sm">({depts.length})</span>
                  )}
                </CardTitle>
                <div className="flex gap-2">
                  {depts.length > 0 && (
                    <Button
                      size="sm" variant="ghost"
                      className="gap-1.5 text-xs text-muted-foreground"
                      disabled={pingingAll}
                      onClick={pingAll}
                    >
                      {pingingAll
                        ? <Loader2 size={12} className="animate-spin" />
                        : <RefreshCw size={12} />}
                      检测全部
                    </Button>
                  )}
                  <Button
                    size="sm" variant="outline" className="gap-1.5 text-xs"
                    onClick={() => { setShowAddDept((v) => !v); setDeptError(null); setAddDeptForm(EMPTY_ADD_DEPT); setAddDeptExpanded(false); }}
                  >
                    <Plus size={13} />
                    添加部门
                  </Button>
                </div>
              </div>
            </CardHeader>

            <CardContent className="space-y-3">
              {/* 添加部门表单 */}
              {showAddDept && (
                <div className="rounded-lg border bg-muted/30 p-4 space-y-3">
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-1">
                      <Label className="text-xs">部门名称 *</Label>
                      <Input
                        placeholder="如 医疗急救(120)"
                        value={addDeptForm.name}
                        onChange={(e) => setAddDeptForm((f) => ({ ...f, name: e.target.value }))}
                        className="h-8 text-sm"
                      />
                    </div>
                    <div className="space-y-1">
                      <Label className="text-xs">部门代码 *</Label>
                      <Input
                        placeholder="如 medical_ems"
                        value={addDeptForm.dept_code}
                        onChange={(e) => setAddDeptForm((f) => ({ ...f, dept_code: e.target.value }))}
                        className="h-8 text-sm font-mono"
                      />
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-1">
                      <Label className="text-xs">A2A 地址</Label>
                      <Input
                        placeholder="http://localhost:9002"
                        value={addDeptForm.a2a_url}
                        onChange={(e) => setAddDeptForm((f) => ({ ...f, a2a_url: e.target.value }))}
                        className="h-8 text-sm font-mono"
                      />
                    </div>
                    <div className="space-y-1">
                      <Label className="text-xs">邀请码（可选）</Label>
                      <Input
                        placeholder="自动生成"
                        value={addDeptForm.invite_code}
                        onChange={(e) => setAddDeptForm((f) => ({ ...f, invite_code: e.target.value }))}
                        className="h-8 text-sm font-mono"
                      />
                    </div>
                  </div>

                  {/* 折叠的提示词配置 */}
                  <button
                    type="button"
                    className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
                    onClick={() => setAddDeptExpanded((v) => !v)}
                  >
                    {addDeptExpanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                    提示词配置（可选）
                  </button>
                  {addDeptExpanded && (
                    <div className="space-y-3">
                      <div className="space-y-1">
                        <Label className="text-xs">Supervisor Hints</Label>
                        <Textarea
                          placeholder="指挥官对该部门的路由提示…"
                          value={addDeptForm.supervisor_hints}
                          onChange={(e) => setAddDeptForm((f) => ({ ...f, supervisor_hints: e.target.value }))}
                          className="text-xs min-h-[80px] font-mono"
                        />
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">Analyst Context</Label>
                        <Textarea
                          placeholder="分析师系统提示词…"
                          value={addDeptForm.analyst_context}
                          onChange={(e) => setAddDeptForm((f) => ({ ...f, analyst_context: e.target.value }))}
                          className="text-xs min-h-[80px] font-mono"
                        />
                      </div>
                    </div>
                  )}

                  {deptError && <p className="text-xs text-destructive">{deptError}</p>}
                  <div className="flex gap-2 justify-end">
                    <Button size="sm" variant="ghost" className="text-xs"
                      onClick={() => { setShowAddDept(false); setDeptError(null); }}>
                      取消
                    </Button>
                    <Button
                      size="sm" className="text-xs gap-1.5"
                      disabled={addingDept || !addDeptForm.name.trim() || !addDeptForm.dept_code.trim()}
                      onClick={handleAddDept}
                    >
                      {addingDept && <Loader2 size={12} className="animate-spin" />}
                      创建部门
                    </Button>
                  </div>
                </div>
              )}

              {/* 部门列表 */}
              {deptsLoading ? (
                <p className="text-sm text-muted-foreground py-1">加载中…</p>
              ) : depts.length === 0 && !showAddDept ? (
                <p className="text-sm text-muted-foreground py-1">
                  暂无注册部门。点击"添加部门"注册 A2A Sub-Agent。
                </p>
              ) : (
                <ul className="divide-y border rounded-lg overflow-hidden">
                  {depts.map((dept) => {
                    const code = dept.dept_code ?? "";
                    const isEditing = editingCode === code;
                    const isConfirmDelete = confirmDeleteCode === code;
                    const isExpanded = expandedCode === code;
                    const status = deptStatuses[code];

                    if (isEditing) {
                      return (
                        <li key={code} className="px-4 py-4 bg-muted/20 space-y-3">
                          <div className="flex items-center gap-2 mb-1">
                            <Badge variant="outline" className="font-mono text-xs shrink-0">{code}</Badge>
                            <span className="text-xs text-muted-foreground">编辑中</span>
                          </div>
                          <div className="grid grid-cols-2 gap-3">
                            <div className="space-y-1">
                              <Label className="text-xs">部门名称</Label>
                              <Input
                                value={editForm.name}
                                onChange={(e) => setEditForm((f) => ({ ...f, name: e.target.value }))}
                                className="h-8 text-sm"
                              />
                            </div>
                            <div className="space-y-1">
                              <Label className="text-xs">A2A 地址</Label>
                              <Input
                                value={editForm.a2a_url}
                                onChange={(e) => setEditForm((f) => ({ ...f, a2a_url: e.target.value }))}
                                className="h-8 text-sm font-mono"
                                placeholder="http://localhost:9002"
                              />
                            </div>
                          </div>
                          <div className="space-y-1">
                            <Label className="text-xs">Supervisor Hints</Label>
                            <Textarea
                              value={editForm.supervisor_hints}
                              onChange={(e) => setEditForm((f) => ({ ...f, supervisor_hints: e.target.value }))}
                              className="text-xs min-h-[80px] font-mono"
                            />
                          </div>
                          <div className="space-y-1">
                            <Label className="text-xs">Analyst Context</Label>
                            <Textarea
                              value={editForm.analyst_context}
                              onChange={(e) => setEditForm((f) => ({ ...f, analyst_context: e.target.value }))}
                              className="text-xs min-h-[80px] font-mono"
                            />
                          </div>
                          {editError && <p className="text-xs text-destructive">{editError}</p>}
                          <div className="flex gap-2 justify-end">
                            <Button size="sm" variant="ghost" className="text-xs"
                              onClick={() => { setEditingCode(null); setEditError(null); }}>
                              取消
                            </Button>
                            <Button size="sm" className="text-xs gap-1.5"
                              disabled={savingEdit}
                              onClick={() => handleSaveEdit(code)}>
                              {savingEdit && <Loader2 size={12} className="animate-spin" />}
                              保存
                            </Button>
                          </div>
                        </li>
                      );
                    }

                    return (
                      <li key={code} className="bg-background">
                        {/* 主行 */}
                        <div className="flex items-center gap-3 px-4 py-3">
                          <DeptStatusDot deptCode={code} />
                          <Badge variant="outline" className="font-mono text-xs shrink-0">{code}</Badge>
                          <span className="text-sm font-medium flex-1 truncate">{dept.name}</span>
                          <span className="text-xs font-mono text-muted-foreground shrink-0">
                            {parsePort(dept.a2a_url)}
                            {status?.online && status.latency_ms != null && (
                              <span className="ml-1 text-green-600">{status.latency_ms}ms</span>
                            )}
                          </span>
                          <div className="flex items-center gap-1 shrink-0">
                            {/* 展开按钮（有提示词时） */}
                            {(dept.dept_prompts?.supervisor_hints || dept.dept_prompts?.analyst_context) && (
                              <Button
                                size="icon" variant="ghost"
                                className="h-7 w-7 text-muted-foreground"
                                onClick={() => setExpandedCode(isExpanded ? null : code)}
                                title="查看提示词"
                              >
                                {isExpanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                              </Button>
                            )}
                            {/* Ping */}
                            <Button
                              size="icon" variant="ghost"
                              className="h-7 w-7 text-muted-foreground"
                              disabled={pingingCode === code || pingingAll}
                              onClick={() => pingDept(code)}
                              title="Ping A2A"
                            >
                              {pingingCode === code
                                ? <Loader2 size={12} className="animate-spin" />
                                : <RefreshCw size={12} />}
                            </Button>
                            {/* 编辑 */}
                            <Button
                              size="icon" variant="ghost"
                              className="h-7 w-7 text-muted-foreground hover:text-foreground"
                              onClick={() => startEditDept(dept)}
                              title="编辑"
                            >
                              <Pencil size={13} />
                            </Button>
                            {/* 删除 */}
                            {isConfirmDelete ? (
                              <div className="flex items-center gap-1">
                                <span className="text-xs text-destructive">确认？</span>
                                <Button size="sm" variant="destructive"
                                  className="h-6 px-2 text-xs"
                                  disabled={deletingCode === code}
                                  onClick={() => handleDeleteDept(code)}>
                                  {deletingCode === code ? <Loader2 size={11} className="animate-spin" /> : "删"}
                                </Button>
                                <Button size="sm" variant="ghost"
                                  className="h-6 px-2 text-xs"
                                  onClick={() => setConfirmDeleteCode(null)}>
                                  取消
                                </Button>
                              </div>
                            ) : (
                              <Button
                                size="icon" variant="ghost"
                                className="h-7 w-7 text-muted-foreground hover:text-destructive"
                                onClick={() => { setConfirmDeleteCode(code); setEditingCode(null); }}
                                title="删除"
                              >
                                <Trash2 size={13} />
                              </Button>
                            )}
                          </div>
                        </div>

                        {/* 展开的提示词预览 */}
                        {isExpanded && (
                          <div className="px-4 pb-4 space-y-3 border-t bg-muted/10">
                            {dept.dept_prompts?.supervisor_hints && (
                              <div className="pt-3 space-y-1">
                                <p className="text-xs font-medium text-muted-foreground">Supervisor Hints</p>
                                <p className="text-xs text-muted-foreground font-mono whitespace-pre-wrap leading-relaxed">
                                  {dept.dept_prompts.supervisor_hints}
                                </p>
                              </div>
                            )}
                            {dept.dept_prompts?.analyst_context && (
                              <div className="space-y-1">
                                <p className="text-xs font-medium text-muted-foreground">Analyst Context</p>
                                <p className="text-xs text-muted-foreground font-mono whitespace-pre-wrap leading-relaxed">
                                  {dept.dept_prompts.analyst_context}
                                </p>
                              </div>
                            )}
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}

              {/* 全局删除错误提示 */}
              {deptError && !showAddDept && (
                <p className="text-xs text-destructive">{deptError}</p>
              )}
            </CardContent>
          </Card>
        )}

      </div>
    </div>
  );
}
