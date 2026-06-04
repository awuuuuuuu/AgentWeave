"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { BookOpen, Bot, Building2, MessageSquare, LogOut, User, Siren } from "lucide-react";
import { tokenStorage, apiGetMyOrg } from "@/lib/api";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export default function NavHeader() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const router = useRouter();
  const [isCommandOrg, setIsCommandOrg] = useState(false);

  useEffect(() => {
    apiGetMyOrg()
      .then((org) => setIsCommandOrg(org.type === "command"))
      .catch(() => {});
  }, []);

  const isKbDetail = /^\/knowledge\/[^/]+/.test(pathname);
  const kbName = searchParams.get("name");

  function handleLogout() {
    tokenStorage.clear();
    router.push("/login");
  }

  const navItems = isCommandOrg
    ? [
        { href: "/agent", label: "指挥台", icon: Siren },
        { href: "/org", label: "部门", icon: Building2 },
      ]
    : [
        { href: "/knowledge", label: "知识库", icon: BookOpen },
        { href: "/chat", label: "对话", icon: MessageSquare },
        { href: "/agent", label: "智能体", icon: Bot },
        { href: "/org", label: "部门", icon: Building2 },
      ];

  return (
    <header className="h-14 border-b flex items-center px-6 shrink-0 bg-background z-10">
      {/* Logo */}
      <Link href={isCommandOrg ? "/agent" : "/knowledge"} className="flex items-center gap-2 mr-8">
        <div className="w-7 h-7 rounded-lg bg-primary flex items-center justify-center">
          <span className="text-primary-foreground text-xs font-bold">R</span>
        </div>
        <span className="font-semibold text-sm">AgentWeave</span>
      </Link>

      {/* Nav */}
      <nav className="flex items-center gap-1">
        {navItems.map(({ href, label, icon: Icon }) => {
          const isKB = href === "/knowledge";
          const active = pathname === href || pathname.startsWith(href + "/");

          // 知识库详情：合并成一个 pill
          if (isKB && isKbDetail && kbName) {
            return (
              <div
                key={href}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-primary/10 text-primary text-sm font-medium"
              >
                <Icon size={15} />
                <Link href="/knowledge" className="hover:underline underline-offset-2">
                  {label}
                </Link>
                <span className="text-primary/40 mx-0.5 select-none">/</span>
                <span className="max-w-[180px] truncate">{kbName}</span>
              </div>
            );
          }

          return (
            <Link
              key={href}
              href={href}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md transition-colors ${
                active
                  ? "bg-accent text-accent-foreground font-medium"
                  : "text-muted-foreground hover:text-foreground hover:bg-accent/60"
              }`}
            >
              <Icon size={15} />
              {label}
            </Link>
          );
        })}
      </nav>

      {/* User menu */}
      <div className="ml-auto">
        <DropdownMenu>
          <DropdownMenuTrigger className="w-8 h-8 rounded-full bg-muted flex items-center justify-center hover:bg-accent transition-colors outline-none" aria-label="用户菜单">
            <User size={15} className="text-muted-foreground" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-36">
            <DropdownMenuItem onClick={handleLogout} className="gap-2 text-muted-foreground">
              <LogOut size={14} />
              退出登录
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
