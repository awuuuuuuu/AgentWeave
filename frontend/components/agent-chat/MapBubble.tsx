"use client";

import { useEffect, useRef } from "react";
import { IconMap, IconChartBar, IconRoute, IconMapPin } from "@tabler/icons-react";
import type { AgentBubble } from "@/lib/agent-api";

interface MapBubbleProps {
  bubble: AgentBubble;
}

export function MapBubble({ bubble }: MapBubbleProps) {
  const mapData = bubble.mapData!;
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<unknown>(null);

  useEffect(() => {
    const key = process.env.NEXT_PUBLIC_AMAP_KEY;
    if (!key || !containerRef.current) return;

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let A: any = null;

    import("@amap/amap-jsapi-loader")
      .then((mod) =>
        mod.default.load({ key, version: "2.0", plugins: [] })
      )
      .then((amap: unknown) => {
        A = amap;
        if (!containerRef.current) return;

        // 初始 center/zoom 只是占位，setFitView 会在加载后覆盖
        const map = new A.Map(containerRef.current, {
          zoom: mapData.zoom ?? 13,
          center: mapData.center,
          mapStyle: "amap://styles/dark",
          zoomEnable: true,
          dragEnable: true,
        });
        mapRef.current = map;

        const overlays: unknown[] = [];

        // ── 标记点 ──────────────────────────────────────────────────────────
        for (const m of mapData.markers ?? []) {
          const marker = new A.Marker({
            position: m.position,
            content: `<div style="
              display:flex;align-items:center;justify-content:center;
              width:32px;height:32px;border-radius:50%;
              background:#1e1b4b;border:2px solid #7F77DD;
              font-size:16px;box-shadow:0 0 10px rgba(127,119,221,0.7);
              cursor:pointer;">
              ${m.icon ?? "📍"}
            </div>`,
            offset: new A.Pixel(-16, -16),
          });
          marker.setMap(map);
          overlays.push(marker);

          if (m.label) {
            const info = new A.InfoWindow({
              content: `<div style="padding:6px 10px;font-size:12px;color:#1f2937;">${m.label}</div>`,
              offset: new A.Pixel(0, -36),
            });
            marker.on("click", () => info.open(map, m.position));
          }
        }

        // ── 路线折线 ────────────────────────────────────────────────────────
        if (mapData.route?.polyline?.length) {
          const polyline = new A.Polyline({
            path: mapData.route.polyline,
            strokeColor: "#7F77DD",
            strokeWeight: 6,
            strokeOpacity: 0.95,
            lineJoin: "round",
            lineCap: "round",
            zIndex: 100,
          });
          polyline.setMap(map);
          overlays.push(polyline);
        }

        // ── 有覆盖物时自动适配视野 ───────────────────────────────────────────
        if (overlays.length > 0) {
          // 等地图瓦片完成后再 fitView，避免空白时适配导致偏移
          map.on("complete", () => {
            map.setFitView(overlays, false, [60, 80, 60, 80], 16);
          });
          // 兜底：complete 事件偶尔不触发时用延迟保底
          setTimeout(() => {
            if (mapRef.current) {
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              (mapRef.current as any).setFitView(overlays, false, [60, 80, 60, 80], 16);
            }
          }, 800);
        }
      })
      .catch(() => {});

    return () => {
      if (mapRef.current) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        (mapRef.current as any).destroy();
        mapRef.current = null;
      }
    };
  // 仅挂载时初始化一次
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const route = mapData.route;
  const distKm = route?.distance_m != null ? (route.distance_m / 1000).toFixed(1) : null;
  const mins   = route?.duration_seconds != null ? Math.round(route.duration_seconds / 60) : null;

  return (
    <div
      style={{
        borderRadius: 10,
        border: "0.5px solid var(--color-border-secondary)",
        background: "var(--color-background-primary)",
        overflow: "hidden",
        boxShadow: "0 1px 4px rgba(0,0,0,0.06)",
      }}
    >
      {/* 头部：analyst 风格 */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "8px 12px",
          borderBottom: "0.5px solid var(--color-border-secondary)",
          background: "var(--color-background-secondary)",
        }}
      >
        <div
          style={{
            width: 22,
            height: 22,
            borderRadius: 6,
            background: "#EEEDFE",
            color: "#3C3489",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
          }}
        >
          <IconChartBar size={12} />
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <span style={{ fontSize: 12, fontWeight: 600, color: "#534AB7" }}>Analyst</span>
          <span style={{ width: 5, height: 5, borderRadius: "50%", background: "#7F77DD", display: "inline-block" }} />
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 4, marginLeft: 2 }}>
          <IconMap size={12} style={{ color: "#7F77DD" }} />
          <span style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>
            {mapData.title ?? "地图"}
          </span>
        </div>

        {route && distKm && (
          <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 10, fontSize: 11, color: "var(--color-text-tertiary)" }}>
            <span style={{ display: "flex", alignItems: "center", gap: 3 }}>
              <IconRoute size={11} />{distKm} km
            </span>
            <span style={{ display: "flex", alignItems: "center", gap: 3 }}>
              <IconMapPin size={11} />约 {mins} 分钟
            </span>
          </div>
        )}
      </div>

      {/* 地图容器：高度加大，路线自动 fitView */}
      <div ref={containerRef} style={{ height: 400, width: "100%" }} />
    </div>
  );
}
