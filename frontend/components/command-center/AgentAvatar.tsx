"use client";

import { CC, agentColor } from "./tokens";

interface AgentAvatarProps {
  code: string;
  status?: "idle" | "running" | "done" | "error";
  size?: "sm" | "md" | "lg";
}

const SIZE_MAP = { sm: 18, md: 24, lg: 32 };
const FONT_MAP = { sm: 7, md: 9, lg: 11 };
const PIP_MAP  = { sm: 5, md: 6, lg: 8 };

export function AgentAvatar({ code, status = "idle", size = "md" }: AgentAvatarProps) {
  const px    = SIZE_MAP[size];
  const font  = FONT_MAP[size];
  const pip   = PIP_MAP[size];
  const color = agentColor(code);

  const pipColor =
    status === "done"    ? CC.ok :
    status === "running" ? CC.warn :
    status === "error"   ? CC.err :
    CC.muted2;

  return (
    <div style={{ position: "relative", width: px, height: px, flexShrink: 0 }}>
      {/* Avatar circle */}
      <div
        style={{
          width: px,
          height: px,
          borderRadius: "50%",
          background: `color-mix(in oklab, ${color} 20%, ${CC.panel})`,
          border: `1px solid color-mix(in oklab, ${color} 35%, transparent)`,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: font,
          fontWeight: 700,
          color,
          letterSpacing: "0.03em",
          userSelect: "none",
        }}
      >
        {code.slice(0, 2).toUpperCase()}
      </div>

      {/* Status pip */}
      <div
        style={{
          position: "absolute",
          bottom: 0,
          right: 0,
          width: pip,
          height: pip,
          borderRadius: "50%",
          background: pipColor,
          border: `1px solid ${CC.bg2}`,
          animation: status === "running" ? "pip-pulse 1.2s ease-in-out infinite" : "none",
        }}
      />

      <style>{`
        @keyframes pip-pulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.5; transform: scale(0.8); }
        }
      `}</style>
    </div>
  );
}
