import React, { useEffect, useRef, useState } from "react";
import {
  AudioLines,
  Clock3,
  Lock,
  Magnet,
  Music2,
  Pause,
  Play,
  Plus,
  Repeat,
  Sparkles,
  SkipBack,
  SkipForward,
  StepBack,
  StepForward,
  Unlock,
  Volume2,
  VolumeX,
} from "lucide-react";
import {
  apiFetch,
  apiGet,
  apiPost,
  getBackendUrl,
  isProjectRevisionConflict,
  type ApiError,
  type SignedProjectMediaRequest,
} from "../components/api";
import { hasProjectId, resolveProjectId } from "../components/projectSelection";
import { ProgressBar } from "../components/ProgressBar";
import {
  ProjectRevisionConflict,
  expectedRevisionBody,
  projectRevision,
  projectRevisionFromResponse,
} from "../components/ProjectRevisionConflict";
import { useOperationProgress } from "../components/useOperationProgress";
import { useStudioSession } from "../components/studioSession";
import { usePreservedMediaSource, useSignedProjectMedia } from "../hooks/useSignedProjectMedia";
import { useTimelineHistory } from "../shared/commands/useTimelineHistory";
import type { PageProps } from "../types/pageProps";

type AnyDict = Record<string, any>;

type Clip = { id: string; start_s: number; end_s: number; data: AnyDict };
type Track = { id: string; name: string; type: string; locked?: boolean; clips: Clip[] };

function rippleTrackClips(
  clips: Clip[],
  originalClips: Clip[],
  anchorIdx: number,
  boundaryS: number,
  deltaS: number,
  durationS: number,
): Clip[] {
  return clips.map((clip, idx) => {
    if (idx === anchorIdx || Number(originalClips[idx]?.start_s) < boundaryS) return clip;
    const clipDuration = Math.max(TIMELINE_MIN_RANGE_S, Number(clip.end_s) - Number(clip.start_s));
    const start = clamp(
      Number(clip.start_s) + deltaS,
      0,
      Math.max(0, durationS - clipDuration),
    );
    return { ...clip, start_s: start, end_s: start + clipDuration };
  });
}

function snapshotTimeline(timeline: AnyDict): AnyDict {
  return JSON.parse(JSON.stringify(timeline || {})) as AnyDict;
}

type Selected =
  | { kind: "track"; trackIdx: number; clipIdx: number }
  | { kind: "overlay"; layerIdx: number }
  | { kind: "camera"; kfIdx: number }
  | null;

type TimelineDrag =
  | { kind: "playhead"; canvasLeft: number }
  | {
      kind: "track";
      trackIdx: number;
      clipIdx: number;
      mode: "move" | "left" | "right";
      x0: number;
      start0: number;
      end0: number;
      data0: AnyDict;
      historyBefore: AnyDict;
      historyLabel: string;
    }
  | {
      kind: "overlay";
      layerIdx: number;
      mode: "move" | "left" | "right";
      x0: number;
      start0: number;
      end0: number;
      historyBefore: AnyDict;
      historyLabel: string;
    }
  | {
      kind: "camera";
      kfIdx: number;
      x0: number;
      t0: number;
      historyBefore: AnyDict;
      historyLabel: string;
    };

type DockSection = "orchestrator" | "inspector" | "media" | "diffusion" | "curves";
type TimelineDensity = "compact" | "comfortable";
type TimelineTimebase = "bars" | "time";
type TimelineEditTool = "select" | "blade";
type TimelineVideoCodec = "h264" | "hevc" | "prores";

type MotionPreset = {
  id: string;
  label: string;
  description: string;
  data: AnyDict;
};

const TIMELINE_MIN_ZOOM = 4;
const TIMELINE_MAX_ZOOM = 360;
const TIMELINE_MIN_RANGE_S = 0.1;

const TIMELINE_RENDER_RESOLUTIONS = [
  { value: "1280x720", label: "HD landscape · 1280 × 720" },
  { value: "1920x1080", label: "Full HD landscape · 1920 × 1080" },
  { value: "3840x2160", label: "4K UHD landscape · 3840 × 2160" },
  { value: "7680x4320", label: "8K UHD landscape · 7680 × 4320" },
  { value: "1080x1920", label: "Full HD portrait · 1080 × 1920" },
  { value: "2160x3840", label: "4K portrait · 2160 × 3840" },
  { value: "4320x7680", label: "8K portrait · 4320 × 7680" },
] as const;

const TIMELINE_RENDER_FPS_PRESETS = [23.976, 24, 25, 29.97, 30, 48, 50, 59.94, 60, 120] as const;

function sanitizeTimelineRenderNameForEditing(value: string): string {
  return String(value || "")
    .replace(/[^\p{L}\p{N} _.-]/gu, "_")
    .replace(/\s+/g, " ")
    .slice(0, 80);
}

function finalizeTimelineRenderName(value: string): string {
  return sanitizeTimelineRenderNameForEditing(value).trim().replace(/^[. ]+|[. ]+$/g, "");
}

function timelineRenderDimensionError(label: string, value: number): string {
  if (!Number.isFinite(value) || !Number.isInteger(value)) return `${label} must be a whole number.`;
  if (value < 256 || value > 7680) return `${label} must be between 256 and 7680 pixels.`;
  if (value % 2 !== 0) return `${label} must be even for video encoding.`;
  return "";
}

const MOTION_NEUTRAL_DATA: AnyDict = {
  zoom_start: 1,
  zoom_end: 1,
  pan_x_start: 0,
  pan_x_end: 0,
  pan_y_start: 0,
  pan_y_end: 0,
  rotation_start: 0,
  rotation_end: 0,
  pan_z_start: 0,
  pan_z_end: 0,
  pitch_start: 0,
  pitch_end: 0,
  yaw_start: 0,
  yaw_end: 0,
  roll_start: 0,
  roll_end: 0,
  strength: 0.35,
  cfg: 7,
  steps: 12,
};

const MOTION_PRESETS: MotionPreset[] = [
  {
    id: "gentle_push",
    label: "Gentle push",
    description: "A slow push-in with a subtle upward drift.",
    data: { ...MOTION_NEUTRAL_DATA, zoom_end: 1.08, pan_y_start: 2, pan_y_end: -2 },
  },
  {
    id: "pull_back",
    label: "Pull back",
    description: "Ease away from the subject while the frame settles downward.",
    data: { ...MOTION_NEUTRAL_DATA, zoom_start: 1.08, pan_y_start: -2, pan_y_end: 2 },
  },
  {
    id: "pan_left",
    label: "Pan left",
    description: "Travel laterally from right to left with a restrained push.",
    data: { ...MOTION_NEUTRAL_DATA, zoom_start: 1.02, zoom_end: 1.05, pan_x_start: 12, pan_x_end: -12 },
  },
  {
    id: "pan_right",
    label: "Pan right",
    description: "Travel laterally from left to right with a restrained push.",
    data: { ...MOTION_NEUTRAL_DATA, zoom_start: 1.02, zoom_end: 1.05, pan_x_start: -12, pan_x_end: 12 },
  },
  {
    id: "crane_rise",
    label: "Crane rise",
    description: "Lift through the composition with depth and a slight pitch change.",
    data: {
      ...MOTION_NEUTRAL_DATA,
      zoom_start: 1.02,
      zoom_end: 1.07,
      pan_y_start: 10,
      pan_y_end: -10,
      pan_z_end: 8,
      pitch_start: 1.5,
      pitch_end: -1.5,
    },
  },
  {
    id: "orbit_left",
    label: "Orbit left",
    description: "Arc left around the subject using pan, depth, yaw, and roll.",
    data: {
      ...MOTION_NEUTRAL_DATA,
      zoom_start: 1.02,
      zoom_end: 1.08,
      pan_x_start: 8,
      pan_x_end: -8,
      pan_y_start: 3,
      pan_y_end: -3,
      rotation_start: 1.5,
      rotation_end: -1.5,
      pan_z_start: -6,
      pan_z_end: 10,
      yaw_start: -5,
      yaw_end: 5,
      roll_start: 1,
      roll_end: -1,
    },
  },
  {
    id: "orbit_right",
    label: "Orbit right",
    description: "Arc right around the subject using pan, depth, yaw, and roll.",
    data: {
      ...MOTION_NEUTRAL_DATA,
      zoom_start: 1.02,
      zoom_end: 1.08,
      pan_x_start: -8,
      pan_x_end: 8,
      pan_y_start: -3,
      pan_y_end: 3,
      rotation_start: -1.5,
      rotation_end: 1.5,
      pan_z_start: -6,
      pan_z_end: 10,
      yaw_start: 5,
      yaw_end: -5,
      roll_start: -1,
      roll_end: 1,
    },
  },
  {
    id: "parallax_drift",
    label: "Parallax drift",
    description: "Cross the frame while changing depth for a layered parallax feel.",
    data: {
      ...MOTION_NEUTRAL_DATA,
      zoom_start: 1.01,
      zoom_end: 1.06,
      pan_x_start: -10,
      pan_x_end: 10,
      pan_y_start: 5,
      pan_y_end: -5,
      pan_z_start: -12,
      pan_z_end: 14,
      pitch_start: 1,
      pitch_end: -1,
      yaw_start: -2.5,
      yaw_end: 2.5,
    },
  },
  {
    id: "dutch_sweep",
    label: "Dutch sweep",
    description: "Sweep across the frame while rolling through a controlled Dutch angle.",
    data: {
      ...MOTION_NEUTRAL_DATA,
      zoom_start: 1.01,
      zoom_end: 1.05,
      pan_x_start: -4,
      pan_x_end: 4,
      rotation_start: -3,
      rotation_end: 3,
      roll_start: -1.5,
      roll_end: 1.5,
    },
  },
];

const MOTION_PRESET_BY_ID = new Map(MOTION_PRESETS.map((preset) => [preset.id, preset]));
const MOTION_VARIETY_PRESETS = ["pan_right", "orbit_left", "crane_rise", "pan_left", "orbit_right", "parallax_drift"];

const MOTION_CAMERA_ENDPOINT_FIELDS = new Set([
  "zoom_start",
  "zoom_end",
  "pan_x_start",
  "pan_x_end",
  "pan_y_start",
  "pan_y_end",
  "rotation_start",
  "rotation_end",
  "pan_z_start",
  "pan_z_end",
  "pitch_start",
  "pitch_end",
  "yaw_start",
  "yaw_end",
  "roll_start",
  "roll_end",
]);

const MOTION_SCHEDULE_GROUPS: Record<string, string[]> = {
  zoom: ["zoom", "zoom_schedule"],
  pan_x: ["translation_x", "pan_x", "pan_x_schedule"],
  pan_y: ["translation_y", "pan_y", "pan_y_schedule"],
  pan_z: ["translation_z", "pan_z", "pan_z_schedule"],
  rotation: ["angle", "rotation_deg", "rotation_schedule", "rotation_z_schedule"],
  pitch: ["rotation_3d_x", "pitch", "rotation_x_schedule"],
  yaw: ["rotation_3d_y", "yaw", "rotation_y_schedule"],
  roll: ["rotation_3d_z", "roll", "rotation_3d_z_schedule"],
};

const ALL_MOTION_CAMERA_SCHEDULE_KEYS = Array.from(new Set(Object.values(MOTION_SCHEDULE_GROUPS).flat()));

function clamp(n: number, a: number, b: number) {
  return Math.max(a, Math.min(b, n));
}

function hasScheduleValue(data: AnyDict, keys: string[]): boolean {
  return keys.some((key) => {
    const value = data?.[key];
    return (typeof value === "string" && value.trim().length > 0) || (value && typeof value === "object");
  });
}

function hasMotionSchedules(data: AnyDict): boolean {
  if (hasScheduleValue(data, ALL_MOTION_CAMERA_SCHEDULE_KEYS)) return true;
  const nested = data?.motion_schedules;
  return Boolean(nested && typeof nested === "object" && hasScheduleValue(nested, ALL_MOTION_CAMERA_SCHEDULE_KEYS));
}

function hasAxisSchedule(data: AnyDict, keys: string[]): boolean {
  if (hasScheduleValue(data, keys)) return true;
  const nested = data?.motion_schedules;
  return Boolean(nested && typeof nested === "object" && hasScheduleValue(nested, keys));
}

function endpointMoves(data: AnyDict, startKey: string, endKey: string, fallback: number): boolean {
  const start = Number(data?.[startKey] ?? fallback);
  const end = Number(data?.[endKey] ?? start);
  return Number.isFinite(start) && Number.isFinite(end) && Math.abs(end - start) > 0.001;
}

function motionAxes(data: AnyDict): string[] {
  const axes: string[] = [];
  if (endpointMoves(data, "zoom_start", "zoom_end", 1) || hasAxisSchedule(data, MOTION_SCHEDULE_GROUPS.zoom)) {
    axes.push("zoom");
  }
  if (
    endpointMoves(data, "pan_x_start", "pan_x_end", 0) ||
    endpointMoves(data, "pan_y_start", "pan_y_end", 0) ||
    hasAxisSchedule(data, [...MOTION_SCHEDULE_GROUPS.pan_x, ...MOTION_SCHEDULE_GROUPS.pan_y])
  ) {
    axes.push("pan");
  }
  if (endpointMoves(data, "rotation_start", "rotation_end", 0) || hasAxisSchedule(data, MOTION_SCHEDULE_GROUPS.rotation)) {
    axes.push("rotate");
  }
  if (endpointMoves(data, "pan_z_start", "pan_z_end", 0) || hasAxisSchedule(data, MOTION_SCHEDULE_GROUPS.pan_z)) {
    axes.push("depth");
  }
  if (
    endpointMoves(data, "pitch_start", "pitch_end", 0) ||
    endpointMoves(data, "yaw_start", "yaw_end", 0) ||
    endpointMoves(data, "roll_start", "roll_end", 0) ||
    hasAxisSchedule(data, [
      ...MOTION_SCHEDULE_GROUPS.pitch,
      ...MOTION_SCHEDULE_GROUPS.yaw,
      ...MOTION_SCHEDULE_GROUPS.roll,
    ])
  ) {
    axes.push("3D orbit");
  }
  return axes;
}

function motionPresetSelection(data: AnyDict): string {
  const id = String(data?.motion_preset || "");
  if (MOTION_PRESET_BY_ID.has(id)) return id;
  return hasMotionSchedules(data) ? "reactive" : "custom";
}

function fmtMotionLabel(data: AnyDict): string {
  const axes = motionAxes(data);
  const preset = MOTION_PRESET_BY_ID.get(String(data?.motion_preset || ""));
  let name = String(data?.motion_label || preset?.label || "").trim();
  if (!name && hasMotionSchedules(data)) name = "Audio reactive";
  if (!name && axes.includes("3D orbit")) name = "3D orbit";
  if (!name && axes.includes("pan")) {
    const dx = Number(data?.pan_x_end ?? 0) - Number(data?.pan_x_start ?? 0);
    const dy = Number(data?.pan_y_end ?? 0) - Number(data?.pan_y_start ?? 0);
    name = Math.abs(dx) >= Math.abs(dy) ? (dx >= 0 ? "Pan right" : "Pan left") : dy < 0 ? "Crane rise" : "Crane drop";
  }
  if (!name && axes.includes("zoom")) {
    name = Number(data?.zoom_end ?? 1) >= Number(data?.zoom_start ?? 1) ? "Push in" : "Pull back";
  }
  if (!name && axes.includes("rotate")) name = "Rotation sweep";
  if (!name && axes.includes("depth")) name = "Depth move";
  if (!name) name = "Static hold";
  return axes.length ? `${name} · ${axes.join(" + ")}` : name;
}

function clearMotionSchedules(data: AnyDict, field?: string): AnyDict {
  const group = field ? Object.keys(MOTION_SCHEDULE_GROUPS).find((key) => field.startsWith(key)) : null;
  const keys = group ? MOTION_SCHEDULE_GROUPS[group] : ALL_MOTION_CAMERA_SCHEDULE_KEYS;
  const patch: AnyDict = {};
  for (const key of keys) patch[key] = null;
  if (data?.motion_schedules && typeof data.motion_schedules === "object") {
    const nested = { ...data.motion_schedules };
    for (const key of keys) delete nested[key];
    patch.motion_schedules = nested;
  }
  return patch;
}

function motionCameraValuesAt(data: AnyDict, progress: number): AnyDict {
  const mappings: Array<[string, string, string]> = [
    ["zoom", "zoom_start", "zoom_end"],
    ["pan_x", "pan_x_start", "pan_x_end"],
    ["pan_y", "pan_y_start", "pan_y_end"],
    ["rotation_deg", "rotation_start", "rotation_end"],
    ["translation_z", "pan_z_start", "pan_z_end"],
    ["rotation_3d_x", "pitch_start", "pitch_end"],
    ["rotation_3d_y", "yaw_start", "yaw_end"],
    ["rotation_3d_z", "roll_start", "roll_end"],
  ];
  const out: AnyDict = {};
  const u = clamp(progress, 0, 1);
  for (const [cameraField, startField, endField] of mappings) {
    const rawStart = data?.[startField];
    const rawEnd = data?.[endField];
    if ((rawStart == null || rawStart === "") && (rawEnd == null || rawEnd === "")) continue;
    const start = Number(rawStart ?? rawEnd);
    const end = Number(rawEnd ?? rawStart);
    if (Number.isFinite(start) && Number.isFinite(end)) out[cameraField] = start + (end - start) * u;
  }
  return out;
}

function syncMotionClipCameraKeyframes(keyframes: AnyDict[], clip: Clip, data: AnyDict): AnyDict[] {
  const start = Number(clip.start_s || 0);
  const end = Math.max(start, Number(clip.end_s || start));
  const duration = Math.max(0.001, end - start);
  const next = (Array.isArray(keyframes) ? keyframes : []).map((point) => {
    const time = Number(point?.t || 0);
    if (time < start - 0.001 || time > end + 0.001) return { ...point };
    return { ...point, ...motionCameraValuesAt(data, (time - start) / duration) };
  });
  for (const [time, progress] of [[start, 0], [end, 1]] as Array<[number, number]>) {
    const values = motionCameraValuesAt(data, progress);
    if (!Object.keys(values).length) continue;
    const index = next.findIndex((point) => Math.abs(Number(point?.t || 0) - time) < 0.001);
    if (index >= 0) next[index] = { ...next[index], ...values, t: time };
    else next.push({ t: time, ...values });
  }
  return next.sort((a, b) => Number(a?.t || 0) - Number(b?.t || 0));
}

function fmtTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00.0";
  const mins = Math.floor(seconds / 60);
  const secs = seconds - mins * 60;
  return `${mins}:${secs.toFixed(1).padStart(4, "0")}`;
}

function parseDeforumSchedule(s: string): Array<[number, number]> {
  const out: Array<[number, number]> = [];
  const parts = String(s || "").split(",");
  for (const p of parts) {
    const part = p.trim();
    if (!part) continue;
    const m = part.match(/^(\d+)\s*:\s*\(?\s*([-+]?\d*\.?\d+)\s*\)?$/);
    if (!m) continue;
    out.push([Number(m[1]), Number(m[2])]);
  }
  out.sort((a, b) => a[0] - b[0]);
  // de-dup (last wins)
  const dedup: Record<string, number> = {};
  for (const [f, v] of out) dedup[String(f)] = v;
  return Object.entries(dedup)
    .map(([k, v]) => [Number(k), Number(v)] as [number, number])
    .sort((a, b) => a[0] - b[0]);
}

function evalSchedule(pairs: Array<[number, number]>, frame: number): number | null {
  if (!pairs.length) return null;
  const f = Math.round(frame);
  if (f <= pairs[0][0]) return pairs[0][1];
  if (f >= pairs[pairs.length - 1][0]) return pairs[pairs.length - 1][1];
  for (let i = 0; i < pairs.length - 1; i++) {
    const [a, av] = pairs[i];
    const [b, bv] = pairs[i + 1];
    if (a <= f && f <= b) {
      const w = (f - a) / Math.max(1e-9, b - a);
      return av * (1 - w) + bv * w;
    }
  }
  return pairs[pairs.length - 1][1];
}

function upsertPoint(s: string, frame: number, value: number): string {
  const pairs = parseDeforumSchedule(s);
  const map: Record<string, number> = {};
  for (const [f, v] of pairs) map[String(f)] = v;
  map[String(Math.max(0, Math.round(frame)))] = Number(value);
  const next = Object.entries(map)
    .map(([k, v]) => [Number(k), Number(v)] as [number, number])
    .sort((a, b) => a[0] - b[0]);
  return next.map(([f, v]) => `${f}:(${Number(v).toFixed(4)})`).join(", ");
}

function sampleCurve(
  pairs: Array<[number, number]>,
  durationSOrOptions:
    | number
    | { durationS: number; fps: number; samples: number; fallback: number },
  fpsArg?: number,
  samplesArg?: number,
  fallbackArg?: number,
): Array<[number, number]> {
  const opts =
    typeof durationSOrOptions === "object"
      ? durationSOrOptions
      : {
          durationS: durationSOrOptions,
          fps: Number(fpsArg || 0),
          samples: Number(samplesArg || 0),
          fallback: Number(fallbackArg ?? 0),
        };
  const durationS = Number(opts.durationS || 0);
  const fps = Number(opts.fps || 0);
  const samples = Number(opts.samples || 0);
  const fallback = Number(opts.fallback ?? 0);

  const out: Array<[number, number]> = [];
  const n = Math.max(8, samples | 0);
  for (let i = 0; i < n; i++) {
    const u = i / Math.max(1, n - 1);
    const t = u * durationS;
    const f = t * fps;
    const v = evalSchedule(pairs, f);
    out.push([t, v == null ? fallback : v]);
  }
  return out;
}

function svgPath(
  points: Array<[number, number]>,
  xMax: number,
  yMin: number,
  yMax: number,
  w: number,
  h: number,
): string {
  if (!points.length) return "";
  const clampY = (v: number) => {
    const u = (v - yMin) / Math.max(1e-9, yMax - yMin);
    return h - clamp(u, 0, 1) * h;
  };
  const X = (t: number) => clamp(t / Math.max(1e-9, xMax), 0, 1) * w;
  let d = `M ${X(points[0][0]).toFixed(2)} ${clampY(points[0][1]).toFixed(2)}`;
  for (const [t, v] of points.slice(1)) d += ` L ${X(t).toFixed(2)} ${clampY(v).toFixed(2)}`;
  return d;
}

function ensureTimelineShape(timeline: AnyDict, planVariant: AnyDict | null): AnyDict {
  const tl = timeline && typeof timeline === "object" ? { ...timeline } : {};
  const scenes: AnyDict[] =
    planVariant?.scenes && Array.isArray(planVariant.scenes) ? planVariant.scenes : [];

  const ensureTrack = (id: string, name: string, type: string, clips: Clip[]) => {
    tl.tracks = Array.isArray(tl.tracks) ? [...tl.tracks] : [];
    const idx = tl.tracks.findIndex(
      (t: AnyDict) => String(t?.type || "").toLowerCase() === type.toLowerCase(),
    );
    if (idx >= 0) {
      const cur = tl.tracks[idx] || {};
      tl.tracks[idx] = {
        id: cur.id || id,
        name: cur.name || name,
        type,
        clips: Array.isArray(cur.clips) ? cur.clips : clips,
      };
    } else {
      tl.tracks.push({ id, name, type, clips });
    }
  };

  // Prompt track from plan scenes (if not present)
  const promptClips: Clip[] = scenes.map((s: AnyDict, i: number) => ({
    id: String(s.id || `scene_${i}`),
    start_s: Number(s.start_s || i * 5),
    end_s: Number(s.end_s || Number(s.start_s || i * 5) + 5),
    data: { prompt: String(s.prompt || "").trim() || "cinematic" },
  }));
  ensureTrack("track_prompt", "Prompts", "prompt", promptClips);

  // Motion track: basic camera automation (if not present)
  const motionClips: Clip[] = scenes.map((s: AnyDict, i: number) => ({
    id: String(`motion_${s.id || i}`),
    start_s: Number(s.start_s || i * 5),
    end_s: Number(s.end_s || Number(s.start_s || i * 5) + 5),
    data: {
      ...MOTION_PRESETS[0].data,
      motion_preset: MOTION_PRESETS[0].id,
      motion_label: MOTION_PRESETS[0].label,
    },
  }));
  ensureTrack("track_motion", "Motion", "motion", motionClips);

  // Canonical non-destructive edit-decision lane. Source media remains in
  // Outputs; Timeline clips only keep source in/out and finishing decisions.
  ensureTrack("track_video", "Video Edit", "video", []);

  // Overlays/layers (kept as timeline.layers to match compositor)
  tl.layers = Array.isArray(tl.layers) ? tl.layers : [];

  // Camera keyframes (optional)
  tl.camera = tl.camera && typeof tl.camera === "object" ? { ...tl.camera } : {};
  tl.camera.keyframes = Array.isArray(tl.camera.keyframes) ? tl.camera.keyframes : [];

  return tl;
}

function projectAudioPeaks(project: AnyDict | null): number[] {
  const analysis = project?.meta?.analysis || {};
  const candidates = [
    analysis.waveform,
    analysis.features?.waveform,
    analysis.features?.energy_curve,
    project?.meta?.last_reactive_lab?.waveform,
  ];
  const waveform = candidates.find((value) => Array.isArray(value)) as unknown[] | undefined;
  if (!waveform?.length) return [];
  const values = waveform.map(Number).filter(Number.isFinite);
  const maximum = Math.max(1, ...values.map((value) => Math.abs(value)));
  return values.map((value) => clamp(Math.abs(value) / maximum, 0, 1));
}

function fmtLabel(trackType: string, clip: Clip): string {
  const t = String(trackType || "").toLowerCase();
  if (t === "prompt") return String(clip?.data?.prompt || "prompt").slice(0, 34);
  if (t === "motion") return fmtMotionLabel(clip?.data || {});
  if (t === "video") {
    const source = String(clip?.data?.source_path || clip?.data?.name || "video");
    return source.split(/[\\/]/).pop() || "video";
  }
  return String(clip?.id || "clip");
}

function isTimelineInteractiveTarget(target: EventTarget | null): boolean {
  const element = target instanceof Element ? target : null;
  return Boolean(
    element?.closest(
      "input, textarea, select, button, a, [contenteditable='true'], [role='button'], [role='dialog'], [role='menu']",
    ),
  );
}

export default function Timeline({ backendUrl: backendUrlProp, onNavigate }: PageProps) {
  const {
    projectId: sessionProjectId,
    setProjectId,
    selectedVariant,
    setSelectedVariant,
    lastHandoff,
    clearHandoff,
  } = useStudioSession();
  const backendUrl = backendUrlProp || getBackendUrl();

  const [projects, setProjects] = useState<any[]>([]);
  const [project, setProject] = useState<any>(null);
  const [projectsReady, setProjectsReady] = useState(false);

  const [plan, setPlan] = useState<any>(null);

  const [timeline, setTimeline] = useState<AnyDict>({});
  const [timelineDirty, setTimelineDirty] = useState(false);
  const [recoveryNotice, setRecoveryNotice] = useState<string | null>(null);
  const [recoveryBusy, setRecoveryBusy] = useState(false);
  const timelineRef = useRef<AnyDict>(timeline);
  const projectRevisionRef = useRef<number | null>(null);
  const [revisionConflict, setRevisionConflict] = useState<ApiError | null>(null);

  const [durationS, setDurationS] = useState<number>(60);
  const [pxPerSecond, setPxPerSecond] = useState<number>(80);
  const [playheadS, setPlayheadS] = useState<number>(0);

  const [quantizeBeats, setQuantizeBeats] = useState<number>(1);
  const [bpmOverride, setBpmOverride] = useState<number | null>(null);
  const [snapEnabled, setSnapEnabled] = useState<boolean>(true);
  const [timelineTimebase, setTimelineTimebase] = useState<TimelineTimebase>("bars");
  const [loopEnabled, setLoopEnabled] = useState<boolean>(false);
  const [locatorInS, setLocatorInS] = useState<number>(0);
  const [locatorOutS, setLocatorOutS] = useState<number>(5);
  const [lockedLaneIds, setLockedLaneIds] = useState<string[]>([]);
  const [editTool, setEditTool] = useState<TimelineEditTool>("select");
  const [rippleEnabled, setRippleEnabled] = useState<boolean>(false);

  const [selected, setSelected] = useState<Selected>(null);

  const [isPlaying, setIsPlaying] = useState(false);
  const [masterVolume, setMasterVolume] = useState(0.85);
  const [masterMuted, setMasterMuted] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const timelineScrollRef = useRef<HTMLDivElement | null>(null);

  const [peaks, setPeaks] = useState<number[]>([]);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  const [previewQuery, setPreviewQuery] = useState<Record<string, unknown> | null>(null);
  const previewTimer = useRef<any>(null);
  const autoFitKeyRef = useRef<string>("");

  const [mediaLibrary, setMediaLibrary] = useState<AnyDict[]>([]);
  const [mediaLibraryBusy, setMediaLibraryBusy] = useState(false);
  const [selectedMediaPath, setSelectedMediaPath] = useState("");
  const [selectedMediaDuration, setSelectedMediaDuration] = useState(5);
  const [mediaRenderBusy, setMediaRenderBusy] = useState(false);
  const [mediaRenderStatus, setMediaRenderStatus] = useState("");
  const [editOutputName, setEditOutputName] = useState("edited-master");
  const [editWidth, setEditWidth] = useState(1920);
  const [editHeight, setEditHeight] = useState(1080);
  const [editFps, setEditFps] = useState(24);
  const [editVideoCodec, setEditVideoCodec] = useState<TimelineVideoCodec>("h264");
  const [editQuality, setEditQuality] = useState(18);
  const editOutputProjectRef = useRef("");
  const [installedPreviewModels, setInstalledPreviewModels] = useState<Array<{ id: string; name: string }>>([]);
  const [conductorPlan, setConductorPlan] = useState<AnyDict | null>(null);

  const [diffUrl, setDiffUrl] = useState<string>("");
  const [diffBusy, setDiffBusy] = useState(false);
  const [diffStart, setDiffStart] = useState<number>(0);
  const [diffEnd, setDiffEnd] = useState<number>(2);
  const [diffFps, setDiffFps] = useState<number>(2);
  const [diffSteps, setDiffSteps] = useState<number>(6);
  const [diffCfg, setDiffCfg] = useState<number>(7.0);
  const [diffStrength, setDiffStrength] = useState<number>(0.45);
  const [diffW, setDiffW] = useState<number>(512);
  const [diffH, setDiffH] = useState<number>(512);
  const [diffModel, setDiffModel] = useState<string>("auto");
  const [dockSection, setDockSection] = useState<DockSection>("orchestrator");
  const [timelineDensity, setTimelineDensity] = useState<TimelineDensity>("compact");
  const [showReferenceLanes, setShowReferenceLanes] = useState(true);
  const [showAutomationLanes, setShowAutomationLanes] = useState(true);

  const [err, setErr] = useState<string | null>(null);
  const { progress, runOperation } = useOperationProgress();
  const timelineHistory = useTimelineHistory();
  const projectId = projectsReady && hasProjectId(projects, sessionProjectId) ? sessionProjectId : "";
  const selectedTimelineTrack = selected?.kind === "track"
    ? (Array.isArray(timeline?.tracks) ? timeline.tracks : [])[selected.trackIdx]
    : null;
  const selectedTimelineClip = selected?.kind === "track"
    ? selectedTimelineTrack?.clips?.[selected.clipIdx]
    : null;
  const activeTimelineVideoPath = selectedTimelineTrack
    && String(selectedTimelineTrack.type || "").toLowerCase() === "video"
    ? String(selectedTimelineClip?.data?.source_path || "")
    : selectedMediaPath;
  const audioPath = String(project?.meta?.audio?.filename || "").trim();
  const audioRequest: SignedProjectMediaRequest | null = audioPath
    ? { purpose: "audio", path: audioPath }
    : null;
  const previewRequest: SignedProjectMediaRequest | null = previewQuery
    ? { purpose: "preview", query: previewQuery }
    : null;
  const activeVideoRequest: SignedProjectMediaRequest | null = activeTimelineVideoPath
    ? { purpose: "file", path: activeTimelineVideoPath }
    : null;
  const signedMediaRequests = [audioRequest, previewRequest, activeVideoRequest]
    .filter((request): request is SignedProjectMediaRequest => request != null);
  const signedMedia = useSignedProjectMedia(projectId, signedMediaRequests, backendUrl);
  const audioUrl = signedMedia.urlFor(audioRequest);
  const previewUrl = signedMedia.urlFor(previewRequest);
  const activeTimelineVideoUrl = signedMedia.urlFor(activeVideoRequest);
  const activeVideoRef = useRef<HTMLVideoElement | null>(null);
  usePreservedMediaSource(audioRef, audioUrl);
  usePreservedMediaSource(activeVideoRef, activeTimelineVideoUrl);

  const withExpectedRevision = <T extends AnyDict,>(body: T): T & { expected_revision?: number } =>
    expectedRevisionBody(body, { revision: projectRevisionRef.current });

  const setRevisionFromResponse = (response: unknown) => {
    const revision = projectRevisionFromResponse(response);
    if (revision == null) return;
    projectRevisionRef.current = revision;
    setProject((current: AnyDict | null) => current ? { ...current, revision } : current);
  };

  const reportMutationError = (error: unknown) => {
    if (isProjectRevisionConflict(error)) setRevisionConflict(error);
  };

  const replaceTimelineState = (next: AnyDict, dirty: boolean) => {
    timelineRef.current = next;
    setTimeline(next);
    setTimelineDirty(dirty);
  };

  const commitTimeline = (next: AnyDict, label = "edit") => {
    timelineHistory.push(timelineRef.current, next, label);
    replaceTimelineState(next, true);
  };

  const undoTimeline = () => {
    const previous = timelineHistory.undo();
    if (previous) replaceTimelineState(ensureTimelineShape(previous, null), true);
  };

  const redoTimeline = () => {
    const next = timelineHistory.redo();
    if (next) replaceTimelineState(ensureTimelineShape(next, null), true);
  };

  const refreshProjects = async () => {
    const d = await apiGet("/v1/projects");
    const nextProjects = Array.isArray(d?.projects) ? d.projects : [];
    setProjects(nextProjects);
    setProjectsReady(true);
    const nextProjectId = resolveProjectId(nextProjects, sessionProjectId);
    if (nextProjectId !== sessionProjectId) setProjectId(nextProjectId);
    if (!nextProjectId) {
      projectRevisionRef.current = null;
      setRevisionConflict(null);
      setProject(null);
      setPlan(null);
      replaceTimelineState(ensureTimelineShape({}, null), false);
    }
  };

  const refreshTimelineResources = async (pid: string) => {
    setMediaLibraryBusy(true);
    try {
      const [outputs, modelData] = await Promise.all([
        apiGet(`/v1/projects/${pid}/outputs`).catch(() => ({ videos: [] })),
        apiGet("/v1/models/catalog").catch(() => ({ catalog: [], user: [], installed: {} })),
      ]);
      setMediaLibrary(Array.isArray(outputs?.videos) ? outputs.videos : []);

      const installed =
        modelData?.installed && typeof modelData.installed === "object" ? modelData.installed : {};
      const supportedPreviewIds = new Set([
        "hf_sd15_internal",
        "hf_sdxl_internal",
        "hf_sd35_medium_internal",
      ]);
      const catalog = [
        ...(Array.isArray(modelData?.catalog) ? modelData.catalog : []),
        ...(Array.isArray(modelData?.user) ? modelData.user : []),
      ];
      const downloaded = catalog
        .filter((entry: AnyDict) => supportedPreviewIds.has(String(entry?.id || "")) && installed[entry.id] === true)
        .map((entry: AnyDict) => ({ id: String(entry.id), name: String(entry.name || entry.id) }));
      setInstalledPreviewModels(downloaded);
      setDiffModel((current) =>
        current === "auto" || downloaded.some((entry) => entry.id === current) ? current : "auto",
      );
    } finally {
      setMediaLibraryBusy(false);
    }
  };

  const refreshProject = async (pid: string) => {
    const d = await apiGet(`/v1/projects/${encodeURIComponent(pid)}`);
    const loadedProject = d?.project || null;
    projectRevisionRef.current = projectRevision(loadedProject);
    setRevisionConflict(null);
    setProject(loadedProject);
    const p = d?.project || {};
    if (editOutputProjectRef.current !== pid) {
      editOutputProjectRef.current = pid;
      setEditOutputName(
        finalizeTimelineRenderName(`${String(p?.name || "studio")}_edit`) || "edited-master",
      );
    }
    const tl = ensureTimelineShape(
      p?.meta?.timeline || {},
      (p?.meta?.last_plan?.variants || [])[selectedVariant] || null,
    );
    replaceTimelineState(tl, false);
    timelineHistory.clear();

    const dur = Number(
      p?.meta?.audio?.duration_s || p?.meta?.analysis?.features?.duration_s || p?.duration_s || 60,
    );
    setDurationS(Number.isFinite(dur) && dur > 0 ? dur : 60);

    setPlan(p?.meta?.last_plan || null);
    setConductorPlan(
      p?.meta?.last_conductor_plan && typeof p.meta.last_conductor_plan === "object"
        ? p.meta.last_conductor_plan
        : null,
    );
    const variantCount = Array.isArray(p?.meta?.last_plan?.variants) ? p.meta.last_plan.variants.length : 0;
    if (variantCount > 0 && selectedVariant > variantCount - 1) setSelectedVariant(0);

    try {
      const recovery = await apiGet(`/v1/projects/${pid}/recovery`);
      if (recovery?.needs_recovery) {
        const when = recovery?.candidates?.[0]?.saved_at || "recent session";
        setRecoveryNotice(`Unsaved autosave found from ${when}. Restore it or discard.`);
      } else {
        setRecoveryNotice(null);
      }
    } catch {
      setRecoveryNotice(null);
    }
    void refreshTimelineResources(pid);
  };

  useEffect(() => {
    refreshProjects().catch(() => {});
  }, [backendUrl]);
  useEffect(() => {
    if (!projectsReady) return;
    if (projectId) refreshProject(projectId).catch(() => {});
    else {
      projectRevisionRef.current = null;
      setRevisionConflict(null);
      setProject(null);
      setPlan(null);
      replaceTimelineState(ensureTimelineShape({}, null), false);
    }
  }, [backendUrl, projectId, projectsReady, selectedVariant]);

  useEffect(() => {
    setPeaks(projectAudioPeaks(project));
  }, [project]);

  useEffect(() => {
    setIsPlaying(false);
  }, [audioUrl]);

  useEffect(() => {
    setLockedLaneIds([]);
  }, [projectId]);

  useEffect(() => () => {
    if (diffUrl.startsWith("blob:")) URL.revokeObjectURL(diffUrl);
  }, [diffUrl]);

  useEffect(() => {
    if (!projectId || !timelineDirty) return;
    const timer = window.setTimeout(() => {
      apiPost(`/v1/projects/${encodeURIComponent(projectId)}/autosave`, withExpectedRevision({
        timeline,
        reason: "timeline_dirty",
      }))
        .then(setRevisionFromResponse)
        .catch(reportMutationError);
    }, 1500);
    return () => window.clearTimeout(timer);
  }, [projectId, timeline, timelineDirty]);

  useEffect(() => {
    const audioEl = audioRef.current;
    if (!audioEl) return;
    audioEl.volume = clamp(masterVolume, 0, 1);
    audioEl.muted = masterMuted;
  }, [audioUrl, masterMuted, masterVolume]);

  useEffect(() => {
    const audioEl = audioRef.current;
    if (!audioEl) return;

    const handlePlay = () => setIsPlaying(true);
    const handlePause = () => setIsPlaying(false);
    const handleEnded = () => setIsPlaying(false);
    const handleTimeUpdate = () => {
      const currentTime = audioEl.currentTime || 0;
      if (loopEnabled && locatorOutS > locatorInS + TIMELINE_MIN_RANGE_S && currentTime >= locatorOutS) {
        audioEl.currentTime = locatorInS;
        setPlayheadS(locatorInS);
        if (!audioEl.paused) audioEl.play().catch(() => {});
        return;
      }
      setPlayheadS(currentTime);
    };

    audioEl.addEventListener("play", handlePlay);
    audioEl.addEventListener("pause", handlePause);
    audioEl.addEventListener("ended", handleEnded);
    audioEl.addEventListener("timeupdate", handleTimeUpdate);

    return () => {
      audioEl.removeEventListener("play", handlePlay);
      audioEl.removeEventListener("pause", handlePause);
      audioEl.removeEventListener("ended", handleEnded);
      audioEl.removeEventListener("timeupdate", handleTimeUpdate);
    };
  }, [audioUrl, loopEnabled, locatorInS, locatorOutS]);

  // draw waveform
  useEffect(() => {
    const c = canvasRef.current;
    if (!c || !peaks.length) return;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    const w = c.width,
      h = c.height;
    ctx.clearRect(0, 0, w, h);
    const gradient = ctx.createLinearGradient(0, 0, 0, h);
    gradient.addColorStop(0, "rgba(122, 211, 255, 0.9)");
    gradient.addColorStop(0.5, "rgba(67, 167, 219, 0.72)");
    gradient.addColorStop(1, "rgba(34, 105, 148, 0.42)");
    ctx.fillStyle = gradient;
    const n = peaks.length;
    for (let i = 0; i < n; i++) {
      const x = Math.floor((i / n) * w);
      const nextX = Math.ceil(((i + 1) / n) * w);
      const ph = Math.max(1, Math.floor(peaks[i] * (h - 12)));
      ctx.fillRect(x, Math.floor((h - ph) / 2), Math.max(1, nextX - x), ph);
    }
    ctx.fillStyle = "rgba(177, 229, 255, 0.34)";
    ctx.fillRect(0, Math.floor(h / 2), w, 1);
  }, [peaks, durationS, pxPerSecond, showReferenceLanes]);

  // scrub preview frame
  useEffect(() => {
    if (!projectId) return;
    // During playback the playhead updates several times per second; refreshing
    // the cached program-monitor frame on every tick floods the backend and
    // causes jank. The monitor is most useful while paused/scrubbing, so skip
    // refreshes while audio is playing and update once playback stops.
    if (isPlaying) return;
    if (previewTimer.current) clearTimeout(previewTimer.current);
    previewTimer.current = setTimeout(() => {
      setPreviewQuery({ t: playheadS, w: 768, h: 432 });
    }, 60);
    return () => {
      if (previewTimer.current) clearTimeout(previewTimer.current);
    };
  }, [projectId, playheadS, isPlaying]);

  const tracks: Track[] = Array.isArray(timeline?.tracks) ? timeline.tracks : [];
  const layers: AnyDict[] = Array.isArray(timeline?.layers) ? timeline.layers : [];
  const camKeyframes: AnyDict[] = Array.isArray(timeline?.camera?.keyframes)
    ? timeline.camera.keyframes
    : [];

  const editAudioCodec = editVideoCodec === "prores" ? "pcm_s16le" : "aac";
  const editOutputSafeName = finalizeTimelineRenderName(editOutputName);
  const editWidthError = timelineRenderDimensionError("Width", editWidth);
  const editHeightError = timelineRenderDimensionError("Height", editHeight);
  const editFpsError =
    !Number.isFinite(editFps) || editFps < 1 || editFps > 120
      ? "FPS must be between 1 and 120. Decimal frame rates are supported."
      : "";
  const editQualityError = editVideoCodec !== "prores"
    && (!Number.isFinite(editQuality) || !Number.isInteger(editQuality) || editQuality < 1 || editQuality > 51)
      ? "CRF must be a whole number between 1 and 51."
      : "";
  const editNameError = editOutputSafeName ? "" : "Enter a filename-safe output name.";
  const editMasterValidationMessages = [
    editNameError,
    editWidthError,
    editHeightError,
    editFpsError,
    editQualityError,
  ].filter(Boolean);
  const editMasterSettingsValid = editMasterValidationMessages.length === 0;
  const editResolutionPreset = TIMELINE_RENDER_RESOLUTIONS.some(
    (preset) => preset.value === `${editWidth}x${editHeight}`,
  )
    ? `${editWidth}x${editHeight}`
    : "custom";
  const editFpsPreset = TIMELINE_RENDER_FPS_PRESETS.includes(
    editFps as (typeof TIMELINE_RENDER_FPS_PRESETS)[number],
  )
    ? String(editFps)
    : "custom";
  const editQualityPreset =
    editQuality === 18 ? "high" : editQuality === 23 ? "medium" : editQuality === 28 ? "low" : "custom";

  const laneIdForTrack = (track: Track, trackIdx: number) =>
    `track:${String(track.id || trackIdx)}`;
  const isLaneLocked = (laneId: string) => {
    if (!laneId.startsWith("track:")) return lockedLaneIds.includes(laneId);
    return tracks.some((track, trackIdx) =>
      laneIdForTrack(track, trackIdx) === laneId && track.locked === true);
  };
  const toggleLaneLock = (laneId: string) => {
    if (laneId.startsWith("track:")) {
      const nextTracks = tracks.map((track, trackIdx) =>
        laneIdForTrack(track, trackIdx) === laneId
          ? { ...track, locked: !track.locked }
          : track,
      );
      commitTimeline({ ...timelineRef.current, tracks: nextTracks }, "toggle_track_lock");
      return;
    }
    setLockedLaneIds((current) =>
      current.includes(laneId)
        ? current.filter((id) => id !== laneId)
        : [...current, laneId],
    );
  };

  const onWaveformClick = (e: React.MouseEvent) => {
    const c = canvasRef.current;
    if (!c) return;
    const rect = c.getBoundingClientRect();
    const raw = (e.clientX - rect.left) / Math.max(1, pxPerSecond);
    seekTo(snapEnabled && !e.altKey ? _snap(raw) : raw);
  };

  const clipPx = (t: number) => Math.round(t * pxPerSecond);
  const seekTo = (seconds: number, syncAudio = true) => {
    const next = clamp(Number(seconds) || 0, 0, Math.max(0, durationS));
    setPlayheadS(next);
    if (syncAudio && audioRef.current) {
      audioRef.current.currentTime = next;
    }
  };

  const setTimelineZoomWithFocus = (nextZoom: number, focusSeconds?: number) => {
    const scroller = timelineScrollRef.current;
    const clamped = clamp(nextZoom, TIMELINE_MIN_ZOOM, TIMELINE_MAX_ZOOM);
    if (!scroller) {
      setPxPerSecond(clamped);
      return;
    }

    const currentZoom = Math.max(1, pxPerSecond);
    const focus =
      focusSeconds ??
      (scroller.scrollLeft + scroller.clientWidth / 2) / currentZoom;
    setPxPerSecond(clamped);
    requestAnimationFrame(() => {
      const nextLeft = Math.max(0, focus * clamped - scroller.clientWidth / 2);
      if (typeof scroller.scrollTo === "function") {
        scroller.scrollTo({
          left: nextLeft,
          behavior: "smooth",
        });
      } else {
        scroller.scrollLeft = nextLeft;
      }
    });
  };

  const fitTimelineZoom = () => {
    const scroller = timelineScrollRef.current;
    const viewport = scroller?.clientWidth ?? 1100;
    const nextZoom = (Math.max(320, viewport) - 96) / Math.max(durationS, 8);
    setTimelineZoomWithFocus(nextZoom, 0);
  };

  useEffect(() => {
    if (!projectId || durationS <= 0) return;
    const key = `${projectId}:${selectedVariant}:${durationS.toFixed(2)}`;
    if (autoFitKeyRef.current === key) return;
    autoFitKeyRef.current = key;
    const useRaf = typeof window.requestAnimationFrame === "function";
    const handle = useRaf
      ? window.requestAnimationFrame(() => fitTimelineZoom())
      : window.setTimeout(() => fitTimelineZoom(), 0);
    return () => {
      if (useRaf) window.cancelAnimationFrame(handle);
      else window.clearTimeout(handle);
    };
  }, [projectId, selectedVariant, durationS]);

  useEffect(() => {
    if (lastHandoff && lastHandoff.projectId === projectId) setDockSection("orchestrator");
  }, [lastHandoff, projectId]);

  const onTimelineWheel = (event: React.WheelEvent<HTMLDivElement>) => {
    if (!(event.ctrlKey || event.metaKey)) return;
    const scroller = timelineScrollRef.current;
    if (!scroller) return;
    event.preventDefault();
    const rect = scroller.getBoundingClientRect();
    const focusSeconds =
      (scroller.scrollLeft + (event.clientX - rect.left)) / Math.max(1, pxPerSecond);
    const nextZoom = event.deltaY < 0 ? pxPerSecond * 1.1 : pxPerSecond / 1.1;
    setTimelineZoomWithFocus(nextZoom, focusSeconds);
  };

  const dragRef = useRef<TimelineDrag | null>(null);

  const onTrackClipPointerDown =
    (trackIdx: number, clipIdx: number, mode: "move" | "left" | "right") =>
    (e: React.PointerEvent) => {
      e.preventDefault();
      e.stopPropagation();
      const tr = tracks[trackIdx];
      const cl = tr?.clips?.[clipIdx];
      if (!cl) return;
      if (isLaneLocked(laneIdForTrack(tr, trackIdx))) return;
      if (editTool === "blade") {
        if (mode !== "move") return;
        const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
        const clipDuration = Math.max(TIMELINE_MIN_RANGE_S, cl.end_s - cl.start_s);
        const fraction = rect.width > 0
          ? clamp((e.clientX - rect.left) / rect.width, 0, 1)
          : 0.5;
        const rawCut = cl.start_s + clipDuration * fraction;
        const cut = snapEnabled && !e.altKey ? _snap(rawCut) : rawCut;
        seekTo(cut);
        setSelected({ kind: "track", trackIdx, clipIdx });
        splitTrackClipAt(trackIdx, clipIdx, cut);
        return;
      }
      (e.currentTarget as any).setPointerCapture?.(e.pointerId);
      dragRef.current = {
        kind: "track",
        trackIdx,
        clipIdx,
        mode,
        x0: e.clientX,
        start0: cl.start_s,
        end0: cl.end_s,
        data0: { ...(cl.data || {}) },
        historyBefore: snapshotTimeline(timelineRef.current),
        historyLabel: mode === "move" ? "move_clip" : "trim_clip",
      };
      setSelected({ kind: "track", trackIdx, clipIdx });
      setDiffStart(cl.start_s);
      setDiffEnd(cl.end_s);
    };

  const onOverlayPointerDown =
    (layerIdx: number, mode: "move" | "left" | "right") => (e: React.PointerEvent) => {
      e.preventDefault();
      e.stopPropagation();
      const l = layers[layerIdx];
      if (!l) return;
      if (isLaneLocked("overlays")) return;
      (e.currentTarget as any).setPointerCapture?.(e.pointerId);
      const s0 = Number(l.start_s ?? 0);
      const e0 = Number(l.end_s ?? durationS);
      dragRef.current = {
        kind: "overlay",
        layerIdx,
        mode,
        x0: e.clientX,
        start0: s0,
        end0: e0,
        historyBefore: snapshotTimeline(timelineRef.current),
        historyLabel: mode === "move" ? "move_overlay" : "trim_overlay",
      };
      setSelected({ kind: "overlay", layerIdx });
      setDiffStart(s0);
      setDiffEnd(e0);
    };

  const onCameraKfPointerDown = (kfIdx: number) => (e: React.PointerEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const k = camKeyframes[kfIdx];
    if (!k) return;
    if (isLaneLocked("camera")) return;
    (e.currentTarget as any).setPointerCapture?.(e.pointerId);
    dragRef.current = {
      kind: "camera",
      kfIdx,
      x0: e.clientX,
      t0: Number(k.t || 0),
      historyBefore: snapshotTimeline(timelineRef.current),
      historyLabel: "move_camera",
    };
    setSelected({ kind: "camera", kfIdx });
    setDiffStart(Math.max(0, Number(k.t || 0) - 1));
    setDiffEnd(Math.min(durationS, Number(k.t || 0) + 2));
  };

  const onTimelinePointerMove = (e: React.PointerEvent) => {
    const st = dragRef.current;
    if (!st) return;

    if (st.kind === "playhead") {
      const raw = (e.clientX - st.canvasLeft) / Math.max(1, pxPerSecond);
      const t = snapEnabled && !e.altKey ? _snap(raw) : raw;
      seekTo(t);
      return;
    }

    const dx = (e.clientX - st.x0) / pxPerSecond;
    const snapDragTime = (value: number) =>
      snapEnabled && !e.altKey ? _snap(value) : value;

    if (st.kind === "track") {
      const currentTimeline = timelineRef.current;
      const currentTracks = Array.isArray(currentTimeline.tracks) ? currentTimeline.tracks : [];
      const tr = currentTracks[st.trackIdx];
      const cl = tr?.clips?.[st.clipIdx];
      if (!tr || !cl) return;
      if (isLaneLocked(laneIdForTrack(tr, st.trackIdx))) return;
      let start = st.start0,
        end = st.end0;
      if (st.mode === "move") {
        const clipDuration = Math.max(TIMELINE_MIN_RANGE_S, st.end0 - st.start0);
        start = clamp(
          snapDragTime(st.start0 + dx),
          0,
          Math.max(0, durationS - clipDuration),
        );
        end = start + clipDuration;
      }
      if (st.mode === "left") {
        start = snapDragTime(st.start0 + dx);
      }
      if (st.mode === "right") {
        end = snapDragTime(st.end0 + dx);
      }
      start = clamp(start, 0, durationS - 0.05);
      end = clamp(end, start + 0.05, durationS);
      let nextData = cl.data || {};
      if (String(tr.type || "").toLowerCase() === "video" && st.mode !== "move") {
        const speed = clamp(Number(st.data0?.speed ?? 1), 0.25, 4);
        const sourceIn0 = Math.max(0, Number(st.data0?.source_in_s ?? 0));
        const sourceOut0 = Math.max(
          sourceIn0 + TIMELINE_MIN_RANGE_S,
          Number(st.data0?.source_out_s ?? sourceIn0 + (st.end0 - st.start0) * speed),
        );
        nextData = {
          ...st.data0,
          source_in_s:
            st.mode === "left"
              ? Math.max(0, sourceIn0 + (start - st.start0) * speed)
              : sourceIn0,
          source_out_s:
            st.mode === "right"
              ? Math.max(sourceIn0 + TIMELINE_MIN_RANGE_S, sourceOut0 + (end - st.end0) * speed)
              : sourceOut0,
        };
      }

      const nextTracks = currentTracks.map((t, i) => {
        if (i !== st.trackIdx) return t;
        const nextClips = (t.clips || []).map((c, j) =>
          j === st.clipIdx ? { ...c, start_s: start, end_s: end, data: nextData } : c,
        );
        return { ...t, clips: nextClips };
      });

      replaceTimelineState({ ...currentTimeline, tracks: nextTracks }, true);
      return;
    }

    if (st.kind === "overlay") {
      const currentTimeline = timelineRef.current;
      const currentLayers = Array.isArray(currentTimeline.layers) ? currentTimeline.layers : [];
      const l = currentLayers[st.layerIdx];
      if (!l) return;
      if (isLaneLocked("overlays")) return;
      let start = st.start0,
        end = st.end0;
      if (st.mode === "move") {
        const clipDuration = Math.max(TIMELINE_MIN_RANGE_S, st.end0 - st.start0);
        start = clamp(
          snapDragTime(st.start0 + dx),
          0,
          Math.max(0, durationS - clipDuration),
        );
        end = start + clipDuration;
      }
      if (st.mode === "left") {
        start = snapDragTime(st.start0 + dx);
      }
      if (st.mode === "right") {
        end = snapDragTime(st.end0 + dx);
      }
      start = clamp(start, 0, durationS - 0.05);
      end = clamp(end, start + 0.05, durationS);

      const nextLayers = currentLayers.map((x, i) =>
        i === st.layerIdx ? { ...x, start_s: start, end_s: end } : x,
      );
      replaceTimelineState({ ...currentTimeline, layers: nextLayers }, true);
      return;
    }

    if (st.kind === "camera") {
      const currentTimeline = timelineRef.current;
      const currentKeyframes = Array.isArray(currentTimeline.camera?.keyframes)
        ? currentTimeline.camera.keyframes
        : [];
      const k = currentKeyframes[st.kfIdx];
      if (!k) return;
      if (isLaneLocked("camera")) return;
      const t = clamp(snapDragTime(st.t0 + dx), 0, durationS);
      const next = currentKeyframes.map((x, i) => (i === st.kfIdx ? { ...x, t } : x));
      next.sort((a, b) => Number(a.t || 0) - Number(b.t || 0));
      replaceTimelineState(
        { ...currentTimeline, camera: { ...(currentTimeline.camera || {}), keyframes: next } },
        true,
      );
    }
  };

  const onTimelinePointerUp = () => {
    const drag = dragRef.current;
    dragRef.current = null;
    if (!drag || drag.kind === "playhead") return;
    if (rippleEnabled && drag.kind === "track") {
      const current = timelineRef.current;
      const currentTracks = Array.isArray(current.tracks) ? current.tracks : [];
      const originalTracks = Array.isArray(drag.historyBefore.tracks) ? drag.historyBefore.tracks : [];
      const edited = currentTracks[drag.trackIdx]?.clips?.[drag.clipIdx];
      const originalClips = originalTracks[drag.trackIdx]?.clips || [];
      if (edited) {
        const nextTracks = currentTracks.map((track, trackIdx) =>
          trackIdx === drag.trackIdx
            ? {
                ...track,
                clips: rippleTrackClips(
                  track.clips || [],
                  originalClips,
                  drag.clipIdx,
                  drag.end0,
                  Number(edited.end_s) - drag.end0,
                  durationS,
                ),
              }
            : track,
        );
        replaceTimelineState({ ...current, tracks: nextTracks }, true);
      }
    }
    timelineHistory.push(drag.historyBefore, timelineRef.current, drag.historyLabel);
  };

  const onRulerPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const rect = e.currentTarget.getBoundingClientRect();
    (e.currentTarget as any).setPointerCapture?.(e.pointerId);
    dragRef.current = { kind: "playhead", canvasLeft: rect.left };
    const raw = (e.clientX - rect.left) / Math.max(1, pxPerSecond);
    seekTo(snapEnabled && !e.altKey ? _snap(raw) : raw);
  };

  const onLanePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const raw = (e.clientX - rect.left) / Math.max(1, pxPerSecond);
    seekTo(snapEnabled && !e.altKey ? _snap(raw) : raw);
  };

  const saveTimeline = async () => {
    if (!projectId) return;
    setErr(null);
    try {
      const saved = await runOperation(
        {
          label: "Saving timeline",
          detail: "Persisting arrangement edits for the active session.",
          successDetail: "Timeline saved.",
        },
        () => apiPost(
          `/v1/projects/${encodeURIComponent(projectId)}/timeline`,
          withExpectedRevision({ timeline }),
        ),
      );
      setRevisionFromResponse(saved);
      replaceTimelineState(saved?.timeline || timelineRef.current, false);
    } catch (e: any) {
      reportMutationError(e);
      setErr(String(e));
    }
  };

  const restoreAutosave = async () => {
    if (!projectId) return;
    setRecoveryBusy(true);
    setErr(null);
    try {
      const response = await apiPost(
        `/v1/projects/${encodeURIComponent(projectId)}/recovery/apply`,
        withExpectedRevision({ source: "journal" }),
      );
      setRevisionFromResponse(response);
      setRecoveryNotice(null);
      await refreshProject(projectId);
    } catch (e: any) {
      reportMutationError(e);
      setErr(String(e));
    } finally {
      setRecoveryBusy(false);
    }
  };

  const discardAutosave = async () => {
    if (!projectId) return;
    setRecoveryBusy(true);
    setErr(null);
    try {
      const response = await apiPost(
        `/v1/projects/${encodeURIComponent(projectId)}/recovery/discard`,
        withExpectedRevision({}),
      );
      setRevisionFromResponse(response);
      setRecoveryNotice(null);
    } catch (e: any) {
      reportMutationError(e);
      setErr(String(e));
    } finally {
      setRecoveryBusy(false);
    }
  };

  const deleteSelection = () => {
    if (!selected) return;
    if (selected.kind === "track") {
      const tr = tracks[selected.trackIdx];
      if (!tr) return;
      if (isLaneLocked(laneIdForTrack(tr, selected.trackIdx))) return;
      const deleted = tr.clips[selected.clipIdx];
      const nextTracks = tracks.map((t, i) => {
        if (i !== selected.trackIdx) return t;
        const remaining = (t.clips || []).filter((_, j) => j !== selected.clipIdx);
        if (!rippleEnabled || !deleted) return { ...t, clips: remaining };
        const originalWithoutDeleted = (t.clips || []).filter((_, j) => j !== selected.clipIdx);
        return {
          ...t,
          clips: rippleTrackClips(
            remaining,
            originalWithoutDeleted,
            -1,
            Number(deleted.end_s),
            -(Number(deleted.end_s) - Number(deleted.start_s)),
            durationS,
          ),
        };
      });
      commitTimeline({ ...timeline, tracks: nextTracks }, "delete_clip");
      setSelected(null);
      return;
    }
    if (selected.kind === "overlay") {
      if (isLaneLocked("overlays")) return;
      const nextLayers = layers.filter((_, i) => i !== selected.layerIdx);
      commitTimeline({ ...timeline, layers: nextLayers }, "delete_overlay");
      setSelected(null);
      return;
    }
    if (selected.kind === "camera") {
      if (isLaneLocked("camera")) return;
      const next = camKeyframes.filter((_, i) => i !== selected.kfIdx);
      commitTimeline({ ...timeline, camera: { ...(timeline.camera || {}), keyframes: next } }, "delete_camera");
      setSelected(null);
    }
  };

  const nudgeSelectionToPlayhead = () => {
    if (!selected) return;
    const target = clamp(playheadS, 0, durationS);
    if (selected.kind === "track") {
      const picked = selectedTrackClip(selected);
      if (!picked) return;
      if (isLaneLocked(laneIdForTrack(picked.tr, selected.trackIdx))) return;
      const dur = Math.max(_minLen, Number(picked.cl.end_s) - Number(picked.cl.start_s));
      const s = clamp(target, 0, Math.max(0, durationS - dur));
      const e = clamp(s + dur, s + _minLen, durationS);
      const nextTracks = tracks.map((t, i) => {
        if (i !== selected.trackIdx) return t;
        let nextClips = (t.clips || []).map((c, j) =>
          j === selected.clipIdx ? { ...c, start_s: s, end_s: e } : c,
        );
        if (rippleEnabled) {
          nextClips = rippleTrackClips(
            nextClips,
            t.clips || [],
            selected.clipIdx,
            Number(picked.cl.end_s),
            e - Number(picked.cl.end_s),
            durationS,
          );
        }
        return { ...t, clips: nextClips };
      });
      commitTimeline({ ...timelineRef.current, tracks: nextTracks }, "move_clip");
      return;
    }
    if (selected.kind === "overlay") {
      const l = layers[selected.layerIdx];
      if (!l) return;
      if (isLaneLocked("overlays")) return;
      const dur = Math.max(_minLen, Number(l.end_s ?? durationS) - Number(l.start_s ?? 0));
      const s = clamp(target, 0, Math.max(0, durationS - dur));
      const e = clamp(s + dur, s + _minLen, durationS);
      updateSelectedOverlayTimes(s, e, "move_overlay");
      return;
    }
    if (selected.kind === "camera") {
      if (isLaneLocked("camera")) return;
      updateSelectedCamera({ t: target }, "move_camera");
    }
  };

  const syncToRenderer = async () => {
    if (!projectId) return;
    setErr(null);
    try {
      const saved = await runOperation(
        {
          label: "Syncing timeline to renderer",
          detail: "Saving arrangement edits and opening the internal renderer.",
          successDetail: "Timeline saved and sent to the internal renderer.",
        },
        () => apiPost(
          `/v1/projects/${encodeURIComponent(projectId)}/timeline`,
          withExpectedRevision({ timeline }),
        ),
      );
      setRevisionFromResponse(saved);
      replaceTimelineState(saved?.timeline || timelineRef.current, false);
      onNavigate?.("render");
    } catch (e: any) {
      reportMutationError(e);
      setErr(String(e));
    }
  };

  const addClip = (type: "prompt" | "motion") => {
    const idx = tracks.findIndex((t) => String(t.type).toLowerCase() === type);
    if (idx < 0) return;
    if (isLaneLocked(laneIdForTrack(tracks[idx], idx))) return;
    const s = clamp(playheadS, 0, Math.max(0, durationS - 0.5));
    const e = clamp(s + 5, s + 0.2, durationS);
    const id = `${type}_${Date.now()}`;
    const data =
      type === "prompt"
        ? { prompt: "cinematic" }
        : {
            ...MOTION_PRESETS[0].data,
            motion_preset: MOTION_PRESETS[0].id,
            motion_label: MOTION_PRESETS[0].label,
          };

    const nextTracks = tracks.map((t, i) =>
      i === idx ? { ...t, clips: [...(t.clips || []), { id, start_s: s, end_s: e, data }] } : t,
    );
    commitTimeline({ ...timelineRef.current, tracks: nextTracks }, `add_${type}_clip`);
  };

  const addMediaClip = (entry: AnyDict) => {
    const sourcePath = String(entry?.path || "").trim();
    if (!sourcePath) return;
    const trackIdx = tracks.findIndex((track) => String(track.type).toLowerCase() === "video");
    if (trackIdx < 0) return;
    if (isLaneLocked(laneIdForTrack(tracks[trackIdx], trackIdx))) return;

    const sourceDuration = Math.max(
      TIMELINE_MIN_RANGE_S,
      Number(entry?.duration_s || entry?.metadata?.duration_s || selectedMediaDuration || 5),
    );
    const start = clamp(playheadS, 0, Math.max(0, durationS - TIMELINE_MIN_RANGE_S));
    const end = clamp(start + sourceDuration, start + TIMELINE_MIN_RANGE_S, durationS);
    const visibleSourceDuration = Math.max(TIMELINE_MIN_RANGE_S, end - start);
    const clip: Clip = {
      id: `video_${Date.now()}`,
      start_s: start,
      end_s: end,
      data: {
        source_path: sourcePath,
        name: String(entry?.name || sourcePath.split(/[\\/]/).pop() || "Video clip"),
        source_in_s: 0,
        source_out_s: Math.min(sourceDuration, visibleSourceDuration),
        source_duration_s: sourceDuration,
        speed: 1,
        volume: 1,
        muted: false,
        fade_in_s: 0,
        fade_out_s: 0,
      },
    };
    const nextTracks = tracks.map((track, index) =>
      index === trackIdx
        ? { ...track, clips: [...(track.clips || []), clip].sort((a, b) => a.start_s - b.start_s) }
        : track,
    );
    commitTimeline({ ...timelineRef.current, tracks: nextTracks }, "add_video_clip");
    setSelected({ kind: "track", trackIdx, clipIdx: nextTracks[trackIdx].clips.indexOf(clip) });
    setSelectedMediaPath(sourcePath);
    setDockSection("inspector");
  };

  const renderEditedTimeline = async () => {
    if (!projectId) return;
    if (!editMasterSettingsValid) {
      setDockSection("media");
      setErr(`Fix the edited-master settings: ${editMasterValidationMessages.join(" ")}`);
      return;
    }
    const videoClips = tracks
      .filter((track) => String(track.type).toLowerCase() === "video")
      .flatMap((track) => track.clips || []);
    if (!videoClips.length) {
      setErr("Add at least one output video to the Video Edit track before rendering an edited master.");
      return;
    }
    setMediaRenderBusy(true);
    setMediaRenderStatus("Saving the edit decision list…");
    setErr(null);
    try {
      const saved = await apiPost(
        `/v1/projects/${encodeURIComponent(projectId)}/timeline`,
        withExpectedRevision({ timeline: timelineRef.current }),
      );
      setRevisionFromResponse(saved);
      replaceTimelineState(saved?.timeline || timelineRef.current, false);
      setMediaRenderStatus("Queueing the non-destructive video edit…");
      const renderRequest: AnyDict = {
        width: editWidth,
        height: editHeight,
        fps: editFps,
        video_codec: editVideoCodec,
        audio_codec: editAudioCodec,
        name: editOutputSafeName,
      };
      if (editVideoCodec !== "prores") renderRequest.quality = editQuality;
      const response = await apiPost(
        `/v1/projects/${encodeURIComponent(projectId)}/timeline/render`,
        withExpectedRevision(renderRequest),
      );
      setRevisionFromResponse(response);
      setMediaRenderStatus(
        response?.job?.id
          ? `Edit queued as ${response.job.id}. Track progress in Render Queue.`
          : "Edit queued. Track progress in Render Queue.",
      );
    } catch (error: any) {
      reportMutationError(error);
      setErr(String(error));
      setMediaRenderStatus("");
    } finally {
      setMediaRenderBusy(false);
    }
  };

  const addCameraKeyframe = () => {
    if (isLaneLocked("camera")) return;
    const s = clamp(playheadS, 0, durationS);
    const k = { t: s, zoom: 1.0, pan_x: 0.0, pan_y: 0.0, rotation_deg: 0.0 };
    const next = [...camKeyframes, k].sort((a, b) => Number(a.t || 0) - Number(b.t || 0));
    commitTimeline(
      { ...timelineRef.current, camera: { ...(timelineRef.current.camera || {}), keyframes: next } },
      "add_camera_keyframe",
    );
  };

  const _bpm = () => {
    const b =
      bpmOverride ??
      Number(
        project?.meta?.analysis?.features?.bpm ??
          project?.meta?.analysis?.features?.tempo_bpm ??
          project?.meta?.analysis?.features?.tempo ??
          project?.meta?.last_plan?.bpm ??
          0,
      );
    return Number.isFinite(b) && b > 20 ? b : null;
  };

  const _beatTimes = (): number[] => {
    const feats = project?.meta?.analysis?.features || {};
    const raw =
      feats.beat_times_s ??
      feats.beat_times ??
      feats.beats_s ??
      feats.beats ??
      feats.beat_times_seconds ??
      null;

    const out: number[] = [];
    const push = (v: any) => {
      const n = Number(v);
      if (Number.isFinite(n) && n >= 0) out.push(n);
    };

    if (Array.isArray(raw)) {
      for (const it of raw) {
        if (typeof it === "number" || typeof it === "string") push(it);
        else if (it && typeof it === "object") push(it.t ?? it.time ?? it.sec ?? it.s);
      }
    }

    out.sort((a, b) => a - b);
    // Ensure 0 exists to make snapping predictable.
    if (!out.length || out[0] > 0.05) out.unshift(0);
    // De-dupe near-equals.
    const dedup: number[] = [];
    for (const t of out) {
      if (!dedup.length || Math.abs(dedup[dedup.length - 1] - t) > 1e-3) dedup.push(t);
    }
    return dedup;
  };

  const _quantStepS = () => {
    const bpm = _bpm();
    if (!bpm) return null;
    const beats = Number(quantizeBeats) || 1;
    return (60.0 / bpm) * beats;
  };

  const _beatGrid = (): number[] | null => {
    const beats = _beatTimes();
    if (beats.length < 2) return null;
    const quantize = Math.max(0.25, Number(quantizeBeats) || 1);
    if (quantize < 1) {
      const divisions = Math.max(1, Math.round(1 / quantize));
      const subdivided: number[] = [];
      for (let i = 0; i < beats.length - 1; i++) {
        const start = beats[i];
        const end = beats[i + 1];
        for (let part = 0; part < divisions; part++) {
          subdivided.push(Number((start + ((end - start) * part) / divisions).toFixed(4)));
        }
      }
      subdivided.push(beats[beats.length - 1]);
      return subdivided;
    }

    const n = Math.max(1, Math.round(quantize));
    if (n === 1) return beats;
    const grid: number[] = [];
    for (let i = 0; i < beats.length; i++) if (i % n === 0) grid.push(beats[i]);
    return grid.length >= 2 ? grid : beats;
  };

  const _nearestInSorted = (arr: number[], t: number) => {
    if (!arr.length) return t;
    let lo = 0;
    let hi = arr.length - 1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      const v = arr[mid];
      if (v < t) lo = mid + 1;
      else if (v > t) hi = mid - 1;
      else return v;
    }
    const a = clamp(hi, 0, arr.length - 1);
    const b = clamp(lo, 0, arr.length - 1);
    return Math.abs(arr[a] - t) <= Math.abs(arr[b] - t) ? arr[a] : arr[b];
  };

  const _nextAfter = (arr: number[], t: number) => {
    if (!arr.length) return null;
    let lo = 0;
    let hi = arr.length - 1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (arr[mid] < t) lo = mid + 1;
      else hi = mid - 1;
    }
    return lo >= 0 && lo < arr.length ? arr[lo] : null;
  };

  const _snap = (t: number) => {
    // Prefer true beat timestamps when available.
    const grid = _beatGrid();
    if (grid && grid.length >= 2) return Math.max(0, _nearestInSorted(grid, t));

    // Fallback: BPM grid.
    const step = _quantStepS();
    if (!step) return t;
    return Math.max(0, Math.round(t / step) * step);
  };

  const _minLen = TIMELINE_MIN_RANGE_S;

  const splitTrackClipAt = (trackIdx: number, clipIdx: number, cutTime: number) => {
    const currentTimeline = timelineRef.current;
    const currentTracks: Track[] = Array.isArray(currentTimeline.tracks) ? currentTimeline.tracks : [];
    const tr = currentTracks[trackIdx];
    const cl = tr?.clips?.[clipIdx];
    if (!tr || !cl) return false;
    if (isLaneLocked(laneIdForTrack(tr, trackIdx))) return false;
    const tSplit = clamp(cutTime, 0, durationS);
    if (!(cl.start_s + _minLen < tSplit && tSplit < cl.end_s - _minLen)) return false;

    let leftData = cl.data || {};
    let rightData = cl.data || {};
    if (String(tr.type || "").toLowerCase() === "video") {
      const speed = clamp(Number(cl.data?.speed ?? 1), 0.25, 4);
      const sourceIn = Math.max(0, Number(cl.data?.source_in_s ?? 0));
      const sourceSplit = sourceIn + (tSplit - cl.start_s) * speed;
      leftData = { ...(cl.data || {}), source_out_s: sourceSplit };
      rightData = { ...(cl.data || {}), source_in_s: sourceSplit };
    }

    const left = { ...cl, end_s: tSplit, data: leftData };
    const right = {
      ...cl,
      id: `${String(tr.type)}_${Date.now()}`,
      start_s: tSplit,
      data: rightData,
    };
    const nextTracks = currentTracks.map((track, index) => {
      if (index !== trackIdx) return track;
      const nextClips = (track.clips || []).flatMap((clip, indexInTrack) =>
        indexInTrack === clipIdx ? [left, right] : [clip],
      );
      return { ...track, clips: nextClips };
    });
    commitTimeline({ ...currentTimeline, tracks: nextTracks }, "split_clip");
    return true;
  };

  const duplicateSelection = () => {
    if (!selected) return;
    if (selected.kind === "track") {
      const tr = tracks[selected.trackIdx];
      const cl = tr?.clips?.[selected.clipIdx];
      if (!tr || !cl) return;
      if (isLaneLocked(laneIdForTrack(tr, selected.trackIdx))) return;
      const dur = Math.max(_minLen, Number(cl.end_s) - Number(cl.start_s));
      const s = clamp(playheadS, 0, Math.max(0, durationS - dur));
      const e = clamp(s + dur, s + _minLen, durationS);
      const id = `${String(tr.type)}_${Date.now()}`;
      const nextTracks = tracks.map((t, i) => {
        if (i !== selected.trackIdx) return t;
        return { ...t, clips: [...(t.clips || []), { ...cl, id, start_s: s, end_s: e }] };
      });
      commitTimeline({ ...timelineRef.current, tracks: nextTracks }, "duplicate_clip");
      return;
    }
    if (selected.kind === "overlay") {
      const l = layers[selected.layerIdx];
      if (!l) return;
      if (isLaneLocked("overlays")) return;
      const dur = Math.max(_minLen, Number(l.end_s ?? durationS) - Number(l.start_s ?? 0));
      const s = clamp(playheadS, 0, Math.max(0, durationS - dur));
      const e = clamp(s + dur, s + _minLen, durationS);
      const nextLayers = layers.map((x, i) => (i === selected.layerIdx ? x : x));
      nextLayers.push({ ...l, start_s: s, end_s: e });
      commitTimeline({ ...timelineRef.current, layers: nextLayers }, "duplicate_overlay");
      return;
    }
    if (selected.kind === "camera") {
      const k = camKeyframes[selected.kfIdx];
      if (!k) return;
      if (isLaneLocked("camera")) return;
      const t = clamp(playheadS, 0, durationS);
      const next = [...camKeyframes, { ...k, t }].sort(
        (a, b) => Number(a.t || 0) - Number(b.t || 0),
      );
      commitTimeline(
        { ...timelineRef.current, camera: { ...(timelineRef.current.camera || {}), keyframes: next } },
        "duplicate_camera",
      );
    }
  };

  const splitSelection = () => {
    if (!selected) return;
    const tSplit = clamp(playheadS, 0, durationS);
    if (selected.kind === "track") {
      splitTrackClipAt(selected.trackIdx, selected.clipIdx, tSplit);
      return;
    }
    if (selected.kind === "overlay") {
      const l = layers[selected.layerIdx];
      if (!l) return;
      if (isLaneLocked("overlays")) return;
      const s0 = Number(l.start_s ?? 0),
        e0 = Number(l.end_s ?? durationS);
      if (!(s0 + _minLen < tSplit && tSplit < e0 - _minLen)) return;
      const left = { ...l, end_s: tSplit };
      const right = { ...l, start_s: tSplit };
      const nextLayers = layers.flatMap((x, i) => (i === selected.layerIdx ? [left, right] : [x]));
      commitTimeline({ ...timelineRef.current, layers: nextLayers }, "split_overlay");
    }
  };

  const quantizeSelection = () => {
    const grid = _beatGrid();
    const step = _quantStepS();
    if (!grid && !step) {
      setErr(
        "No beat grid available for quantize. Run Analyze (beat detection) or set BPM override.",
      );
      return;
    }
    if (!selected) return;

    const snapRange = (s: number, e: number) => {
      const ss = clamp(_snap(s), 0, durationS - _minLen);

      // If beat-grid snapping is active, prefer aligning end to a later beat boundary.
      const grid = _beatGrid();
      let ee0 = _snap(e);
      const minEnd = ss + _minLen;
      if (grid && grid.length >= 2 && ee0 < minEnd) {
        const next = _nextAfter(grid, minEnd);
        if (next != null) ee0 = next;
      }
      const ee = clamp(ee0, minEnd, durationS);
      return [ss, ee] as const;
    };

    if (selected.kind === "track") {
      const tr = tracks[selected.trackIdx];
      const cl = tr?.clips?.[selected.clipIdx];
      if (!tr || !cl) return;
      if (isLaneLocked(laneIdForTrack(tr, selected.trackIdx))) return;
      const [ss, ee] = snapRange(Number(cl.start_s), Number(cl.end_s));
      const nextTracks = tracks.map((t, i) => {
        if (i !== selected.trackIdx) return t;
        const originalClips = t.clips || [];
        let nextClips = originalClips.map((c, j) =>
          j === selected.clipIdx ? { ...c, start_s: ss, end_s: ee } : c,
        );
        if (rippleEnabled) {
          nextClips = rippleTrackClips(
            nextClips,
            originalClips,
            selected.clipIdx,
            Number(cl.end_s),
            ee - Number(cl.end_s),
            durationS,
          );
        }
        return { ...t, clips: nextClips };
      });
      commitTimeline({ ...timelineRef.current, tracks: nextTracks }, "quantize_clip");
      return;
    }

    if (selected.kind === "overlay") {
      const l = layers[selected.layerIdx];
      if (!l) return;
      if (isLaneLocked("overlays")) return;
      const [ss, ee] = snapRange(Number(l.start_s ?? 0), Number(l.end_s ?? durationS));
      updateSelectedOverlayTimes(ss, ee, "quantize_overlay");
      return;
    }

    if (selected.kind === "camera") {
      const k = camKeyframes[selected.kfIdx];
      if (!k) return;
      if (isLaneLocked("camera")) return;
      const tt = clamp(_snap(Number(k.t || 0)), 0, durationS);
      updateSelectedCamera({ t: tt }, "quantize_camera");
    }
  };

  // Hotkeys (Timeline page)
  // S = split, D = duplicate, Q = quantize, L = loop, arrows = grid navigation.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (isTimelineInteractiveTarget(e.target) || isTimelineInteractiveTarget(document.activeElement)) {
        return;
      }

      if (e.ctrlKey || e.metaKey) {
        const key = e.key.toLowerCase();
        if (key === "z" && !e.shiftKey) {
          e.preventDefault();
          undoTimeline();
          return;
        }
        if (key === "y" || (key === "z" && e.shiftKey)) {
          e.preventDefault();
          redoTimeline();
          return;
        }
        return;
      }

      const k = e.key;
      if (k === "s" || k === "S") {
        e.preventDefault();
        splitSelection();
        return;
      }
      if (k === "d" || k === "D") {
        e.preventDefault();
        duplicateSelection();
        return;
      }
      if (k === "q" || k === "Q") {
        e.preventDefault();
        quantizeSelection();
        return;
      }
      if (k === "l" || k === "L") {
        e.preventDefault();
        setLoopEnabled((value) => !value);
        return;
      }
      if (k === "ArrowLeft") {
        e.preventDefault();
        jumpToPreviousGrid();
        return;
      }
      if (k === "ArrowRight") {
        e.preventDefault();
        jumpToNextGrid();
        return;
      }
      if (k === "Home") {
        e.preventDefault();
        seekTo(0);
        return;
      }
      if (k === "End") {
        e.preventDefault();
        seekTo(durationS);
        return;
      }
      if (k === "Delete" || k === "Backspace") {
        e.preventDefault();
        deleteSelection();
        return;
      }
    };

    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [
    selected,
    playheadS,
    durationS,
    quantizeBeats,
    bpmOverride,
    timeline,
    splitSelection,
    duplicateSelection,
    quantizeSelection,
    deleteSelection,
  ]);

  const setDiffRangeFromSelection = () => {
    if (!selected) return;
    if (selected.kind === "track") {
      const picked = selectedTrackClip(selected);
      if (!picked) return;
      setDiffStart(Number(picked.cl.start_s));
      setDiffEnd(Number(picked.cl.end_s));
      return;
    }
    if (selected.kind === "overlay") {
      const l = layers[selected.layerIdx];
      if (!l) return;
      setDiffStart(Number(l.start_s ?? 0));
      setDiffEnd(Number(l.end_s ?? durationS));
      return;
    }
    if (selected.kind === "camera") {
      const k = camKeyframes[selected.kfIdx];
      if (!k) return;
      const t = Number(k.t || 0);
      setDiffStart(clamp(t - 1.0, 0, durationS));
      setDiffEnd(clamp(t + 1.0, 0, durationS));
    }
  };

  const generateDiffusionPreview = async () => {
    if (!projectId) return;
    const s = clamp(Number(diffStart), 0, durationS);
    const e = clamp(Number(diffEnd), s + 0.05, durationS);
    setDiffBusy(true);
    setErr(null);
    try {
      const path =
        `/v1/projects/${projectId}/preview/diffusion_segment?start_s=${encodeURIComponent(String(s))}&end_s=${encodeURIComponent(String(e))}` +
        `&w=${encodeURIComponent(String(diffW))}&h=${encodeURIComponent(String(diffH))}` +
        `&fps=${encodeURIComponent(String(diffFps))}&steps=${encodeURIComponent(String(diffSteps))}` +
        `&cfg=${encodeURIComponent(String(diffCfg))}&strength=${encodeURIComponent(String(diffStrength))}` +
        `&model_id=${encodeURIComponent(String(diffModel))}` +
        `&variant_index=${encodeURIComponent(String(selectedVariant || 0))}` +
        `&force=1`;
      const response = await apiFetch(path);
      if (!response.ok) throw new Error("Could not generate the diffusion preview.");
      const nextUrl = URL.createObjectURL(await response.blob());
      setDiffUrl((current) => {
        if (current.startsWith("blob:")) URL.revokeObjectURL(current);
        return nextUrl;
      });
    } catch (error: any) {
      setDiffBusy(false);
      setErr(String(error?.message ?? error));
    }
  };

  const selectedTrackClip = (sel: Selected) => {
    if (!sel || sel.kind !== "track") return null;
    const tr = tracks[sel.trackIdx];
    const cl = tr?.clips?.[sel.clipIdx];
    if (!tr || !cl) return null;
    return { tr, cl };
  };

  const updateSelectedClipData = (patch: AnyDict, label = "update_clip_property") => {
    if (!selected || selected.kind !== "track") return;
    const tr = tracks[selected.trackIdx];
    const cl = tr?.clips?.[selected.clipIdx];
    if (!tr || !cl) return;
    if (isLaneLocked(laneIdForTrack(tr, selected.trackIdx))) return;
    const nextTracks = tracks.map((t, i) => {
      if (i !== selected.trackIdx) return t;
      const nextClips = (t.clips || []).map((c, j) =>
        j === selected.clipIdx ? { ...c, data: { ...(c.data || {}), ...patch } } : c,
      );
      return { ...t, clips: nextClips };
    });
    const isMotion = String(tr.type || "").toLowerCase() === "motion";
    const syncCamera = isMotion && Object.keys(patch).some((field) => MOTION_CAMERA_ENDPOINT_FIELDS.has(field));
    const currentTimeline = timelineRef.current;
    const nextTimeline: AnyDict = { ...currentTimeline, tracks: nextTracks };
    if (syncCamera) {
      const nextData = { ...(cl.data || {}), ...patch };
      nextTimeline.camera = {
        ...(currentTimeline.camera || {}),
        keyframes: syncMotionClipCameraKeyframes(camKeyframes, cl, nextData),
      };
    }
    commitTimeline(nextTimeline, label);
  };

  const applySelectedMotionPreset = (presetId: string) => {
    const picked = selectedTrackClip(selected);
    if (!picked || String(picked.tr.type || "").toLowerCase() !== "motion") return;
    if (presetId === "custom" || presetId === "reactive") {
      updateSelectedClipData(
        { motion_preset: "custom", motion_label: "Custom motion" },
        "apply_motion_preset",
      );
      return;
    }
    const preset = MOTION_PRESET_BY_ID.get(presetId);
    if (!preset) return;
    updateSelectedClipData({
      ...clearMotionSchedules(picked.cl.data || {}),
      ...preset.data,
      motion_preset: preset.id,
      motion_label: preset.label,
    }, "apply_motion_preset");
  };

  const updateSelectedMotionField = (field: string, value: number) => {
    const picked = selectedTrackClip(selected);
    if (!picked || String(picked.tr.type || "").toLowerCase() !== "motion") return;
    let schedulePatch: AnyDict = {};
    if (MOTION_CAMERA_ENDPOINT_FIELDS.has(field)) {
      schedulePatch = clearMotionSchedules(picked.cl.data || {}, field);
    } else if (field === "strength") {
      schedulePatch = { strength_schedule: null };
    } else if (field === "cfg") {
      schedulePatch = { cfg_scale_schedule: null };
    } else if (field === "steps") {
      schedulePatch = { steps_schedule: null };
    }
    updateSelectedClipData({
      ...schedulePatch,
      [field]: value,
      motion_preset: "custom",
      motion_label: "Custom motion",
    }, "update_motion_property");
  };

  const applyMotionVariety = (trackIdx: number) => {
    const tr = tracks[trackIdx];
    if (!tr || String(tr.type || "").toLowerCase() !== "motion") return;
    if (isLaneLocked(laneIdForTrack(tr, trackIdx))) return;
    let variedCount = 0;
    let nextKeyframes = camKeyframes;
    const nextClips = (tr.clips || []).map((clip) => {
      const currentData = clip.data || {};
      if (hasMotionSchedules(currentData)) return clip;
      const presetId = MOTION_VARIETY_PRESETS[variedCount % MOTION_VARIETY_PRESETS.length];
      const preset = MOTION_PRESET_BY_ID.get(presetId);
      if (!preset) return clip;
      variedCount += 1;
      const nextData = {
        ...currentData,
        ...clearMotionSchedules(currentData),
        ...preset.data,
        motion_preset: preset.id,
        motion_label: preset.label,
      };
      nextKeyframes = syncMotionClipCameraKeyframes(nextKeyframes, clip, nextData);
      return { ...clip, data: nextData };
    });
    if (!variedCount) return;
    const nextTracks = tracks.map((track, index) => index === trackIdx ? { ...track, clips: nextClips } : track);
    commitTimeline({
      ...timelineRef.current,
      tracks: nextTracks,
      camera: { ...(timelineRef.current.camera || {}), keyframes: nextKeyframes },
    }, "apply_motion_variety");
  };

  const updateSelectedOverlayTimes = (start_s: number, end_s: number, label = "update_overlay") => {
    if (!selected || selected.kind !== "overlay") return;
    if (isLaneLocked("overlays")) return;
    const idx = selected.layerIdx;
    const nextLayers = layers.map((x, i) => (i === idx ? { ...x, start_s, end_s } : x));
    commitTimeline({ ...timelineRef.current, layers: nextLayers }, label);
  };

  const updateSelectedCamera = (patch: AnyDict, label = "update_camera") => {
    if (!selected || selected.kind !== "camera") return;
    if (isLaneLocked("camera")) return;
    const idx = selected.kfIdx;
    const next = camKeyframes
      .map((x, i) => (i === idx ? { ...x, ...patch } : x))
      .sort((a, b) => Number(a.t || 0) - Number(b.t || 0));
    commitTimeline(
      { ...timelineRef.current, camera: { ...(timelineRef.current.camera || {}), keyframes: next } },
      label,
    );
  };

  const updateSelectedClipTimes = (start_s: number, end_s: number, label = "trim_clip") => {
    if (!selected || selected.kind !== "track") return;
    const tr = tracks[selected.trackIdx];
    const cl = tr?.clips?.[selected.clipIdx];
    if (!tr || !cl || isLaneLocked(laneIdForTrack(tr, selected.trackIdx))) return;
    const start = clamp(Number(start_s) || 0, 0, Math.max(0, durationS - _minLen));
    const end = clamp(Number(end_s) || start + _minLen, start + _minLen, durationS);
    let data = cl.data || {};
    if (String(tr.type || "").toLowerCase() === "video") {
      const speed = clamp(Number(data.speed ?? 1), 0.25, 4);
      const sourceIn = Math.max(0, Number(data.source_in_s ?? 0) + (start - cl.start_s) * speed);
      const sourceOut = Math.max(
        sourceIn + _minLen,
        Number(data.source_out_s ?? sourceIn + (cl.end_s - cl.start_s) * speed) +
          (end - cl.end_s) * speed,
      );
      data = { ...data, source_in_s: sourceIn, source_out_s: sourceOut };
    }
    const nextTracks = tracks.map((track, trackIdx) => {
      if (trackIdx !== selected.trackIdx) return track;
      const originalClips = track.clips || [];
      let nextClips = originalClips.map((clip, clipIdx) =>
        clipIdx === selected.clipIdx ? { ...clip, start_s: start, end_s: end, data } : clip,
      );
      if (rippleEnabled) {
        nextClips = rippleTrackClips(
          nextClips,
          originalClips,
          selected.clipIdx,
          Number(cl.end_s),
          end - Number(cl.end_s),
          durationS,
        );
      }
      return {
        ...track,
        clips: nextClips,
      };
    });
    commitTimeline({ ...timelineRef.current, tracks: nextTracks }, label);
  };

  const selectedTimeRange = (): [number, number] | null => {
    if (!selected) return null;
    if (selected.kind === "track") {
      const picked = selectedTrackClip(selected);
      if (!picked) return null;
      return [Number(picked.cl.start_s), Number(picked.cl.end_s)];
    }
    if (selected.kind === "overlay") {
      const layer = layers[selected.layerIdx];
      if (!layer) return null;
      return [Number(layer.start_s ?? 0), Number(layer.end_s ?? durationS)];
    }
    if (selected.kind === "camera") {
      const keyframe = camKeyframes[selected.kfIdx];
      if (!keyframe) return null;
      const t = Number(keyframe.t || 0);
      return [clamp(t - 1, 0, durationS), clamp(t + 1, 0, durationS)];
    }
    return null;
  };

  const setLoopRange = (start: number, end: number) => {
    const s = clamp(Number(start) || 0, 0, Math.max(0, durationS - TIMELINE_MIN_RANGE_S));
    const e = clamp(Number(end) || durationS, s + TIMELINE_MIN_RANGE_S, durationS);
    setLocatorInS(s);
    setLocatorOutS(e);
  };

  const useSelectionAsLoopRange = () => {
    const range = selectedTimeRange();
    if (!range) return;
    setLoopRange(range[0], range[1]);
    setLoopEnabled(true);
  };

  const timelineGridMarkers = (() => {
    const detectedGrid = _beatGrid();
    const detected = detectedGrid && detectedGrid.length >= 2 ? detectedGrid : null;
    if (detected) return detected.filter((t) => t >= 0 && t <= durationS);

    const step = _quantStepS();
    if (!step || step <= 0) return [];
    const count = Math.floor(durationS / step);
    const stride = Math.max(1, Math.ceil(count / 700));
    const markers: number[] = [];
    for (let i = 0; i <= count; i += stride) markers.push(Number((i * step).toFixed(4)));
    if (!markers.includes(0)) markers.unshift(0);
    if (durationS > 0 && markers[markers.length - 1] < durationS - TIMELINE_MIN_RANGE_S) {
      markers.push(durationS);
    }
    return markers;
  })();

  const jumpToPreviousGrid = () => {
    const before = [...timelineGridMarkers].reverse().find((t) => t < playheadS - 0.03);
    seekTo(before ?? 0);
  };

  const jumpToNextGrid = () => {
    const after = timelineGridMarkers.find((t) => t > playheadS + 0.03);
    seekTo(after ?? durationS);
  };

  const playPause = () => {
    const a = audioRef.current;
    if (!a) return;
    if (a.paused) {
      if (a.currentTime >= durationS - TIMELINE_MIN_RANGE_S) {
        a.currentTime = loopEnabled ? locatorInS : 0;
        setPlayheadS(a.currentTime);
      }
      a.play().catch(() => {});
    } else {
      a.pause();
    }
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === " " && !isTimelineInteractiveTarget(e.target)) {
      e.preventDefault();
      playPause();
    }
  };

  const bpm = _bpm();
  const beatGrid = _beatGrid();
  const detectedBeatTimes = _beatTimes().filter((time) => time <= durationS);
  const fallbackBeatStep = bpm ? 60 / bpm : 0.5;
  const musicalPositionAt = (seconds: number) => {
    const time = clamp(seconds, 0, durationS);
    let beatFloat = 0;
    if (detectedBeatTimes.length >= 2) {
      const nextIndex = detectedBeatTimes.findIndex((beatTime) => beatTime > time);
      if (nextIndex > 0) {
        const previous = detectedBeatTimes[nextIndex - 1];
        const next = detectedBeatTimes[nextIndex];
        beatFloat = nextIndex - 1 + (time - previous) / Math.max(1e-6, next - previous);
      } else if (nextIndex === 0) {
        beatFloat = 0;
      } else {
        const lastIndex = detectedBeatTimes.length - 1;
        beatFloat =
          lastIndex +
          (time - detectedBeatTimes[lastIndex]) / Math.max(1e-6, fallbackBeatStep);
      }
    } else {
      beatFloat = time / Math.max(1e-6, fallbackBeatStep);
    }
    const wholeBeat = Math.max(0, Math.floor(beatFloat));
    const bar = Math.floor(wholeBeat / 4) + 1;
    const beat = (wholeBeat % 4) + 1;
    const tick = Math.min(959, Math.max(0, Math.floor((beatFloat - wholeBeat) * 960)));
    return `${String(bar).padStart(3, "0")}.${String(beat).padStart(2, "0")}.${String(tick).padStart(3, "0")}`;
  };
  const timelineViewportWidth = timelineScrollRef.current?.clientWidth ?? 0;
  const timelineCanvasWidth = Math.max(
    720,
    timelineViewportWidth,
    Math.ceil(durationS * pxPerSecond) + 120,
  );
  const rulerStepS = pxPerSecond >= 180 ? 1 : pxPerSecond >= 120 ? 2 : pxPerSecond >= 72 ? 4 : 8;
  const rulerEnd = Math.ceil(durationS / Math.max(1, rulerStepS)) * rulerStepS;
  const rulerTicks = Array.from(
    { length: Math.floor(rulerEnd / Math.max(1, rulerStepS)) + 1 },
    (_, i) => Number((i * rulerStepS).toFixed(3)),
  );
  const musicalRulerTimes = (() => {
    if (detectedBeatTimes.length >= 2) return detectedBeatTimes;
    if (!bpm) return [];
    const count = Math.min(700, Math.floor(durationS / fallbackBeatStep) + 1);
    return Array.from({ length: count }, (_, index) =>
      Number((index * fallbackBeatStep).toFixed(4)),
    );
  })();
  const musicalRulerTicks = musicalRulerTimes.map((time, index) => ({
    time,
    isBar: index % 4 === 0,
    label: index % 4 === 0 ? String(Math.floor(index / 4) + 1) : `${Math.floor(index / 4) + 1}.${(index % 4) + 1}`,
  }));
  const barMarkerTimes = new Set(
    musicalRulerTicks.filter((tick) => tick.isBar).map((tick) => tick.time.toFixed(4)),
  );
  const quantizeStatus =
    beatGrid && beatGrid.length >= 2
      ? `${beatGrid.length} detected beat markers`
      : bpm
        ? `BPM fallback ${bpm.toFixed(1)}`
        : "Run Analyze or set BPM to enable quantize";
  const musicalClock = detectedBeatTimes.length >= 2 || bpm ? musicalPositionAt(playheadS) : "---.--.---";
  const plannerLabMeta = project?.meta?.last_planner_lab || null;
  const reactiveLabMeta = project?.meta?.last_reactive_lab || null;
  const plannerImportedAt = Number(plannerLabMeta?.imported_at || 0);
  const reactiveAppliedAt = Number(reactiveLabMeta?.applied_at || 0);
  const handoffReadyCount = [plannerImportedAt > 0, reactiveAppliedAt > 0].filter(Boolean).length;
  const plannerVariantCount = Array.isArray(plan?.variants) ? plan.variants.length : 0;
  const plannerSceneCount = Array.isArray(plan?.variants?.[selectedVariant]?.scenes)
    ? plan.variants[selectedVariant].scenes.length
    : 0;
  const reactiveCueCount = Array.isArray(reactiveLabMeta?.cue_events) ? reactiveLabMeta.cue_events.length : 0;
  const reactiveSectionCount = Array.isArray(reactiveLabMeta?.sections) ? reactiveLabMeta.sections.length : 0;
  const musicSections = (
    Array.isArray(reactiveLabMeta?.sections)
      ? reactiveLabMeta.sections
      : Array.isArray(project?.meta?.analysis?.features?.sections)
        ? project.meta.analysis.features.sections
        : Array.isArray(plan?.variants?.[selectedVariant]?.scenes)
          ? plan.variants[selectedVariant].scenes
          : []
  )
    .map((section: AnyDict, index: number) => ({
      id: String(section?.id || `section_${index}`),
      start: Number(section?.start_s ?? section?.start ?? section?.time ?? 0),
      end: Number(section?.end_s ?? section?.end ?? section?.start_s ?? section?.start ?? 0),
      label: String(section?.label || section?.name || section?.section || `Section ${index + 1}`),
    }))
    .map((section: AnyDict, index: number, all: AnyDict[]) => ({
      ...section,
      end:
        section.end > section.start
          ? section.end
          : Number(all[index + 1]?.start ?? Math.min(durationS, section.start + 4)),
    }))
    .filter((section: AnyDict) => Number.isFinite(section.start) && section.start < durationS);
  const musicCues = (
    Array.isArray(reactiveLabMeta?.cue_events)
      ? reactiveLabMeta.cue_events
      : Array.isArray(project?.meta?.analysis?.features?.cue_events)
        ? project.meta.analysis.features.cue_events
        : []
  )
    .map((cue: AnyDict, index: number) => ({
      id: String(cue?.id || `cue_${index}`),
      time: Number(cue?.time_s ?? cue?.t ?? cue?.time ?? cue?.start_s ?? 0),
      label: String(cue?.label || cue?.name || cue?.type || `Cue ${index + 1}`),
    }))
    .filter((cue: AnyDict) => Number.isFinite(cue.time) && cue.time >= 0 && cue.time <= durationS);
  const densityConfig =
    timelineDensity === "compact"
      ? { rail: 196, lane: 54, wave: 92, clipTop: 9, clipHeight: 34, keyframeTop: 19 }
      : { rail: 220, lane: 64, wave: 106, clipTop: 12, clipHeight: 40, keyframeTop: 24 };
  const pickedSelection =
    selected?.kind === "track" ? selectedTrackClip(selected) : null;
  const selectionStatus =
    selected?.kind === "track" && pickedSelection
      ? `${pickedSelection.tr.name} clip`
      : selected?.kind === "overlay"
        ? `Overlay ${selected.layerIdx + 1}`
        : selected?.kind === "camera"
          ? `Camera keyframe ${selected.kfIdx + 1}`
          : "No selection";
  const selectedLaneLocked =
    selected?.kind === "track" && tracks[selected.trackIdx]
      ? isLaneLocked(laneIdForTrack(tracks[selected.trackIdx], selected.trackIdx))
      : selected?.kind === "overlay"
        ? isLaneLocked("overlays")
        : selected?.kind === "camera"
          ? isLaneLocked("camera")
          : false;
  const timelineSurfaceStyle = {
    width: timelineCanvasWidth,
    ["--timeline-rail-width" as any]: `${densityConfig.rail}px`,
    ["--timeline-lane-height" as any]: `${densityConfig.lane}px`,
    ["--timeline-wave-height" as any]: `${densityConfig.wave}px`,
    ["--timeline-clip-top" as any]: `${densityConfig.clipTop}px`,
    ["--timeline-clip-height" as any]: `${densityConfig.clipHeight}px`,
    ["--timeline-keyframe-top" as any]: `${densityConfig.keyframeTop}px`,
    ["--timeline-major-grid" as any]: `${Math.max(pxPerSecond, 24)}px`,
    ["--timeline-minor-grid" as any]: `${Math.max(pxPerSecond / 4, 12)}px`,
  } as React.CSSProperties;

  const pageHeader = (
    <div className="timeline-pageHeader">
      <div>
        <div className="timeline-kicker">Arrangement View</div>
        <div className="timeline-titleRow">
          <h1>Timeline</h1>
          <span className="badge">{timelineDirty ? "Unsaved edits" : "Saved"}</span>
        </div>
        <div className="small timeline-headerCopy">
          Arrange prompts, motion, overlays, and camera automation against the music with a
          beat-aware grid and a renderer-ready edit workflow.
        </div>
      </div>
      <div className="timeline-statusStrip">
        <div className="timeline-stat">
          <span className="small">Duration</span>
          <strong>{fmtTime(durationS)}</strong>
        </div>
        <div className="timeline-stat">
          <span className="small">Playhead</span>
          <strong>{fmtTime(playheadS)}</strong>
        </div>
        <div className="timeline-stat">
          <span className="small">Loop</span>
          <strong>{loopEnabled ? "On" : "Off"} {fmtTime(locatorInS)} - {fmtTime(locatorOutS)}</strong>
        </div>
        <div className="timeline-stat">
          <span className="small">Selection</span>
          <strong>{selectionStatus}</strong>
        </div>
        <div className="timeline-stat">
          <span className="small">Handoffs</span>
          <strong>{handoffReadyCount}/2 ready</strong>
        </div>
      </div>
    </div>
  );

  const toolbarCard = (
    <div className="card timeline-toolbarCard">
      <div className="timeline-toolbarGrid">
        <div className="timeline-toolbarGroup">
          <div className="timeline-toolbarLabel">Session</div>
          <div className="timeline-toolbarFields">
            <div className="timeline-miniField">
              <span className="timeline-miniLabel">Project</span>
              <select value={projectId} onChange={(e) => setProjectId(e.target.value)}>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name || p.id}
                  </option>
                ))}
              </select>
            </div>
            <div className="timeline-miniField">
              <span className="timeline-miniLabel">Variant</span>
              <select
                value={selectedVariant}
                onChange={(e) => setSelectedVariant(Number(e.target.value))}
              >
                {Array.from({ length: Math.max(1, plan?.variants?.length || 1) }).map((_, i) => (
                  <option key={i} value={i}>
                    {plan?.variants?.[i]?.name
                      ? `${i + 1}. ${plan.variants[i].name}`
                      : `Variant ${i + 1}`}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>

        <div className="timeline-toolbarGroup">
          <div className="timeline-toolbarLabel">Transport</div>
          <div className="timeline-toolbarFields">
            <div className="timeline-miniField timeline-miniField--clock">
              <span className="timeline-miniLabel">Position</span>
              <div className="timeline-transportClock" aria-label="Musical position">
                <strong>{musicalClock}</strong>
                <span>{fmtTime(playheadS)}</span>
              </div>
              <input
                aria-label="Playhead time"
                className="timeline-playheadInput"
                type="number"
                step={0.1}
                value={playheadS}
                onChange={(e) => seekTo(Number(e.target.value))}
              />
            </div>
            <div className="timeline-miniField timeline-miniField--transport">
              <span className="timeline-miniLabel">Controls</span>
              <div className="timeline-transportControls">
                <button className="secondary timeline-iconButton" type="button" aria-label="Go to start" title="Go to start" onClick={() => seekTo(0)}>
                  <SkipBack size={16} aria-hidden="true" />
                </button>
                <button className="secondary timeline-iconButton" type="button" aria-label="Previous grid" title="Previous grid" onClick={jumpToPreviousGrid}>
                  <StepBack size={16} aria-hidden="true" />
                </button>
                <button className="primary timeline-iconButton" type="button" aria-label={isPlaying ? "Pause" : "Play"} title={isPlaying ? "Pause" : "Play"} onClick={playPause}>
                  {isPlaying ? <Pause size={17} aria-hidden="true" /> : <Play size={17} aria-hidden="true" />}
                </button>
                <button className="secondary timeline-iconButton" type="button" aria-label="Next grid" title="Next grid" onClick={jumpToNextGrid}>
                  <StepForward size={16} aria-hidden="true" />
                </button>
                <button className="secondary timeline-iconButton" type="button" aria-label="Go to end" title="Go to end" onClick={() => seekTo(durationS)}>
                  <SkipForward size={16} aria-hidden="true" />
                </button>
                <button
                  className={`${loopEnabled ? "primary" : "secondary"} timeline-loopButton`}
                  type="button"
                  aria-label={loopEnabled ? "Disable loop" : "Enable loop"}
                  title={loopEnabled ? "Disable loop" : "Enable loop"}
                  onClick={() => setLoopEnabled((value) => !value)}
                >
                  <Repeat size={15} aria-hidden="true" />
                  <span>Loop</span>
                </button>
              </div>
            </div>
            <div className="timeline-miniField timeline-miniField--master">
              <span className="timeline-miniLabel">Master</span>
              <div className="timeline-masterControl">
                <button
                  className={`secondary timeline-iconButton${masterMuted ? " is-active" : ""}`}
                  type="button"
                  aria-label={masterMuted ? "Unmute master" : "Mute master"}
                  title={masterMuted ? "Unmute master" : "Mute master"}
                  onClick={() => setMasterMuted((value) => !value)}
                >
                  {masterMuted ? <VolumeX size={16} aria-hidden="true" /> : <Volume2 size={16} aria-hidden="true" />}
                </button>
                <input
                  aria-label="Master volume"
                  type="range"
                  min={0}
                  max={1}
                  step={0.01}
                  value={masterVolume}
                  onChange={(e) => setMasterVolume(Number(e.target.value))}
                />
                <span>{Math.round(masterVolume * 100)}</span>
              </div>
            </div>
            <div className="timeline-miniField">
              <span className="timeline-miniLabel">Zoom</span>
              <div className="timeline-zoomControls">
                <button className="secondary" type="button" onClick={() => setTimelineZoomWithFocus(pxPerSecond / 1.18)}>
                  -
                </button>
                <input
                  aria-label="Timeline zoom"
                  type="range"
                  min={TIMELINE_MIN_ZOOM}
                  max={TIMELINE_MAX_ZOOM}
                  value={pxPerSecond}
                  onChange={(e) => setTimelineZoomWithFocus(Number(e.target.value))}
                />
                <button className="secondary" type="button" onClick={() => setTimelineZoomWithFocus(pxPerSecond * 1.18)}>
                  +
                </button>
                <button className="secondary" type="button" onClick={fitTimelineZoom}>
                  Fit all
                </button>
              </div>
              <div className="timeline-zoomMeta">
                <input
                  aria-label="Timeline zoom pixels per second"
                  type="number"
                  step={5}
                  value={Number(pxPerSecond.toFixed(1))}
                  onChange={(e) => setTimelineZoomWithFocus(Number(e.target.value))}
                />
                <span className="small">Ctrl/Cmd + wheel zoom</span>
              </div>
            </div>
            <div className="timeline-toolbarActions">
              <button
                className="primary"
                onClick={() => void syncToRenderer()}
                disabled={!projectId}
                title="Save the timeline and open it in the internal renderer"
              >
                Sync to renderer
              </button>
              <button className="secondary" onClick={() => setSelected(null)}>
                Clear selection
              </button>
            </div>
          </div>
        </div>

        <div className="timeline-toolbarGroup">
          <div className="timeline-toolbarLabel">Grid</div>
          <div className="timeline-toolbarFields">
            <div className="timeline-miniField">
              <span className="timeline-miniLabel">BPM</span>
              <input
                type="number"
                step={0.1}
                placeholder="auto"
                value={bpmOverride ?? ""}
                onChange={(e) => setBpmOverride(e.target.value ? Number(e.target.value) : null)}
              />
            </div>
            <div className="timeline-miniField">
              <span className="timeline-miniLabel">Quantize</span>
              <select
                aria-label="Quantize grid"
                value={String(quantizeBeats)}
                onChange={(e) => setQuantizeBeats(Number(e.target.value))}
              >
                <option value="1">1 beat</option>
                <option value="0.5">1/2 beat</option>
                <option value="0.25">1/4 beat</option>
              </select>
            </div>
            <div className="timeline-miniField">
              <span className="timeline-miniLabel">Density</span>
              <select value={timelineDensity} onChange={(e) => setTimelineDensity(e.target.value as TimelineDensity)}>
                <option value="compact">Compact</option>
                <option value="comfortable">Comfortable</option>
              </select>
            </div>
            <div className="timeline-miniField">
              <span className="timeline-miniLabel">Editor</span>
              <div className="timeline-editorToggles">
                <button
                  className={`secondary timeline-toggleButton${editTool === "select" ? " is-active" : ""}`}
                  type="button"
                  aria-pressed={editTool === "select"}
                  title="Select, move, and trim timeline clips."
                  onClick={() => setEditTool("select")}
                >
                  Select
                </button>
                <button
                  className={`secondary timeline-toggleButton${editTool === "blade" ? " is-active" : ""}`}
                  type="button"
                  aria-pressed={editTool === "blade"}
                  title="Split a clip by clicking the desired cut point."
                  onClick={() => setEditTool("blade")}
                >
                  Blade
                </button>
                <button
                  className={`secondary timeline-toggleButton${snapEnabled ? " is-active" : ""}`}
                  type="button"
                  aria-label={snapEnabled ? "Disable snap" : "Enable snap"}
                  title="Snap clip moves, trims, and playhead parking to the active grid. Hold Alt to bypass."
                  onClick={() => setSnapEnabled((value) => !value)}
                >
                  <Magnet size={15} aria-hidden="true" />
                  Snap
                </button>
                <button
                  className="secondary timeline-toggleButton"
                  type="button"
                  aria-label={timelineTimebase === "bars" ? "Show time ruler" : "Show bars and beats ruler"}
                  onClick={() => setTimelineTimebase((value) => (value === "bars" ? "time" : "bars"))}
                >
                  {timelineTimebase === "bars" ? <Music2 size={15} aria-hidden="true" /> : <Clock3 size={15} aria-hidden="true" />}
                  {timelineTimebase === "bars" ? "Bars" : "Time"}
                </button>
                <button
                  className={`secondary timeline-toggleButton${rippleEnabled ? " is-active" : ""}`}
                  type="button"
                  aria-pressed={rippleEnabled}
                  title="Close or create time on the edited track after moving, trimming, quantizing, or deleting a clip."
                  onClick={() => setRippleEnabled((value) => !value)}
                >
                  Ripple
                </button>
              </div>
            </div>
            <div className="timeline-miniField timeline-miniField--locator">
              <span className="timeline-miniLabel">Loop range</span>
              <div className="timeline-locatorInputs">
                <label className="small">
                  In
                  <input
                    aria-label="Loop in"
                    type="number"
                    step={0.1}
                    value={locatorInS}
                    onChange={(e) => setLoopRange(Number(e.target.value), locatorOutS)}
                  />
                </label>
                <label className="small">
                  Out
                  <input
                    aria-label="Loop out"
                    type="number"
                    step={0.1}
                    value={locatorOutS}
                    onChange={(e) => setLoopRange(locatorInS, Number(e.target.value))}
                  />
                </label>
              </div>
              <div className="timeline-locatorActions">
                <button className="secondary" type="button" onClick={() => setLoopRange(playheadS, locatorOutS)}>
                  Set in
                </button>
                <button className="secondary" type="button" onClick={() => setLoopRange(locatorInS, playheadS)}>
                  Set out
                </button>
                <button className="secondary" type="button" disabled={!selected} onClick={useSelectionAsLoopRange}>
                  Use selection
                </button>
                <button className="secondary" type="button" onClick={() => setLoopRange(0, durationS)}>
                  Full
                </button>
              </div>
            </div>
            <div className="timeline-toolbarActions timeline-toolbarActions--wide">
              <button className="secondary" disabled={!timelineHistory.canUndo} onClick={undoTimeline}>
                Undo
              </button>
              <button className="secondary" disabled={!timelineHistory.canRedo} onClick={redoTimeline}>
                Redo
              </button>
              <button className="secondary" disabled={!selected || selectedLaneLocked} onClick={quantizeSelection}>
                Quantize
              </button>
              <button className="secondary" disabled={!selected || selectedLaneLocked} onClick={splitSelection}>
                Split
              </button>
              <button className="secondary" disabled={!selected || selectedLaneLocked} onClick={duplicateSelection}>
                Duplicate
              </button>
              <button className="secondary" disabled={!selected || selectedLaneLocked} onClick={nudgeSelectionToPlayhead}>
                Nudge to playhead
              </button>
              <button className="secondary" disabled={!selected || selectedLaneLocked} onClick={deleteSelection}>
                Delete
              </button>
              <button className={timelineDirty ? "primary" : "secondary"} onClick={saveTimeline}>
                {timelineDirty ? "Save timeline *" : "Save timeline"}
              </button>
            </div>
          </div>
        </div>
      </div>

      <div className="timeline-toolbarFooter">
        <div className="small">
          Space play/pause · S split · D duplicate · Q quantize · L loop · ←/→ grid · Alt bypasses snap · Ripple shifts later clips on the same track
        </div>
        <div className="small timeline-gridSource"><Magnet size={13} aria-hidden="true" /> {snapEnabled ? "Snap on" : "Snap off"} · {quantizeStatus}</div>
      </div>

      {audioUrl ? (
        <audio
          ref={audioRef}
          preload="metadata"
          className="timeline-audioElement"
        />
      ) : (
        <div className="small timeline-audioEmpty">No audio uploaded for this project.</div>
      )}
    </div>
  );

  const arrangementCard = (
    <div
      className="card timeline-arrangementCard"
      onPointerMove={onTimelinePointerMove}
      onPointerUp={onTimelinePointerUp}
      onPointerCancel={onTimelinePointerUp}
    >
      <div className="timeline-panelHeader">
        <div>
          <div className="timeline-panelTitle timeline-panelTitle--arrangement">
            <AudioLines size={17} aria-hidden="true" />
            Arrangement
          </div>
          <div className="small">
            Drag clips to arrange, pull their edges to trim, and click empty lane space to park the playhead.
          </div>
        </div>
        <div className="timeline-panelTools">
          <div className="timeline-viewControls" role="group" aria-label="Arrangement lane visibility">
            <button
              className={`secondary timeline-viewToggle${showReferenceLanes ? " is-active" : ""}`}
              type="button"
              aria-label={`${showReferenceLanes ? "Hide" : "Show"} reference lanes`}
              aria-pressed={showReferenceLanes}
              onClick={() => setShowReferenceLanes((visible) => !visible)}
            >
              <AudioLines size={14} aria-hidden="true" />
              Reference
            </button>
            <button
              className={`secondary timeline-viewToggle${showAutomationLanes ? " is-active" : ""}`}
              type="button"
              aria-label={`${showAutomationLanes ? "Hide" : "Show"} automation lanes`}
              aria-pressed={showAutomationLanes}
              onClick={() => setShowAutomationLanes((visible) => !visible)}
            >
              <Sparkles size={14} aria-hidden="true" />
              Automation
            </button>
          </div>
          <div className="timeline-panelMeta">
            <span className="badge">{tracks.length} tracks</span>
            <span className="badge">{layers.length} overlays</span>
            <span className="badge">{camKeyframes.length} camera keyframes</span>
          </div>
        </div>
      </div>

      <div className="timeline-board">
        <div className="timeline-boardRail">
          <div className="timeline-railCell timeline-railCell--header">
            <div className="timeline-trackIdentity">
              <span className="timeline-trackNumber">#</span>
              <div>
                <div className="timeline-railTitle">Tracks</div>
                <div className="timeline-railMeta">{timelineTimebase === "bars" ? "Bars + beats" : "Minutes + seconds"}</div>
              </div>
            </div>
          </div>

          {showReferenceLanes ? (
            <>
              <div className="timeline-railCell timeline-railCell--wave" aria-label="Audio master reference lane header">
                <div className="timeline-trackIdentity">
                  <span className="timeline-trackNumber timeline-trackNumber--audio"><AudioLines size={14} aria-hidden="true" /></span>
                  <div>
                    <div className="timeline-railTitle">Audio master</div>
                    <div className="timeline-railMeta">Reference waveform</div>
                  </div>
                </div>
                <span className="timeline-railState">REF</span>
              </div>

              <div className="timeline-railCell timeline-railCell--musicMap" aria-label="Music map reference lane header">
                <div className="timeline-trackIdentity">
                  <span className="timeline-trackNumber">M</span>
                  <div className="timeline-trackCopy">
                    <div className="timeline-railTitle">Music map</div>
                    <div className="timeline-railMeta">{musicSections.length} sections · {musicCues.length} cues</div>
                  </div>
                </div>
                <span className="timeline-railState">MAP</span>
              </div>
            </>
          ) : null}

          {tracks.map((tr, trackIdx) => {
            const laneId = laneIdForTrack(tr, trackIdx);
            const locked = isLaneLocked(laneId);
            const type = String(tr.type).toLowerCase();
            return (
              <div key={tr.id || trackIdx} className={`timeline-railCell timeline-railCell--${type}${locked ? " is-locked" : ""}`}>
                <div className="timeline-trackIdentity">
                  <span className="timeline-trackNumber">{String(trackIdx + 1).padStart(2, "0")}</span>
                    <div className="timeline-trackCopy">
                      <div className="timeline-railTitle">{tr.name}</div>
                      <div className="timeline-railMeta">
                        {type === "motion" ? "MOTION · 2D/3D" : type.toUpperCase()} · {(tr.clips || []).length} clips
                      </div>
                    </div>
                  </div>
                  <div className="timeline-railActions">
                    {type === "motion" ? (
                      <button
                        className="secondary timeline-trackButton"
                        type="button"
                        aria-label="Add motion variety"
                        title="Apply varied pan, crane, orbit, and parallax presets to simple motion clips"
                        disabled={locked}
                        onClick={() => applyMotionVariety(trackIdx)}
                      >
                        <Sparkles size={14} aria-hidden="true" />
                      </button>
                    ) : null}
                    {type === "prompt" || type === "motion" ? (
                    <button
                      className="secondary timeline-trackButton"
                      type="button"
                      aria-label={`Add ${type} clip`}
                      title={`Add ${type} clip at playhead`}
                      disabled={locked}
                      onClick={() => addClip(type as "prompt" | "motion")}
                    >
                      <Plus size={14} aria-hidden="true" />
                    </button>
                  ) : null}
                  <button
                    className={`secondary timeline-trackButton${locked ? " is-active" : ""}`}
                    type="button"
                    aria-label={`${locked ? "Unlock" : "Lock"} ${tr.name} track`}
                    title={`${locked ? "Unlock" : "Lock"} ${tr.name} editing`}
                    onClick={() => toggleLaneLock(laneId)}
                  >
                    {locked ? <Lock size={14} aria-hidden="true" /> : <Unlock size={14} aria-hidden="true" />}
                  </button>
                </div>
              </div>
            );
          })}

          {showAutomationLanes ? (
            <>
              <div className={`timeline-railCell timeline-railCell--overlay${isLaneLocked("overlays") ? " is-locked" : ""}`} aria-label="Overlays automation lane header">
                <div className="timeline-trackIdentity">
                  <span className="timeline-trackNumber">{String(tracks.length + 1).padStart(2, "0")}</span>
                  <div className="timeline-trackCopy">
                    <div className="timeline-railTitle">Overlays</div>
                    <div className="timeline-railMeta">VISUAL · {layers.length} clips</div>
                  </div>
                </div>
                <div className="timeline-railActions">
                  <button
                    className={`secondary timeline-trackButton${isLaneLocked("overlays") ? " is-active" : ""}`}
                    type="button"
                    aria-label={`${isLaneLocked("overlays") ? "Unlock" : "Lock"} Overlays track`}
                    title={`${isLaneLocked("overlays") ? "Unlock" : "Lock"} Overlays editing`}
                    onClick={() => toggleLaneLock("overlays")}
                  >
                    {isLaneLocked("overlays") ? <Lock size={14} aria-hidden="true" /> : <Unlock size={14} aria-hidden="true" />}
                  </button>
                </div>
              </div>

              <div className={`timeline-railCell timeline-railCell--camera${isLaneLocked("camera") ? " is-locked" : ""}`} aria-label="Camera automation lane header">
                <div className="timeline-trackIdentity">
                  <span className="timeline-trackNumber">{String(tracks.length + 2).padStart(2, "0")}</span>
                  <div className="timeline-trackCopy">
                    <div className="timeline-railTitle">Camera</div>
                    <div className="timeline-railMeta">AUTOMATION · {camKeyframes.length} points</div>
                  </div>
                </div>
                <div className="timeline-railActions">
                  <button
                    className="secondary timeline-trackButton"
                    type="button"
                    aria-label="Add camera keyframe"
                    title="Add camera keyframe at playhead"
                    disabled={isLaneLocked("camera")}
                    onClick={addCameraKeyframe}
                  >
                    <Plus size={14} aria-hidden="true" />
                  </button>
                  <button
                    className={`secondary timeline-trackButton${isLaneLocked("camera") ? " is-active" : ""}`}
                    type="button"
                    aria-label={`${isLaneLocked("camera") ? "Unlock" : "Lock"} Camera track`}
                    title={`${isLaneLocked("camera") ? "Unlock" : "Lock"} Camera editing`}
                    onClick={() => toggleLaneLock("camera")}
                  >
                    {isLaneLocked("camera") ? <Lock size={14} aria-hidden="true" /> : <Unlock size={14} aria-hidden="true" />}
                  </button>
                </div>
              </div>
            </>
          ) : null}
        </div>

        <div
          className="timeline-boardScroll"
          ref={timelineScrollRef}
          onWheel={onTimelineWheel}
        >
          <div className="timeline-boardCanvas" style={timelineSurfaceStyle}>
            <div
              className="timeline-locatorRange"
              style={{
                left: clipPx(locatorInS),
                width: Math.max(2, clipPx(locatorOutS) - clipPx(locatorInS)),
              }}
              aria-hidden="true"
            />
            <div className="timeline-locatorFlag timeline-locatorFlag--in" style={{ left: clipPx(locatorInS) }} aria-hidden="true">IN</div>
            <div className="timeline-locatorFlag timeline-locatorFlag--out" style={{ left: clipPx(locatorOutS) }} aria-hidden="true">OUT</div>
            {timelineGridMarkers.map((marker) => (
              <div
                key={marker}
                className={`timeline-gridMarker${barMarkerTimes.has(marker.toFixed(4)) ? " is-bar" : ""}`}
                style={{ left: clipPx(marker) }}
                aria-hidden="true"
              />
            ))}
            <div className="timeline-rulerRow" onPointerDown={onRulerPointerDown}>
              {timelineTimebase === "bars" && musicalRulerTicks.length ? (
                musicalRulerTicks.map((tick) => (
                  <div key={tick.time} className={`timeline-rulerTick timeline-rulerTick--beat${tick.isBar ? " is-bar" : ""}`} style={{ left: clipPx(tick.time) }}>
                    <div className="timeline-rulerTickLine" />
                    {(tick.isBar || pxPerSecond >= 84) ? <div className="timeline-rulerTickLabel">{tick.label}</div> : null}
                  </div>
                ))
              ) : (
                rulerTicks.map((tick) => (
                  <div key={tick} className="timeline-rulerTick" style={{ left: clipPx(tick) }}>
                    <div className="timeline-rulerTickLine" />
                    <div className="timeline-rulerTickLabel">{fmtTime(tick)}</div>
                  </div>
                ))
              )}
            </div>

            {showReferenceLanes ? (
              <>
                <div className="timeline-waveformRow" onClick={onWaveformClick} aria-label="Audio master reference lane">
                  <canvas
                    ref={canvasRef}
                    width={timelineCanvasWidth}
                    height={92}
                    className="timeline-waveformCanvas"
                  />
                </div>

                <div className="timeline-laneRow timeline-laneRow--musicMap" onPointerDown={onLanePointerDown} aria-label="Music map reference lane">
                  {musicSections.map((section: AnyDict) => (
                    <div
                      key={section.id}
                      className="timeline-musicSection"
                      style={{
                        left: clipPx(clamp(section.start, 0, durationS)),
                        width: Math.max(18, clipPx(clamp(section.end, section.start, durationS)) - clipPx(section.start)),
                      }}
                      title={`${section.label}: ${fmtTime(section.start)}–${fmtTime(section.end)}`}
                    >
                      {section.label}
                    </div>
                  ))}
                  {musicCues.map((cue: AnyDict) => (
                    <div
                      key={cue.id}
                      className="timeline-musicCue"
                      style={{ left: clipPx(cue.time) }}
                      title={`${cue.label}: ${fmtTime(cue.time)}`}
                      aria-label={`${cue.label} at ${fmtTime(cue.time)}`}
                    >
                      <span>{cue.label}</span>
                    </div>
                  ))}
                </div>
              </>
            ) : null}

            {tracks.map((tr, trackIdx) => (
              <div
                key={tr.id || trackIdx}
                className={`timeline-laneRow timeline-laneRow--${String(tr.type).toLowerCase()}${isLaneLocked(laneIdForTrack(tr, trackIdx)) ? " is-locked" : ""}`}
                onPointerDown={onLanePointerDown}
              >
                {(tr.clips || []).map((cl, i) => {
                  const left = clipPx(cl.start_s);
                  const width = Math.max(16, clipPx(cl.end_s) - clipPx(cl.start_s));
                  const isSel =
                    selected?.kind === "track" &&
                    selected.trackIdx === trackIdx &&
                    selected.clipIdx === i;
                  return (
                    <div
                      key={cl.id || i}
                      className={`timeline-laneClip timeline-laneClip--${String(tr.type).toLowerCase()}${isSel ? " is-selected" : ""}${isLaneLocked(laneIdForTrack(tr, trackIdx)) ? " is-locked" : ""}`}
                      onPointerDown={onTrackClipPointerDown(trackIdx, i, "move")}
                      role="button"
                      tabIndex={0}
                      aria-pressed={isSel}
                      aria-label={`${tr.name} clip, ${fmtLabel(tr.type, cl)}, ${fmtTime(cl.start_s)} to ${fmtTime(cl.end_s)}`}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          event.stopPropagation();
                          setSelected({ kind: "track", trackIdx, clipIdx: i });
                          setDiffStart(cl.start_s);
                          setDiffEnd(cl.end_s);
                        }
                      }}
                      style={{ left, width }}
                      title={fmtLabel(tr.type, cl)}
                    >
                      <div className="timeline-laneClipBody">
                        <div className="timeline-laneClipLabel">{fmtLabel(tr.type, cl)}</div>
                        <div className="timeline-laneClipTime">{fmtTime(cl.end_s - cl.start_s)}</div>
                      </div>
                      <div
                        className="timeline-laneClipHandle timeline-laneClipHandle--left"
                        onPointerDown={onTrackClipPointerDown(trackIdx, i, "left")}
                      />
                      <div
                        className="timeline-laneClipHandle timeline-laneClipHandle--right"
                        onPointerDown={onTrackClipPointerDown(trackIdx, i, "right")}
                      />
                    </div>
                  );
                })}
              </div>
            ))}

            {showAutomationLanes ? (
              <>
                <div className={`timeline-laneRow timeline-laneRow--overlay${isLaneLocked("overlays") ? " is-locked" : ""}`} onPointerDown={onLanePointerDown} aria-label="Overlays automation lane">
              {layers.map((l, i) => {
                const s = Number(l.start_s ?? 0);
                const e = Number(l.end_s ?? durationS);
                const left = clipPx(s);
                const width = Math.max(16, clipPx(e) - clipPx(s));
                const label =
                  l.type === "image"
                    ? String(l.asset || "image")
                    : l.type === "text"
                      ? String(l.text || "text").slice(0, 32)
                      : String(l.type || "layer");
                const isSel = selected?.kind === "overlay" && selected.layerIdx === i;

                return (
                  <div
                    key={i}
                    className={`timeline-laneClip timeline-laneClip--overlay${isSel ? " is-selected" : ""}${isLaneLocked("overlays") ? " is-locked" : ""}`}
                    onPointerDown={onOverlayPointerDown(i, "move")}
                    style={{ left, width }}
                    title={label}
                  >
                    <div className="timeline-laneClipBody">
                      <div className="timeline-laneClipLabel">{label}</div>
                      <div className="timeline-laneClipTime">{fmtTime(e - s)}</div>
                    </div>
                    <div
                      className="timeline-laneClipHandle timeline-laneClipHandle--left"
                      onPointerDown={onOverlayPointerDown(i, "left")}
                    />
                    <div
                      className="timeline-laneClipHandle timeline-laneClipHandle--right"
                      onPointerDown={onOverlayPointerDown(i, "right")}
                    />
                  </div>
                );
              })}
                </div>

                <div className={`timeline-laneRow timeline-laneRow--camera${isLaneLocked("camera") ? " is-locked" : ""}`} onPointerDown={onLanePointerDown} aria-label="Camera automation lane">
              {camKeyframes.map((k, i) => {
                const x = clipPx(Number(k.t || 0));
                const isSel = selected?.kind === "camera" && selected.kfIdx === i;
                return (
                  <div
                    key={i}
                    className={`timeline-keyframe${isSel ? " is-selected" : ""}`}
                    onPointerDown={onCameraKfPointerDown(i)}
                    aria-label={`Camera keyframe at ${fmtTime(Number(k.t || 0))}`}
                    title={`t=${fmtTime(Number(k.t || 0))} • zoom=${Number(k.zoom || 1).toFixed(2)}`}
                    style={{ left: x - 7 }}
                  />
                );
              })}
                </div>
              </>
            ) : null}
            <div className="timeline-globalPlayhead" style={{ left: clipPx(playheadS) }} aria-hidden="true">
              <span />
            </div>
          </div>
        </div>
      </div>
    </div>
  );

  const inspectorPanel = (
    <div className="timeline-dockPanel">
      <div className="timeline-panelHeader">
        <div>
          <div className="timeline-panelTitle">Inspector</div>
          <div className="small">
            Selection-aware controls stay docked instead of hiding below the timeline.
          </div>
        </div>
        <span className="badge">{selected ? selected.kind : "none"}</span>
      </div>

      <div className="timeline-inlineActions">
        <button className="secondary" disabled={!selected || selectedLaneLocked} onClick={nudgeSelectionToPlayhead}>
          Nudge to playhead
        </button>
        <button className="secondary" disabled={!selected || selectedLaneLocked} onClick={deleteSelection}>
          Delete selection
        </button>
      </div>

      {selected?.kind === "track" ? (
        (() => {
          const picked = selectedTrackClip(selected);
          if (!picked) return <div className="small timeline-emptyState">No selection.</div>;
          const { tr, cl } = picked;
          const tt = String(tr.type).toLowerCase();
          return (
            <>
              <div className="small timeline-inspectorMeta">
                {tr.name}: {fmtTime(cl.start_s)} → {fmtTime(cl.end_s)}
              </div>
              <div className="timeline-clipTiming" aria-label="Clip timing">
                <label>
                  <span>Start</span>
                  <input
                    aria-label="Clip start"
                    type="number"
                    disabled={selectedLaneLocked}
                    step={0.1}
                    value={cl.start_s}
                    onChange={(e) => updateSelectedClipTimes(Number(e.target.value), cl.end_s)}
                  />
                </label>
                <label>
                  <span>End</span>
                  <input
                    aria-label="Clip end"
                    type="number"
                    disabled={selectedLaneLocked}
                    step={0.1}
                    value={cl.end_s}
                    onChange={(e) => updateSelectedClipTimes(cl.start_s, Number(e.target.value))}
                  />
                </label>
                <label>
                  <span>Length</span>
                  <input
                    aria-label="Clip length"
                    type="number"
                    disabled={selectedLaneLocked}
                    step={0.1}
                    value={Number((cl.end_s - cl.start_s).toFixed(3))}
                    onChange={(e) => updateSelectedClipTimes(cl.start_s, cl.start_s + Number(e.target.value))}
                  />
                </label>
              </div>
              {tt === "prompt" ? (
                <>
                  <textarea
                    className="timeline-inspectorTextarea"
                    aria-label="Prompt text"
                    disabled={selectedLaneLocked}
                    value={String(cl.data?.prompt || "")}
                    onChange={(e) => updateSelectedClipData({ prompt: e.target.value })}
                  />
                  <div className="small timeline-inspectorHint">
                    Internal render uses this prompt track whenever a prompt lane is present.
                  </div>
                </>
              ) : tt === "motion" ? (
                (() => {
                  const presetId = motionPresetSelection(cl.data || {});
                  const preset = MOTION_PRESET_BY_ID.get(presetId);
                  const presetDescription =
                    presetId === "reactive"
                      ? "Audio-reactive schedules are already driving one or more motion axes. Choose a preset to replace those camera schedules for this clip."
                      : preset?.description || "Fine-tune any axis below to create a custom camera move.";
                  return (
                    <>
                      <div className="small timeline-inspectorHint">
                        Motion clips drive real 2D and 3D camera movement as well as diffusion controls.
                      </div>
                      <div className="timeline-motionPreset">
                        <label className="small" htmlFor="timeline-motion-preset">Motion preset</label>
                        <select
                          id="timeline-motion-preset"
                          aria-label="Motion preset"
                          disabled={selectedLaneLocked}
                          value={presetId}
                          onChange={(e) => applySelectedMotionPreset(e.target.value)}
                        >
                          {presetId === "reactive" ? <option value="reactive">Audio reactive schedules</option> : null}
                          <option value="custom">Custom motion</option>
                          {MOTION_PRESETS.map((option) => (
                            <option key={option.id} value={option.id}>{option.label}</option>
                          ))}
                        </select>
                        <div className="small timeline-motionPresetDescription">{presetDescription}</div>
                      </div>
                      <div className="timeline-fieldGrid timeline-fieldGrid--compact timeline-motionAxisGrid">
                        <label className="small">Zoom start</label>
                        <input
                          aria-label="Motion zoom start"
                          type="number"
                          step={0.01}
                          disabled={selectedLaneLocked}
                          value={Number(cl.data?.zoom_start ?? 1)}
                          onChange={(e) => updateSelectedMotionField("zoom_start", Number(e.target.value))}
                        />
                        <label className="small">Zoom end</label>
                        <input
                          aria-label="Motion zoom end"
                          type="number"
                          step={0.01}
                          disabled={selectedLaneLocked}
                          value={Number(cl.data?.zoom_end ?? 1)}
                          onChange={(e) => updateSelectedMotionField("zoom_end", Number(e.target.value))}
                        />
                        <label className="small">Pan X start</label>
                        <input
                          aria-label="Motion pan X start"
                          type="number"
                          step={0.5}
                          disabled={selectedLaneLocked}
                          value={Number(cl.data?.pan_x_start ?? 0)}
                          onChange={(e) => updateSelectedMotionField("pan_x_start", Number(e.target.value))}
                        />
                        <label className="small">Pan X end</label>
                        <input
                          aria-label="Motion pan X end"
                          type="number"
                          step={0.5}
                          disabled={selectedLaneLocked}
                          value={Number(cl.data?.pan_x_end ?? 0)}
                          onChange={(e) => updateSelectedMotionField("pan_x_end", Number(e.target.value))}
                        />
                        <label className="small">Pan Y start</label>
                        <input
                          aria-label="Motion pan Y start"
                          type="number"
                          step={0.5}
                          disabled={selectedLaneLocked}
                          value={Number(cl.data?.pan_y_start ?? 0)}
                          onChange={(e) => updateSelectedMotionField("pan_y_start", Number(e.target.value))}
                        />
                        <label className="small">Pan Y end</label>
                        <input
                          aria-label="Motion pan Y end"
                          type="number"
                          step={0.5}
                          disabled={selectedLaneLocked}
                          value={Number(cl.data?.pan_y_end ?? 0)}
                          onChange={(e) => updateSelectedMotionField("pan_y_end", Number(e.target.value))}
                        />
                        <label className="small">Rotation start</label>
                        <input
                          aria-label="Motion rotation start"
                          type="number"
                          step={0.1}
                          disabled={selectedLaneLocked}
                          value={Number(cl.data?.rotation_start ?? 0)}
                          onChange={(e) => updateSelectedMotionField("rotation_start", Number(e.target.value))}
                        />
                        <label className="small">Rotation end</label>
                        <input
                          aria-label="Motion rotation end"
                          type="number"
                          step={0.1}
                          disabled={selectedLaneLocked}
                          value={Number(cl.data?.rotation_end ?? 0)}
                          onChange={(e) => updateSelectedMotionField("rotation_end", Number(e.target.value))}
                        />
                      </div>
                      <details className="timeline-motionAdvanced">
                        <summary>3D orbit + render controls</summary>
                        <div className="timeline-fieldGrid timeline-fieldGrid--compact">
                          <label className="small">Depth start</label>
                          <input aria-label="Motion depth start" type="number" step={0.5} disabled={selectedLaneLocked} value={Number(cl.data?.pan_z_start ?? 0)} onChange={(e) => updateSelectedMotionField("pan_z_start", Number(e.target.value))} />
                          <label className="small">Depth end</label>
                          <input aria-label="Motion depth end" type="number" step={0.5} disabled={selectedLaneLocked} value={Number(cl.data?.pan_z_end ?? 0)} onChange={(e) => updateSelectedMotionField("pan_z_end", Number(e.target.value))} />
                          <label className="small">Pitch start</label>
                          <input aria-label="Motion pitch start" type="number" step={0.1} disabled={selectedLaneLocked} value={Number(cl.data?.pitch_start ?? 0)} onChange={(e) => updateSelectedMotionField("pitch_start", Number(e.target.value))} />
                          <label className="small">Pitch end</label>
                          <input aria-label="Motion pitch end" type="number" step={0.1} disabled={selectedLaneLocked} value={Number(cl.data?.pitch_end ?? 0)} onChange={(e) => updateSelectedMotionField("pitch_end", Number(e.target.value))} />
                          <label className="small">Yaw start</label>
                          <input aria-label="Motion yaw start" type="number" step={0.1} disabled={selectedLaneLocked} value={Number(cl.data?.yaw_start ?? 0)} onChange={(e) => updateSelectedMotionField("yaw_start", Number(e.target.value))} />
                          <label className="small">Yaw end</label>
                          <input aria-label="Motion yaw end" type="number" step={0.1} disabled={selectedLaneLocked} value={Number(cl.data?.yaw_end ?? 0)} onChange={(e) => updateSelectedMotionField("yaw_end", Number(e.target.value))} />
                          <label className="small">Roll start</label>
                          <input aria-label="Motion roll start" type="number" step={0.1} disabled={selectedLaneLocked} value={Number(cl.data?.roll_start ?? 0)} onChange={(e) => updateSelectedMotionField("roll_start", Number(e.target.value))} />
                          <label className="small">Roll end</label>
                          <input aria-label="Motion roll end" type="number" step={0.1} disabled={selectedLaneLocked} value={Number(cl.data?.roll_end ?? 0)} onChange={(e) => updateSelectedMotionField("roll_end", Number(e.target.value))} />
                          <label className="small">Strength</label>
                          <input aria-label="Motion strength" type="number" step={0.01} disabled={selectedLaneLocked} value={Number(cl.data?.strength ?? 0.35)} onChange={(e) => updateSelectedMotionField("strength", Number(e.target.value))} />
                          <label className="small">CFG</label>
                          <input aria-label="Motion CFG" type="number" step={0.1} disabled={selectedLaneLocked} value={Number(cl.data?.cfg ?? 7)} onChange={(e) => updateSelectedMotionField("cfg", Number(e.target.value))} />
                          <label className="small">Steps</label>
                          <input aria-label="Motion steps" type="number" step={1} disabled={selectedLaneLocked} value={Number(cl.data?.steps ?? 12)} onChange={(e) => updateSelectedMotionField("steps", Number(e.target.value))} />
                        </div>
                      </details>
                      <div className="small timeline-inspectorHint">
                        Endpoint edits replace a conflicting schedule for that axis and update matching Camera-lane boundary keyframes.
                      </div>
                    </>
                  );
                })()
              ) : tt === "video" ? (
                <>
                  <div className="small timeline-inspectorHint">
                    The original output is never changed. These source, speed, audio, and fade values are stored as edit decisions.
                  </div>
                  <div className="timeline-fieldGrid timeline-fieldGrid--compact">
                    <label className="small">Source in</label>
                    <input
                      aria-label="Video source in"
                      type="number"
                      min={0}
                      step={0.01}
                      disabled={selectedLaneLocked}
                      value={Number(cl.data?.source_in_s ?? 0)}
                      onChange={(e) => updateSelectedClipData({ source_in_s: Math.max(0, Number(e.target.value)) }, "trim_video_source")}
                    />
                    <label className="small">Source out</label>
                    <input
                      aria-label="Video source out"
                      type="number"
                      min={0.1}
                      step={0.01}
                      disabled={selectedLaneLocked}
                      value={Number(cl.data?.source_out_s ?? cl.end_s - cl.start_s)}
                      onChange={(e) => updateSelectedClipData({ source_out_s: Math.max(0.1, Number(e.target.value)) }, "trim_video_source")}
                    />
                    <label className="small">Speed</label>
                    <input
                      aria-label="Video speed"
                      type="number"
                      min={0.25}
                      max={4}
                      step={0.05}
                      disabled={selectedLaneLocked}
                      value={Number(cl.data?.speed ?? 1)}
                      onChange={(e) => updateSelectedClipData({ speed: clamp(Number(e.target.value), 0.25, 4) }, "change_video_speed")}
                    />
                    <label className="small">Volume</label>
                    <input
                      aria-label="Video volume"
                      type="number"
                      min={0}
                      max={2}
                      step={0.05}
                      disabled={selectedLaneLocked || Boolean(cl.data?.muted)}
                      value={Number(cl.data?.volume ?? 1)}
                      onChange={(e) => updateSelectedClipData({ volume: clamp(Number(e.target.value), 0, 2) }, "change_video_volume")}
                    />
                    <label className="small">Fade in</label>
                    <input
                      aria-label="Video fade in"
                      type="number"
                      min={0}
                      step={0.05}
                      disabled={selectedLaneLocked}
                      value={Number(cl.data?.fade_in_s ?? 0)}
                      onChange={(e) => updateSelectedClipData({ fade_in_s: Math.max(0, Number(e.target.value)) }, "change_video_fade")}
                    />
                    <label className="small">Fade out</label>
                    <input
                      aria-label="Video fade out"
                      type="number"
                      min={0}
                      step={0.05}
                      disabled={selectedLaneLocked}
                      value={Number(cl.data?.fade_out_s ?? 0)}
                      onChange={(e) => updateSelectedClipData({ fade_out_s: Math.max(0, Number(e.target.value)) }, "change_video_fade")}
                    />
                  </div>
                  <label className="timeline-checkRow">
                    <input
                      type="checkbox"
                      disabled={selectedLaneLocked}
                      checked={Boolean(cl.data?.muted)}
                      onChange={(e) => updateSelectedClipData({ muted: e.target.checked }, "mute_video_clip")}
                    />
                    Mute source audio
                  </label>
                  <div className="small timeline-inspectorMeta">
                    Source: {String(cl.data?.source_path || "Missing source")}
                  </div>
                </>
              ) : (
                <div className="small timeline-emptyState">Unsupported track type.</div>
              )}
            </>
          );
        })()
      ) : selected?.kind === "overlay" ? (
        (() => {
          const l = layers[selected.layerIdx];
          if (!l) return <div className="small timeline-emptyState">No selection.</div>;
          const s0 = Number(l.start_s ?? 0);
          const e0 = Number(l.end_s ?? durationS);
          const label =
            l.type === "image"
              ? String(l.asset || "image")
              : l.type === "text"
                ? String(l.text || "text").slice(0, 40)
                : String(l.type || "layer");
          return (
            <>
              <div className="small timeline-inspectorMeta">Overlay: {label}</div>
              <div className="timeline-fieldGrid timeline-fieldGrid--compact">
                <label className="small">start</label>
                <input
                  aria-label="Overlay start"
                  type="number"
                  step={0.1}
                  disabled={selectedLaneLocked}
                  value={s0}
                  onChange={(e) => updateSelectedOverlayTimes(Number(e.target.value), e0)}
                />
                <label className="small">end</label>
                <input
                  aria-label="Overlay end"
                  type="number"
                  step={0.1}
                  disabled={selectedLaneLocked}
                  value={e0}
                  onChange={(e) => updateSelectedOverlayTimes(s0, Number(e.target.value))}
                />
              </div>
              <div className="small timeline-inspectorHint">
                Edit overlay content and screen placement in Render → Visual editor.
              </div>
            </>
          );
        })()
      ) : selected?.kind === "camera" ? (
        (() => {
          const k = camKeyframes[selected.kfIdx];
          if (!k) return <div className="small timeline-emptyState">No selection.</div>;
          return (
            <>
              <div className="small timeline-inspectorMeta">Camera keyframe</div>
              <div className="timeline-fieldGrid timeline-fieldGrid--compact">
                <label className="small">t</label>
                <input
                  aria-label="Camera time"
                  type="number"
                  step={0.1}
                  disabled={selectedLaneLocked}
                  value={Number(k.t || 0)}
                  onChange={(e) => updateSelectedCamera({ t: Number(e.target.value) })}
                />
                <label className="small">zoom</label>
                <input
                  aria-label="Camera zoom"
                  type="number"
                  step={0.01}
                  disabled={selectedLaneLocked}
                  value={Number(k.zoom || 1)}
                  onChange={(e) => updateSelectedCamera({ zoom: Number(e.target.value) })}
                />
                <label className="small">pan_x</label>
                <input
                  aria-label="Camera pan X"
                  type="number"
                  step={0.1}
                  disabled={selectedLaneLocked}
                  value={Number(k.pan_x || 0)}
                  onChange={(e) => updateSelectedCamera({ pan_x: Number(e.target.value) })}
                />
                <label className="small">pan_y</label>
                <input
                  aria-label="Camera pan Y"
                  type="number"
                  step={0.1}
                  disabled={selectedLaneLocked}
                  value={Number(k.pan_y || 0)}
                  onChange={(e) => updateSelectedCamera({ pan_y: Number(e.target.value) })}
                />
                <label className="small">rot</label>
                <input
                  aria-label="Camera rotation"
                  type="number"
                  step={0.1}
                  disabled={selectedLaneLocked}
                  value={Number(k.rotation_deg || 0)}
                  onChange={(e) => updateSelectedCamera({ rotation_deg: Number(e.target.value) })}
                />
              </div>
            </>
          );
        })()
      ) : (
        <div className="small timeline-emptyState">
          Click a clip, overlay, or keyframe to edit it here.
        </div>
      )}
    </div>
  );

  const monitorCard = (
    <div className="card timeline-dockCard">
      <div className="timeline-panelHeader">
        <div>
          <div className="timeline-panelTitle">Program Monitor</div>
          <div className="small">
            {activeTimelineVideoPath
              ? "Source monitor for the selected non-destructive video edit."
              : "Model/timeline frame preview for scrubbing and timing checks."}
          </div>
        </div>
        <span className="badge">Frame</span>
      </div>
      <div className="timeline-monitor">
        {activeTimelineVideoPath && projectId ? (
          <video
            key={activeTimelineVideoPath}
            ref={activeVideoRef}
            controls
            className="timeline-monitorVideo"
            onLoadedMetadata={(event) => {
              const value = Number(event.currentTarget.duration || 0);
              if (Number.isFinite(value) && value > 0) setSelectedMediaDuration(value);
            }}
          />
        ) : previewUrl ? (
          <img src={previewUrl} className="timeline-monitorImage" />
        ) : (
          <div className="small">No preview.</div>
        )}
      </div>
    </div>
  );

  const orchestratorPanel = (
    <div className="timeline-dockPanel">
      <div className="timeline-panelHeader">
        <div>
          <div className="timeline-panelTitle">Render Orchestrator</div>
          <div className="small">
            The current plan, downloaded preview models, and session handoffs stay visible while you edit.
          </div>
        </div>
        <span className="badge">
          {Array.isArray(conductorPlan?.sections) ? `${conductorPlan.sections.length} routed scenes` : "Plan not routed"}
        </span>
      </div>
      <div className="timeline-handoffCard">
        <div className="timeline-handoffLabel">Downloaded model preview</div>
        <strong>
          {installedPreviewModels.length
            ? `${installedPreviewModels.length} compatible model${installedPreviewModels.length === 1 ? "" : "s"} ready`
            : "No compatible internal still model installed"}
        </strong>
        <div className="small">
          {installedPreviewModels.length
            ? installedPreviewModels.map((model) => model.name).join(" • ")
            : "Look Dev will stop with a clear model-readiness error; it never substitutes a synthetic proxy."}
        </div>
      </div>
      <div className="timeline-handoffGrid">
        <div className="timeline-handoffCard">
          <div className="timeline-handoffLabel">Planner</div>
          <strong>{plannerImportedAt ? `${plannerVariantCount} variants available` : "Awaiting planner sync"}</strong>
          <div className="small">
            {plannerImportedAt
              ? `${plannerSceneCount} scenes in the active variant.`
              : "Open Workspace or AI Planner Lab to import storyboard and prompt tracks."}
          </div>
          <ProgressBar value={plannerImportedAt ? 100 : plannerVariantCount ? 72 : 0} compact />
        </div>
        <div className="timeline-handoffCard">
          <div className="timeline-handoffLabel">Reactive</div>
          <strong>{reactiveAppliedAt ? `${reactiveCueCount} cues wired` : "Awaiting reactive sync"}</strong>
          <div className="small">
            {reactiveAppliedAt
              ? `${reactiveSectionCount} reactive sections are merged into motion/camera tracks.`
              : "Open Workspace or Reactive Lab to apply motion schedules and camera data."}
          </div>
          <ProgressBar value={reactiveAppliedAt ? 100 : 0} compact />
        </div>
      </div>
      <div className="timeline-inlineActions">
        <button className="primary" onClick={() => void syncToRenderer()} disabled={!projectId}>
          Sync to renderer
        </button>
        <button className="secondary" onClick={() => setDockSection("inspector")}>
          Open inspector
        </button>
        <button className="secondary" onClick={() => onNavigate?.("models")}>
          Manage models
        </button>
        <button className="secondary" onClick={() => clearHandoff()} disabled={!lastHandoff}>
          Clear notice
        </button>
      </div>
    </div>
  );

  const mediaPanel = (
    <div className="timeline-dockPanel">
      <div className="timeline-panelHeader">
        <div>
          <div className="timeline-panelTitle">Media &amp; Edit Master</div>
          <div className="small">
            Add completed renders to the Video Edit lane, trim them non-destructively, then render a new master.
          </div>
        </div>
        <span className="badge">{mediaLibrary.length} videos</span>
      </div>

      {mediaLibraryBusy ? (
        <ProgressBar value={54} label="Refreshing output media" compact />
      ) : mediaLibrary.length ? (
        <div className="timeline-mediaLibrary" aria-label="Project output videos">
          {mediaLibrary.slice(0, 12).map((entry) => {
            const path = String(entry?.path || "");
            const selectedSource = selectedMediaPath === path;
            return (
              <div key={path} className={`timeline-mediaItem${selectedSource ? " is-selected" : ""}`}>
                <button
                  type="button"
                  className="timeline-mediaSelect"
                  aria-pressed={selectedSource}
                  onClick={() => {
                    setSelectedMediaPath(path);
                    setSelectedMediaDuration(Number(entry?.duration_s || entry?.metadata?.duration_s || 5));
                  }}
                >
                  <span>{String(entry?.name || path)}</span>
                  <span className="small">{String(entry?.kind || "video")}</span>
                </button>
                <button type="button" className="secondary" onClick={() => addMediaClip(entry)}>
                  Add at playhead
                </button>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="small timeline-emptyState">
          No completed output video is available yet. Render with a downloaded model, then refresh this library.
        </div>
      )}

      <div className="timeline-inlineActions">
        <button className="secondary" type="button" onClick={() => void refreshTimelineResources(projectId)} disabled={!projectId || mediaLibraryBusy}>
          Refresh media
        </button>
        <button className="secondary" type="button" onClick={() => onNavigate?.("outputs")}>
          Open Outputs
        </button>
      </div>

      <div className="timeline-panelHeader timeline-editMasterHeader">
        <div>
          <div className="timeline-panelTitle">Edited master</div>
          <div className="small">Real FFmpeg export from the saved Video Edit decisions. Source files stay untouched.</div>
        </div>
      </div>
      <div className="timeline-fieldGrid timeline-fieldGrid--compact timeline-editMasterFields">
        <label className="small" htmlFor="timeline-edit-output-name">Output name</label>
        <div className="timeline-editMasterControl">
          <input
            id="timeline-edit-output-name"
            aria-label="Edited master output name"
            aria-invalid={Boolean(editNameError)}
            maxLength={80}
            value={editOutputName}
            onChange={(event) => setEditOutputName(sanitizeTimelineRenderNameForEditing(event.target.value))}
            onBlur={() => setEditOutputName(editOutputSafeName)}
          />
          <div className="small timeline-editMasterHelp">
            {editOutputSafeName || "edited-master"}{editVideoCodec === "prores" ? ".mov" : ".mp4"} · unsafe filename characters become underscores
          </div>
          {editNameError ? <div className="small timeline-editMasterValidation" role="alert">{editNameError}</div> : null}
        </div>

        <label className="small" htmlFor="timeline-edit-resolution-preset">Size preset</label>
        <select
          id="timeline-edit-resolution-preset"
          aria-label="Output size preset"
          value={editResolutionPreset}
          onChange={(event) => {
            if (event.target.value === "custom") return;
            const [width, height] = event.target.value.split("x").map(Number);
            setEditWidth(width);
            setEditHeight(height);
          }}
        >
          {TIMELINE_RENDER_RESOLUTIONS.map((preset) => (
            <option key={preset.value} value={preset.value}>{preset.label}</option>
          ))}
          <option value="custom">Custom dimensions</option>
        </select>

        <label className="small" htmlFor="timeline-edit-width">Width</label>
        <div className="timeline-editMasterControl">
          <input
            id="timeline-edit-width"
            aria-label="Output width"
            aria-invalid={Boolean(editWidthError)}
            type="number"
            min={256}
            max={7680}
            step={2}
            value={editWidth}
            onChange={(event) => setEditWidth(Number(event.target.value))}
          />
          {editWidthError ? <div className="small timeline-editMasterValidation" role="alert">{editWidthError}</div> : null}
        </div>

        <label className="small" htmlFor="timeline-edit-height">Height</label>
        <div className="timeline-editMasterControl">
          <input
            id="timeline-edit-height"
            aria-label="Output height"
            aria-invalid={Boolean(editHeightError)}
            type="number"
            min={256}
            max={7680}
            step={2}
            value={editHeight}
            onChange={(event) => setEditHeight(Number(event.target.value))}
          />
          {editHeightError ? <div className="small timeline-editMasterValidation" role="alert">{editHeightError}</div> : null}
        </div>

        <label className="small" htmlFor="timeline-edit-fps-preset">Frame-rate preset</label>
        <select
          id="timeline-edit-fps-preset"
          aria-label="Frame-rate preset"
          value={editFpsPreset}
          onChange={(event) => {
            if (event.target.value !== "custom") setEditFps(Number(event.target.value));
          }}
        >
          {TIMELINE_RENDER_FPS_PRESETS.map((value) => <option key={value} value={value}>{value} fps</option>)}
          <option value="custom">Custom FPS</option>
        </select>

        <label className="small" htmlFor="timeline-edit-fps">FPS</label>
        <div className="timeline-editMasterControl">
          <input
            id="timeline-edit-fps"
            aria-label="Output FPS"
            aria-invalid={Boolean(editFpsError)}
            type="number"
            min={1}
            max={120}
            step={0.001}
            value={editFps}
            onChange={(event) => setEditFps(Number(event.target.value))}
          />
          {editFpsError ? <div className="small timeline-editMasterValidation" role="alert">{editFpsError}</div> : null}
        </div>

        <label className="small" htmlFor="timeline-edit-codec">Video codec</label>
        <select
          id="timeline-edit-codec"
          aria-label="Edited master video codec"
          value={editVideoCodec}
          onChange={(event) => setEditVideoCodec(event.target.value as TimelineVideoCodec)}
        >
          <option value="h264">H.264 delivery</option>
          <option value="hevc">H.265 / HEVC</option>
          <option value="prores">ProRes editing master</option>
        </select>

        <label className="small">Audio codec</label>
        <div className="timeline-editMasterPair" aria-label="Edited master audio codec">
          <strong>{editAudioCodec === "pcm_s16le" ? "PCM 16-bit" : "AAC 192 kbps"}</strong>
          <span className="small">Paired automatically with {editVideoCodec === "prores" ? "ProRes" : editVideoCodec === "hevc" ? "HEVC" : "H.264"}</span>
        </div>

        <label className="small" htmlFor="timeline-edit-quality-preset">Quality preset</label>
        <select
          id="timeline-edit-quality-preset"
          aria-label="Edited master quality preset"
          value={editQualityPreset}
          disabled={editVideoCodec === "prores"}
          onChange={(event) => {
            const values: Record<string, number> = { high: 18, medium: 23, low: 28 };
            if (values[event.target.value]) setEditQuality(values[event.target.value]);
          }}
        >
          <option value="high">High · CRF 18</option>
          <option value="medium">Medium · CRF 23</option>
          <option value="low">Smaller file · CRF 28</option>
          <option value="custom">Custom CRF</option>
        </select>

        <label className="small" htmlFor="timeline-edit-quality">CRF</label>
        <div className="timeline-editMasterControl">
          <input
            id="timeline-edit-quality"
            aria-label="Edited master CRF"
            aria-invalid={Boolean(editQualityError)}
            type="number"
            min={1}
            max={51}
            step={1}
            value={editQuality}
            disabled={editVideoCodec === "prores"}
            onChange={(event) => setEditQuality(Number(event.target.value))}
          />
          <div className="small timeline-editMasterHelp">
            {editVideoCodec === "prores" ? "ProRes uses its editing-master profile; CRF does not apply." : "1 is highest quality; 51 is smallest. Presets remain editable."}
          </div>
          {editQualityError ? <div className="small timeline-editMasterValidation" role="alert">{editQualityError}</div> : null}
        </div>
      </div>
      <div
        id="timeline-edit-validation-summary"
        className={`small timeline-editMasterSummary${editMasterSettingsValid ? " is-ready" : " is-invalid"}`}
        aria-live="polite"
      >
        {editMasterSettingsValid
          ? `${editWidth} × ${editHeight} · ${editFps} fps · ${editVideoCodec === "prores" ? "ProRes + PCM" : `${editVideoCodec.toUpperCase()} + AAC`} · ${editVideoCodec === "prores" ? "editing-master profile" : `CRF ${editQuality}`}`
          : editMasterValidationMessages.join(" ")}
      </div>
      <div className="timeline-inlineActions">
        <button
          className="primary"
          type="button"
          aria-describedby="timeline-edit-validation-summary"
          disabled={!projectId || mediaRenderBusy || !editMasterSettingsValid}
          onClick={() => void renderEditedTimeline()}
        >
          {mediaRenderBusy ? "Queueing edit…" : "Render edited master"}
        </button>
        <button className="secondary" type="button" onClick={() => onNavigate?.("queue")}>
          Render Queue
        </button>
      </div>
      {mediaRenderStatus ? <div className="small timeline-inspectorHint">{mediaRenderStatus}</div> : null}
      <div className="small timeline-inspectorHint">
        Synthetic proxy generation has been removed. Look Dev uses downloaded models; video editing uses the original rendered outputs.
      </div>
    </div>
  );

  const diffusionPanel = (
    <div className="timeline-dockPanel">
      <div className="timeline-panelHeader">
        <div>
          <div className="timeline-panelTitle">Downloaded Model Look Dev</div>
          <div className="small">
            Render a short example through an installed internal model without leaving the timeline.
          </div>
        </div>
        <span className="badge">{installedPreviewModels.length} ready</span>
      </div>
      <div className="timeline-inlineActions">
        <button className="secondary" disabled={!selected} onClick={setDiffRangeFromSelection}>
          Use selection
        </button>
      </div>
      <div className="timeline-fieldGrid timeline-fieldGrid--compact">
        <label className="small">start</label>
        <input
          type="number"
          step={0.1}
          value={diffStart}
          onChange={(e) => setDiffStart(Number(e.target.value))}
        />
        <label className="small">end</label>
        <input
          type="number"
          step={0.1}
          value={diffEnd}
          onChange={(e) => setDiffEnd(Number(e.target.value))}
        />
        <label className="small">fps</label>
        <input
          type="number"
          step={1}
          value={diffFps}
          onChange={(e) => setDiffFps(Number(e.target.value))}
        />
        <label className="small">steps</label>
        <input
          type="number"
          step={1}
          value={diffSteps}
          onChange={(e) => setDiffSteps(Number(e.target.value))}
        />
        <label className="small">cfg</label>
        <input
          type="number"
          step={0.1}
          value={diffCfg}
          onChange={(e) => setDiffCfg(Number(e.target.value))}
        />
        <label className="small">strength</label>
        <input
          type="number"
          step={0.01}
          value={diffStrength}
          onChange={(e) => setDiffStrength(Number(e.target.value))}
        />
        <label className="small">width</label>
        <input
          type="number"
          step={64}
          value={diffW}
          onChange={(e) => setDiffW(Number(e.target.value))}
        />
        <label className="small">height</label>
        <input
          type="number"
          step={64}
          value={diffH}
          onChange={(e) => setDiffH(Number(e.target.value))}
        />
        <label className="small">model</label>
        <select value={diffModel} onChange={(e) => setDiffModel(e.target.value)}>
          <option value="auto">Auto-select downloaded model</option>
          {installedPreviewModels.map((model) => (
            <option key={model.id} value={model.id}>{model.name}</option>
          ))}
        </select>
      </div>
      <div className="timeline-inlineActions">
        <button className="primary" disabled={!installedPreviewModels.length} onClick={generateDiffusionPreview}>
          Generate
        </button>
        <button className="secondary" onClick={() => { setDiffUrl(""); setDiffBusy(false); }}>
          Clear
        </button>
        <button className="secondary" onClick={() => onNavigate?.("models")}>
          Manage models
        </button>
      </div>
      {diffBusy ? (
        <ProgressBar
          value={68}
          label="Generating diffusion preview"
          detail="Waiting for the look-dev clip to finish writing."
          compact
        />
      ) : null}
      <div className="timeline-monitor timeline-monitor--video">
        {diffUrl ? (
          <video
            src={diffUrl}
            controls
            className="timeline-monitorVideo"
            onLoadedData={() => setDiffBusy(false)}
            onError={() => setDiffBusy(false)}
          />
        ) : (
          <div className="small">
            {installedPreviewModels.length
              ? "No model preview generated."
              : "Install an internal SD model in Models to enable Look Dev."}
          </div>
        )}
      </div>
    </div>
  );

  const motionCurvesPanel = (() => {
    const tr = (tracks || []).find((t: any) => String(t?.type || "").toLowerCase() === "motion");
    const clip = (tr?.clips || [])[0];
    if (!clip || !timeline) return null;
    const data: AnyDict = clip?.data && typeof clip.data === "object" ? clip.data : {};
    const fps = 24;
    const duration = Number(durationS || 0) || 60;

    const strengthSched = String(data.denoise_schedule || data.strength_schedule || "");
    const cfgSched = String(data.cfg_scale_schedule || "");
    const stepsSched = String(data.steps_schedule || "");
    const strengthPairs = parseDeforumSchedule(strengthSched);
    const cfgPairs = parseDeforumSchedule(cfgSched);
    const stepsPairs = parseDeforumSchedule(stepsSched);
    const strengthCurve = sampleCurve(strengthPairs, {
      durationS: duration,
      fps,
      samples: 220,
      fallback: 0.35,
    });
    const cfgCurve = sampleCurve(cfgPairs, {
      durationS: duration,
      fps,
      samples: 220,
      fallback: 7.0,
    });
    const stepsCurve = sampleCurve(stepsPairs, {
      durationS: duration,
      fps,
      samples: 220,
      fallback: 15,
    });

    const W = 720;
    const H = 160;

    const strengthPath = svgPath(strengthCurve, duration, 0, 1, W, H);
    const cfgPath = svgPath(cfgCurve, duration, 1, 30, W, H);
    const stepsPath = svgPath(stepsCurve, duration, 4, 60, W, H);

    const updateMotionField = (field: string, val: string) => {
      const next = { ...timelineRef.current };
      next.tracks = Array.isArray(next.tracks)
        ? next.tracks.map((t: any) => {
            if (String(t?.type || "").toLowerCase() !== "motion") return t;
            const clips = Array.isArray(t.clips) ? t.clips : [];
            if (!clips.length) return t;
            const c0 = clips[0] || {};
            const d0 = c0.data && typeof c0.data === "object" ? { ...c0.data } : {};
            d0[field] = val;
            return { ...t, clips: [{ ...c0, data: d0 }, ...clips.slice(1)] };
          })
        : next.tracks;
      commitTimeline(next, "update_motion_curve");
    };

    const insertPointAtPlayhead = (
      field: "strength_schedule" | "cfg_scale_schedule" | "steps_schedule" | "denoise_schedule",
      value: number,
    ) => {
      const f = Math.round(Number(playheadS || 0) * fps);
      const cur = String((data as any)[field] || "");
      const next = upsertPoint(cur, f, value);
      updateMotionField(field, next);
    };

    const curStrength = evalSchedule(strengthPairs, Number(playheadS || 0) * fps) ?? 0.35;
    const curCfg = evalSchedule(cfgPairs, Number(playheadS || 0) * fps) ?? 7.0;
    const curSteps = evalSchedule(stepsPairs, Number(playheadS || 0) * fps) ?? 15;

    return (
      <div className="timeline-dockPanel">
        <div className="timeline-panelHeader">
          <div>
            <div className="timeline-panelTitle">Motion Curves</div>
            <div className="small">Deforum schedules for cfg, strength, steps, and denoise.</div>
          </div>
          <span className="badge">24 fps</span>
        </div>

        <div className="timeline-curveStage">
          <svg width={W} height={H} style={{ width: "100%", height: H }}>
            {Array.from({ length: 9 }).map((_, i) => (
              <line
                key={i}
                x1={(i / 8) * W}
                y1={0}
                x2={(i / 8) * W}
                y2={H}
                stroke="rgba(255,255,255,0.06)"
                strokeWidth={1}
              />
            ))}
            {Array.from({ length: 5 }).map((_, i) => (
              <line
                key={i}
                x1={0}
                y1={(i / 4) * H}
                x2={W}
                y2={(i / 4) * H}
                stroke="rgba(255,255,255,0.06)"
                strokeWidth={1}
              />
            ))}
            <path d={strengthPath} fill="none" stroke="rgba(120,200,255,0.85)" strokeWidth={2} />
            <path d={cfgPath} fill="none" stroke="rgba(255,210,120,0.85)" strokeWidth={2} />
            <path d={stepsPath} fill="none" stroke="rgba(180,255,180,0.85)" strokeWidth={2} />
            <line
              x1={(clamp(Number(playheadS || 0), 0, duration) / Math.max(1e-6, duration)) * W}
              y1={0}
              x2={(clamp(Number(playheadS || 0), 0, duration) / Math.max(1e-6, duration)) * W}
              y2={H}
              stroke="rgba(255,120,120,0.9)"
              strokeWidth={2}
            />
          </svg>
        </div>

        <div className="timeline-inlineActions">
          <span className="small">strength {curStrength.toFixed(3)}</span>
          <button
            className="secondary"
            onClick={() => insertPointAtPlayhead("strength_schedule", curStrength)}
          >
            Insert strength
          </button>
          <span className="small">cfg {curCfg.toFixed(2)}</span>
          <button
            className="secondary"
            onClick={() => insertPointAtPlayhead("cfg_scale_schedule", curCfg)}
          >
            Insert cfg
          </button>
          <span className="small">steps {Math.round(curSteps)}</span>
          <button
            className="secondary"
            onClick={() => insertPointAtPlayhead("steps_schedule", curSteps)}
          >
            Insert steps
          </button>
        </div>

        <div className="timeline-textareaStack">
          <div>
            <div className="small timeline-stackLabel">strength_schedule</div>
            <textarea
              aria-label="Strength schedule"
              value={String(data.strength_schedule || "")}
              onChange={(e) => updateMotionField("strength_schedule", e.target.value)}
            />
          </div>
          <div>
            <div className="small timeline-stackLabel">cfg_scale_schedule</div>
            <textarea
              aria-label="CFG schedule"
              value={String(data.cfg_scale_schedule || "")}
              onChange={(e) => updateMotionField("cfg_scale_schedule", e.target.value)}
            />
          </div>
          <div>
            <div className="small timeline-stackLabel">steps_schedule</div>
            <textarea
              aria-label="Steps schedule"
              value={String(data.steps_schedule || "")}
              onChange={(e) => updateMotionField("steps_schedule", e.target.value)}
            />
          </div>
          <div>
            <div className="small timeline-stackLabel">denoise_schedule</div>
            <textarea
              aria-label="Denoise schedule"
              value={String(data.denoise_schedule || "")}
              onChange={(e) => updateMotionField("denoise_schedule", e.target.value)}
            />
          </div>
        </div>
      </div>
    );
  })();

  const dockTabs: Array<{ id: DockSection; label: string; meta: string }> = [
    {
      id: "orchestrator",
      label: "Orchestrator",
      meta: conductorPlan ? "planned" : `${handoffReadyCount}/2`,
    },
    {
      id: "inspector",
      label: "Inspector",
      meta: selected ? selectionStatus : "idle",
    },
    {
      id: "media",
      label: "Media",
      meta: `${mediaLibrary.length}`,
    },
    {
      id: "diffusion",
      label: "Look Dev",
      meta: diffUrl ? "ready" : "draft",
    },
    ...(motionCurvesPanel
      ? [
          {
            id: "curves" as DockSection,
            label: "Curves",
            meta: "motion",
          },
        ]
      : []),
  ];

  const activeDockPanel =
    dockSection === "orchestrator"
      ? orchestratorPanel
      : dockSection === "media"
      ? mediaPanel
      : dockSection === "diffusion"
        ? diffusionPanel
        : dockSection === "curves" && motionCurvesPanel
          ? motionCurvesPanel
          : inspectorPanel;

  return (
    <div className="timeline-page" onKeyDown={onKeyDown} tabIndex={0}>
      {pageHeader}
      <ProjectRevisionConflict
        conflict={revisionConflict}
        onReload={async () => {
          if (!projectId) return;
          setErr(null);
          await refreshProject(projectId);
        }}
      />
      {recoveryNotice ? (
        <div className="card timeline-sessionCard timeline-sessionCard--accent" style={{ marginBottom: 12 }}>
          <div className="timeline-sessionLabel">Crash recovery</div>
          <div className="timeline-sessionValue">{recoveryNotice}</div>
          <div className="timeline-toolbarActions" style={{ marginTop: 8 }}>
            <button className="primary" type="button" disabled={recoveryBusy} onClick={() => void restoreAutosave()}>
              Restore autosave
            </button>
            <button className="secondary" type="button" disabled={recoveryBusy} onClick={() => void discardAutosave()}>
              Discard
            </button>
          </div>
        </div>
      ) : null}
      <div className="timeline-sessionStrip">
        <div className="timeline-sessionCard">
          <div className="timeline-sessionLabel">Shared session</div>
          <div className="timeline-sessionValue">
            {project?.name || "No active project"} • {plan?.variants?.[selectedVariant]?.name || `Variant ${selectedVariant + 1}`}
          </div>
          <div className="small">
            Workspace, Timeline, and the standalone labs now follow the same active project and variant.
          </div>
        </div>
        {lastHandoff ? (
          <div className="timeline-sessionCard timeline-sessionCard--accent">
            <div className="timeline-sessionLabel">Last handoff</div>
            <div className="timeline-sessionValue">
              {lastHandoff.type === "planner" ? "Planner sync" : "Reactive sync"}
            </div>
            <div className="small">{lastHandoff.summary}</div>
          </div>
        ) : null}
      </div>
      {progress.label ? (
        <div className="card timeline-progressCard">
          <ProgressBar
            value={progress.value}
            label={progress.label}
            detail={progress.detail}
            tone={progress.tone}
          />
        </div>
      ) : null}
      {toolbarCard}
      <details className="card timeline-guideCard">
        <summary className="timeline-guideSummary">Quick guide and capabilities</summary>
        <div className="timeline-guideBody">
          <div className="guide-grid">
            <section className="guide-block">
              <div className="guide-kicker">What this view does</div>
              <p>Timeline is the full arrangement and non-destructive video editor for the saved Studio plan. It combines output media, prompt clips, motion clips, overlays, camera moves, and model look-development in one horizontal workspace.</p>
            </section>
            <section className="guide-block">
              <div className="guide-kicker">Capabilities</div>
              <ul className="guide-list">
                <li>Play audio, move the playhead, switch variants, and review timing against the active soundtrack.</li>
                <li>Zoom with buttons, slider, numeric entry, Fit all, or Ctrl/Cmd plus mouse wheel.</li>
                <li>Switch density between compact and comfortable depending on how many tracks or keyframes you need to see.</li>
                <li>Use the dock for orchestration, clip inspection, output media, downloaded-model look-dev, and motion-curve editing without removing advanced controls.</li>
                <li>Add completed renders to the Video Edit lane, split and trim them, change speed/audio/fades, then render a new master while preserving the original files.</li>
              </ul>
            </section>
            <section className="guide-block">
              <div className="guide-kicker">Recommended flow</div>
              <ul className="guide-list">
                <li>Start in Fit all mode to see the whole arrangement, then zoom into dense sections for clip or keyframe edits.</li>
                <li>Check the Orchestrator tab before editing so planner, reactive, model, and route state is visible without leaving Timeline.</li>
                <li>Keep the inspector open for clip-level adjustments; use Look Dev for a real model example and Media for pre/post-render video finishing.</li>
                <li>Use Timeline after Workspace or Storyboard when the plan is ready for precise arrangement and finishing decisions.</li>
              </ul>
            </section>
          </div>
        </div>
      </details>
      {err ? (
        <div className="card timeline-errorBanner">
          <div className="small">{err}</div>
        </div>
      ) : null}
      <div className="timeline-workspace">
        <div className="timeline-mainColumn">{arrangementCard}</div>
        <div className="timeline-dock">
          {monitorCard}
          <div className="card timeline-dockCard timeline-dockHub">
            <div className="timeline-dockTabs" role="tablist" aria-label="Timeline utilities">
              {dockTabs.map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  role="tab"
                  aria-selected={dockSection === tab.id}
                  className={`timeline-dockTab${dockSection === tab.id ? " is-active" : ""}`}
                  onClick={() => setDockSection(tab.id)}
                >
                  <span>{tab.label}</span>
                  <span className="timeline-dockTabMeta">{tab.meta}</span>
                </button>
              ))}
            </div>
            <div className="timeline-dockStatusBar">
              <div className="small">Selection: <b>{selectionStatus}</b></div>
              <div className="small">
                Playhead {fmtTime(playheadS)} • Grid {quantizeStatus}
              </div>
            </div>
            <div className="timeline-dockBody">{activeDockPanel}</div>
          </div>
        </div>
      </div>
    </div>
  );
}
