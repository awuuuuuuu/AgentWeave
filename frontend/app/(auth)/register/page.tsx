"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiRegister, tokenStorage } from "@/lib/api";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

interface OrgPreview {
  name: string;
  type: "department" | "command" | string;
}

const ORG_TYPE_LABEL: Record<string, string> = {
  department: "部门",
  command: "指挥中心",
};

export default function RegisterPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const from = searchParams.get("from") ?? "/knowledge";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [inviteCode, setInviteCode] = useState(searchParams.get("code") ?? "");
  const [orgPreview, setOrgPreview] = useState<OrgPreview | null>(null);
  const [codeChecking, setCodeChecking] = useState(false);
  const [codeError, setCodeError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // 邀请码实时预检（防抖 500ms）
  useEffect(() => {
    const code = inviteCode.trim().toUpperCase();
    setOrgPreview(null);
    setCodeError(null);
    if (!code) return;

    setCodeChecking(true);
    const timer = setTimeout(async () => {
      try {
        const res = await fetch(`${API_BASE}/orgs/validate?code=${encodeURIComponent(code)}`);
        if (res.ok) {
          const data: OrgPreview = await res.json();
          setOrgPreview(data);
          setCodeError(null);
        } else {
          setOrgPreview(null);
          setCodeError("邀请码无效");
        }
      } catch {
        setCodeError("验证失败，请检查网络");
      } finally {
        setCodeChecking(false);
      }
    }, 500);

    return () => clearTimeout(timer);
  }, [inviteCode]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    if (password !== confirm) {
      setError("两次输入的密码不一致");
      return;
    }
    if (!/[A-Z]/.test(password)) {
      setError("密码必须包含至少一个大写字母");
      return;
    }
    if (!/\d/.test(password)) {
      setError("密码必须包含至少一个数字");
      return;
    }
    if (!inviteCode.trim()) {
      setError("请填写部门邀请码");
      return;
    }
    if (codeChecking) {
      setError("正在验证邀请码，请稍候");
      return;
    }
    if (codeError) {
      setError("邀请码无效，请核对后重试");
      return;
    }

    setLoading(true);
    try {
      const tokens = await apiRegister(
        email,
        password,
        inviteCode.trim().toUpperCase()
      );
      tokenStorage.set(tokens.access_token, tokens.refresh_token);
      router.push(from);
    } catch (err) {
      setError(err instanceof Error ? err.message : "注册失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card className="w-full max-w-sm">
      <CardHeader>
        <CardTitle>创建账号</CardTitle>
        <CardDescription>注册后即可使用知识库和 Agent</CardDescription>
      </CardHeader>
      <form onSubmit={handleSubmit}>
        <CardContent className="space-y-4">
          {error && (
            <p className="text-sm text-destructive bg-destructive/10 rounded-md px-3 py-2">
              {error}
            </p>
          )}

          <div className="space-y-2">
            <Label htmlFor="email">邮箱</Label>
            <Input
              id="email"
              type="email"
              placeholder="you@example.com"
              value={email}
              onChange={(e) => { setEmail(e.target.value); if (error) setError(null); }}
              required
              autoComplete="email"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="password">密码</Label>
            <Input
              id="password"
              type="password"
              placeholder="至少 8 位，含大写字母和数字"
              value={password}
              onChange={(e) => { setPassword(e.target.value); if (error) setError(null); }}
              required
              minLength={8}
              autoComplete="new-password"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="confirm">确认密码</Label>
            <Input
              id="confirm"
              type="password"
              placeholder="再次输入密码"
              value={confirm}
              onChange={(e) => { setConfirm(e.target.value); if (error) setError(null); }}
              required
              autoComplete="new-password"
            />
          </div>

          {/* 邀请码（必填） */}
          <div className="space-y-2">
            <Label htmlFor="invite_code">部门邀请码</Label>
            <Input
              id="invite_code"
              type="text"
              placeholder="A3KX7DQF"
              value={inviteCode}
              onChange={(e) => {
                setInviteCode(e.target.value.toUpperCase());
                setError(null);
              }}
              className={
                inviteCode
                  ? orgPreview
                    ? "border-green-500"
                    : codeError
                    ? "border-destructive"
                    : ""
                  : ""
              }
              maxLength={32}
              autoComplete="off"
              required
            />
            {/* 邀请码预览区 */}
            {inviteCode.trim() && (
              <div className="text-xs">
                {codeChecking && (
                  <span className="text-muted-foreground">验证中…</span>
                )}
                {!codeChecking && orgPreview && (
                  <span className="text-green-600 dark:text-green-400">
                    ✓ 加入：{orgPreview.name}
                    <span className="ml-1 text-muted-foreground">
                      ({ORG_TYPE_LABEL[orgPreview.type] ?? orgPreview.type})
                    </span>
                  </span>
                )}
                {!codeChecking && codeError && (
                  <span className="text-destructive">{codeError}</span>
                )}
              </div>
            )}
          </div>
        </CardContent>
        <CardFooter className="flex flex-col gap-3">
          <Button type="submit" className="w-full" disabled={loading}>
            {loading ? "注册中…" : "注册"}
          </Button>
          <p className="text-sm text-muted-foreground text-center">
            已有账号？{" "}
            <Link href="/login" className="underline underline-offset-4 hover:text-foreground">
              登录
            </Link>
          </p>
        </CardFooter>
      </form>
    </Card>
  );
}
