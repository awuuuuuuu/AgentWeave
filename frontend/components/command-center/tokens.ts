/** Command-Center 颜色系统（浅色主题，与 RAGent 普通会话保持一致） */
export const CC = {
  // 背景层次（四级白色阶）
  bg:       "oklch(1 0 0)",            // 纯白（对应 --background）
  bg2:      "oklch(0.985 0 0)",        // 近白（头部/底部条带，对应 --sidebar）
  panel:    "oklch(0.97 0 0)",         // 浅灰卡片背景（对应 --muted/--secondary）
  panel2:   "oklch(0.965 0 0)",        // 次级浅灰表面（用于 color-mix 产生粉彩色）

  // 边框
  line:     "oklch(0.922 0 0)",        // 标准边框（对应 --border）
  lineSoft: "oklch(0.94 0 0)",         // 柔和分隔线

  // 文字层次
  text:     "oklch(0.145 0 0)",        // 近黑主文字（对应 --foreground）
  text2:    "oklch(0.35 0.006 255)",   // 深灰次级文字
  muted:    "oklch(0.556 0 0)",        // 辅助标签（对应 --muted-foreground）
  muted2:   "oklch(0.70 0 0)",         // 装饰性 chrome / 禁用态

  // 状态色（白底可读性 AA 对比度 ≥4.5:1）
  emerg:  "oklch(0.47 0.22 25)",       // 应急红   ~6.8:1
  emerg2: "oklch(0.38 0.22 25)",       // 深应急红
  warn:   "oklch(0.50 0.17 50)",       // 深琥珀   ~6.0:1
  ok:     "oklch(0.50 0.16 150)",      // 深绿     ~6.0:1
  info:   "oklch(0.40 0.14 255)",      // 深蓝 ≈ #185FA5  ~9.2:1
  err:    "oklch(0.44 0.22 22)",       // 深红     ~7.8:1

  // 各部门 Agent 身份色（深色，白底可读，18% color-mix 产生粉彩头像背景）
  agPl: "oklch(0.42 0.18 295)",   // PL 指挥中心 — 紫  ~8.5:1
  agEn: "oklch(0.50 0.09 190)",   // EN 环保局   — 青  ~6.0:1
  agMe: "oklch(0.41 0.12 165)",   // ME 医疗急救 — 绿  ~8.8:1
  agTr: "oklch(0.52 0.14 65)",    // TR 交通管控 — 琥珀 ~5.5:1
  agLg: "oklch(0.40 0.14 255)",   // LG 应急物资 — 蓝  ~9.2:1
  agFf: "oklch(0.46 0.20 32)",    // FF 消防救援 — 橙红 ~7.5:1
} as const;

/** 部门 emoji 图标（全局共用） */
export const DEPT_ICONS: Record<string, string> = {
  PL: "🎯", EN: "🌿", ME: "🏥", TR: "🚦", LG: "📦", FF: "🚒",
};

/** 部门显示名称（全局共用） */
export const DEPT_NAMES: Record<string, string> = {
  PL: "指挥中心", EN: "环保局", ME: "医疗急救", TR: "交通管控", LG: "应急物资", FF: "消防救援",
};

/** Agent 代码 → 颜色 */
export function agentColor(code: string): string {
  const map: Record<string, string> = {
    PL: CC.agPl, EN: CC.agEn, ME: CC.agMe,
    TR: CC.agTr, LG: CC.agLg, FF: CC.agFf,
  };
  return map[code.toUpperCase()] ?? CC.muted;
}
