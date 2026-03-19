import React, { useEffect, useMemo, useRef, useState } from "react";

type AnyDict = Record<string, any>;

type GroupBox = { x: number; y: number; w: number; h: number; cx: number; cy: number };

type GuideLine = { axis: "x" | "y"; pos: number; label?: string };

type Action =
  | { kind: "none" }
  | { kind: "marquee"; startX: number; startY: number; curX: number; curY: number; additive: boolean; subtractive: boolean; prev: number[]; moved: boolean }
  | { kind: "drag_layer"; idx: number; startX: number; startY: number; layer0: AnyDict; moved: boolean; altDeselect: boolean; dupDone: boolean }
  | { kind: "resize_layer"; idx: number; handle: string; startX: number; startY: number; layer0: AnyDict }
  | { kind: "rotate_layer"; idx: number; startAngle: number; startRot: number; centerX: number; centerY: number }
  | { kind: "drag_group"; clickedIdx: number; indices: number[]; startX: number; startY: number; layers0: AnyDict[]; moved: boolean; altDeselect: boolean; dupDone: boolean }
  | { kind: "resize_group"; indices: number[]; handle: string; startX: number; startY: number; box0: GroupBox; layers0: AnyDict[] }
  | { kind: "rotate_group"; indices: number[]; startAngle: number; centerX: number; centerY: number; layers0: AnyDict[] }
  | { kind: "drag_mask"; idx: number; startX: number; startY: number; mask0: AnyDict; layer0: AnyDict }
  | { kind: "resize_mask"; idx: number; startX: number; startY: number; mask0: AnyDict; layer0: AnyDict }
  | { kind: "rotate_mask"; idx: number; startAngle: number; startRot: number; centerX: number; centerY: number; mask0: AnyDict };

function clamp(n: number, a: number, b: number) {
  return Math.max(a, Math.min(b, n));
}

function deg(radVal: number) {
  return (radVal * 180) / Math.PI;
}

function rad(degVal: number) {
  return (degVal * Math.PI) / 180;
}

function angleBetween(cx: number, cy: number, x: number, y: number) {
  return deg(Math.atan2(y - cy, x - cx));
}

function ensureLayerBox(l: AnyDict): AnyDict {
  const out = { ...l };
  if (typeof out.x !== "number") out.x = Number(out.x ?? 20);
  if (typeof out.y !== "number") out.y = Number(out.y ?? 20);
  if (out.type === "image") {
    if (typeof out.w !== "number") out.w = Number(out.w ?? 220);
    if (typeof out.h !== "number") out.h = Number(out.h ?? 220);
  } else {
    if (typeof out.w !== "number") out.w = Number(out.w ?? 320);
    if (typeof out.h !== "number") out.h = Number(out.h ?? 90);
  }
  if (typeof out.rotation_deg !== "number") out.rotation_deg = Number(out.rotation_deg ?? 0);
  if (typeof out.opacity !== "number") out.opacity = Number(out.opacity ?? 1);
  return out;
}

function ensureMask(l: AnyDict): AnyDict {
  return {
    mask_x: Number(l.mask_x ?? 0),
    mask_y: Number(l.mask_y ?? 0),
    mask_scale: Number(l.mask_scale ?? 1),
    mask_rotation_deg: Number(l.mask_rotation_deg ?? 0),
  };
}

function deepClone<T>(v: T): T {
  return JSON.parse(JSON.stringify(v));
}

function genId(prefix: string) {
  return `${prefix}_${Math.random().toString(16).slice(2)}_${Date.now().toString(16)}`;
}

function cloneLayerForDuplicate(layer: AnyDict): AnyDict {
  const c = deepClone(layer);
  if (typeof c.id === "string") c.id = `${c.id}_copy_${Date.now().toString(16)}`;
  else c.id = genId("layer");
  if (typeof c.name === "string") c.name = `${c.name} (copy)`;
  return c;
}

function groupBox(layers: AnyDict[], indices: number[]): GroupBox | null {
  const pts = indices
    .map((i) => layers[i])
    .filter(Boolean)
    .map(ensureLayerBox);

  if (!pts.length) return null;

  let minX = pts[0].x;
  let minY = pts[0].y;
  let maxX = pts[0].x + pts[0].w;
  let maxY = pts[0].y + pts[0].h;

  for (const l of pts.slice(1)) {
    minX = Math.min(minX, l.x);
    minY = Math.min(minY, l.y);
    maxX = Math.max(maxX, l.x + l.w);
    maxY = Math.max(maxY, l.y + l.h);
  }

  const w = Math.max(1, maxX - minX);
  const h = Math.max(1, maxY - minY);
  return { x: minX, y: minY, w, h, cx: minX + w / 2, cy: minY + h / 2 };
}

function groupBoxFromLayers(layers0: AnyDict[]): GroupBox {
  const pts = layers0.map(ensureLayerBox);
  let minX = pts[0].x;
  let minY = pts[0].y;
  let maxX = pts[0].x + pts[0].w;
  let maxY = pts[0].y + pts[0].h;
  for (const l of pts.slice(1)) {
    minX = Math.min(minX, l.x);
    minY = Math.min(minY, l.y);
    maxX = Math.max(maxX, l.x + l.w);
    maxY = Math.max(maxY, l.y + l.h);
  }
  const w = Math.max(1, maxX - minX);
  const h = Math.max(1, maxY - minY);
  return { x: minX, y: minY, w, h, cx: minX + w / 2, cy: minY + h / 2 };
}

function upsertKeyframe(layer: AnyDict, t: number, patch: AnyDict): AnyDict {
  const out = { ...layer };
  const kfs: AnyDict[] = Array.isArray(out.keyframes) ? [...out.keyframes] : [];
  const eps = 1e-6;
  const i = kfs.findIndex((k) => typeof k?.t === "number" && Math.abs(k.t - t) < eps);

  const kf = { ...(i >= 0 ? kfs[i] : {}), t, ...patch };
  if (i >= 0) kfs[i] = kf;
  else kfs.push(kf);

  kfs.sort((a, b) => Number(a?.t ?? 0) - Number(b?.t ?? 0));
  out.keyframes = kfs;
  return out;
}

function minKeyframeT(layer: AnyDict): number {
  const kfs: AnyDict[] = Array.isArray(layer?.keyframes) ? layer.keyframes : [];
  let minT = Number.POSITIVE_INFINITY;
  for (const k of kfs) {
    const t = Number(k?.t);
    if (Number.isFinite(t)) minT = Math.min(minT, t);
  }
  return Number.isFinite(minT) ? minT : 0;
}

function shiftKeyframes(layer: AnyDict, dt: number): AnyDict {
  if (!Number.isFinite(dt) || Math.abs(dt) < 1e-9) return layer;
  const out = { ...layer };
  const kfs: AnyDict[] = Array.isArray(out.keyframes) ? out.keyframes : [];
  out.keyframes = kfs
    .map((k) => {
      const t = Number(k?.t);
      const nk = { ...k };
      if (Number.isFinite(t)) nk.t = t + dt;
      return nk;
    })
    .filter((k) => Number.isFinite(Number(k?.t)) && Number(k.t) >= 0)
    .sort((a, b) => Number(a?.t ?? 0) - Number(b?.t ?? 0));
  return out;
}

function shiftKeyframeXY(layer: AnyDict, dx: number, dy: number): AnyDict {
  if (!Number.isFinite(dx) && !Number.isFinite(dy)) return layer;
  const out = { ...layer };
  if (Number.isFinite(dx)) out.x = Number(out.x ?? 0) + dx;
  if (Number.isFinite(dy)) out.y = Number(out.y ?? 0) + dy;

  const kfs: AnyDict[] = Array.isArray(out.keyframes) ? out.keyframes : [];
  out.keyframes = kfs.map((k) => {
    const nk = { ...k };
    if (Number.isFinite(dx) && typeof nk.x === "number") nk.x = nk.x + dx;
    if (Number.isFinite(dy) && typeof nk.y === "number") nk.y = nk.y + dy;
    return nk;
  });
  return out;
}

function rectFromPoints(x0: number, y0: number, x1: number, y1: number) {
  const x = Math.min(x0, x1);
  const y = Math.min(y0, y1);
  const w = Math.abs(x1 - x0);
  const h = Math.abs(y1 - y0);
  return { x, y, w, h };
}

function rectIntersects(a: { x: number; y: number; w: number; h: number }, b: { x: number; y: number; w: number; h: number }) {
  return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
}

function nearestSnapDelta(value: number, targets: number[], threshold: number): { delta: number; target: number } | null {
  let best: { delta: number; target: number } | null = null;
  for (const t of targets) {
    const d = t - value;
    const ad = Math.abs(d);
    if (ad <= threshold && (!best || ad < Math.abs(best.delta))) best = { delta: d, target: t };
  }
  return best;
}

function gridSnap(value: number, grid: number) {
  if (grid <= 1) return value;
  return Math.round(value / grid) * grid;
}

function computeSnapTargets(width: number, height: number, layers: AnyDict[], exclude: Set<number>) {
  const xs: number[] = [0, width / 2, width];
  const ys: number[] = [0, height / 2, height];

  layers.forEach((l0, i) => {
    if (exclude.has(i)) return;
    const l = ensureLayerBox(l0);
    xs.push(l.x, l.x + l.w / 2, l.x + l.w);
    ys.push(l.y, l.y + l.h / 2, l.y + l.h);
  });

  return { xs, ys };
}

function snapDragBox(
  box: { x: number; y: number; w: number; h: number },
  targets: { xs: number[]; ys: number[] },
  cfg: { snapEnabled: boolean; gridEnabled: boolean; gridSize: number; threshold: number },
) {
  if (!cfg.snapEnabled) return { x: box.x, y: box.y, guides: [] as GuideLine[] };

  const guides: GuideLine[] = [];
  let x = box.x;
  let y = box.y;

  const pickSnap = (cand: number[], targetsArr: number[]) => {
    let best: { delta: number; target: number; kind: "target" | "grid" } | null = null;

    // target snap
    for (const c of cand) {
      const s = nearestSnapDelta(c, targetsArr, cfg.threshold);
      if (!s) continue;
      if (!best || Math.abs(s.delta) < Math.abs(best.delta)) best = { ...s, kind: "target" };
    }

    // grid snap (always, but competes by delta magnitude)
    if (cfg.gridEnabled) {
      const gridCands = cand.map((c) => ({ c, g: gridSnap(c, cfg.gridSize) }));
      for (const gc of gridCands) {
        const d = gc.g - gc.c;
        if (!best || Math.abs(d) < Math.abs(best.delta)) best = { delta: d, target: gc.g, kind: "grid" };
      }
    }

    return best;
  };

  // X: left/center/right
  const xCands = [x, x + box.w / 2, x + box.w];
  const bestX = pickSnap(xCands, targets.xs);
  if (bestX) {
    x += bestX.delta;
    if (bestX.kind === "target") guides.push({ axis: "x", pos: bestX.target });
  }

  // Y: top/center/bottom
  const yCands = [y, y + box.h / 2, y + box.h];
  const bestY = pickSnap(yCands, targets.ys);
  if (bestY) {
    y += bestY.delta;
    if (bestY.kind === "target") guides.push({ axis: "y", pos: bestY.target });
  }

  return { x, y, guides };
}

function snapResizeBox(
  box: { x: number; y: number; w: number; h: number },
  handle: string,
  targets: { xs: number[]; ys: number[] },
  cfg: { snapEnabled: boolean; gridEnabled: boolean; gridSize: number; threshold: number },
  minW = 20,
  minH = 20,
) {
  if (!cfg.snapEnabled) return { ...box, guides: [] as GuideLine[] };
  let { x, y, w, h } = box;
  const guides: GuideLine[] = [];

  const right0 = x + w;
  const bot0 = y + h;

  const snapEdge = (value: number, axis: "x" | "y") => {
    const targetArr = axis === "x" ? targets.xs : targets.ys;
    let best: { delta: number; target: number; kind: "target" | "grid" } | null = null;

    const t = nearestSnapDelta(value, targetArr, cfg.threshold);
    if (t) best = { ...t, kind: "target" };

    if (cfg.gridEnabled) {
      const g = gridSnap(value, cfg.gridSize);
      const d = g - value;
      if (!best || Math.abs(d) < Math.abs(best.delta)) best = { delta: d, target: g, kind: "grid" };
    }
    if (!best) return { value, guide: null as GuideLine | null };

    const v1 = value + best.delta;
    if (best.kind === "target") return { value: v1, guide: { axis, pos: best.target } as GuideLine };
    return { value: v1, guide: null as GuideLine | null };
  };

  // Snap moving edges depending on handle
  if (handle.includes("e")) {
    const s = snapEdge(x + w, "x");
    const newRight = s.value;
    w = Math.max(minW, newRight - x);
    if (s.guide) guides.push(s.guide);
  }
  if (handle.includes("w")) {
    const s = snapEdge(x, "x");
    const newLeft = s.value;
    const newW = Math.max(minW, right0 - newLeft);
    x = right0 - newW;
    w = newW;
    if (s.guide) guides.push(s.guide);
  }
  if (handle.includes("s")) {
    const s = snapEdge(y + h, "y");
    const newBot = s.value;
    h = Math.max(minH, newBot - y);
    if (s.guide) guides.push(s.guide);
  }
  if (handle.includes("n")) {
    const s = snapEdge(y, "y");
    const newTop = s.value;
    const newH = Math.max(minH, bot0 - newTop);
    y = bot0 - newH;
    h = newH;
    if (s.guide) guides.push(s.guide);
  }

  // If only one axis is being resized, still offer gentle grid snap on position
  if (!handle.match(/[ew]/) && cfg.gridEnabled) x = gridSnap(x, cfg.gridSize);
  if (!handle.match(/[ns]/) && cfg.gridEnabled) y = gridSnap(y, cfg.gridSize);

  return { x, y, w, h, guides };
}

export function OverlayStage(props: {
  projectId: string;
  backendUrl: string;
  width: number;
  height: number;
  timeline: AnyDict;
  selectedIndices: number[];
  onSelect: (indices: number[]) => void;
  onChange: (timeline: AnyDict) => void;
  editingMask: boolean;
  onEditingMaskChange: (v: boolean) => void;
  backgroundUrl?: string | null;
  playheadS: number;
  autoKey: boolean;
}) {
  const {
    projectId,
    backendUrl,
    width,
    height,
    timeline,
    selectedIndices,
    onSelect,
    onChange,
    editingMask,
    onEditingMaskChange,
    backgroundUrl,
    playheadS,
    autoKey,
  } = props;


  const clipboardRef = useRef<{ layers: AnyDict[]; box: GroupBox | null; minT: number } | null>(null);
  const pasteSerialRef = useRef<number>(0);
  const lastPointerRef = useRef<{ x: number; y: number } | null>(null);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const timelineRef = useRef<AnyDict>(timeline);
  useEffect(() => {
    timelineRef.current = timeline;
  }, [timeline]);

  const [action, setAction] = useState<Action>({ kind: "none" });
  const [scale, setScale] = useState<number>(1);

  const [snapEnabled, setSnapEnabled] = useState<boolean>(true);
  const [gridEnabled, setGridEnabled] = useState<boolean>(true);
  const [gridSize, setGridSize] = useState<number>(10);
  const [guides, setGuides] = useState<GuideLine[]>([]);

  const layers: AnyDict[] = useMemo(() => (timeline?.layers || []).map(ensureLayerBox), [timeline]);
  const selectedSet = useMemo(() => new Set<number>(selectedIndices || []), [selectedIndices]);
  const primaryIndex = selectedIndices?.length ? selectedIndices[selectedIndices.length - 1] : null;

  const fileUrl = (rel: string) => `${backendUrl}/v1/projects/${projectId}/file?path=${encodeURIComponent(rel)}`;

  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      const r = el.getBoundingClientRect();
      setScale(r.width / width);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [width]);

  const toLocal = (e: React.PointerEvent) => {
    const el = stageRef.current;
    if (!el) return { x: 0, y: 0 };
    const r = el.getBoundingClientRect();
    return { x: (e.clientX - r.left) / scale, y: (e.clientY - r.top) / scale };
  };

  const setLayersBulk = (patches: { idx: number; patch: AnyDict }[]) => {
    const base = timelineRef.current || {};
    const next = { ...base, layers: [...(base?.layers || [])] };
    for (const p of patches) {
      const layer0 = next.layers[p.idx] || {};
      let layer1 = { ...layer0, ...p.patch };
      if (autoKey && Number.isFinite(playheadS)) {
        layer1 = upsertKeyframe(layer1, Number(playheadS), p.patch);
      }
      next.layers[p.idx] = layer1;
    }
    timelineRef.current = next;
    onChange(next);
  };

  const setLayer = (idx: number, patch: AnyDict) => setLayersBulk([{ idx, patch }]);

  const toggleSelect = (idx: number) => {
    const next = new Set<number>(selectedSet);
    if (next.has(idx)) next.delete(idx);
    else next.add(idx);
    onSelect(Array.from(next.values()).sort((a, b) => a - b));
  };

  const startLayerPointer = (e: React.PointerEvent, idx: number) => {
    e.stopPropagation();
    (e.currentTarget as any).setPointerCapture?.(e.pointerId);
    setGuides([]);
    stageRef.current?.focus?.();

    const p = toLocal(e);
    lastPointerRef.current = p;

    if (e.shiftKey) {
      onEditingMaskChange(false);
      toggleSelect(idx);
      return;
    }

    const altDeselect = !!e.altKey && selectedSet.has(idx);
    onEditingMaskChange(false);

    if (!altDeselect) {
      if (!selectedSet.has(idx)) {
        onSelect([idx]);
      } else if ((selectedIndices || []).length <= 1) {
        onSelect([idx]);
      }
    }

    if ((selectedIndices || []).length > 1 && selectedSet.has(idx)) {
      const layers0 = (selectedIndices || []).map((i) => ensureLayerBox(layers[i]));
      setAction({
        kind: "drag_group",
        clickedIdx: idx,
        indices: [...selectedIndices],
        startX: p.x,
        startY: p.y,
        layers0,
        moved: false,
        altDeselect,
        dupDone: false,
      });
    } else {
      setAction({
        kind: "drag_layer",
        idx,
        startX: p.x,
        startY: p.y,
        layer0: ensureLayerBox(layers[idx]),
        moved: false,
        altDeselect,
        dupDone: false,
      });
    }
  };

  const startLayerResize = (e: React.PointerEvent, idx: number, handle: string) => {
    e.stopPropagation();
    (e.currentTarget as any).setPointerCapture?.(e.pointerId);
    setGuides([]);
    stageRef.current?.focus?.();

    const p = toLocal(e);

    if ((selectedIndices || []).length > 1) {
      const box0 = groupBox(layers, selectedIndices);
      if (!box0) return;
      const layers0 = selectedIndices.map((i) => ensureLayerBox(layers[i]));
      setAction({ kind: "resize_group", indices: [...selectedIndices], handle, startX: p.x, startY: p.y, box0, layers0 });
      return;
    }

    onSelect([idx]);
    onEditingMaskChange(false);
    setAction({ kind: "resize_layer", idx, handle, startX: p.x, startY: p.y, layer0: ensureLayerBox(layers[idx]) });
  };

  const startLayerRotate = (e: React.PointerEvent, idx: number) => {
    e.stopPropagation();
    (e.currentTarget as any).setPointerCapture?.(e.pointerId);
    setGuides([]);
    stageRef.current?.focus?.();

    const p = toLocal(e);

    if ((selectedIndices || []).length > 1) {
      const box0 = groupBox(layers, selectedIndices);
      if (!box0) return;
      const layers0 = selectedIndices.map((i) => ensureLayerBox(layers[i]));
      setAction({
        kind: "rotate_group",
        indices: [...selectedIndices],
        startAngle: angleBetween(box0.cx, box0.cy, p.x, p.y),
        centerX: box0.cx,
        centerY: box0.cy,
        layers0,
      });
      return;
    }

    onSelect([idx]);
    onEditingMaskChange(false);
    const l0 = ensureLayerBox(layers[idx]);
    const cx = l0.x + l0.w / 2;
    const cy = l0.y + l0.h / 2;
    setAction({
      kind: "rotate_layer",
      idx,
      startAngle: angleBetween(cx, cy, p.x, p.y),
      startRot: Number(l0.rotation_deg ?? 0),
      centerX: cx,
      centerY: cy,
    });
  };

  const startMaskDrag = (e: React.PointerEvent, idx: number) => {
    e.stopPropagation();
    (e.currentTarget as any).setPointerCapture?.(e.pointerId);
    setGuides([]);
    stageRef.current?.focus?.();

    const p = toLocal(e);
    onSelect([idx]);
    onEditingMaskChange(true);
    const l0 = ensureLayerBox(layers[idx]);
    setAction({ kind: "drag_mask", idx, startX: p.x, startY: p.y, mask0: ensureMask(layers[idx]), layer0: l0 });
  };

  const startMaskResize = (e: React.PointerEvent, idx: number) => {
    e.stopPropagation();
    (e.currentTarget as any).setPointerCapture?.(e.pointerId);
    setGuides([]);
    stageRef.current?.focus?.();

    const p = toLocal(e);
    onSelect([idx]);
    onEditingMaskChange(true);
    const l0 = ensureLayerBox(layers[idx]);
    setAction({ kind: "resize_mask", idx, startX: p.x, startY: p.y, mask0: ensureMask(layers[idx]), layer0: l0 });
  };

  const startMaskRotate = (e: React.PointerEvent, idx: number) => {
    e.stopPropagation();
    (e.currentTarget as any).setPointerCapture?.(e.pointerId);
    setGuides([]);
    stageRef.current?.focus?.();

    const p = toLocal(e);
    onSelect([idx]);
    onEditingMaskChange(true);
    const l0 = ensureLayerBox(layers[idx]);
    const m0 = ensureMask(layers[idx]);
    const cx = l0.x + l0.w / 2 + m0.mask_x;
    const cy = l0.y + l0.h / 2 + m0.mask_y;
    setAction({
      kind: "rotate_mask",
      idx,
      startAngle: angleBetween(cx, cy, p.x, p.y),
      startRot: m0.mask_rotation_deg,
      centerX: cx,
      centerY: cy,
      mask0: m0,
    });
  };

  const snapCfg = useMemo(
    () => ({ snapEnabled, gridEnabled, gridSize: clamp(gridSize, 2, 200), threshold: 7 }),
    [snapEnabled, gridEnabled, gridSize],
  );

  const onMove = (e: React.PointerEvent) => {
    if (action.kind === "none") return;
    const p = toLocal(e);
    const effectiveSnapCfg = (e.ctrlKey || e.metaKey) ? { ...snapCfg, snapEnabled: false } : snapCfg;
    const alt = !!e.altKey;
    const isMoveDrag = action.kind === "drag_layer" || action.kind === "drag_group";
    const fineScale = alt && !isMoveDrag ? 0.1 : 1.0;
    const layersNow: AnyDict[] = (timelineRef.current?.layers || []).map(ensureLayerBox);

    if (action.kind === "drag_layer") {
      const dxRaw = p.x - action.startX;
      const dyRaw = p.y - action.startY;
      const movedNow = action.moved || Math.hypot(dxRaw, dyRaw) > 3;
      if (!action.moved && movedNow) setAction({ ...action, moved: true });

      // Alt+drag duplicates selected layers (single move). Fine movement for move is handled via arrow keys.
      const dx = dxRaw;
      const dy = dyRaw;

      const base = timelineRef.current || {};
      const baseLayers: AnyDict[] = (base?.layers || []).map(ensureLayerBox);

      const shouldDuplicate = alt && movedNow && !action.dupDone;
      if (shouldDuplicate) {
        const layersArr: AnyDict[] = Array.isArray(base?.layers) ? [...base.layers] : [...(timeline?.layers || [])];
        const original = ensureLayerBox(layersArr[action.idx] ?? action.layer0);
        const clone = cloneLayerForDuplicate(original);
        const newIdx = layersArr.length;

        const proposed = { x: original.x + dx, y: original.y + dy, w: original.w, h: original.h };
        const targets = computeSnapTargets(width, height, baseLayers.length ? baseLayers : layersNow, new Set<number>([action.idx]));
        const s = snapDragBox(proposed, targets, effectiveSnapCfg);

        clone.x = Math.round(s.x);
        clone.y = Math.round(s.y);
        layersArr.push(clone);

        const next = { ...base, layers: layersArr };
        timelineRef.current = next;
        setGuides(s.guides);
        onChange(next);
        onSelect([newIdx]);

        setAction({
          kind: "drag_layer",
          idx: newIdx,
          startX: action.startX,
          startY: action.startY,
          layer0: ensureLayerBox(clone),
          moved: true,
          altDeselect: false,
          dupDone: true,
        });
        return;
      }

      const proposed = { x: action.layer0.x + dx, y: action.layer0.y + dy, w: action.layer0.w, h: action.layer0.h };
      const targets = computeSnapTargets(width, height, baseLayers.length ? baseLayers : layersNow, new Set<number>([action.idx]));
      const s = snapDragBox(proposed, targets, effectiveSnapCfg);

      setGuides(s.guides);
      setLayer(action.idx, { x: Math.round(s.x), y: Math.round(s.y) });
      return;
    }

    if (action.kind === "drag_group") {
      const dxRaw = p.x - action.startX;
      const dyRaw = p.y - action.startY;
      const movedNow = action.moved || Math.hypot(dxRaw, dyRaw) > 3;
      if (!action.moved && movedNow) setAction({ ...action, moved: true });

      const dx = dxRaw;
      const dy = dyRaw;

      const base = timelineRef.current || {};
      const baseLayers: AnyDict[] = (base?.layers || []).map(ensureLayerBox);

      const shouldDuplicate = alt && movedNow && !action.dupDone;
      if (shouldDuplicate) {
        const layersArr: AnyDict[] = Array.isArray(base?.layers) ? [...base.layers] : [...(timeline?.layers || [])];
        const clones = action.indices.map((i) => cloneLayerForDuplicate(ensureLayerBox(layersArr[i] ?? layersNow[i] ?? {})));
        const newIndices = clones.map((_, j) => layersArr.length + j);

        const box0 = groupBoxFromLayers(action.layers0);
        const proposed = { x: box0.x + dx, y: box0.y + dy, w: box0.w, h: box0.h };
        const targets = computeSnapTargets(width, height, baseLayers.length ? baseLayers : layersNow, new Set<number>(action.indices));
        const s = snapDragBox(proposed, targets, effectiveSnapCfg);

        const dx2 = s.x - box0.x;
        const dy2 = s.y - box0.y;

        clones.forEach((cl, i) => {
          cl.x = Math.round(action.layers0[i].x + dx2);
          cl.y = Math.round(action.layers0[i].y + dy2);
        });

        clones.forEach((cl) => layersArr.push(cl));

        const next = { ...base, layers: layersArr };
        timelineRef.current = next;
        setGuides(s.guides);
        onChange(next);
        onSelect(newIndices);

        setAction({
          kind: "drag_group",
          clickedIdx: newIndices[newIndices.length - 1],
          indices: newIndices,
          startX: action.startX,
          startY: action.startY,
          layers0: clones.map(ensureLayerBox),
          moved: true,
          altDeselect: false,
          dupDone: true,
        });
        return;
      }

      const box0 = groupBoxFromLayers(action.layers0);
      const proposed = { x: box0.x + dx, y: box0.y + dy, w: box0.w, h: box0.h };
      const targets = computeSnapTargets(width, height, baseLayers.length ? baseLayers : layersNow, new Set<number>(action.indices));
      const s = snapDragBox(proposed, targets, effectiveSnapCfg);

      const dx2 = s.x - box0.x;
      const dy2 = s.y - box0.y;
      setGuides(s.guides);

      setLayersBulk(
        action.indices.map((idx, i) => ({
          idx,
          patch: { x: Math.round(action.layers0[i].x + dx2), y: Math.round(action.layers0[i].y + dy2) },
        })),
      );
      return;
    }

    if (action.kind === "resize_layer") {
      const dx = (p.x - action.startX) * fineScale;
      const dy = (p.y - action.startY) * fineScale;
      let { x, y, w, h } = action.layer0;

      const hnd = action.handle;
      if (hnd.includes("e")) w = clamp(w + dx, 20, 99999);
      if (hnd.includes("s")) h = clamp(h + dy, 20, 99999);
      if (hnd.includes("w")) {
        const nw = clamp(w - dx, 20, 99999);
        x = x + (w - nw);
        w = nw;
      }
      if (hnd.includes("n")) {
        const nh = clamp(h - dy, 20, 99999);
        y = y + (h - nh);
        h = nh;
      }

      const targets = computeSnapTargets(width, height, layers, new Set<number>([action.idx]));
      const s = snapResizeBox({ x, y, w, h }, hnd, targets, effectiveSnapCfg);

      setGuides(s.guides);
      setLayer(action.idx, { x: Math.round(s.x), y: Math.round(s.y), w: Math.round(s.w), h: Math.round(s.h) });
      return;
    }

    if (action.kind === "resize_group") {
      const dx = (p.x - action.startX) * fineScale;
      const dy = (p.y - action.startY) * fineScale;
      let { x, y, w, h } = action.box0;

      const hnd = action.handle;
      if (hnd.includes("e")) w = clamp(w + dx, 20, 99999);
      if (hnd.includes("s")) h = clamp(h + dy, 20, 99999);
      if (hnd.includes("w")) {
        const nw = clamp(w - dx, 20, 99999);
        x = x + (w - nw);
        w = nw;
      }
      if (hnd.includes("n")) {
        const nh = clamp(h - dy, 20, 99999);
        y = y + (h - nh);
        h = nh;
      }

      const targets = computeSnapTargets(width, height, layers, new Set<number>(action.indices));
      const sBox = snapResizeBox({ x, y, w, h }, hnd, targets, effectiveSnapCfg);

      const sx = sBox.w / action.box0.w;
      const sy = sBox.h / action.box0.h;

      setGuides(sBox.guides);

      setLayersBulk(
        action.indices.map((idx, i) => {
          const l0 = action.layers0[i];
          const relX = (l0.x - action.box0.x) / action.box0.w;
          const relY = (l0.y - action.box0.y) / action.box0.h;
          return {
            idx,
            patch: {
              x: Math.round(sBox.x + relX * sBox.w),
              y: Math.round(sBox.y + relY * sBox.h),
              w: Math.round(l0.w * sx),
              h: Math.round(l0.h * sy),
            },
          };
        }),
      );
      return;
    }

    if (action.kind === "rotate_layer") {
      setGuides([]);
      const a = angleBetween(action.centerX, action.centerY, p.x, p.y);
      const deltaRaw = a - action.startAngle;
      const delta = deltaRaw * fineScale;
      setLayer(action.idx, { rotation_deg: Math.round((action.startRot + delta) * 10) / 10 });
      return;
    }

    if (action.kind === "rotate_group") {
      setGuides([]);
      const a = angleBetween(action.centerX, action.centerY, p.x, p.y);
      const deltaRaw = a - action.startAngle;
      const delta = deltaRaw * fineScale;
      const dr = rad(delta);

      setLayersBulk(
        action.indices.map((idx, i) => {
          const l0 = action.layers0[i];
          const cx0 = l0.x + l0.w / 2;
          const cy0 = l0.y + l0.h / 2;
          const vx = cx0 - action.centerX;
          const vy = cy0 - action.centerY;
          const rx = vx * Math.cos(dr) - vy * Math.sin(dr);
          const ry = vx * Math.sin(dr) + vy * Math.cos(dr);
          const cx1 = action.centerX + rx;
          const cy1 = action.centerY + ry;
          return {
            idx,
            patch: {
              x: Math.round(cx1 - l0.w / 2),
              y: Math.round(cy1 - l0.h / 2),
              rotation_deg: Math.round((Number(l0.rotation_deg ?? 0) + delta) * 10) / 10,
            },
          };
        }),
      );
      return;
    }

    if (action.kind === "drag_mask") {
      setGuides([]);
      const dx = (p.x - action.startX) * fineScale;
      const dy = (p.y - action.startY) * fineScale;
      setLayer(action.idx, {
        mask_x: Math.round((action.mask0.mask_x + dx) * 10) / 10,
        mask_y: Math.round((action.mask0.mask_y + dy) * 10) / 10,
      });
      return;
    }

    if (action.kind === "resize_mask") {
      setGuides([]);
      const l0 = action.layer0;
      const diag0 = Math.sqrt(l0.w * l0.w + l0.h * l0.h);
      const dx = (p.x - action.startX) * fineScale;
      const dy = (p.y - action.startY) * fineScale;
      const diag1 = Math.max(1, diag0 + (dx + dy) / 2);
      const scale1 = clamp((diag1 / diag0) * action.mask0.mask_scale, 0.05, 20);
      setLayer(action.idx, { mask_scale: Math.round(scale1 * 1000) / 1000 });
      return;
    }

    if (action.kind === "rotate_mask") {
      setGuides([]);
      const a = angleBetween(action.centerX, action.centerY, p.x, p.y);
      const deltaRaw = a - action.startAngle;
      const delta = deltaRaw * fineScale;
      setLayer(action.idx, { mask_rotation_deg: Math.round((action.startRot + delta) * 10) / 10 });
      return;
    }
  };

  const finalizeMarquee = (a: Extract<Action, { kind: "marquee" }>) => {
    const r = rectFromPoints(a.startX, a.startY, a.curX, a.curY);
    const isClick = !a.moved || (r.w < 4 && r.h < 4);

    if (isClick) {
      if (!a.additive && !a.subtractive) onSelect([]);
      return;
    }

    const hits: number[] = [];
    for (let i = 0; i < layers.length; i++) {
      const l = ensureLayerBox(layers[i]);
      const lb = { x: l.x, y: l.y, w: l.w, h: l.h };
      if (rectIntersects(r, lb)) hits.push(i);
    }

    if (a.subtractive) {
      const next = new Set<number>(a.prev);
      hits.forEach((h) => next.delete(h));
      onSelect(Array.from(next.values()).sort((x, y) => x - y));
    } else if (a.additive) {
      const next = new Set<number>(a.prev);
      hits.forEach((h) => next.add(h));
      onSelect(Array.from(next.values()).sort((x, y) => x - y));
    } else {
      onSelect(hits.sort((x, y) => x - y));
    }
  };

  const endAction = () => {
    if (action.kind === "marquee") finalizeMarquee(action);

    if (action.kind === "drag_layer" && action.altDeselect && !action.moved) {
      const next = new Set<number>(selectedIndices || []);
      next.delete(action.idx);
      onSelect(Array.from(next.values()).sort((a, b) => a - b));
    }

    if (action.kind === "drag_group" && action.altDeselect && !action.moved) {
      const next = new Set<number>(selectedIndices || []);
      next.delete(action.clickedIdx);
      onSelect(Array.from(next.values()).sort((a, b) => a - b));
    }

    setAction({ kind: "none" });
    setGuides([]);
  };


  const nudgeSelection = (dx: number, dy: number) => {
    if (!selectedIndices?.length) return;
    const baseLayers: AnyDict[] = (timelineRef.current?.layers || []).map(ensureLayerBox);
    setGuides([]);
    setLayersBulk(
      selectedIndices.map((idx) => {
        const l0 = ensureLayerBox(baseLayers[idx] ?? layers[idx] ?? {});
        return { idx, patch: { x: Math.round(l0.x + dx), y: Math.round(l0.y + dy) } };
      }),
    );
  };

  
  const onStageKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const t = e.target as HTMLElement | null;
    const tag = (t?.tagName || "").toUpperCase();
    if (tag === "INPUT" || tag === "TEXTAREA" || (t as any)?.isContentEditable) return;

    const isMod = !!(e.ctrlKey || e.metaKey);
    const key = (e.key || "").toLowerCase();

    if (isMod && key === "c") {
      if (!selectedIndices?.length) return;
      e.preventDefault();

      const baseLayers: AnyDict[] = Array.isArray(timelineRef.current?.layers) ? [...timelineRef.current.layers] : [];
      const picked = selectedIndices
        .map((i) => baseLayers[i])
        .filter(Boolean)
        .map((l) => deepClone(ensureLayerBox(l)));

      const idxs = picked.map((_, i) => i);
      const box = groupBox(picked, idxs);

      let minT = Number.POSITIVE_INFINITY;
      for (const l of picked) minT = Math.min(minT, minKeyframeT(l));
      if (!Number.isFinite(minT)) minT = 0;

      clipboardRef.current = { layers: picked, box, minT };
      pasteSerialRef.current = 0;
      return;
    }

    if (isMod && key === "v") {
      const clip = clipboardRef.current;
      if (!clip?.layers?.length) return;
      e.preventDefault();

      const base = timelineRef.current || {};
      const layers: AnyDict[] = Array.isArray(base?.layers) ? [...base.layers] : [];

      const target = lastPointerRef.current ?? { x: width / 2, y: height / 2 };
      const box = clip.box ?? groupBox(clip.layers, clip.layers.map((_, i) => i));
      const cx = box?.cx ?? 0;
      const cy = box?.cy ?? 0;

      pasteSerialRef.current += 1;
      const off = 12 * pasteSerialRef.current;

      const dx = (target.x - cx) + off;
      const dy = (target.y - cy) + off;

      const dt = Number.isFinite(playheadS) ? Number(playheadS) - Number(clip.minT ?? 0) : 0;

      const newIndices: number[] = [];
      for (const l0 of clip.layers) {
        let nl = cloneLayerForDuplicate(l0);
        nl = shiftKeyframes(nl, dt);
        nl = shiftKeyframeXY(nl, dx, dy);

        if (autoKey && Number.isFinite(playheadS)) {
          const patch: AnyDict = {
            x: Number(nl.x ?? 0),
            y: Number(nl.y ?? 0),
            w: Number(nl.w ?? 0),
            h: Number(nl.h ?? 0),
            rotation_deg: Number(nl.rotation_deg ?? 0),
            opacity: Number(nl.opacity ?? 1),
            ...ensureMask(nl),
          };
          nl = upsertKeyframe(nl, Number(playheadS), patch);
        }

        newIndices.push(layers.length);
        layers.push(nl);
      }

      onChange({ ...base, layers });
      onSelect(newIndices);
      return;
    }

    // Arrow key nudging
    if (!selectedIndices?.length) return;
    if (!key.startsWith("arrow")) return;

    const step = e.shiftKey ? 10 : e.altKey ? 1 : 5;
    let dx = 0;
    let dy = 0;
    if (key === "arrowleft") dx = -step;
    if (key === "arrowright") dx = step;
    if (key === "arrowup") dy = -step;
    if (key === "arrowdown") dy = step;
    if (!dx && !dy) return;

    e.preventDefault();
    nudgeSelection(dx, dy);
  };


  const primary = primaryIndex != null ? ensureLayerBox(layers[primaryIndex]) : null;
  const multiBox = useMemo(() => {
    if (!selectedIndices?.length) return null;
    if (selectedIndices.length <= 1) return null;
    if (editingMask) return null;
    return groupBox(layers, selectedIndices);
  }, [layers, selectedIndices, editingMask]);

  const canEditMask = primaryIndex != null && selectedIndices.length === 1 && primary?.type === "image";

  const marqueeRect =
    action.kind === "marquee" && action.moved ? rectFromPoints(action.startX, action.startY, action.curX, action.curY) : null;

  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <div style={{ fontWeight: 900 }}>Visual editor</div>
        <div className="row" style={{ gap: 10, flexWrap: "wrap", justifyContent: "flex-end" }}>
          <label className="small row" style={{ gap: 6 }}>
            <input type="checkbox" checked={snapEnabled} onChange={(e) => setSnapEnabled(e.target.checked)} />
            snap
          </label>
          <label className="small row" style={{ gap: 6 }}>
            <input type="checkbox" checked={gridEnabled} onChange={(e) => setGridEnabled(e.target.checked)} disabled={!snapEnabled} />
            grid
          </label>
          <label className="small row" style={{ gap: 6 }}>
            <span style={{ opacity: 0.8 }}>grid</span>
            <input
              type="number"
              value={gridSize}
              min={2}
              max={200}
              step={1}
              onChange={(e) => setGridSize(Number(e.target.value || 10))}
              style={{ width: 70 }}
              disabled={!snapEnabled || !gridEnabled}
            />
          </label>

          <label className="small row" style={{ gap: 6 }}>
            <input
              type="checkbox"
              checked={editingMask}
              disabled={!canEditMask || primary?.type !== "image"}
              onChange={(e) => onEditingMaskChange(e.target.checked)}
            />
            edit mask
          </label>
          <button
            className="secondary"
            onClick={() => {
              onSelect([]);
              onEditingMaskChange(false);
            }}
          >
            Deselect
          </button>
        </div>
      </div>

      <div
        ref={stageRef}
        className="overlay-stage"
        tabIndex={0}
        onKeyDown={onStageKeyDown}
        style={{
          width: "100%",
          maxWidth: 820,
          aspectRatio: `${width}/${height}`,
          position: "relative",
          borderRadius: 14,
          border: "1px solid rgba(255,255,255,0.12)",
          overflow: "hidden",
          background:
            "linear-gradient(45deg, rgba(255,255,255,0.06) 25%, rgba(0,0,0,0) 25%)," +
            "linear-gradient(-45deg, rgba(255,255,255,0.06) 25%, rgba(0,0,0,0) 25%)," +
            "linear-gradient(45deg, rgba(0,0,0,0) 75%, rgba(255,255,255,0.06) 75%)," +
            "linear-gradient(-45deg, rgba(0,0,0,0) 75%, rgba(255,255,255,0.06) 75%)",
          backgroundSize: "24px 24px",
          backgroundPosition: "0 0, 0 12px, 12px -12px, -12px 0px",
        }}
        onPointerMove={onMove}
        onPointerUp={endAction}
        onPointerCancel={endAction}
        onPointerLeave={endAction}
        onPointerDown={(e) => {
          (e.currentTarget as any).setPointerCapture?.(e.pointerId);
          setGuides([]);
          stageRef.current?.focus?.();
          if (editingMask) onEditingMaskChange(false);

          const p = toLocal(e);
          lastPointerRef.current = p;
          const subtractive = !!e.altKey;
          const additive = !!e.shiftKey && !subtractive;
          const prev = [...(selectedIndices || [])];
          if (!additive && !subtractive) onSelect([]);

          setAction({
            kind: "marquee",
            startX: p.x,
            startY: p.y,
            curX: p.x,
            curY: p.y,
            additive,
            subtractive,
            prev,
            moved: false,
          });
        }}
        title="Drag empty space to box-select. Shift+drag adds. Alt+drag subtracts. Shift+click toggles selection. Alt+click removes from selection. Arrow keys nudge (Shift=10px, Alt=1px). Alt+drag selected layers duplicates. Hold Ctrl to disable snapping while dragging/resizing."
      >
        {backgroundUrl ? (
          <img
            src={backgroundUrl}
            style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover", opacity: 0.95 }}
          />
        ) : null}

        {/* snapping guides */}
        {guides.map((g, i) =>
          g.axis === "x" ? (
            <div key={i} className="snap-guide x" style={{ position: "absolute", left: g.pos, top: 0, width: 1, height: "100%" }} />
          ) : (
            <div key={i} className="snap-guide y" style={{ position: "absolute", top: g.pos, left: 0, height: 1, width: "100%" }} />
          ),
        )}

        {/* marquee */}
        {marqueeRect ? (
          <div
            className="marquee"
            style={{
              position: "absolute",
              left: marqueeRect.x,
              top: marqueeRect.y,
              width: marqueeRect.w,
              height: marqueeRect.h,
              pointerEvents: "none",
              zIndex: 9995,
            }}
          />
        ) : null}

        {/* layers */}
        {layers.map((l, idx) => {
          const isSel = selectedSet.has(idx);
          const isPrimary = primaryIndex === idx;
          const rot = Number(l.rotation_deg ?? 0);
          const layerStyle: React.CSSProperties = {
            position: "absolute",
            left: l.x,
            top: l.y,
            width: l.w,
            height: l.h,
            transform: `rotate(${rot}deg)`,
            transformOrigin: "center center",
            opacity: clamp(Number(l.opacity ?? 1), 0, 1),
            zIndex: Number(l.z ?? idx),
            pointerEvents: editingMask && isPrimary ? "none" : "auto",
          };

          return (
            <div
              key={idx}
              style={layerStyle}
              className={isSel ? "overlay-box selected" : "overlay-box"}
              onPointerDown={(e) => startLayerPointer(e, idx)}
              title="Click to select. Shift+click toggles. Alt+click removes from selection. Alt+drag duplicates. Arrow keys nudge (Shift=10, Alt=1)."
            >
              {l.type === "image" ? (
                l.asset ? (
                  <img
                    src={fileUrl(`assets/overlays/${l.asset}`)}
                    style={{ width: "100%", height: "100%", objectFit: "contain" }}
                    draggable={false}
                  />
                ) : (
                  <div className="small" style={{ padding: 10 }}>
                    Select overlay asset
                  </div>
                )
              ) : (
                <div
                  style={{
                    width: "100%",
                    height: "100%",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    textAlign: "center",
                    padding: 8,
                    fontSize: Number(l.size ?? 34),
                    color: l.color ?? "#ffffff",
                    textShadow: l.stroke_width ? `0 0 ${Number(l.stroke_width)}px ${l.stroke_color ?? "#000"}` : "0 0 2px #000",
                    background: "rgba(0,0,0,0.15)",
                    borderRadius: 10,
                  }}
                >
                  {l.text || "Text"}
                </div>
              )}

              {/* selection chrome (single only) */}
              {isPrimary && (selectedIndices?.length || 0) === 1 && !editingMask ? (
                <>
                  <div className="handle rotate" onPointerDown={(e) => startLayerRotate(e, idx)} />
                  {["nw", "n", "ne", "e", "se", "s", "sw", "w"].map((hnd) => (
                    <div key={hnd} className={`handle ${hnd}`} onPointerDown={(e) => startLayerResize(e, idx, hnd)} />
                  ))}
                </>
              ) : null}
            </div>
          );
        })}

        {/* group chrome */}
        {multiBox ? (
          <div
            className="group-box selected"
            style={{
              position: "absolute",
              left: multiBox.x,
              top: multiBox.y,
              width: multiBox.w,
              height: multiBox.h,
              zIndex: 9998,
              pointerEvents: "none",
            }}
          >
            <div className="handle rotate" style={{ pointerEvents: "auto" }} onPointerDown={(e) => startLayerRotate(e, selectedIndices[0])} />
            {["nw", "n", "ne", "e", "se", "s", "sw", "w"].map((hnd) => (
              <div
                key={hnd}
                className={`handle ${hnd}`}
                style={{ pointerEvents: "auto" }}
                onPointerDown={(e) => startLayerResize(e, selectedIndices[0], hnd)}
              />
            ))}
          </div>
        ) : null}

        {/* mask editing overlay (single only) */}
        {primaryIndex != null && editingMask && canEditMask && primary?.type === "image" && primary?.mask_asset ? (
          (() => {
            const m = ensureMask(primary);
            const cx = primary.x + primary.w / 2 + m.mask_x;
            const cy = primary.y + primary.h / 2 + m.mask_y;
            const mw = primary.w * m.mask_scale;
            const mh = primary.h * m.mask_scale;

            return (
              <div
                className="mask-box selected"
                style={{
                  position: "absolute",
                  left: cx - mw / 2,
                  top: cy - mh / 2,
                  width: mw,
                  height: mh,
                  transform: `rotate(${m.mask_rotation_deg}deg)`,
                  transformOrigin: "center center",
                  zIndex: 9999,
                }}
                onPointerDown={(e) => startMaskDrag(e, primaryIndex)}
              >
                <img
                  src={fileUrl(`assets/masks/${primary.mask_asset}`)}
                  style={{ width: "100%", height: "100%", objectFit: "contain", opacity: 0.35, mixBlendMode: "screen" }}
                  draggable={false}
                />
                <div className="handle rotate" onPointerDown={(e) => startMaskRotate(e, primaryIndex)} />
                <div className="handle se" onPointerDown={(e) => startMaskResize(e, primaryIndex)} />
              </div>
            );
          })()
        ) : null}
      </div>

      {primaryIndex != null ? (
        <div className="small" style={{ marginTop: 10, opacity: 0.85 }}>
          Selected: {selectedIndices.length} • primary #{primaryIndex + 1} • t={Number(playheadS).toFixed(2)}s • auto-key={autoKey ? "on" : "off"}
        </div>
      ) : (
        <div className="small" style={{ marginTop: 10, opacity: 0.85 }}>
          Drag on empty space to box-select. Shift+drag adds. Snap-to-grid and snapping guides help align overlays.
        </div>
      )}
    </div>
  );
}
