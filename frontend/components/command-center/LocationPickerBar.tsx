"use client";

import { useState } from "react";
import { CC } from "./tokens";
import type { LocationCandidate } from "./types";

export type { LocationCandidate };

interface LocationPickerBarProps {
  candidates: LocationCandidate[];
  query: string;
  onSelect: (location: LocationCandidate) => void;
  onRetry: () => void;
}

export function LocationPickerBar({
  candidates,
  query,
  onSelect,
  onRetry,
}: LocationPickerBarProps) {
  const [hovered, setHovered] = useState<number | null>(null);

  return (
    <div
      style={{
        zIndex: 4,
        display: "flex",
        flexDirection: "column",
        gap: 8,
        padding: "10px 16px",
        background: `linear-gradient(90deg,
          color-mix(in oklab, ${CC.info} 14%, ${CC.panel}) 0%,
          color-mix(in oklab, ${CC.info} 6%, ${CC.panel}) 100%)`,
        borderBottom: `1px solid color-mix(in oklab, ${CC.info} 38%, transparent)`,
        flexShrink: 0,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: CC.info }}>
          📍 请确认事发地点
        </span>
        <span style={{ fontSize: 11, color: CC.muted }}>
          搜索「{query}」· {candidates.length} 个结果
        </span>
        <button
          onClick={onRetry}
          style={{
            marginLeft: "auto",
            fontSize: 11,
            cursor: "pointer",
            padding: "2px 8px",
            borderRadius: 4,
            border: `1px solid ${CC.line}`,
            background: "transparent",
            color: CC.muted,
          }}
        >
          重新输入地点
        </button>
      </div>

      {candidates.length === 0 ? (
        <div style={{ fontSize: 11, color: CC.muted }}>
          未找到匹配地点，请点击「重新输入地点」提供更详细描述。
        </div>
      ) : (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {candidates.map((c, i) => (
            <button
              key={i}
              onClick={() => onSelect(c)}
              onMouseEnter={() => setHovered(i)}
              onMouseLeave={() => setHovered(null)}
              title={c.address || c.name}
              style={{
                padding: "4px 12px",
                borderRadius: 5,
                fontSize: 11,
                fontWeight: 500,
                cursor: "pointer",
                border: `1px solid color-mix(in oklab, ${CC.info} ${hovered === i ? "55%" : "30%"}, transparent)`,
                background: `color-mix(in oklab, ${CC.info} ${hovered === i ? "18%" : "9%"}, transparent)`,
                color: CC.info,
                maxWidth: 200,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
                transition: "all 0.12s",
              }}
            >
              {c.name}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
