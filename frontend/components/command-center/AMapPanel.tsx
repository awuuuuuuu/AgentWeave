"use client";

import { useEffect, useRef, useState } from "react";
import { CC } from "./tokens";
import type { MapLayer } from "./types";
import type { MapPayload } from "@/lib/agent-api";

interface AMapPanelProps {
  incidentCenter?: [number, number];   // [lng, lat]，默认港城大道388号
  incidentLabel?: string;
  mapEvents?: MapPayload[];
  layers?: MapLayer[];
}

const DEFAULT_CENTER: [number, number] = [117.7148, 39.1290];
const DEFAULT_LABEL = "港城大道 388 号";

const DEFAULT_LAYERS: MapLayer[] = [
  { id: "r1", name: "R₁ 警戒圈", color: CC.emerg, enabled: true },
  { id: "r2", name: "R₂ 健康圈", color: CC.warn,  enabled: true },
  { id: "r3", name: "R₃ 缓冲圈", color: CC.info,  enabled: true },
  { id: "wind", name: "风向",     color: CC.muted, enabled: true },
  { id: "units", name: "单元点位", color: CC.ok,   enabled: true },
];

export function AMapPanel({
  incidentCenter = DEFAULT_CENTER,
  incidentLabel = DEFAULT_LABEL,
  mapEvents = [],
  layers,
}: AMapPanelProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const mapRef = useRef<any>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const amapRef = useRef<any>(null);          // AMap module reference
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const mapObjectsRef = useRef<any[]>([]);    // rendered Markers / Polylines
  const [layerState, setLayerState] = useState<MapLayer[]>(layers ?? DEFAULT_LAYERS);
  const [showLayers, setShowLayers] = useState(false);

  useEffect(() => {
    const key = process.env.NEXT_PUBLIC_AMAP_KEY;
    if (!key || !containerRef.current) return;

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let A: any = null;

    import("@amap/amap-jsapi-loader")
      .then((mod) => mod.default.load({ key, version: "2.0", plugins: ["AMap.Circle"] }))
      .then((amap) => {
        A = amap;
        if (!containerRef.current) return;

        const map = new A.Map(containerRef.current, {
          zoom: 13,
          center: incidentCenter,
          mapStyle: "amap://styles/dark",
          zoomEnable: true,
          dragEnable: true,
        });
        mapRef.current = map;
        amapRef.current = A; // expose module to mapEvents effect

        // 事件标记（橙红色）
        const pin = new A.Marker({
          position: incidentCenter,
          content: `<div style="
            display:flex;align-items:center;justify-content:center;
            width:28px;height:28px;border-radius:50%;
            background:color-mix(in oklab,${CC.emerg} 25%,#0a0e15);
            border:2px solid ${CC.emerg};
            box-shadow:0 0 12px color-mix(in oklab,${CC.emerg} 60%,transparent);
            font-size:14px;">
            ⚠
          </div>`,
          offset: new A.Pixel(-14, -14),
        });
        pin.setMap(map);

        // 警戒圈 × 3
        const rings = [
          { radius: 600,  strokeColor: CC.emerg, strokeStyle: "solid",  strokeOpacity: 0.7 },
          { radius: 1200, strokeColor: CC.warn,  strokeStyle: "dashed", strokeOpacity: 0.6 },
          { radius: 2000, strokeColor: CC.info,  strokeStyle: "dashed", strokeOpacity: 0.45 },
        ];
        for (const r of rings) {
          new A.Circle({
            center: incidentCenter,
            radius: r.radius,
            strokeColor: r.strokeColor,
            strokeWeight: 2,
            strokeOpacity: r.strokeOpacity,
            strokeStyle: r.strokeStyle,
            fillOpacity: 0,
          }).setMap(map);
        }
      })
      .catch(() => {});

    return () => {
      if (mapRef.current) {
        mapRef.current.destroy();
        mapRef.current = null;
      }
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Render new map events (markers + routes) as they arrive
  useEffect(() => {
    if (!mapRef.current || !mapEvents || mapEvents.length === 0) return;
    const AMap = amapRef.current;
    if (!AMap) return;

    const event = mapEvents[mapEvents.length - 1]; // process latest event only

    if (event.center) mapRef.current.setCenter(event.center);
    if (event.zoom)   mapRef.current.setZoom(event.zoom);

    event.markers?.forEach((m) => {
      const marker = new AMap.Marker({
        position: m.position,
        content: `<div style="width:10px;height:10px;border-radius:50%;background:${CC.info};border:2px solid #fff;box-shadow:0 0 6px ${CC.info}"></div>`,
        offset: new AMap.Pixel(-5, -5),
        title: m.label ?? "",
      });
      marker.setMap(mapRef.current);
      mapObjectsRef.current.push(marker);
    });

    if (event.route?.polyline?.length) {
      const polyline = new AMap.Polyline({
        path: event.route.polyline,
        strokeColor: CC.ok,
        strokeWeight: 3,
        strokeOpacity: 0.85,
        lineJoin: "round",
      });
      polyline.setMap(mapRef.current);
      mapObjectsRef.current.push(polyline);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapEvents?.length]);

  function toggleLayer(id: string) {
    setLayerState((prev) =>
      prev.map((l) => (l.id === id ? { ...l, enabled: !l.enabled } : l))
    );
  }

  return (
    <div style={{ position: "relative", width: "100%", height: "100%", background: CC.bg, overflow: "hidden" }}>
      {/* Amap 容器 */}
      <div ref={containerRef} style={{ width: "100%", height: "100%" }} />

      {/* 顶部 pill bar */}
      <div
        style={{
          position: "absolute",
          top: 10,
          left: 10,
          right: 50,
          display: "flex",
          alignItems: "center",
          gap: 8,
          background: "rgba(10,14,21,0.82)",
          backdropFilter: "blur(8px)",
          border: `1px solid ${CC.line}`,
          borderRadius: 8,
          padding: "6px 12px",
          zIndex: 10,
          pointerEvents: "none",
        }}
      >
        <span style={{ fontSize: 10, color: CC.emerg }}>📍</span>
        <span style={{ fontSize: 11, color: "rgba(255,255,255,0.85)", flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {incidentLabel}
        </span>
        <span
          style={{
            fontSize: 9,
            fontWeight: 700,
            letterSpacing: "0.08em",
            color: CC.emerg,
            background: `color-mix(in oklab, ${CC.emerg} 15%, transparent)`,
            border: `1px solid color-mix(in oklab, ${CC.emerg} 30%, transparent)`,
            padding: "1px 6px",
            borderRadius: 4,
          }}
        >
          实时
        </span>
      </div>

      {/* 左侧 legend */}
      <div
        style={{
          position: "absolute",
          top: 52,
          left: 10,
          background: "rgba(10,14,21,0.85)",
          backdropFilter: "blur(8px)",
          border: `1px solid ${CC.line}`,
          borderRadius: 8,
          padding: "8px 10px",
          zIndex: 10,
          minWidth: 130,
        }}
      >
        <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: "0.08em", color: "rgba(255,255,255,0.45)", marginBottom: 6 }}>
          警戒分级
        </div>
        {[
          { label: "R₁ 立即警戒", dist: "600 m", color: CC.emerg },
          { label: "R₂ 健康关注", dist: "1.2 km", color: CC.warn },
          { label: "R₃ 预案缓冲", dist: "2.0 km", color: CC.info },
        ].map((item) => (
          <div key={item.label} style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
            <div style={{
              width: 8, height: 8, borderRadius: "50%",
              background: item.color, flexShrink: 0,
            }} />
            <span style={{ fontSize: 10, color: "rgba(255,255,255,0.82)" }}>{item.label}</span>
            <span style={{ fontSize: 9, color: "rgba(255,255,255,0.4)", marginLeft: "auto" }}>{item.dist}</span>
          </div>
        ))}
      </div>

      {/* 右侧地图控件 */}
      <div
        style={{
          position: "absolute",
          top: 10,
          right: 10,
          display: "flex",
          flexDirection: "column",
          gap: 4,
          zIndex: 10,
        }}
      >
        {["+", "−", "⊙"].map((label) => (
          <button
            key={label}
            onClick={() => {
              if (!mapRef.current) return;
              if (label === "+") mapRef.current.zoomIn();
              else if (label === "−") mapRef.current.zoomOut();
              else mapRef.current.setCenter(incidentCenter);
            }}
            style={{
              width: 28,
              height: 28,
              background: "rgba(10,14,21,0.85)",
              border: `1px solid ${CC.line}`,
              borderRadius: 6,
              color: "rgba(255,255,255,0.75)",
              fontSize: label === "⊙" ? 12 : 16,
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            {label}
          </button>
        ))}
        <button
          onClick={() => setShowLayers((p) => !p)}
          style={{
            marginTop: 4,
            width: 28,
            height: 28,
            background: showLayers
              ? `color-mix(in oklab, ${CC.info} 20%, rgba(10,14,21,0.85))`
              : "rgba(10,14,21,0.85)",
            border: `1px solid ${showLayers ? CC.info : CC.line}`,
            borderRadius: 6,
            color: showLayers ? CC.info : "rgba(255,255,255,0.75)",
            fontSize: 11,
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
          title="图层"
        >
          ⊞
        </button>
      </div>

      {/* 图层面板（hover 展开） */}
      {showLayers && (
        <div
          style={{
            position: "absolute",
            top: 148,
            right: 10,
            background: "rgba(10,14,21,0.92)",
            backdropFilter: "blur(8px)",
            border: `1px solid ${CC.line}`,
            borderRadius: 8,
            padding: "8px 10px",
            zIndex: 10,
            minWidth: 110,
          }}
        >
          <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: "0.08em", color: "rgba(255,255,255,0.45)", marginBottom: 6 }}>
            图层
          </div>
          {layerState.map((layer) => (
            <div
              key={layer.id}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                marginBottom: 4,
                cursor: "pointer",
              }}
              onClick={() => toggleLayer(layer.id)}
            >
              <div style={{
                width: 8, height: 8, borderRadius: 2,
                background: layer.enabled ? layer.color : "rgba(255,255,255,0.2)",
                flexShrink: 0,
                transition: "background 0.15s",
              }} />
              <span style={{ fontSize: 10, color: layer.enabled ? "rgba(255,255,255,0.82)" : "rgba(255,255,255,0.35)" }}>{layer.name}</span>
            </div>
          ))}
        </div>
      )}

      {/* 左下 phase chip */}
      <div
        style={{
          position: "absolute",
          bottom: 24,
          left: 10,
          display: "flex",
          alignItems: "center",
          gap: 5,
          background: "rgba(10,14,21,0.82)",
          border: `1px solid ${CC.line}`,
          borderRadius: 6,
          padding: "4px 8px",
          zIndex: 10,
        }}
      >
        <div style={{
          width: 6, height: 6, borderRadius: "50%",
          background: CC.info,
          animation: "phase-pulse 2s ease-in-out infinite",
        }} />
        <span style={{ fontSize: 9, color: "rgba(255,255,255,0.7)", fontWeight: 600, letterSpacing: "0.06em" }}>
          阶段 A · 态势底图
        </span>
      </div>

      {/* 水印 */}
      <div
        style={{
          position: "absolute",
          bottom: 8,
          right: 10,
          fontSize: 9,
          color: "rgba(255,255,255,0.25)",
          zIndex: 10,
          pointerEvents: "none",
        }}
      >
        © 暗色态势底图 · 演示数据
      </div>

      <style>{`
        @keyframes phase-pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
      `}</style>
    </div>
  );
}
