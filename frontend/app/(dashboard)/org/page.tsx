"use client";

import { useEffect, useState } from "react";
import { Copy, Check, Building2, Users, Plug, Trash2, Plus, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  apiGetMyOrg,
  apiGetOrgMembers,
  apiListMcpConnections,
  apiAddMcpConnection,
  apiDeleteMcpConnection,
  type OrgInfo,
  type OrgMember,
  type McpConnection,
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

const EMPTY_FORM: McpConnection = { name: "", url: "", description: "" };

export default function OrgPage() {
  const [org, setOrg] = useState<OrgInfo | null>(null);
  const [members, setMembers] = useState<OrgMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [noOrg, setNoOrg] = useState(false);
  const [codeCopied, setCodeCopied] = useState(false);
  const [linkCopied, setLinkCopied] = useState(false);

  // MCP 连接管理状态
  const [mcpConns, setMcpConns] = useState<McpConnection[]>([]);
  const [mcpLoading, setMcpLoading] = useState(false);
  const [showAddForm, setShowAddForm] = useState(false);
  const [form, setForm] = useState<McpConnection>(EMPTY_FORM);
  const [adding, setAdding] = useState(false);
  const [deletingName, setDeletingName] = useState<string | null>(null);
  const [mcpError, setMcpError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([apiGetMyOrg(), apiGetOrgMembers()])
      .then(([orgData, membersData]) => {
        setOrg(orgData);
        setMembers(membersData);
      })
      .catch(() => setNoOrg(true))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!org) return;
    setMcpLoading(true);
    apiListMcpConnections()
      .then(setMcpConns)
      .catch(() => {})
      .finally(() => setMcpLoading(false));
  }, [org]);

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

  async function handleAddMcp() {
    if (!form.name.trim() || !form.url.trim()) return;
    setAdding(true);
    setMcpError(null);
    try {
      const updated = await apiAddMcpConnection({
        name: form.name.trim(),
        url: form.url.trim(),
        description: form.description.trim(),
      });
      setMcpConns(updated);
      setForm(EMPTY_FORM);
      setShowAddForm(false);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "添加失败";
      setMcpError(msg);
    } finally {
      setAdding(false);
    }
  }

  async function handleDeleteMcp(name: string) {
    setDeletingName(name);
    try {
      const updated = await apiDeleteMcpConnection(name);
      setMcpConns(updated);
    } catch {
      // 静默失败，刷新列表
    } finally {
      setDeletingName(null);
    }
  }

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
                  <span className="text-muted-foreground font-normal text-sm">
                    ({mcpConns.length})
                  </span>
                )}
              </CardTitle>
              <Button
                size="sm"
                variant="outline"
                className="gap-1.5 text-xs"
                onClick={() => {
                  setShowAddForm((v) => !v);
                  setMcpError(null);
                  setForm(EMPTY_FORM);
                }}
              >
                <Plus size={13} />
                添加连接
              </Button>
            </div>
          </CardHeader>

          <CardContent className="space-y-3">
            {/* 添加表单 */}
            {showAddForm && (
              <div className="rounded-lg border bg-muted/30 p-4 space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1">
                    <Label className="text-xs">名称 *</Label>
                    <Input
                      placeholder="如 ambulance_dispatch"
                      value={form.name}
                      onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                      className="h-8 text-sm"
                    />
                  </div>
                  <div className="space-y-1">
                    <Label className="text-xs">URL *</Label>
                    <Input
                      placeholder="http://localhost:8102/mcp"
                      value={form.url}
                      onChange={(e) => setForm((f) => ({ ...f, url: e.target.value }))}
                      className="h-8 text-sm"
                    />
                  </div>
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">描述</Label>
                  <Input
                    placeholder="救护车调度 MCP Server"
                    value={form.description}
                    onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                    className="h-8 text-sm"
                  />
                </div>
                {mcpError && (
                  <p className="text-xs text-destructive">{mcpError}</p>
                )}
                <div className="flex gap-2 justify-end">
                  <Button
                    size="sm"
                    variant="ghost"
                    className="text-xs"
                    onClick={() => { setShowAddForm(false); setMcpError(null); }}
                  >
                    取消
                  </Button>
                  <Button
                    size="sm"
                    className="text-xs gap-1.5"
                    disabled={adding || !form.name.trim() || !form.url.trim()}
                    onClick={handleAddMcp}
                  >
                    {adding && <Loader2 size={12} className="animate-spin" />}
                    确认添加
                  </Button>
                </div>
              </div>
            )}

            {/* 连接列表 */}
            {mcpLoading ? (
              <p className="text-sm text-muted-foreground py-1">加载中…</p>
            ) : mcpConns.length === 0 && !showAddForm ? (
              <p className="text-sm text-muted-foreground py-1">
                暂无 MCP 连接。添加后 Agent 将在回答问题时调用对应工具。
              </p>
            ) : (
              <ul className="divide-y border rounded-lg overflow-hidden">
                {mcpConns.map((conn) => (
                  <li key={conn.name} className="flex items-start gap-3 px-4 py-3 bg-background">
                    <Plug size={14} className="text-muted-foreground mt-0.5 shrink-0" />
                    <div className="flex-1 min-w-0 space-y-0.5">
                      <p className="text-sm font-medium truncate">{conn.name}</p>
                      <p className="text-xs text-muted-foreground font-mono truncate" title={conn.url}>
                        {conn.url}
                      </p>
                      {conn.description && (
                        <p className="text-xs text-muted-foreground">{conn.description}</p>
                      )}
                    </div>
                    <Button
                      size="icon"
                      variant="ghost"
                      className="h-7 w-7 shrink-0 text-muted-foreground hover:text-destructive"
                      disabled={deletingName === conn.name}
                      onClick={() => handleDeleteMcp(conn.name)}
                    >
                      {deletingName === conn.name ? (
                        <Loader2 size={13} className="animate-spin" />
                      ) : (
                        <Trash2 size={13} />
                      )}
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

      </div>
    </div>
  );
}
