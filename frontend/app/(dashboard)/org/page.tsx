"use client";

import { useEffect, useState } from "react";
import { Copy, Check, Building2, Users } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { apiGetMyOrg, apiGetOrgMembers, type OrgInfo, type OrgMember } from "@/lib/api";

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

export default function OrgPage() {
  const [org, setOrg] = useState<OrgInfo | null>(null);
  const [members, setMembers] = useState<OrgMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [noOrg, setNoOrg] = useState(false);
  const [codeCopied, setCodeCopied] = useState(false);
  const [linkCopied, setLinkCopied] = useState(false);

  useEffect(() => {
    Promise.all([apiGetMyOrg(), apiGetOrgMembers()])
      .then(([orgData, membersData]) => {
        setOrg(orgData);
        setMembers(membersData);
      })
      .catch(() => setNoOrg(true))
      .finally(() => setLoading(false));
  }, []);

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

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center text-muted-foreground text-sm">
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
    <div className="flex-1 overflow-auto p-8">
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

      </div>
    </div>
  );
}
