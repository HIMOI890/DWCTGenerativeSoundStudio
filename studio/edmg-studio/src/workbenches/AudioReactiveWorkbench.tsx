import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Copy,
  Download,
  Mic,
  MicOff,
  Pause,
  Play,
  Save,
  Settings,
  Square,
  Upload,
  Waves,
  Wrench,
  Zap,
} from 'lucide-react';
import type { SignedProjectMediaRequest } from '../components/api';
import { ProgressBar } from '../components/ProgressBar';
import { usePreservedMediaSource, useSignedProjectMedia } from '../hooks/useSignedProjectMedia';

type MappingPreset = 'cinematic' | 'psychedelic' | 'ambient' | 'percussive';
type RenderMode = 'smooth' | 'cut-heavy' | 'performance-led' | 'ambient';
type ScheduleField =
  | 'zoom'
  | 'rotation_y'
  | 'rotation_z'
  | 'translation_x'
  | 'translation_y'
  | 'translation_z'
  | 'strength'
  | 'cfg_scale'
  | 'brightness';

type ParameterScaling = { zoom: number; rotation: number; translation: number; color: number };
type ReactiveParams = {
  zoom: number;
  rotation_x: number;
  rotation_y: number;
  rotation_z: number;
  translation_x: number;
  translation_y: number;
  translation_z: number;
  cfg_scale: number;
  strength: number;
  brightness: number;
  contrast: number;
  energy_level: number;
  bass_intensity: number;
  mid_intensity: number;
  treble_intensity: number;
};

type AudioMetrics = { energy: number; bass: number; mid: number; treble: number };
type Keyframe = { frame: number; time: number; metrics: AudioMetrics; params: ReactiveParams };
type BeatMarker = { frame: number; time: number; intensity: number };
type CueEvent = { id: number; frame: number; time: number; cueType: 'cut' | 'push' | 'orbit' | 'hold'; instruction: string };
type SectionSummary = { id: number; startTime: number; endTime: number; label: string; avgEnergy: number; approved: boolean; renderMode: RenderMode };
type RepairSuggestion = { id: number; sectionId: number; issue: string; action: string };
type SavedPreset = {
  name: string;
  preset: MappingPreset;
  sensitivity: number;
  smoothing: number;
  fps: number;
  minCutFrames: number;
  renderMode: RenderMode;
  scheduleStride: number;
  scaling: ParameterScaling;
};

type RenderHandoffManifest = {
  approvedSectionIds: number[];
  renderMode: RenderMode;
  scheduleStride: number;
  cueEvents: CueEvent[];
  repairSuggestions: RepairSuggestion[];
  schedules: Record<ScheduleField, string>;
  modelHints: { executionPriority: string; continuityPriority: string; fallbackAction: string };
};

type ExportBundle = {
  metadata: {
    createdAt: string;
    preset: MappingPreset;
    sensitivity: number;
    smoothing: number;
    fps: number;
    source: 'microphone-history' | 'audio-file';
    fileName?: string;
    totalFrames: number;
    scaling: ParameterScaling;
    beatCount: number;
    minCutFrames: number;
    renderMode: RenderMode;
    scheduleStride: number;
  };
  keyframes: Keyframe[];
  beatMarkers: BeatMarker[];
  cueEvents: CueEvent[];
  sections: SectionSummary[];
  repairSuggestions: RepairSuggestion[];
  schedules: Record<ScheduleField, string>;
  deforum: Record<string, unknown>;
};

type ReactiveLabSyncPayload = {
  metadata: ExportBundle["metadata"];
  keyframes: Keyframe[];
  beat_markers: BeatMarker[];
  cue_events: CueEvent[];
  sections: SectionSummary[];
  repair_suggestions: RepairSuggestion[];
  schedules: Record<ScheduleField, string>;
  handoff_manifest: RenderHandoffManifest;
};

type AudioReactiveWorkbenchProps = {
  studioProjectId?: string;
  studioProjectName?: string;
  studioProject?: any;
  studioSelectedVariant?: number;
  onSyncToStudio?: (payload: ReactiveLabSyncPayload) => Promise<string | void>;
  compact?: boolean;
};

const DEFAULT_PARAMS: ReactiveParams = {
  zoom: 1,
  rotation_x: 0,
  rotation_y: 0,
  rotation_z: 0,
  translation_x: 0,
  translation_y: 0,
  translation_z: 0,
  cfg_scale: 7,
  strength: 0.72,
  brightness: 0.5,
  contrast: 1,
  energy_level: 0,
  bass_intensity: 0,
  mid_intensity: 0,
  treble_intensity: 0,
};

const DEFAULT_SCALING: ParameterScaling = { zoom: 1, rotation: 1, translation: 1, color: 1 };

const AudioContextCtor: typeof AudioContext | undefined =
  typeof window !== 'undefined'
    ? (window.AudioContext || (window as Window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext)
    : undefined;

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function clamp01(value: number): number {
  return clamp(value, 0, 1);
}

function formatTime(seconds: number): string {
  const mins = Math.floor(seconds / 60).toString().padStart(2, '0');
  const secs = Math.floor(seconds % 60).toString().padStart(2, '0');
  return `${mins}:${secs}`;
}

function averageMetric(items: Keyframe[], selector: (frame: Keyframe) => number): number {
  if (!items.length) return 0;
  return items.reduce((sum, item) => sum + selector(item), 0) / items.length;
}

function copyText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text);
  const textarea = document.createElement('textarea');
  textarea.value = text;
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand('copy');
  document.body.removeChild(textarea);
  return Promise.resolve();
}

function downloadText(filename: string, contents: string, type = 'text/plain'): void {
  const blob = new Blob([contents], { type });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function buildMono(buffer: AudioBuffer): Float32Array {
  const mono = new Float32Array(buffer.length);
  for (let channel = 0; channel < buffer.numberOfChannels; channel += 1) {
    const data = buffer.getChannelData(channel);
    for (let index = 0; index < data.length; index += 1) mono[index] += data[index] / buffer.numberOfChannels;
  }
  return mono;
}

function deriveWindowMetrics(samples: Float32Array, start: number, size: number): AudioMetrics {
  const end = Math.min(samples.length, start + size);
  if (end - start < 8) return { energy: 0, bass: 0, mid: 0, treble: 0 };
  let sumSq = 0;
  let bassAccumulator = 0;
  let trebleAccumulator = 0;
  let movingAverage = 0;
  const smoothing = 0.08;

  for (let i = start; i < end; i += 1) {
    const sample = samples[i];
    sumSq += sample * sample;
    movingAverage += (sample - movingAverage) * smoothing;
    bassAccumulator += Math.abs(movingAverage);
    if (i > start) trebleAccumulator += Math.abs(sample - samples[i - 1]);
  }

  const windowLength = end - start;
  const energy = Math.sqrt(sumSq / windowLength);
  const bass = bassAccumulator / windowLength;
  const treble = trebleAccumulator / Math.max(1, windowLength - 1);
  const mid = clamp01(energy * 1.35 - bass * 0.62 - treble * 0.22);
  return { energy, bass, mid, treble };
}

function normalizeMetrics(frames: AudioMetrics[]): AudioMetrics[] {
  const peaks = frames.reduce(
    (acc, frame) => ({
      energy: Math.max(acc.energy, frame.energy),
      bass: Math.max(acc.bass, frame.bass),
      mid: Math.max(acc.mid, frame.mid),
      treble: Math.max(acc.treble, frame.treble),
    }),
    { energy: 0.0001, bass: 0.0001, mid: 0.0001, treble: 0.0001 }
  );

  return frames.map((frame) => ({
    energy: clamp01(frame.energy / peaks.energy),
    bass: clamp01(frame.bass / peaks.bass),
    mid: clamp01(frame.mid / peaks.mid),
    treble: clamp01(frame.treble / peaks.treble),
  }));
}

function smoothMetrics(frames: AudioMetrics[], amount: number): AudioMetrics[] {
  if (!frames.length || amount <= 0) return frames;
  const radius = clamp(Math.round(amount * 8), 1, 16);
  return frames.map((_, index) => {
    let weightSum = 0;
    const blended = { energy: 0, bass: 0, mid: 0, treble: 0 };
    for (let offset = -radius; offset <= radius; offset += 1) {
      const target = frames[index + offset];
      if (!target) continue;
      const weight = radius + 1 - Math.abs(offset);
      weightSum += weight;
      blended.energy += target.energy * weight;
      blended.bass += target.bass * weight;
      blended.mid += target.mid * weight;
      blended.treble += target.treble * weight;
    }
    return { energy: blended.energy / weightSum, bass: blended.bass / weightSum, mid: blended.mid / weightSum, treble: blended.treble / weightSum };
  });
}

function dynamicColorSwing(bass: number, treble: number): number {
  return clamp01(bass * 0.58 + treble * 0.42);
}

function mapMetricsToParams(metrics: AudioMetrics, preset: MappingPreset, sensitivity: number, time: number, scaling: ParameterScaling): ReactiveParams {
  const { energy, bass, mid, treble } = metrics;
  const wobble = Math.sin(time * 1.5);
  const pulse = Math.sin(time * 3.2);
  const sweep = Math.sin(time * 0.82);
  const lift = Math.cos(time * 0.61);
  const sens = sensitivity;
  const next: ReactiveParams = { ...DEFAULT_PARAMS, energy_level: energy, bass_intensity: bass, mid_intensity: mid, treble_intensity: treble };

  switch (preset) {
    case 'cinematic':
      next.zoom = 1 + (0.04 + energy * 0.16 + bass * 0.02) * sens * scaling.zoom;
      next.rotation_y = (wobble * bass * 18 + sweep * 7) * sens * scaling.rotation;
      next.rotation_z = sweep * treble * 5 * sens * scaling.rotation;
      next.translation_z = -(12 + energy * 20 + bass * 4) * sens * scaling.translation;
      next.translation_x = sweep * (8 + bass * 10 + mid * 6) * sens * scaling.translation;
      next.translation_y = lift * (3 + treble * 5) * sens * scaling.translation;
      next.cfg_scale = 6.8 + mid * sens * 2.4;
      next.strength = clamp(0.6 + treble * sens * 0.2, 0.45, 0.92);
      next.brightness = clamp(0.48 + energy * 0.25 * scaling.color, 0.2, 1.4);
      next.contrast = clamp(0.95 + dynamicColorSwing(bass, treble) * 0.3 * scaling.color, 0.6, 1.65);
      break;
    case 'psychedelic':
      next.zoom = 1 + (0.06 + bass * 0.12 + treble * 0.14) * sens * scaling.zoom;
      next.rotation_z = pulse * (treble * 42 + bass * 18) * sens * scaling.rotation;
      next.rotation_x = wobble * mid * sens * 24 * scaling.rotation;
      next.translation_x = Math.sin(time * 2.3) * (12 + energy * 12) * sens * scaling.translation;
      next.translation_y = Math.cos(time * 1.8) * (8 + treble * 10) * sens * scaling.translation;
      next.translation_z = -(10 + mid * 16) * sens * scaling.translation;
      next.cfg_scale = 8 + treble * sens * 2;
      next.strength = clamp(0.64 + energy * 0.22, 0.45, 0.95);
      next.brightness = clamp(0.42 + treble * 0.42 * scaling.color, 0.15, 1.6);
      next.contrast = clamp(1 + bass * 0.35 * scaling.color, 0.7, 1.8);
      break;
    case 'ambient':
      next.zoom = 1 + (0.02 + mid * 0.08) * sens * scaling.zoom;
      next.rotation_x = Math.sin(time * 0.55) * bass * sens * 8 * scaling.rotation;
      next.rotation_y = Math.cos(time * 0.45) * mid * sens * 8 * scaling.rotation;
      next.translation_x = sweep * (4 + mid * 4) * sens * scaling.translation;
      next.translation_y = Math.sin(time * 0.6) * (3 + energy * 6) * sens * scaling.translation;
      next.translation_z = -(6 + energy * 8) * sens * scaling.translation;
      next.cfg_scale = 6.2 + mid * 1.3 * sens;
      next.strength = clamp(0.5 + energy * 0.16, 0.38, 0.82);
      next.brightness = clamp(0.5 + mid * 0.18 * scaling.color, 0.2, 1.25);
      next.contrast = clamp(0.9 + bass * 0.16 * scaling.color, 0.65, 1.35);
      break;
    case 'percussive':
      next.zoom = 1 + (0.05 + bass * 0.18) * sens * scaling.zoom;
      next.rotation_z = pulse * bass * sens * 20 * scaling.rotation;
      next.translation_x = (sweep * 10 + (bass - 0.5) * 18) * sens * scaling.translation;
      next.translation_y = (lift * 6 + (treble - 0.5) * 10) * sens * scaling.translation;
      next.translation_z = -(10 + energy * 16) * sens * scaling.translation;
      next.cfg_scale = 7.4 + treble * sens * 1.6;
      next.strength = clamp(0.62 + bass * 0.2, 0.45, 0.92);
      next.brightness = clamp(0.48 + treble * 0.24 * scaling.color, 0.22, 1.35);
      next.contrast = clamp(1 + bass * 0.28 * scaling.color, 0.75, 1.7);
      break;
  }

  return next;
}

function detectBeatMarkers(keyframes: Keyframe[], minCutFrames: number): BeatMarker[] {
  if (keyframes.length < 3) return [];
  const meanEnergy = averageMetric(keyframes, (frame) => frame.metrics.energy);
  const threshold = Math.min(0.98, meanEnergy + 0.16);
  const markers: BeatMarker[] = [];

  for (let index = 1; index < keyframes.length - 1; index += 1) {
    const previous = keyframes[index - 1].metrics.energy;
    const current = keyframes[index].metrics.energy;
    const next = keyframes[index + 1].metrics.energy;
    if (current > threshold && current >= previous && current > next) {
      if (!markers.length || keyframes[index].frame - markers[markers.length - 1].frame >= minCutFrames) {
        markers.push({ frame: keyframes[index].frame, time: keyframes[index].time, intensity: current });
      }
    }
  }

  return markers;
}

function buildCueEvents(keyframes: Keyframe[], beatMarkers: BeatMarker[]): CueEvent[] {
  return beatMarkers.slice(0, 32).map((beat, index) => {
    const frame = keyframes.find((item) => item.frame === beat.frame) ?? keyframes[Math.min(index, keyframes.length - 1)];
    const cueType: CueEvent['cueType'] = beat.intensity > 0.88 ? 'cut' : frame.metrics.bass > 0.65 ? 'push' : frame.metrics.treble > 0.58 ? 'orbit' : 'hold';
    const instruction = cueType === 'cut'
      ? 'hard cut or flash-frame transition'
      : cueType === 'push'
      ? 'push camera forward into impact moment'
      : cueType === 'orbit'
      ? 'introduce rotational move or angle pivot'
      : 'hold composition and let motion settle';
    return { id: index + 1, frame: beat.frame, time: beat.time, cueType, instruction };
  });
}

function buildSections(keyframes: Keyframe[], fps: number): SectionSummary[] {
  if (!keyframes.length) return [];
  const sectionSize = Math.max(16, Math.round(fps * 4));
  const sections: SectionSummary[] = [];
  for (let start = 0; start < keyframes.length; start += sectionSize) {
    const slice = keyframes.slice(start, start + sectionSize);
    const avgEnergy = averageMetric(slice, (frame) => frame.metrics.energy);
    const label = avgEnergy > 0.78 ? 'peak section' : avgEnergy > 0.58 ? 'lift section' : avgEnergy > 0.38 ? 'steady section' : 'breathing section';
    sections.push({
      id: sections.length + 1,
      startTime: slice[0].time,
      endTime: slice[slice.length - 1].time,
      label,
      avgEnergy,
      approved: false,
      renderMode: chooseSectionRenderMode(avgEnergy),
    });
  }
  return sections;
}

function buildRepairSuggestions(sections: SectionSummary[]): RepairSuggestion[] {
  return sections.map((section) => ({
    id: section.id,
    sectionId: section.id,
    issue: section.avgEnergy > 0.78 ? 'Potential over-aggressive motion or unstable transitions.' : section.avgEnergy < 0.28 ? 'Potentially under-animated section that may feel flat.' : 'Section may need tighter continuity between cues.',
    action: section.avgEnergy > 0.78 ? 'Lower zoom and rotation scaling, keep the lateral pan direction stable across nearby cuts, and smooth abrupt direction flips.' : section.avgEnergy < 0.28 ? 'Increase side-to-side drift, brighten the section slightly, or introduce a controlled sweep cue instead of a static hold.' : 'Preserve motif continuity, soften abrupt cue changes at the section boundary, and keep camera travel coherent from one section to the next.',
  }));
}

function buildSchedule(keyframes: Keyframe[], accessor: (params: ReactiveParams) => number): string {
  return keyframes.map((frame) => `${frame.frame}:(${accessor(frame.params).toFixed(4)})`).join(',');
}

function buildSchedules(keyframes: Keyframe[]): Record<ScheduleField, string> {
  return {
    zoom: buildSchedule(keyframes, (params) => params.zoom),
    rotation_y: buildSchedule(keyframes, (params) => params.rotation_y),
    rotation_z: buildSchedule(keyframes, (params) => params.rotation_z),
    translation_x: buildSchedule(keyframes, (params) => params.translation_x),
    translation_y: buildSchedule(keyframes, (params) => params.translation_y),
    translation_z: buildSchedule(keyframes, (params) => params.translation_z),
    strength: buildSchedule(keyframes, (params) => params.strength),
    cfg_scale: buildSchedule(keyframes, (params) => params.cfg_scale),
    brightness: buildSchedule(keyframes, (params) => params.brightness),
  };
}

function chooseSectionRenderMode(avgEnergy: number): RenderMode {
  if (avgEnergy > 0.8) return 'cut-heavy';
  if (avgEnergy > 0.62) return 'performance-led';
  if (avgEnergy > 0.38) return 'smooth';
  return 'ambient';
}

function sparseSchedule(schedule: string, stride: number): string {
  if (!schedule || stride <= 1) return schedule;
  return schedule
    .split(',')
    .filter((entry, index) => index % stride === 0)
    .join(',');
}

function buildSparseSchedules(schedules: Record<ScheduleField, string>, stride: number): Record<ScheduleField, string> {
  return {
    zoom: sparseSchedule(schedules.zoom, stride),
    rotation_y: sparseSchedule(schedules.rotation_y, stride),
    rotation_z: sparseSchedule(schedules.rotation_z, stride),
    translation_x: sparseSchedule(schedules.translation_x, stride),
    translation_y: sparseSchedule(schedules.translation_y, stride),
    translation_z: sparseSchedule(schedules.translation_z, stride),
    strength: sparseSchedule(schedules.strength, stride),
    cfg_scale: sparseSchedule(schedules.cfg_scale, stride),
    brightness: sparseSchedule(schedules.brightness, stride),
  };
}

function buildRenderHandoffManifest(args: {
  sections: SectionSummary[];
  cueEvents: CueEvent[];
  repairSuggestions: RepairSuggestion[];
  schedules: Record<ScheduleField, string>;
  renderMode: RenderMode;
  scheduleStride: number;
}): RenderHandoffManifest {
  const { sections, cueEvents, repairSuggestions, schedules, renderMode, scheduleStride } = args;
  return {
    approvedSectionIds: sections.filter((section) => section.approved).map((section) => section.id),
    renderMode,
    scheduleStride,
    cueEvents,
    repairSuggestions,
    schedules,
    modelHints: {
      executionPriority: renderMode === 'cut-heavy' ? 'render section previews first, then commit only approved peaks' : 'render approved sections in chronological order',
      continuityPriority: 'carry camera direction, lateral sweep intent, and palette rules through adjacent approved sections',
      fallbackAction: 'if a section fails, rerender only the flagged section using its repair suggestion and keep neighboring seeds stable',
    },
  };
}

function buildDeforumBundle(keyframes: Keyframe[], preset: MappingPreset, fps: number): Record<string, unknown> {
  return {
    animation_mode: '3D',
    fps,
    max_frames: keyframes.length,
    prompts: { '0': `audio reactive ${preset} sequence, highly detailed, dynamic lighting, motion driven by audio keyframes` },
    negative_prompts: { '0': 'low quality, static frame, muddy details, unmotivated camera movement' },
    zoom: buildSchedule(keyframes, (params) => params.zoom),
    angle: buildSchedule(keyframes, (params) => params.rotation_z),
    rotation_3d_x: buildSchedule(keyframes, (params) => params.rotation_x),
    rotation_3d_y: buildSchedule(keyframes, (params) => params.rotation_y),
    translation_x: buildSchedule(keyframes, (params) => params.translation_x),
    translation_y: buildSchedule(keyframes, (params) => params.translation_y),
    translation_z: buildSchedule(keyframes, (params) => params.translation_z),
    strength_schedule: buildSchedule(keyframes, (params) => params.strength),
    contrast_schedule: buildSchedule(keyframes, (params) => params.contrast),
    brightness_schedule: buildSchedule(keyframes, (params) => params.brightness),
    cfg_scale_schedule: buildSchedule(keyframes, (params) => params.cfg_scale),
  };
}

async function buildOfflineKeyframes(file: File, preset: MappingPreset, sensitivity: number, smoothing: number, fps: number, scaling: ParameterScaling): Promise<Keyframe[]> {
  if (!AudioContextCtor) throw new Error('Web Audio API is not available in this browser.');
  const context = new AudioContextCtor();
  try {
    const buffer = await file.arrayBuffer();
    const audioBuffer = await context.decodeAudioData(buffer.slice(0));
    const mono = buildMono(audioBuffer);
    const frameSampleSize = Math.max(1024, Math.floor(audioBuffer.sampleRate / fps));
    const rawMetrics: AudioMetrics[] = [];
    for (let start = 0; start < mono.length; start += frameSampleSize) rawMetrics.push(deriveWindowMetrics(mono, start, frameSampleSize));
    const normalized = smoothMetrics(normalizeMetrics(rawMetrics), smoothing);
    return normalized.map((metrics, frame) => ({ frame, time: frame / fps, metrics, params: mapMetricsToParams(metrics, preset, sensitivity, frame / fps, scaling) }));
  } finally {
    void context.close();
  }
}

function loadSavedPresets(): SavedPreset[] {
  try {
    const raw = window.localStorage.getItem('audio-reactive-presets');
    if (!raw) return [];
    const parsed = JSON.parse(raw) as SavedPreset[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function savePresets(presets: SavedPreset[]): void {
  window.localStorage.setItem('audio-reactive-presets', JSON.stringify(presets));
}

const AudioReactiveGenerator: React.FC<AudioReactiveWorkbenchProps> = ({
  studioProjectId,
  studioProjectName,
  studioProject,
  studioSelectedVariant = 0,
  onSyncToStudio,
  compact = false,
}) => {
  const studioHydrationKeyRef = useRef('');
  const [isRecording, setIsRecording] = useState(false);
  const [isPlaying, setIsPlaying] = useState(false);
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [audioContext, setAudioContext] = useState<AudioContext | null>(null);
  const [analyserNode, setAnalyserNode] = useState<AnalyserNode | null>(null);
  const [audioData, setAudioData] = useState<Uint8Array>(new Uint8Array(1024));
  const [frequencyData, setFrequencyData] = useState<Uint8Array>(new Uint8Array(1024));
  const [reactiveParams, setReactiveParams] = useState<ReactiveParams>(DEFAULT_PARAMS);
  const [sensitivity, setSensitivity] = useState(1);
  const [mappingPreset, setMappingPreset] = useState<MappingPreset>('cinematic');
  const [smoothing, setSmoothing] = useState(0.28);
  const [parameterScaling, setParameterScaling] = useState<ParameterScaling>(DEFAULT_SCALING);
  const [generationLog, setGenerationLog] = useState<string[]>([]);
  const [isGenerating, setIsGenerating] = useState(false);
  const [currentFrame, setCurrentFrame] = useState(0);
  const [fps, setFps] = useState(24);
  const [error, setError] = useState<string | null>(null);
  const [offlineKeyframes, setOfflineKeyframes] = useState<Keyframe[]>([]);
  const [selectedSchedule, setSelectedSchedule] = useState<ScheduleField>('zoom');
  const [copiedSchedule, setCopiedSchedule] = useState(false);
  const [minCutFrames, setMinCutFrames] = useState(12);
  const [renderMode, setRenderMode] = useState<RenderMode>('smooth');
  const [scheduleStride, setScheduleStride] = useState(1);
  const [savedPresets, setSavedPresets] = useState<SavedPreset[]>(() => (typeof window === 'undefined' ? [] : loadSavedPresets()));
  const [presetName, setPresetName] = useState('');
  const [sectionApproval, setSectionApproval] = useState<Record<number, boolean>>({});
  const [studioSyncing, setStudioSyncing] = useState(false);
  const [studioSyncMessage, setStudioSyncMessage] = useState<string | null>(null);
  const [studioSyncError, setStudioSyncError] = useState<string | null>(null);
  const [studioSeedStatus, setStudioSeedStatus] = useState<string | null>(null);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const animationRef = useRef<number | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const historyRef = useRef<Keyframe[]>([]);
  const studioAudioName = String(studioProject?.meta?.audio?.filename || '').trim();
  const studioAudioRequest = useMemo<SignedProjectMediaRequest | null>(
    () => studioProjectId && studioAudioName
      ? { purpose: 'audio', path: studioAudioName }
      : null,
    [studioAudioName, studioProjectId],
  );
  const signedAudio = useSignedProjectMedia(
    studioProjectId || '',
    studioAudioRequest ? [studioAudioRequest] : [],
  );
  const playbackUrl = audioUrl || signedAudio.urlFor(studioAudioRequest);
  usePreservedMediaSource(audioRef, playbackUrl);

  const addToLog = useCallback((message: string) => {
    setGenerationLog((previous) => [...previous, `${new Date().toLocaleTimeString()}: ${message}`].slice(-16));
  }, []);

  const activeKeyframes = useMemo(() => {
    if (offlineKeyframes.length) return offlineKeyframes;
    return historyRef.current.filter((frame, index, array) => index === 0 || frame.frame !== array[index - 1].frame);
  }, [offlineKeyframes, currentFrame]);

  const beatMarkers = useMemo(() => detectBeatMarkers(activeKeyframes, minCutFrames), [activeKeyframes, minCutFrames]);
  const cueEvents = useMemo(() => buildCueEvents(activeKeyframes, beatMarkers), [activeKeyframes, beatMarkers]);
  const rawSections = useMemo(() => buildSections(activeKeyframes, fps), [activeKeyframes, fps]);
  const sections = useMemo(() => rawSections.map((section) => ({ ...section, approved: sectionApproval[section.id] ?? false })), [rawSections, sectionApproval]);
  const repairSuggestions = useMemo(() => buildRepairSuggestions(sections), [sections]);
  const schedules = useMemo(() => buildSchedules(activeKeyframes), [activeKeyframes]);
  const sparseSchedules = useMemo(() => buildSparseSchedules(schedules, scheduleStride), [schedules, scheduleStride]);
  const handoffManifest = useMemo(() => buildRenderHandoffManifest({ sections, cueEvents, repairSuggestions, schedules: sparseSchedules, renderMode, scheduleStride }), [sections, cueEvents, repairSuggestions, sparseSchedules, renderMode, scheduleStride]);
  const schedulePreview = sparseSchedules[selectedSchedule] ?? '';

  useEffect(() => {
    let cancelled = false;
    const hydrationKey = [
      studioProjectId || '',
      studioSelectedVariant,
      studioAudioName,
      String(studioProject?.meta?.analysis?.timestamp || ''),
      String(studioProject?.meta?.last_reactive_lab?.applied_at || ''),
    ].join(':');
    if (!studioProjectId || !studioProject || hydrationKey === studioHydrationKeyRef.current) return;
    studioHydrationKeyRef.current = hydrationKey;
    setAudioFile(null);
    setAudioUrl((current) => {
      if (current) URL.revokeObjectURL(current);
      return null;
    });

    const hydrate = async () => {
      const savedReactive = studioProject?.meta?.last_reactive_lab || {};
      const metadata = savedReactive?.metadata || {};

      if (Array.isArray(savedReactive?.keyframes) && savedReactive.keyframes.length) {
        setOfflineKeyframes(savedReactive.keyframes);
        setSectionApproval(
          Array.isArray(savedReactive?.sections)
            ? Object.fromEntries(savedReactive.sections.map((section: any) => [section.id, Boolean(section.approved)]))
            : {},
        );
        if (Number.isFinite(Number(metadata?.sensitivity))) setSensitivity(Number(metadata.sensitivity));
        if (Number.isFinite(Number(metadata?.smoothing))) setSmoothing(Number(metadata.smoothing));
        if (Number.isFinite(Number(metadata?.fps))) setFps(Number(metadata.fps));
        if (typeof metadata?.preset === 'string') setMappingPreset(metadata.preset as MappingPreset);
        if (typeof metadata?.renderMode === 'string') setRenderMode(metadata.renderMode as RenderMode);
        if (Number.isFinite(Number(metadata?.scheduleStride))) setScheduleStride(Number(metadata.scheduleStride));
        if (Number.isFinite(Number(metadata?.minCutFrames))) setMinCutFrames(Number(metadata.minCutFrames));
        if (metadata?.scaling && typeof metadata.scaling === 'object') {
          setParameterScaling({
            zoom: Number(metadata.scaling.zoom ?? DEFAULT_SCALING.zoom),
            rotation: Number(metadata.scaling.rotation ?? DEFAULT_SCALING.rotation),
            translation: Number(metadata.scaling.translation ?? DEFAULT_SCALING.translation),
            color: Number(metadata.scaling.color ?? DEFAULT_SCALING.color),
          });
        }
        setStudioSeedStatus(
          `Loaded the saved reactive pass from ${studioProjectName || 'the shared Studio project'} — the session audio is reused automatically, so there is no need to re-upload. Keep refining it here or replace it with a new audio run.`,
        );
      } else {
        setStudioSeedStatus(
          `Using the analyzed Overview track from ${studioProjectName || 'the shared Studio project'} — the session audio is reused automatically, so there is no need to re-upload. Build a reactive pass here when you want motion schedules, or skip it and keep the core creative direction only.`,
        );
      }

    };

    void hydrate();
    return () => {
      cancelled = true;
    };
  }, [studioAudioName, studioProject, studioProjectId, studioProjectName, studioSelectedVariant]);

  const buildStudioPayload = (): ReactiveLabSyncPayload | null => {
    if (!activeKeyframes.length) return null;
    return {
      metadata: {
        createdAt: new Date().toISOString(),
        preset: mappingPreset,
        sensitivity,
        smoothing,
        fps,
        source: offlineKeyframes.length ? 'audio-file' : 'microphone-history',
        fileName: audioFile?.name,
        totalFrames: activeKeyframes.length,
        scaling: parameterScaling,
        beatCount: beatMarkers.length,
        minCutFrames,
        renderMode,
        scheduleStride,
      },
      keyframes: activeKeyframes,
      beat_markers: beatMarkers,
      cue_events: cueEvents,
      sections,
      repair_suggestions: repairSuggestions,
      schedules: sparseSchedules,
      handoff_manifest: handoffManifest,
    };
  };

  const summaryStats = useMemo(() => {
    if (!activeKeyframes.length) return null;
    return {
      frameCount: activeKeyframes.length,
      avgEnergy: averageMetric(activeKeyframes, (frame) => frame.metrics.energy),
      avgBass: averageMetric(activeKeyframes, (frame) => frame.metrics.bass),
      avgTreble: averageMetric(activeKeyframes, (frame) => frame.metrics.treble),
      maxZoom: Math.max(...activeKeyframes.map((frame) => frame.params.zoom)),
      maxRotation: Math.max(...activeKeyframes.map((frame) => Math.abs(frame.params.rotation_y))),
      maxPan: Math.max(...activeKeyframes.map((frame) => Math.max(Math.abs(frame.params.translation_x), Math.abs(frame.params.translation_y)))),
    };
  }, [activeKeyframes]);

  const copySchedulePreview = useCallback(async () => {
    if (!schedulePreview) return;
    await copyText(schedulePreview);
    setCopiedSchedule(true);
    window.setTimeout(() => setCopiedSchedule(false), 1200);
  }, [schedulePreview]);

  const updateVisualization = useCallback((waveform: Uint8Array, spectrum: Uint8Array, params: ReactiveParams) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext('2d');
    if (!context) return;

    const { width, height } = canvas;
    context.fillStyle = 'rgba(2, 6, 23, 0.18)';
    context.fillRect(0, 0, width, height);

    context.lineWidth = 2;
    context.strokeStyle = `hsl(${params.energy_level * 300 + 40}, 85%, 65%)`;
    context.beginPath();
    const sliceWidth = width / waveform.length;
    let x = 0;
    for (let i = 0; i < waveform.length; i += 1) {
      const value = waveform[i] / 255;
      const y = value * height;
      if (i === 0) context.moveTo(x, y);
      else context.lineTo(x, y);
      x += sliceWidth;
    }
    context.stroke();

    const barWidth = width / spectrum.length;
    for (let i = 0; i < spectrum.length; i += 1) {
      const value = spectrum[i] / 255;
      const barHeight = value * height * 0.45;
      context.fillStyle = `hsla(${180 + value * 120}, 90%, 60%, 0.55)`;
      context.fillRect(i * barWidth, height - barHeight, barWidth * 0.8, barHeight);
    }

    context.fillStyle = 'rgba(255,255,255,0.9)';
    context.font = '14px sans-serif';
    context.fillText(`Zoom ${params.zoom.toFixed(3)}  RotY ${params.rotation_y.toFixed(1)}  Strength ${params.strength.toFixed(3)}`, 18, 24);
  }, []);

  const cleanupAudioEngine = useCallback(async () => {
    if (animationRef.current !== null) {
      cancelAnimationFrame(animationRef.current);
      animationRef.current = null;
    }
    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach((track) => track.stop());
      mediaStreamRef.current = null;
    }
    if (audioContext) await audioContext.close();
    setAudioContext(null);
    setAnalyserNode(null);
    setIsPlaying(false);
    setIsRecording(false);
  }, [audioContext]);

  useEffect(() => {
    return () => {
      void cleanupAudioEngine();
      if (audioUrl) URL.revokeObjectURL(audioUrl);
    };
  }, [audioUrl, cleanupAudioEngine]);

  useEffect(() => {
    if (!analyserNode) return;
    const waveform = new Uint8Array(analyserNode.fftSize);
    const spectrum = new Uint8Array(analyserNode.frequencyBinCount);

    const tick = (): void => {
      analyserNode.getByteTimeDomainData(waveform);
      analyserNode.getByteFrequencyData(spectrum);
      setAudioData(new Uint8Array(waveform));
      setFrequencyData(new Uint8Array(spectrum));

      const energy = waveform.reduce((sum, value) => sum + Math.abs(value - 128), 0) / waveform.length / 128;
      const bass = spectrum.slice(0, Math.floor(spectrum.length * 0.1)).reduce((sum, value) => sum + value, 0) / Math.max(1, Math.floor(spectrum.length * 0.1)) / 255;
      const mid = spectrum.slice(Math.floor(spectrum.length * 0.1), Math.floor(spectrum.length * 0.45)).reduce((sum, value) => sum + value, 0) / Math.max(1, Math.floor(spectrum.length * 0.35)) / 255;
      const treble = spectrum.slice(Math.floor(spectrum.length * 0.45)).reduce((sum, value) => sum + value, 0) / Math.max(1, Math.ceil(spectrum.length * 0.55)) / 255;
      const metrics = { energy: clamp01(energy), bass: clamp01(bass), mid: clamp01(mid), treble: clamp01(treble) };
      const time = audioRef.current?.currentTime ?? performance.now() / 1000;
      const params = mapMetricsToParams(metrics, mappingPreset, sensitivity, time, parameterScaling);
      setReactiveParams(params);
      updateVisualization(waveform, spectrum, params);
      const frame = Math.round(time * fps);
      historyRef.current.push({ frame, time, metrics, params });
      setCurrentFrame(frame);
      animationRef.current = requestAnimationFrame(tick);
    };

    animationRef.current = requestAnimationFrame(tick);
    return () => {
      if (animationRef.current !== null) cancelAnimationFrame(animationRef.current);
    };
  }, [analyserNode, fps, mappingPreset, parameterScaling, sensitivity, updateVisualization]);

  const handleFileUpload = async (event: React.ChangeEvent<HTMLInputElement>): Promise<void> => {
    const file = event.target.files?.[0] ?? null;
    if (!file) return;
    setError(null);
    setStudioSyncMessage(null);
    setStudioSyncError(null);
    setStudioSeedStatus(`Using local file ${file.name}. The shared Studio audio remains the base session, but this local file now drives playback and deterministic keyframe generation here.`);
    setAudioFile(file);
    setOfflineKeyframes([]);
    historyRef.current = [];
    setCurrentFrame(0);
    setSectionApproval({});
    if (audioUrl) URL.revokeObjectURL(audioUrl);
    const nextUrl = URL.createObjectURL(file);
    setAudioUrl(nextUrl);
    addToLog(`Loaded ${file.name}`);
  };

  const startMicrophone = async (): Promise<void> => {
    if (!AudioContextCtor) {
      setError('Web Audio API is not available in this browser.');
      return;
    }
    try {
      setStudioSyncMessage(null);
      setStudioSyncError(null);
      historyRef.current = [];
      setOfflineKeyframes([]);
      setError(null);
      setSectionApproval({});
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaStreamRef.current = stream;
      const context = new AudioContextCtor();
      const source = context.createMediaStreamSource(stream);
      const analyser = context.createAnalyser();
      analyser.fftSize = 2048;
      source.connect(analyser);
      setAudioContext(context);
      setAnalyserNode(analyser);
      setIsRecording(true);
      addToLog('Microphone input started');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not access microphone.');
    }
  };

  const stopMicrophone = async (): Promise<void> => {
    await cleanupAudioEngine();
    addToLog('Microphone input stopped');
  };

  const togglePlayback = async (): Promise<void> => {
    if (!audioRef.current || !playbackUrl) return;
    if (isPlaying) {
      audioRef.current.pause();
      setIsPlaying(false);
      return;
    }
    if (!AudioContextCtor) {
      setError('Web Audio API is not available in this browser.');
      return;
    }
    if (!audioContext || !analyserNode) {
      const context = new AudioContextCtor();
      const analyser = context.createAnalyser();
      analyser.fftSize = 2048;
      const source = context.createMediaElementSource(audioRef.current);
      source.connect(analyser);
      analyser.connect(context.destination);
      setAudioContext(context);
      setAnalyserNode(analyser);
    }
    historyRef.current = [];
    setOfflineKeyframes([]);
    setSectionApproval({});
    await audioRef.current.play();
    setIsPlaying(true);
    addToLog(`Playback started for ${audioFile?.name || studioAudioName || 'project audio'}`);
  };

  const buildOfflineBundle = async (): Promise<void> => {
    if (!audioFile) return;
    setIsGenerating(true);
    setError(null);
    setStudioSyncMessage(null);
    setStudioSyncError(null);
    try {
      const keyframes = await buildOfflineKeyframes(audioFile, mappingPreset, sensitivity, smoothing, fps, parameterScaling);
      setOfflineKeyframes(keyframes);
      setCurrentFrame(0);
      setSectionApproval({});
      addToLog(`Built ${keyframes.length} offline keyframes`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not build offline keyframes.');
    } finally {
      setIsGenerating(false);
    }
  };

  const exportBundle = (): void => {
    if (!activeKeyframes.length) return;
    const payload: ExportBundle = {
      metadata: {
        createdAt: new Date().toISOString(),
        preset: mappingPreset,
        sensitivity,
        smoothing,
        fps,
        source: offlineKeyframes.length ? 'audio-file' : 'microphone-history',
        fileName: audioFile?.name,
        totalFrames: activeKeyframes.length,
        scaling: parameterScaling,
        beatCount: beatMarkers.length,
        minCutFrames,
        renderMode,
        scheduleStride,
      },
      keyframes: activeKeyframes,
      beatMarkers,
      cueEvents,
      sections,
      repairSuggestions,
      schedules: sparseSchedules,
      deforum: buildDeforumBundle(activeKeyframes, mappingPreset, fps),
    };
    downloadText(`audio-reactive-bundle-${Date.now()}.json`, JSON.stringify(payload, null, 2), 'application/json');
  };

  const syncToStudio = async (): Promise<void> => {
    if (!onSyncToStudio) return;
    const payload = buildStudioPayload();
    if (!payload) return;
    setStudioSyncing(true);
    setStudioSyncMessage(null);
    setStudioSyncError(null);
    try {
      const result = await onSyncToStudio(payload);
      setStudioSyncMessage(
        typeof result === 'string' && result.trim()
          ? result
          : `Layered reactive schedules into ${studioProjectName || 'the selected Studio project'} and updated the internal renderer motion/camera timeline without replacing the saved story pass.`,
      );
    } catch (caught) {
      setStudioSyncError(caught instanceof Error ? caught.message : 'Could not sync the reactive lab output into Studio.');
    } finally {
      setStudioSyncing(false);
    }
  };

  const exportCsv = (): void => {
    if (!activeKeyframes.length) return;
    const rows = [
      ['frame', 'time', 'energy', 'bass', 'mid', 'treble', 'zoom', 'rotation_y', 'rotation_z', 'translation_x', 'translation_y', 'translation_z', 'strength', 'cfg_scale', 'brightness'].join(','),
      ...activeKeyframes.map((frame) =>
        [
          frame.frame,
          frame.time.toFixed(4),
          frame.metrics.energy.toFixed(4),
          frame.metrics.bass.toFixed(4),
          frame.metrics.mid.toFixed(4),
          frame.metrics.treble.toFixed(4),
          frame.params.zoom.toFixed(4),
          frame.params.rotation_y.toFixed(4),
          frame.params.rotation_z.toFixed(4),
          frame.params.translation_x.toFixed(4),
          frame.params.translation_y.toFixed(4),
          frame.params.translation_z.toFixed(4),
          frame.params.strength.toFixed(4),
          frame.params.cfg_scale.toFixed(4),
          frame.params.brightness.toFixed(4),
        ].join(',')
      ),
    ].join('\n');
    downloadText(`audio-reactive-keyframes-${Date.now()}.csv`, rows, 'text/csv');
  };

  const exportCueCsv = (): void => {
    if (!cueEvents.length) return;
    const rows = [
      ['id', 'frame', 'time', 'cue_type', 'instruction'].join(','),
      ...cueEvents.map((cue) => [cue.id, cue.frame, cue.time.toFixed(4), cue.cueType, `"${cue.instruction.replace(/"/g, '""')}"`].join(',')),
    ].join('\n');
    downloadText(`audio-reactive-cues-${Date.now()}.csv`, rows, 'text/csv');
  };

  const saveCurrentPreset = (): void => {
    const name = presetName.trim();
    if (!name) return;
    const nextPreset: SavedPreset = { name, preset: mappingPreset, sensitivity, smoothing, fps, minCutFrames, renderMode, scheduleStride, scaling: parameterScaling };
    const nextList = [...savedPresets.filter((item) => item.name !== name), nextPreset].sort((a, b) => a.name.localeCompare(b.name));
    setSavedPresets(nextList);
    savePresets(nextList);
    addToLog(`Saved preset ${name}`);
    setPresetName('');
  };

  const applyPreset = (preset: SavedPreset): void => {
    setMappingPreset(preset.preset);
    setSensitivity(preset.sensitivity);
    setSmoothing(preset.smoothing);
    setFps(preset.fps);
    setMinCutFrames(preset.minCutFrames);
    setParameterScaling(preset.scaling);
    setRenderMode(preset.renderMode);
    setScheduleStride(preset.scheduleStride);
    addToLog(`Applied preset ${preset.name}`);
  };

  const deletePreset = (name: string): void => {
    const next = savedPresets.filter((item) => item.name !== name);
    setSavedPresets(next);
    savePresets(next);
    addToLog(`Deleted preset ${name}`);
  };

  const approveAllSections = (): void => {
    setSectionApproval(Object.fromEntries(sections.map((section) => [section.id, true])));
    addToLog('Approved all sections');
  };

  const clearSectionApprovals = (): void => {
    setSectionApproval({});
    addToLog('Cleared section approvals');
  };

  const autoApproveStableSections = (): void => {
    setSectionApproval(Object.fromEntries(sections.map((section) => [section.id, section.avgEnergy >= 0.36 && section.avgEnergy <= 0.88])));
    addToLog('Auto-approved stable sections');
  };

  const exportHandoffManifest = (): void => {
    if (!activeKeyframes.length) return;
    downloadText(`audio-reactive-handoff-${Date.now()}.json`, JSON.stringify(handoffManifest, null, 2), 'application/json');
  };

  const toggleSectionApproval = (sectionId: number): void => {
    setSectionApproval((current) => ({ ...current, [sectionId]: !current[sectionId] }));
  };

  const waveformBars = useMemo(() => Array.from(audioData.slice(0, 48)), [audioData]);
  const spectrumBars = useMemo(() => Array.from(frequencyData.slice(0, 48)), [frequencyData]);
  const rootClassName = compact
    ? 'min-h-0 bg-transparent p-0 text-slate-100'
    : 'min-h-screen bg-slate-950 p-6 text-slate-100';
  const frameClassName = compact ? 'mx-0 max-w-none space-y-6' : 'mx-auto max-w-7xl space-y-6';
  const introCardClassName = compact
    ? 'rounded-3xl border border-white/10 bg-white/5 p-6 backdrop-blur-sm'
    : 'rounded-3xl border border-white/10 bg-white/5 p-8 backdrop-blur-sm';
  const titleClassName = compact ? 'text-2xl font-bold tracking-tight' : 'text-4xl font-bold tracking-tight';

  return (
    <div className={rootClassName}>
      <audio ref={audioRef} crossOrigin="anonymous" onEnded={() => setIsPlaying(false)} hidden />
      <div className={frameClassName}>
        <section className={introCardClassName}>
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <div className="mb-3 inline-flex items-center gap-2 rounded-full bg-cyan-500/15 px-3 py-1 text-sm font-medium text-cyan-300"><Waves size={16} />AI-directed reactive scheduling</div>
              <h1 className={titleClassName}>Audio Reactive Generator</h1>
              <p className="mt-3 max-w-3xl text-slate-300">This tool takes the saved Overview and Planner story pass, then adds motion scheduling on top: cues, sections, camera travel, and export-ready schedules that you can approve without replacing the underlying storyboard.</p>
            </div>
            <div className="flex flex-wrap gap-3">
              <button onClick={exportBundle} disabled={!activeKeyframes.length} className="inline-flex items-center gap-2 rounded-xl bg-white px-4 py-2 text-sm font-medium text-slate-900 disabled:cursor-not-allowed disabled:opacity-50"><Download size={16} />Export JSON bundle</button>
              <button onClick={exportHandoffManifest} disabled={!activeKeyframes.length} className="inline-flex items-center gap-2 rounded-xl border border-white/20 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"><Download size={16} />Export handoff JSON</button>
              <button
                onClick={() => void syncToStudio()}
                disabled={!activeKeyframes.length || !onSyncToStudio || !studioProjectId || studioSyncing}
                className="inline-flex items-center gap-2 rounded-xl bg-emerald-400 px-4 py-2 text-sm font-medium text-slate-950 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Upload size={16} />
                {studioSyncing ? 'Syncing renderer' : 'Apply to internal renderer'}
              </button>
              <button onClick={exportCsv} disabled={!activeKeyframes.length} className="inline-flex items-center gap-2 rounded-xl border border-white/20 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"><Download size={16} />Export keyframes CSV</button>
            </div>
            <div className="mt-4 rounded-2xl border border-white/10 bg-black/20 p-4 text-sm text-slate-300">
              <div className="font-medium text-white">Studio renderer bridge</div>
              <div className="mt-1">
                Target project: <strong>{studioProjectName || 'Select a project in the page bridge above'}</strong>
              </div>
              <div className="mt-1">
                Apply stores the full reactive bundle in project metadata, then updates the canonical motion track and camera data the internal renderer reads during render.
              </div>
              {studioSyncing ? (
                <div className="mt-3">
                  <ProgressBar
                    value={78}
                    label="Reactive handoff"
                    detail="Writing cue schedules, section metadata, and motion/camera tracks."
                    compact
                  />
                </div>
              ) : null}
              {studioSyncMessage && <div className="mt-3 rounded-xl bg-emerald-500/15 px-3 py-2 text-emerald-200">{studioSyncMessage}</div>}
              {studioSyncError && <div className="mt-3 rounded-xl bg-rose-500/15 px-3 py-2 text-rose-200">{studioSyncError}</div>}
            </div>
            {studioSeedStatus ? (
              <div className="mt-4 rounded-2xl border border-cyan-400/20 bg-cyan-500/10 p-4 text-sm text-cyan-100">
                {studioSeedStatus}
              </div>
            ) : null}
          </div>
        </section>

        <section className="grid gap-6 xl:grid-cols-[1.05fr_0.95fr]">
          <div className="space-y-6 rounded-3xl border border-white/10 bg-white/5 p-6 backdrop-blur-sm">
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              <div className="rounded-2xl border border-white/10 bg-black/20 p-4">
                <div className="mb-3 flex items-center gap-2 font-semibold"><Mic size={18} />Live input</div>
                <button onClick={() => void (isRecording ? stopMicrophone() : startMicrophone())} className={`w-full rounded-xl px-4 py-3 font-medium ${isRecording ? 'bg-red-500/90' : 'bg-cyan-500/90 text-slate-950'}`}>{isRecording ? <><MicOff className="mr-2 inline" size={16} />Stop recording</> : <><Mic className="mr-2 inline" size={16} />Start recording</>}</button>
              </div>
              <div className="rounded-2xl border border-white/10 bg-black/20 p-4">
                <div className="mb-3 flex items-center gap-2 font-semibold"><Upload size={18} />Audio file</div>
                <label className="block"><input type="file" accept="audio/*" className="hidden" onChange={(event) => void handleFileUpload(event)} /><div className="cursor-pointer rounded-xl bg-indigo-500/90 px-4 py-3 text-center font-medium">Choose audio file</div></label>
                {audioFile || studioAudioName ? <div className="mt-3 truncate text-sm text-slate-300">{audioFile?.name || studioAudioName}</div> : null}
                <div className="mt-3 flex gap-2">
                  <button onClick={() => void togglePlayback()} disabled={!playbackUrl} className="flex-1 rounded-xl border border-white/15 px-4 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50">{isPlaying ? <><Pause className="mr-2 inline" size={16} />Pause</> : <><Play className="mr-2 inline" size={16} />Play</>}</button>
                  <button onClick={() => { if (audioRef.current) { audioRef.current.pause(); audioRef.current.currentTime = 0; setIsPlaying(false); } }} disabled={!playbackUrl} className="rounded-xl border border-white/15 px-4 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50"><Square size={16} /></button>
                </div>
              </div>
              <div className="rounded-2xl border border-white/10 bg-black/20 p-4">
                <div className="mb-3 flex items-center gap-2 font-semibold"><Zap size={18} />Offline build</div>
                <button onClick={() => void buildOfflineBundle()} disabled={!audioFile || isGenerating} className="w-full rounded-xl bg-fuchsia-500/90 px-4 py-3 font-medium disabled:cursor-not-allowed disabled:opacity-50">{isGenerating ? 'Building keyframes...' : 'Build deterministic keyframes'}</button>
                <div className="mt-2 text-xs text-slate-400">Use this before export if you want a full frame-by-frame schedule.</div>
              </div>
            </div>

            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              <label className="space-y-2 text-sm"><span className="font-medium text-slate-200">Mapping preset</span><select value={mappingPreset} onChange={(e) => setMappingPreset(e.target.value as MappingPreset)} className="w-full rounded-xl border border-white/15 bg-slate-900 px-3 py-2"><option value="cinematic">Cinematic</option><option value="psychedelic">Psychedelic</option><option value="ambient">Ambient</option><option value="percussive">Percussive</option></select></label>
              <label className="space-y-2 text-sm"><span className="font-medium text-slate-200">Sensitivity</span><input type="range" min="0.2" max="2.5" step="0.1" value={sensitivity} onChange={(e) => setSensitivity(parseFloat(e.target.value))} className="w-full" /><div className="text-xs text-slate-400">{sensitivity.toFixed(1)}</div></label>
              <label className="space-y-2 text-sm"><span className="font-medium text-slate-200">Smoothing</span><input type="range" min="0" max="1" step="0.05" value={smoothing} onChange={(e) => setSmoothing(parseFloat(e.target.value))} className="w-full" /><div className="text-xs text-slate-400">{smoothing.toFixed(2)}</div></label>
              <label className="space-y-2 text-sm"><span className="font-medium text-slate-200">FPS</span><input type="number" min={12} max={60} value={fps} onChange={(e) => setFps(clamp(parseInt(e.target.value || '24', 10), 12, 60))} className="w-full rounded-xl border border-white/15 bg-slate-900 px-3 py-2" /></label>
              <label className="space-y-2 text-sm"><span className="font-medium text-slate-200">Min cut spacing (frames)</span><input type="number" min={4} max={96} value={minCutFrames} onChange={(e) => setMinCutFrames(clamp(parseInt(e.target.value || '12', 10), 4, 96))} className="w-full rounded-xl border border-white/15 bg-slate-900 px-3 py-2" /></label>
              <label className="space-y-2 text-sm"><span className="font-medium text-slate-200">Render mode</span><select value={renderMode} onChange={(e) => setRenderMode(e.target.value as RenderMode)} className="w-full rounded-xl border border-white/15 bg-slate-900 px-3 py-2"><option value="smooth">Smooth</option><option value="cut-heavy">Cut-heavy</option><option value="performance-led">Performance-led</option><option value="ambient">Ambient</option></select></label>
              <label className="space-y-2 text-sm"><span className="font-medium text-slate-200">Schedule stride</span><input type="number" min={1} max={24} value={scheduleStride} onChange={(e) => setScheduleStride(clamp(parseInt(e.target.value || '1', 10), 1, 24))} className="w-full rounded-xl border border-white/15 bg-slate-900 px-3 py-2" /></label>
              <div className="rounded-2xl border border-white/10 bg-black/20 p-4 text-sm">
                <div className="mb-2 flex items-center gap-2 font-semibold"><Settings size={16} />Scaling</div>
                <div className="grid grid-cols-2 gap-2 text-xs">
                  <label className="space-y-1"><span>Zoom</span><input type="number" step="0.1" value={parameterScaling.zoom} onChange={(e) => setParameterScaling((current) => ({ ...current, zoom: parseFloat(e.target.value || '1') }))} className="w-full rounded-lg border border-white/15 bg-slate-900 px-2 py-1" /></label>
                  <label className="space-y-1"><span>Rotation</span><input type="number" step="0.1" value={parameterScaling.rotation} onChange={(e) => setParameterScaling((current) => ({ ...current, rotation: parseFloat(e.target.value || '1') }))} className="w-full rounded-lg border border-white/15 bg-slate-900 px-2 py-1" /></label>
                  <label className="space-y-1"><span>Translation</span><input type="number" step="0.1" value={parameterScaling.translation} onChange={(e) => setParameterScaling((current) => ({ ...current, translation: parseFloat(e.target.value || '1') }))} className="w-full rounded-lg border border-white/15 bg-slate-900 px-2 py-1" /></label>
                  <label className="space-y-1"><span>Color</span><input type="number" step="0.1" value={parameterScaling.color} onChange={(e) => setParameterScaling((current) => ({ ...current, color: parseFloat(e.target.value || '1') }))} className="w-full rounded-lg border border-white/15 bg-slate-900 px-2 py-1" /></label>
                </div>
              </div>
            </div>

            <div className="rounded-2xl border border-white/10 bg-black/20 p-4">
              <div className="mb-3 flex items-center gap-2 font-semibold"><Save size={18} />Preset manager</div>
              <div className="grid gap-3 md:grid-cols-[1fr_auto]">
                <input value={presetName} onChange={(e) => setPresetName(e.target.value)} placeholder="Preset name" className="rounded-xl border border-white/15 bg-slate-900 px-3 py-2 text-sm" />
                <button onClick={saveCurrentPreset} className="rounded-xl bg-emerald-500/90 px-4 py-2 text-sm font-medium text-slate-950">Save preset</button>
              </div>
              {savedPresets.length ? <div className="mt-3 flex flex-wrap gap-2">{savedPresets.map((preset) => <div key={preset.name} className="inline-flex items-center gap-1 rounded-full border border-white/15 px-2 py-1 text-xs font-medium text-slate-200"><button onClick={() => applyPreset(preset)}>{preset.name}</button><button onClick={() => deletePreset(preset.name)} className="rounded-full bg-white/10 px-1">×</button></div>)}</div> : <div className="mt-3 text-xs text-slate-400">Save presets to recall mapping setups quickly.</div>}
            </div>
            {error ? <div className="rounded-2xl bg-red-500/10 px-4 py-3 text-sm text-red-300">{error}</div> : null}
          </div>

          <div className="space-y-6">
            <div className="rounded-3xl border border-white/10 bg-white/5 p-6 backdrop-blur-sm">
              <div className="mb-4 text-lg font-semibold">Live visualization</div>
              <canvas ref={canvasRef} width={900} height={300} className="h-72 w-full rounded-2xl bg-black/40" />
              <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <div className="rounded-2xl bg-black/20 p-3"><div className="text-xs text-slate-400">Zoom</div><div className="text-xl font-semibold">{reactiveParams.zoom.toFixed(3)}</div></div>
                <div className="rounded-2xl bg-black/20 p-3"><div className="text-xs text-slate-400">Pan X</div><div className="text-xl font-semibold">{reactiveParams.translation_x.toFixed(1)}</div></div>
                <div className="rounded-2xl bg-black/20 p-3"><div className="text-xs text-slate-400">Pan Y</div><div className="text-xl font-semibold">{reactiveParams.translation_y.toFixed(1)}</div></div>
                <div className="rounded-2xl bg-black/20 p-3"><div className="text-xs text-slate-400">Strength</div><div className="text-xl font-semibold">{reactiveParams.strength.toFixed(3)}</div></div>
                <div className="rounded-2xl bg-black/20 p-3"><div className="text-xs text-slate-400">Frame</div><div className="text-xl font-semibold">{currentFrame}</div></div>
              </div>
            </div>

            <div className="rounded-3xl border border-white/10 bg-white/5 p-6 backdrop-blur-sm">
              <div className="mb-4 text-lg font-semibold">Mini analyzers</div>
              <div className="grid gap-4 md:grid-cols-2">
                <div><div className="mb-2 text-sm text-slate-300">Waveform</div><div className="flex h-24 items-end gap-[2px] rounded-2xl bg-black/20 p-3">{waveformBars.map((value, index) => <div key={index} className="flex-1 rounded-full bg-cyan-400/70" style={{ height: `${Math.max(6, (value / 255) * 100)}%` }} />)}</div></div>
                <div><div className="mb-2 text-sm text-slate-300">Spectrum</div><div className="flex h-24 items-end gap-[2px] rounded-2xl bg-black/20 p-3">{spectrumBars.map((value, index) => <div key={index} className="flex-1 rounded-full bg-fuchsia-400/70" style={{ height: `${Math.max(6, (value / 255) * 100)}%` }} />)}</div></div>
              </div>
            </div>
          </div>
        </section>

        <section className="grid gap-6 xl:grid-cols-[0.9fr_1.1fr]">
          <div className="space-y-6">
            <div className="rounded-3xl border border-white/10 bg-white/5 p-6 backdrop-blur-sm">
              <div className="mb-4 text-lg font-semibold">Reactive stats</div>
              {summaryStats ? (
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="rounded-2xl bg-black/20 p-4"><div className="text-xs text-slate-400">Frames</div><div className="text-2xl font-semibold">{summaryStats.frameCount}</div></div>
                  <div className="rounded-2xl bg-black/20 p-4"><div className="text-xs text-slate-400">Beat markers</div><div className="text-2xl font-semibold">{beatMarkers.length}</div></div>
                  <div className="rounded-2xl bg-black/20 p-4"><div className="text-xs text-slate-400">Avg energy</div><div className="text-2xl font-semibold">{summaryStats.avgEnergy.toFixed(3)}</div></div>
                  <div className="rounded-2xl bg-black/20 p-4"><div className="text-xs text-slate-400">Avg bass</div><div className="text-2xl font-semibold">{summaryStats.avgBass.toFixed(3)}</div></div>
                  <div className="rounded-2xl bg-black/20 p-4"><div className="text-xs text-slate-400">Peak zoom</div><div className="text-2xl font-semibold">{summaryStats.maxZoom.toFixed(3)}</div></div>
                  <div className="rounded-2xl bg-black/20 p-4"><div className="text-xs text-slate-400">Peak rotation</div><div className="text-2xl font-semibold">{summaryStats.maxRotation.toFixed(1)}°</div></div>
                  <div className="rounded-2xl bg-black/20 p-4"><div className="text-xs text-slate-400">Peak pan</div><div className="text-2xl font-semibold">{summaryStats.maxPan.toFixed(1)}</div></div>
                </div>
              ) : <div className="rounded-2xl bg-black/20 p-4 text-sm text-slate-400">Play live audio or build an offline schedule to populate stats.</div>}
            </div>

            <div className="rounded-3xl border border-white/10 bg-white/5 p-6 backdrop-blur-sm">
              <div className="mb-4 text-lg font-semibold">Generation log</div>
              <div className="rounded-2xl bg-black/30 p-4 text-sm font-mono">{generationLog.length ? generationLog.map((entry, index) => <div key={index} className="mb-1 text-emerald-300 last:mb-0">{entry}</div>) : <div className="text-slate-500">Log messages will appear here.</div>}</div>
            </div>
          </div>

          <div className="space-y-6">
            <div className="rounded-3xl border border-white/10 bg-white/5 p-6 backdrop-blur-sm">
              <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                <div><div className="text-lg font-semibold">Schedule preview</div><div className="text-xs text-slate-400">Preview is stride-compressed using {scheduleStride}.</div></div>
                <div className="flex flex-wrap gap-2">
                  <select value={selectedSchedule} onChange={(e) => setSelectedSchedule(e.target.value as ScheduleField)} className="rounded-xl border border-white/15 bg-slate-900 px-3 py-2 text-sm"><option value="zoom">Zoom</option><option value="rotation_y">Rotation Y</option><option value="rotation_z">Rotation Z</option><option value="translation_x">Pan X</option><option value="translation_y">Pan Y</option><option value="translation_z">Translation Z</option><option value="strength">Strength</option><option value="cfg_scale">CFG scale</option><option value="brightness">Brightness</option></select>
                  <button onClick={() => void copySchedulePreview()} disabled={!schedulePreview} className="rounded-xl border border-white/15 px-3 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50"><Copy className="mr-2 inline" size={14} />{copiedSchedule ? 'Copied' : 'Copy'}</button>
                </div>
              </div>
              <div className="rounded-2xl bg-black/30 p-4 text-xs leading-6 text-cyan-200 break-all">{schedulePreview || 'No schedule available yet.'}</div>
            </div>

            <div className="rounded-3xl border border-white/10 bg-white/5 p-6 backdrop-blur-sm">
              <div className="mb-4 flex flex-wrap items-center justify-between gap-3"><div className="text-lg font-semibold">Cue events</div><button onClick={exportCueCsv} disabled={!cueEvents.length} className="rounded-xl border border-white/15 px-3 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50"><Download className="mr-2 inline" size={14} />Export cue CSV</button></div>
              {cueEvents.length ? (
                <div className="overflow-x-auto">
                  <table className="min-w-full text-sm"><thead className="text-left text-slate-400"><tr><th className="px-3 py-2">Time</th><th className="px-3 py-2">Frame</th><th className="px-3 py-2">Type</th><th className="px-3 py-2">Instruction</th></tr></thead><tbody>{cueEvents.slice(0, 12).map((cue) => <tr key={cue.id} className="border-t border-white/10"><td className="px-3 py-2 text-slate-300">{formatTime(cue.time)}</td><td className="px-3 py-2 text-slate-300">{cue.frame}</td><td className="px-3 py-2"><span className="rounded-full bg-white/10 px-2 py-1 text-xs uppercase tracking-wide">{cue.cueType}</span></td><td className="px-3 py-2 text-slate-300">{cue.instruction}</td></tr>)}</tbody></table>
                </div>
              ) : <div className="rounded-2xl bg-black/20 p-4 text-sm text-slate-400">Cue events appear after live capture or offline keyframe generation.</div>}
            </div>

            <div className="rounded-3xl border border-white/10 bg-white/5 p-6 backdrop-blur-sm">
              <div className="mb-4 flex flex-wrap items-center justify-between gap-3"><div className="flex items-center gap-2 text-lg font-semibold"><Wrench className="text-orange-300" size={18} />Sections + repair suggestions</div><div className="flex flex-wrap gap-2"><button onClick={autoApproveStableSections} className="rounded-xl border border-white/15 px-3 py-2 text-xs font-medium">Auto-approve stable</button><button onClick={approveAllSections} className="rounded-xl border border-white/15 px-3 py-2 text-xs font-medium">Approve all</button><button onClick={clearSectionApprovals} className="rounded-xl border border-white/15 px-3 py-2 text-xs font-medium">Clear approvals</button></div></div>
              {sections.length ? (
                <div className="space-y-4">
                  <div className="grid gap-3 md:grid-cols-2">{sections.map((section) => <div key={section.id} className="rounded-2xl bg-black/20 p-4"><div className="text-xs text-slate-400">{formatTime(section.startTime)}–{formatTime(section.endTime)}</div><div className="mt-1 flex items-center justify-between gap-2"><div className="text-lg font-semibold">{section.label}</div><span className="rounded-full bg-white/10 px-2 py-1 text-[10px] uppercase tracking-wide">{section.renderMode}</span></div><div className="mt-2 text-sm text-slate-300">Average energy {section.avgEnergy.toFixed(3)}</div><button onClick={() => toggleSectionApproval(section.id)} className={`mt-3 rounded-xl px-3 py-2 text-xs font-medium ${section.approved ? 'bg-emerald-400/20 text-emerald-200' : 'bg-white/10 text-slate-200'}`}>{section.approved ? 'Approved' : 'Approve for render'}</button></div>)}</div>
                  <div className="space-y-3">{repairSuggestions.map((item) => <div key={item.id} className="rounded-2xl bg-black/20 p-4 text-sm"><div className="font-medium text-slate-100">Section {item.sectionId}</div><div className="mt-1 text-slate-300">{item.issue}</div><div className="mt-2 text-slate-200"><strong>Action:</strong> {item.action}</div></div>)}</div>
                </div>
              ) : <div className="rounded-2xl bg-black/20 p-4 text-sm text-slate-400">Sections appear after you generate keyframes.</div>}
            </div>
          </div>
        </section>
      </div>
    </div>
  );
};

export default AudioReactiveGenerator;
