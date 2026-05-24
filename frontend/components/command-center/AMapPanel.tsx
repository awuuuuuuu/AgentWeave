"use client";

import { useEffect, useRef, useState } from "react";
import { CC } from "./tokens";
import type { MapLayer } from "./types";
import type { MapPayload } from "@/lib/agent-api";

interface AMapPanelProps {
  incidentCenter?: [number, number] | null;  // null = 事故位置未知，不渲染 ⚠ 标记
  incidentLabel?: string;
  mapEvents?: MapPayload[];
  layers?: MapLayer[];
  activeStepId?: string | null;                       // A5：当前高亮的执行步骤
  onStepSelect?: (stepId: string | null) => void;     // A5：点击地图对象回传步骤
}

// 地图初始中心：在事故位置未知前显示滨海新区全局视野
const MAP_INIT_CENTER: [number, number] = [117.7148, 39.1000];
const MAP_INIT_ZOOM = 11;
const DEFAULT_LABEL = "港城大道 388 号";

// B2：图层默认列表（与后端 layer 字段对齐）
const DEFAULT_LAYERS: MapLayer[] = [
  { id: "plume",           name: "扩散圈",     color: "#ef4444", enabled: true },
  { id: "resources",       name: "资源点位",   color: "#22c55e", enabled: true },
  { id: "signals",         name: "路口信号",   color: "#3b82f6", enabled: true },
  { id: "routes",          name: "派遣路线",   color: "#a855f7", enabled: true },
  { id: "sensors",         name: "传感器告警", color: "#facc15", enabled: true },
  { id: "warehouse",       name: "仓库",       color: "#f97316", enabled: true },
  { id: "cordon",          name: "警戒圈",     color: "#dc2626", enabled: true },
  { id: "incident_source", name: "事故源点",   color: "#7c2d12", enabled: true },
];

const DEPT_COLORS: Record<string, string> = {
  env_agency:         "#22c55e",
  medical_ems:        "#ef4444",
  traffic_control:    "#3b82f6",
  emergency_supplies: "#f97316",
  enterprise_safety:  "#a855f7",
};

// A4：路线动画车头图标（按部门）
const ROUTE_HEAD_ICON: Record<string, string> = {
  medical_ems:        "🚑",
  traffic_control:    "🚓",
  emergency_supplies: "📦",
};

// 路线循环动画速度倍率（>1 = 更慢）；物资车队整体慢一倍
const DEPT_SPEED_MULT: Record<string, number> = {
  emergency_supplies: 2.0,
  traffic_control:    1.3,
  medical_ems:        1.0,
};

// B4：按部门聚焦的部门列表
const FOCUS_DEPTS: { code: string; dept_code: string; name: string }[] = [
  { code: "EN", dept_code: "env_agency",         name: "环保" },
  { code: "ME", dept_code: "medical_ems",        name: "医疗" },
  { code: "TR", dept_code: "traffic_control",    name: "交通" },
  { code: "LG", dept_code: "emergency_supplies", name: "物资" },
  { code: "SF", dept_code: "enterprise_safety",  name: "安全" },
];

// 渲染对象的统一包装：携带图层 / 步骤 / 部门 元数据，支撑图层 toggle + 步骤联动
interface RenderedObj {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  obj: any;
  layer: string;
  kind: "marker" | "polyline" | "circle" | "arrow" | "head";
  stepId?: string;
  deptCode?: string;
  baseWeight?: number;
  baseOpacity?: number;
}

export function AMapPanel({
  incidentCenter = null,
  incidentLabel = DEFAULT_LABEL,
  mapEvents = [],
  layers,
  activeStepId = null,
  onStepSelect,
}: AMapPanelProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const mapRef = useRef<any>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const amapRef = useRef<any>(null);                  // AMap module reference
  const mapObjectsRef = useRef<RenderedObj[]>([]);    // rendered objects + metadata
  const animRef = useRef<number[]>([]);               // requestAnimationFrame ids（A4 清理用）
  const loopRef = useRef<ReturnType<typeof setInterval>[]>([]); // 路线循环 setInterval ids
  const renderedUpToRef = useRef(0);                  // 已渲染到 mapEvents 的第几个
  const [mapReady, setMapReady] = useState(false);   // 地图实例就绪后置 true，触发补渲早期事件
  const [layerState, setLayerState] = useState<MapLayer[]>(layers ?? DEFAULT_LAYERS);
  const layerStateRef = useRef(layerState);           // 供渲染 effect 读取最新启用状态
  const [showLayers, setShowLayers] = useState(false);
  const [phaseToast, setPhaseToast] = useState<string | null>(null);  // C3
  const prevPhaseRef = useRef<string>("");

  useEffect(() => { layerStateRef.current = layerState; }, [layerState]);

  function isLayerEnabled(id: string): boolean {
    const l = layerStateRef.current.find((x) => x.id === id);
    return l ? l.enabled : true;
  }

  // ── 地图初始化 ──────────────────────────────────────────────────────────────
  useEffect(() => {
    const key = process.env.NEXT_PUBLIC_AMAP_KEY;
    if (!key || !containerRef.current) return;

    import("@amap/amap-jsapi-loader")
      .then((mod) => mod.default.load({ key, version: "2.0", plugins: ["AMap.Circle"] }))
      .then((amap) => {
        if (!containerRef.current) return;
        const map = new amap.Map(containerRef.current, {
          zoom: incidentCenter ? 13 : MAP_INIT_ZOOM,
          center: incidentCenter ?? MAP_INIT_CENTER,
          mapStyle: "amap://styles/dark",
          zoomEnable: true,
          dragEnable: true,
        });
        mapRef.current = map;
        amapRef.current = amap;
        setMapReady(true);  // 触发 mapEvents effect 补渲在地图就绪前已到达的事件

        if (incidentCenter) addIncidentPin(amap, map, incidentCenter);
      })
      .catch(() => {});

    return () => {
      animRef.current.forEach((id) => cancelAnimationFrame(id));
      animRef.current = [];
      loopRef.current.forEach((id) => clearInterval(id));
      loopRef.current = [];
      if (mapRef.current) {
        mapRef.current.destroy();
        mapRef.current = null;
      }
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function addIncidentPin(AMap: any, map: any, center: [number, number]) {
    const pin = new AMap.Marker({
      position: center,
      content: `<div style="
        display:flex;align-items:center;justify-content:center;
        width:38px;height:38px;border-radius:50%;
        background:#fde047;
        border:3px solid #f59e0b;
        color:#1f2937;font-weight:900;font-size:20px;line-height:1;
        box-shadow:0 0 22px rgba(253,224,71,0.95),0 0 44px rgba(253,224,71,0.45);
      ">⚠</div>`,
      offset: new AMap.Pixel(-19, -19),
      zIndex: 100,
    });
    pin.setMap(map);
  }

  // 事故坐标由 ERPG 数据派生后，动态渲染 ⚠ 标记并聚焦
  const incidentPinAddedRef = useRef(false);
  useEffect(() => {
    if (!incidentCenter || !mapRef.current || !amapRef.current) return;
    if (incidentPinAddedRef.current) return;
    incidentPinAddedRef.current = true;
    addIncidentPin(amapRef.current, mapRef.current, incidentCenter);
    mapRef.current.setCenter(incidentCenter);
    mapRef.current.setZoom(13);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [incidentCenter]);

  // ── 增量渲染所有新到达的 map events ─────────────────────────────────────────
  useEffect(() => {
    if (!mapRef.current) return;
    const AMap = amapRef.current;
    if (!AMap) return;
    const map = mapRef.current;

    // mapEvents 清空（切换会话）：移除旧覆盖物、取消动画、重置计数
    if (!mapEvents?.length) {
      animRef.current.forEach((id) => cancelAnimationFrame(id));
      animRef.current = [];
      loopRef.current.forEach((id) => clearInterval(id));
      loopRef.current = [];
      for (const o of mapObjectsRef.current) {
        try { o.obj.setMap(null); } catch { /* ignore */ }
      }
      mapObjectsRef.current = [];
      renderedUpToRef.current = 0;
      incidentPinAddedRef.current = false;
      return;
    }

    const newEvents = mapEvents.slice(renderedUpToRef.current);
    renderedUpToRef.current = mapEvents.length;
    if (!newEvents.length) return;

    // 统一登记：按当前图层启用状态决定是否立即可见
    function track(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      obj: any, layer: string, kind: RenderedObj["kind"],
      meta: { stepId?: string; deptCode?: string; baseWeight?: number; baseOpacity?: number } = {},
    ) {
      const enabled = isLayerEnabled(layer);
      try { obj.setMap(enabled ? map : null); } catch { /* ignore */ }
      mapObjectsRef.current.push({ obj, layer, kind, ...meta });
    }

    let hasCircles = false;

    for (const event of newEvents) {
      const layer = event.layer ?? "resources";
      const stepId = event.step_id;

      // ── markers ──
      event.markers?.forEach((m) => {
        const borderColor = m.color ?? CC.info;
        const marker = new AMap.Marker({
          position: m.position,
          content: `<div style="
            display:flex;align-items:center;justify-content:center;
            width:32px;height:32px;border-radius:50%;
            background:color-mix(in oklab,${borderColor} 25%,#0a0e15);
            border:2.5px solid ${borderColor};
            box-shadow:0 0 10px color-mix(in oklab,${borderColor} 60%,transparent);
            font-size:16px;cursor:pointer;">${m.icon ?? "📍"}</div>`,
          offset: new AMap.Pixel(-16, -16),
        });
        track(marker, layer, "marker", { stepId, deptCode: event.dept_code });

        // C1：信息窗增强（部门 chip + meta + 联动提示）
        if (m.label) {
          const deptColor = DEPT_COLORS[event.dept_code ?? ""] ?? CC.info;
          const deptChip = event.dept_code
            ? `<span style="display:inline-block;padding:1px 6px;border-radius:4px;font-size:10px;margin-bottom:4px;background:${deptColor}22;border:1px solid ${deptColor}55;color:${deptColor};">${event.title ?? event.dept_code}</span><br/>`
            : "";
          const metaLine = m.meta ? `<span style="color:#6b7280">${m.meta}</span>` : "";
          const linkLine = stepId
            ? `<br/><span style="color:#0891b2;font-size:11px;cursor:pointer;">↗ 查看关联步骤</span>`
            : "";
          const info = new AMap.InfoWindow({
            content: `<div style="padding:7px 11px;font-size:12px;color:#1f2937;min-width:120px;">${deptChip}<b>${m.label}</b><br/>${metaLine}${linkLine}</div>`,
            offset: new AMap.Pixel(0, -36),
          });
          marker.on("click", () => {
            info.open(map, m.position);
            if (stepId && onStepSelect) onStepSelect(stepId);
          });
        }
      });

      // ── route（A4 动画）──
      if (event.route?.polyline?.length) {
        const r = event.route;
        const color = r.color ?? DEPT_COLORS[r.dept_code ?? ""] ?? CC.ok;
        const path = r.polyline as [number, number][];

        // 持久的细线（动画完成后保留；A5 点击执行卡时高亮的就是它）
        const backdrop = new AMap.Polyline({
          path, strokeColor: color, strokeWeight: 2.5, strokeOpacity: 0.35,
          lineJoin: "round", lineCap: "round",
        });
        track(backdrop, "routes", "polyline", { stepId, deptCode: r.dept_code, baseWeight: 2.5, baseOpacity: 0.35 });

        // 动画进度线（粗亮，推进完成后移除，只留细线）
        const progress = new AMap.Polyline({
          path: [path[0]], strokeColor: color, strokeWeight: 4.5, strokeOpacity: 0.95,
          lineJoin: "round", lineCap: "round", zIndex: 55,
        });
        track(progress, "routes", "head", { stepId, deptCode: r.dept_code });  // kind=head：不参与 A5 高亮

        // 车头 marker（完成后停在终点）
        const headIcon = ROUTE_HEAD_ICON[r.dept_code ?? ""] ?? "🚗";
        const head = new AMap.Marker({
          position: path[0],
          content: `<div style="font-size:18px;filter:drop-shadow(0 0 4px ${color});">${headIcon}</div>`,
          offset: new AMap.Pixel(-9, -9),
          zIndex: 70,
        });
        track(head, "routes", "head", { stepId, deptCode: r.dept_code });

        // 路线循环动画（按部门减速；持续播放直到会话切换）
        if (path.length > 1) {
          const distM = r.distance_m ?? 0;
          const baseMs = distM ? Math.min(Math.max((distM / 100) * 1000, 3000), 16000) : 6000;
          const speedMult = DEPT_SPEED_MULT[r.dept_code ?? ""] ?? 1.0;
          const durationMs = baseMs * speedMult;
          const loopStart = performance.now();
          const total = path.length;
          const id = setInterval(() => {
            const elapsed = (performance.now() - loopStart) % durationMs;
            const t = elapsed / durationMs;
            const idx = Math.max(1, Math.floor(t * (total - 1)));
            try {
              progress.setPath(path.slice(0, idx + 1));
              head.setPosition(path[idx]);
            } catch { /* ignore */ }
          }, 50);
          loopRef.current.push(id);
        }
      }

      // ── circles（ERPG）──
      if (event.circles?.length) {
        hasCircles = true;
        let circleCenter: [number, number] | null = null;
        event.circles.forEach((c) => {
          const circle = new AMap.Circle({
            center: c.center, radius: c.radius,
            strokeColor: c.color, strokeWeight: 3, strokeOpacity: 0.92,
            strokeStyle: "solid", fillColor: c.color, fillOpacity: 0.15,
          });
          track(circle, event.layer ?? "plume", "circle", { stepId });
          if (!circleCenter) circleCenter = c.center as [number, number];
        });
        if (circleCenter && isLayerEnabled(event.layer ?? "plume")) {
          map.setCenter(circleCenter);
          map.setZoom(15);
        }
      }

    }

    // 渲染完成后缩出全局视野（C2：圆圈静止 1.5s 让用户读图例，再 fitView）
    const fitObjs = mapObjectsRef.current.filter((o) => isLayerEnabled(o.layer)).map((o) => o.obj);
    if (fitObjs.length > 0) {
      const delay = hasCircles ? 1800 : 0;
      setTimeout(() => {
        if (mapRef.current && fitObjs.length > 0) {
          mapRef.current.setFitView(fitObjs, true, [80, 80, 80, 80], 14);
        }
      }, delay);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapEvents?.length, mapReady]);

  // ── A5：activeStepId 改变 → 高亮对应路线，其余淡出 ──────────────────────────
  useEffect(() => {
    for (const o of mapObjectsRef.current) {
      if (o.kind !== "polyline") continue;
      const isActive = activeStepId != null && o.stepId === activeStepId;
      const dim = activeStepId != null && o.stepId !== activeStepId;
      try {
        o.obj.setOptions({
          strokeWeight: isActive ? (o.baseWeight ?? 5) + 3 : (o.baseWeight ?? 5),
          strokeOpacity: dim ? 0.12 : (o.baseOpacity ?? 0.9),
        });
      } catch { /* ignore */ }
    }
    // 高亮时把该步骤的对象拉入视野
    if (activeStepId != null && mapRef.current) {
      const objs = mapObjectsRef.current.filter((o) => o.stepId === activeStepId).map((o) => o.obj);
      if (objs.length) {
        try { mapRef.current.setFitView(objs, true, [100, 100, 100, 100], 15); } catch { /* ignore */ }
      }
    }
  }, [activeStepId]);

  // ── C3：阶段切换 toast ──────────────────────────────────────────────────────
  const phase = (() => {
    if (mapEvents?.some((e) => e.route)) return { label: "阶段 C · 执行推进", color: CC.ok };
    if (mapEvents?.some((e) => (e.markers?.length ?? 0) > 0 || (e.circles?.length ?? 0) > 0))
      return { label: "阶段 B · 研判底图", color: CC.warn };
    return { label: "阶段 A · 态势底图", color: CC.info };
  })();

  useEffect(() => {
    if (prevPhaseRef.current && prevPhaseRef.current !== phase.label) {
      setPhaseToast(`进入${phase.label}`);
      const t = setTimeout(() => setPhaseToast(null), 2200);
      return () => clearTimeout(t);
    }
    prevPhaseRef.current = phase.label;
  }, [phase.label]);

  // ── 图层 toggle（B1：真实控制可见性）──────────────────────────────────────
  function toggleLayer(id: string) {
    // 在 updater 外计算新的启用状态，避免在纯函数 updater 里执行 DOM 副作用
    const nowEnabled = !(layerStateRef.current.find((l) => l.id === id)?.enabled ?? true);
    setLayerState((prev) => prev.map((l) => (l.id === id ? { ...l, enabled: !l.enabled } : l)));
    for (const o of mapObjectsRef.current) {
      if (o.layer !== id) continue;
      try { o.obj.setMap(nowEnabled ? mapRef.current : null); } catch { /* ignore */ }
    }
  }

  // ── B4：聚焦 ────────────────────────────────────────────────────────────────
  function focusIncident() {
    if (!mapRef.current) return;
    if (incidentCenter) {
      mapRef.current.setCenter(incidentCenter);
      mapRef.current.setZoom(14);
    } else {
      const objs = mapObjectsRef.current.filter((o) => isLayerEnabled(o.layer)).map((o) => o.obj);
      if (objs.length) mapRef.current.setFitView(objs, true, [80, 80, 80, 80], 14);
    }
  }

  function focusDept(deptCode: string) {
    if (!mapRef.current) return;
    const objs = mapObjectsRef.current
      .filter((o) => o.deptCode === deptCode && isLayerEnabled(o.layer))
      .map((o) => o.obj);
    if (objs.length) mapRef.current.setFitView(objs, true, [90, 90, 90, 90], 15);
  }

  // 按 label 去重：多次扩散/警戒圈事件累积会产生重复图例项（React key 冲突）
  const erpgItems = Array.from(
    new Map(
      (mapEvents ?? []).flatMap((e) => e.circles ?? []).filter((c) => c.label).map((c) => [c.label, c]),
    ).values(),
  );

  return (
    <div style={{ position: "relative", width: "100%", height: "100%", background: CC.bg, overflow: "hidden" }}>
      {/* Amap 容器 */}
      <div ref={containerRef} style={{ width: "100%", height: "100%" }} />

      {/* 顶部 pill bar */}
      <div
        style={{
          position: "absolute", top: 10, left: 10, right: 50,
          display: "flex", alignItems: "center", gap: 8,
          background: "rgba(10,14,21,0.82)", backdropFilter: "blur(8px)",
          border: `1px solid ${CC.line}`, borderRadius: 8, padding: "6px 12px",
          zIndex: 10, pointerEvents: "none",
        }}
      >
        <span style={{ fontSize: 10, color: incidentCenter ? CC.emerg : CC.muted }}>📍</span>
        <span style={{ fontSize: 11, color: incidentCenter ? "rgba(255,255,255,0.85)" : "rgba(255,255,255,0.4)", flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {incidentCenter ? incidentLabel : "等待事故位置确认…"}
        </span>
        {incidentCenter && (
          <span style={{
            fontSize: 9, fontWeight: 700, letterSpacing: "0.08em", color: CC.emerg,
            background: `color-mix(in oklab, ${CC.emerg} 15%, transparent)`,
            border: `1px solid color-mix(in oklab, ${CC.emerg} 30%, transparent)`,
            padding: "1px 6px", borderRadius: 4,
          }}>实时</span>
        )}
      </div>

      {/* C3：阶段切换 toast */}
      {phaseToast && (
        <div style={{
          position: "absolute", top: 46, left: "50%", transform: "translateX(-50%)",
          background: "rgba(10,14,21,0.92)", backdropFilter: "blur(8px)",
          border: `1px solid color-mix(in oklab, ${phase.color} 45%, ${CC.line})`,
          borderRadius: 8, padding: "6px 14px", zIndex: 20,
          fontSize: 11, color: "rgba(255,255,255,0.9)", fontWeight: 600,
          boxShadow: `0 4px 18px color-mix(in oklab, ${phase.color} 25%, transparent)`,
          animation: "toast-in 0.3s ease",
        }}>
          {phaseToast}
        </div>
      )}

      {/* 左侧 ERPG 图例 */}
      {erpgItems.length > 0 && (
        <div style={{
          position: "absolute", top: 52, left: 10,
          background: "rgba(10,14,21,0.85)", backdropFilter: "blur(8px)",
          border: `1px solid ${CC.line}`, borderRadius: 8, padding: "8px 10px",
          zIndex: 10, minWidth: 140,
        }}>
          <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: "0.08em", color: "rgba(255,255,255,0.45)", marginBottom: 6 }}>
            气体扩散范围
          </div>
          {erpgItems.map((c) => (
            <div key={c.label} style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
              <div style={{ width: 8, height: 8, borderRadius: "50%", background: c.color, flexShrink: 0 }} />
              <span style={{ fontSize: 10, color: "rgba(255,255,255,0.82)" }}>{c.label}</span>
              <span style={{ fontSize: 9, color: "rgba(255,255,255,0.4)", marginLeft: "auto" }}>
                {c.radius >= 1000 ? `${(c.radius / 1000).toFixed(1)} km` : `${c.radius} m`}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* 右侧地图控件 */}
      <div style={{ position: "absolute", top: 10, right: 10, display: "flex", flexDirection: "column", gap: 4, zIndex: 10 }}>
        {(["+", "−", "⊙"] as const).map((label) => (
          <button
            key={label}
            onClick={() => {
              if (!mapRef.current) return;
              if (label === "+") mapRef.current.zoomIn();
              else if (label === "−") mapRef.current.zoomOut();
              else focusIncident();
            }}
            style={{
              width: 28, height: 28, background: "rgba(10,14,21,0.85)",
              border: `1px solid ${CC.line}`, borderRadius: 6,
              color: "rgba(255,255,255,0.75)", fontSize: label === "⊙" ? 12 : 16,
              cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center",
            }}
            title={label === "⊙" ? "聚焦事故/全览" : undefined}
          >
            {label}
          </button>
        ))}
        <button
          onClick={() => setShowLayers((p) => !p)}
          style={{
            marginTop: 4, width: 28, height: 28,
            background: showLayers ? `color-mix(in oklab, ${CC.info} 20%, rgba(10,14,21,0.85))` : "rgba(10,14,21,0.85)",
            border: `1px solid ${showLayers ? CC.info : CC.line}`, borderRadius: 6,
            color: showLayers ? CC.info : "rgba(255,255,255,0.75)", fontSize: 11,
            cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center",
          }}
          title="图层"
        >
          ⊞
        </button>
      </div>

      {/* B4：按部门聚焦 */}
      <div style={{
        position: "absolute", top: 10, right: 46,
        display: "flex", flexDirection: "column", gap: 4, zIndex: 10,
      }}>
        {FOCUS_DEPTS.map((d) => (
          <button
            key={d.code}
            onClick={() => focusDept(d.dept_code)}
            title={`聚焦${d.name}`}
            style={{
              width: 28, height: 28, borderRadius: 6, cursor: "pointer",
              background: "rgba(10,14,21,0.85)",
              border: `1px solid color-mix(in oklab, ${DEPT_COLORS[d.dept_code]} 40%, ${CC.line})`,
              color: DEPT_COLORS[d.dept_code], fontSize: 9, fontWeight: 700,
              display: "flex", alignItems: "center", justifyContent: "center",
            }}
          >
            {d.code}
          </button>
        ))}
      </div>

      {/* 图层面板 */}
      {showLayers && (
        <div style={{
          position: "absolute", top: 148, right: 10,
          background: "rgba(10,14,21,0.92)", backdropFilter: "blur(8px)",
          border: `1px solid ${CC.line}`, borderRadius: 8, padding: "8px 10px",
          zIndex: 10, minWidth: 120,
        }}>
          <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: "0.08em", color: "rgba(255,255,255,0.45)", marginBottom: 6 }}>
            图层
          </div>
          {layerState.map((layer) => (
            <div
              key={layer.id}
              style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4, cursor: "pointer" }}
              onClick={() => toggleLayer(layer.id)}
            >
              <div style={{
                width: 8, height: 8, borderRadius: 2,
                background: layer.enabled ? layer.color : "rgba(255,255,255,0.2)",
                flexShrink: 0, transition: "background 0.15s",
              }} />
              <span style={{ fontSize: 10, color: layer.enabled ? "rgba(255,255,255,0.82)" : "rgba(255,255,255,0.35)" }}>{layer.name}</span>
            </div>
          ))}
        </div>
      )}

      {/* 左下 phase chip */}
      <div style={{
        position: "absolute", bottom: 24, left: 10,
        display: "flex", alignItems: "center", gap: 5,
        background: "rgba(10,14,21,0.82)",
        border: `1px solid color-mix(in oklab, ${phase.color} 30%, ${CC.line})`,
        borderRadius: 6, padding: "4px 8px", zIndex: 10, transition: "border-color 0.4s",
      }}>
        <div style={{
          width: 6, height: 6, borderRadius: "50%", background: phase.color,
          animation: "phase-pulse 2s ease-in-out infinite", transition: "background 0.4s",
        }} />
        <span style={{ fontSize: 9, color: "rgba(255,255,255,0.7)", fontWeight: 600, letterSpacing: "0.06em" }}>
          {phase.label}
        </span>
      </div>

      {/* 水印 */}
      <div style={{
        position: "absolute", bottom: 8, right: 10, fontSize: 9,
        color: "rgba(255,255,255,0.25)", zIndex: 10, pointerEvents: "none",
      }}>
        © 暗色态势底图 · 演示数据
      </div>

      <style>{`
        @keyframes phase-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
        @keyframes toast-in { from { opacity: 0; transform: translate(-50%, -6px); } to { opacity: 1; transform: translate(-50%, 0); } }
      `}</style>
    </div>
  );
}
