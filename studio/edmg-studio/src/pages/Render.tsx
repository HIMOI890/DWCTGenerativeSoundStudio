import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  apiGet,
  apiPost,
  apiUpload,
  getBackendUrl,
  type ApiError,
  type ApiRequestOptions,
  type SignedProjectMediaRequest,
} from "../components/api";
import { CreativeDirectionPanel } from "../components/CreativeDirectionPanel";
import {
  expectedRevisionBody,
  ProjectRevisionConflict,
  projectRevision,
  projectRevisionFromResponse,
  revisionConflictFrom,
} from "../components/ProjectRevisionConflict";
import { RenewingVideo } from "../components/RenewingMedia";
import { RenderPlanPanel } from "../components/RenderPlanPanel";
import { VisualDnaPanel } from "../components/VisualDnaPanel";
import { OverlayStage } from "../components/OverlayStage";
import { useUiMode } from "../components/uiMode";
import { resolveProjectId } from "../components/projectSelection";
import { useStudioSession } from "../components/studioSession";
import { readRenderDefaults, writeRenderDefaults } from "../components/renderDefaults";
import {
  RenderControlCenter,
  type RenderQuickGoal,
  type RenderQuickQuality,
} from "../components/RenderControlCenter";
import {
  LayeredAnimationControls,
  type LayeredAnimationPayload,
} from "../components/LayeredAnimationControls";
import {
  GENUINE_RENDER_ENGINES,
  RenderOrchestratorIntentControls,
  createDefaultRenderOrchestratorIntent,
  type RenderOrchestratorIntentValue,
} from "../components/RenderOrchestratorIntentControls";
import { desktopActionLabel, runDesktopArtifactAction } from "../components/desktopArtifacts";
import { StructuredSummary } from "../components/StructuredSummary";
import { ProjectJobsPanel } from "../shared/jobs/ProjectJobsPanel";
import { useProjectJobs } from "../shared/jobs/useProjectJobs";
import { isJobActive, type StudioJob } from "../shared/jobs/jobStatus";
import { useSignedProjectMedia } from "../hooks/useSignedProjectMedia";
import type { PageProps } from "../types/pageProps";

type CatalogEntry = {
  id: string;
  name: string;
  kind: string;
  source?: string;
  filename?: string;
  family?: string;
  engine?: string;
  supports_txt2img?: boolean;
  supports_img2img?: boolean;
  supports_inpaint?: boolean;
  supports_outpaint?: boolean;
  supports_controlnet?: boolean;
  supports_internal_video?: boolean;
  render?: {
    engine?: string;
    family?: string;
    checkpoint_name?: string;
    controlnet_name?: string;
    svd_checkpoint?: string;
    conditioning_mode?: string;
    render_modes?: string[];
    workflow_family?: string;
    base_model_id?: string;
    profile_width?: number;
    profile_height?: number;
    max_batch?: number;
    live_preview?: boolean;
    video_model_engine?: string;
    base_family?: string;
  };
};

type InternalVideoModelEngine = "auto" | "svd" | "animatediff";
type ExplicitInternalVideoModelEngine = Exclude<InternalVideoModelEngine, "auto">;
type KeyframeContinuityMode = "scene" | "project";

const CANONICAL_INTERNAL_VIDEO_MODEL_IDS: Record<ExplicitInternalVideoModelEngine, string> = {
  svd: "hf_svd_xt_1_1_internal",
  animatediff: "hf_animatediff_motion_adapter_v15_2_internal",
};

function normalizeInternalVideoModelEngine(value: unknown): InternalVideoModelEngine {
  const engine = String(value || "auto").trim().toLowerCase();
  return engine === "svd" || engine === "animatediff" ? engine : "auto";
}

function normalizeKeyframeContinuityMode(value: unknown): KeyframeContinuityMode {
  return String(value || "scene").trim().toLowerCase() === "project" ? "project" : "scene";
}

function declaredInternalVideoModelEngine(model: CatalogEntry | null | undefined): ExplicitInternalVideoModelEngine | null {
  const engine = String(model?.render?.video_model_engine || "").trim().toLowerCase();
  return engine === "svd" || engine === "animatediff" ? engine : null;
}

type SelectedLora = {
  name: string;
  label: string;
  weight: number;
};

type ConditioningMode = "raw" | "blur" | "edge" | "external";

type ControlNetUnitDraft = {
  key: string;
  model: string;
  reference_asset: string;
  conditioning_mode: ConditioningMode;
  strength: number;
  start_percent: number;
  end_percent: number;
};

type OutpaintDraft = {
  top_px: number;
  right_px: number;
  bottom_px: number;
  left_px: number;
};

type HiresFixDraft = {
  enabled: boolean;
  scale: number;
  steps: number;
  denoise: number;
  upscaler: string;
};

type RefinerDraft = {
  enabled: boolean;
  model: string;
  switch_at: number;
  steps: number;
};

const SAMPLER_OPTIONS = [
  "euler",
  "euler_ancestral",
  "heun",
  "dpmpp_2m",
  "dpmpp_2m_sde",
  "dpmpp_sde",
  "ddim",
];

const UPSCALER_OPTIONS = [
  { value: "latent_bislerp", label: "Latent bislerp" },
  { value: "latent_bicubic", label: "Latent bicubic" },
  { value: "latent_bilinear", label: "Latent bilinear" },
  { value: "pixel_lanczos", label: "Pixel Lanczos" },
  { value: "pixel_bicubic", label: "Pixel bicubic" },
];

const REAL_CONDUCTOR_ENGINES = [...GENUINE_RENDER_ENGINES];

function formatDurationSources(preflight: any): string {
  const sources = Array.isArray(preflight?.duration_sources) ? preflight.duration_sources : [];
  return sources
    .filter((item: any) => item && item.source && Number(item.duration_s) > 0)
    .slice(0, 4)
    .map((item: any) => `${String(item.source).replace(/_/g, " ")} ${Number(item.duration_s).toFixed(1)}s`)
    .join(" • ");
}

function renderAspectRatioForSize(width: number, height: number): RenderOrchestratorIntentValue["aspect_ratio"] {
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return "16:9";
  const ratio = width / height;
  if (Math.abs(ratio - 1) < 0.08) return "1:1";
  if (ratio < 0.8) return "9:16";
  if (ratio > 2.05) return "21:9";
  return "16:9";
}

export default function Render({ onNavigate, backendUrl: backendUrlProp }: RenderProps) {
  const savedRenderDefaults = readRenderDefaults();
  const { mode: uiMode } = useUiMode();
  const { projectId, setProjectId } = useStudioSession();
  const backendUrl = backendUrlProp || getBackendUrl();

  const [projects, setProjects] = useState<any[]>([]);
  const [project, setProject] = useState<any>(null);
  const [visualDnaHints, setVisualDnaHints] = useState<any>(null);

  const [plan, setPlan] = useState<any>(null);
  const [analysis, setAnalysis] = useState<any>(null);
  const [selectedVariant, setSelectedVariant] = useState<number>(0);
  const [conductorPlan, setConductorPlan] = useState<any>(null);
  const [conductorEnvironment, setConductorEnvironment] = useState<any>(null);
  const [continuityReport, setContinuityReport] = useState<any>(null);
  const [performerPlan, setPerformerPlan] = useState<any>(null);
  const [performerStatus, setPerformerStatus] = useState<string>("");
  const [planningPerformer, setPlanningPerformer] = useState(false);
  const [runningPerformer, setRunningPerformer] = useState(false);
  const [orchestratorIntent, setOrchestratorIntent] = useState<RenderOrchestratorIntentValue>(
    () => createDefaultRenderOrchestratorIntent(0),
  );

  const [renderPreset, setRenderPreset] = useState<"fast" | "balanced" | "quality" | "ultra">((savedRenderDefaults.renderPreset as any) || "balanced");
  const [quickRenderGoal, setQuickRenderGoal] = useState<RenderQuickGoal>("auto");
  const [advancedControlsOpen, setAdvancedControlsOpen] = useState(uiMode === "advanced");
  const [checkpointName, setCheckpointName] = useState<string>("");
  const [renderMode, setRenderMode] = useState<"stills" | "motion_ad" | "motion_svd">("stills");
  const [motionFps, setMotionFps] = useState<number>(12);
  const [maxFramesPerScene, setMaxFramesPerScene] = useState<number>(240);
  const [motionContextLength, setMotionContextLength] = useState<number>(16);
  const [motionContextOverlap, setMotionContextOverlap] = useState<number>(4);
  const [stillWorkflow, setStillWorkflow] = useState<"txt2img" | "img2img" | "inpaint" | "outpaint" | "controlnet">("txt2img");
  const [selectedStillModelId, setSelectedStillModelId] = useState<string>("hf_sdxl_base_1_0");
  const [selectedMotionModelId, setSelectedMotionModelId] = useState<string>("hf_sd35_large_turbo_ckpt");
  const [selectedSvdModelId, setSelectedSvdModelId] = useState<string>("hf_svd_xt_1_1");
  const [sourceAsset, setSourceAsset] = useState<string>("");
  const [stillMaskAsset, setStillMaskAsset] = useState<string>("");
  const [controlnetUnits, setControlnetUnits] = useState<ControlNetUnitDraft[]>([]);
  const [outpaint, setOutpaint] = useState<OutpaintDraft>({ top_px: 0, right_px: 0, bottom_px: 0, left_px: 0 });
  const [denoiseStrength, setDenoiseStrength] = useState<number>(0.75);
  const [referenceUploadFile, setReferenceUploadFile] = useState<File | null>(null);
  const [workflowMaskUploadFile, setWorkflowMaskUploadFile] = useState<File | null>(null);
  const [renderWidth, setRenderWidth] = useState<number>(Number(savedRenderDefaults.stillWidth ?? 1024));
  const [renderHeight, setRenderHeight] = useState<number>(Number(savedRenderDefaults.stillHeight ?? 576));
  const [renderSteps, setRenderSteps] = useState<number>(Number(savedRenderDefaults.stillSteps ?? 28));
  const [renderCfg, setRenderCfg] = useState<number>(Number(savedRenderDefaults.stillCfg ?? 7));
  const [renderSampler, setRenderSampler] = useState<string>(String(savedRenderDefaults.stillSampler || "euler"));
  const [renderNegativePrompt, setRenderNegativePrompt] = useState<string>(
    String(savedRenderDefaults.stillNegativePrompt || "blurry, low quality, watermark, text, logo")
  );
  const [renderSeed, setRenderSeed] = useState<string>(String(savedRenderDefaults.stillSeed || ""));
  const [cosmosSceneIndex, setCosmosSceneIndex] = useState<number>(0);
  const [azureFoundrySceneIndex, setAzureFoundrySceneIndex] = useState<number>(0);
  const [hiresFix, setHiresFix] = useState<HiresFixDraft>({
    enabled: Boolean(savedRenderDefaults.hiresFixEnabled ?? false),
    scale: Number(savedRenderDefaults.hiresFixScale ?? 1.5),
    steps: Number(savedRenderDefaults.hiresFixSteps ?? 0),
    denoise: Number(savedRenderDefaults.hiresFixDenoise ?? 0.35),
    upscaler: String(savedRenderDefaults.stillUpscaler || "latent_bislerp"),
  });
  const [refiner, setRefiner] = useState<RefinerDraft>({
    enabled: Boolean(savedRenderDefaults.refinerEnabled ?? false),
    model: String(savedRenderDefaults.refinerModel || ""),
    switch_at: Number(savedRenderDefaults.refinerSwitchAt ?? 0.8),
    steps: Number(savedRenderDefaults.refinerSteps ?? 0),
  });

  const [trtBatchSize, setTrtBatchSize] = useState<number>(1);
  const [trtLivePreview, setTrtLivePreview] = useState<boolean>(false);
  const [trtPreviewImage, setTrtPreviewImage] = useState<string | null>(null);
  const [trtPreviewLoading, setTrtPreviewLoading] = useState<boolean>(false);
  const [selectedLoras, setSelectedLoras] = useState<SelectedLora[]>([]);
  const [loraToAdd, setLoraToAdd] = useState<string>("");

  const [internalFpsOut, setInternalFpsOut] = useState<number>(24);
  const [internalFpsRender, setInternalFpsRender] = useState<number>(2);
  const [internalKeyInterval, setInternalKeyInterval] = useState<number>(5);
  const [internalInterp, setInternalInterp] = useState<"auto"|"minterpolate"|"fps"|"rife">("auto");
  const [internalModelId, setInternalModelId] = useState<string>("auto");
  const [internalRenderMode, setInternalRenderMode] = useState<"auto"|"diffusion"|"hosted"|"tensorrt">("auto");
  const [internalDevicePreference, setInternalDevicePreference] = useState<"auto"|"cpu"|"cuda"|"mps"|"directml">("auto");
  const [internalRenderTier, setInternalRenderTier] = useState<"auto"|"draft"|"balanced"|"quality">((savedRenderDefaults.internalRenderTier as any) || "auto");
  const [internalAllowHostedFallback, setInternalAllowHostedFallback] = useState<boolean>(true);

  const [internalTemporalMode, setInternalTemporalMode] = useState<"off"|"keyframes"|"frame_img2img"|"video_model">("frame_img2img");
  const [internalTemporalStrength, setInternalTemporalStrength] = useState<number>(0.35);
  const [internalTemporalSteps, setInternalTemporalSteps] = useState<number>(12);
  const [internalRefineEvery, setInternalRefineEvery] = useState<number>(1);
  const [internalAnchorStrength, setInternalAnchorStrength] = useState<number>(0.2);
  const [internalPromptBlend, setInternalPromptBlend] = useState<boolean>(true);
  const [internalResumeExisting, setInternalResumeExisting] = useState<boolean>(savedRenderDefaults.internalResumeExisting ?? true);
  const [internalMotionStrategy, setInternalMotionStrategy] = useState<"manual"|"storyboard_full_motion">((savedRenderDefaults.internalMotionStrategy as any) || "manual");
  const [internalStoryboardShotMax, setInternalStoryboardShotMax] = useState<number>(Number(savedRenderDefaults.internalStoryboardShotMaxS ?? 4));
  const [internalVideoModelEngine, setInternalVideoModelEngine] = useState<InternalVideoModelEngine>("auto");
  const [internalVideoModelId, setInternalVideoModelId] = useState<string>("");
  const [internalVideoMaxFrames, setInternalVideoMaxFrames] = useState<number>(25);
  const [internalVideoMotionBucket, setInternalVideoMotionBucket] = useState<number>(127);
  const [internalVideoNoiseAug, setInternalVideoNoiseAug] = useState<number>(0.02);
  const [internalVideoDecodeChunk, setInternalVideoDecodeChunk] = useState<number>(8);
  const [internalVideoDtype, setInternalVideoDtype] = useState<"auto"|"float16"|"bfloat16"|"float32">("auto");
  const [internalVideoCpuOffload, setInternalVideoCpuOffload] = useState<boolean>(false);
  const [internalVideoMotionScoreMode, setInternalVideoMotionScoreMode] = useState<"auto"|"manual"|"off">("auto");
  const [internalVideoManualMotionScore, setInternalVideoManualMotionScore] = useState<number>(4);
  const [internalVideoAnchorMode, setInternalVideoAnchorMode] = useState<"start"|"end"|"both"|"loop">("start");
  const [internalVideoPromptRefine, setInternalVideoPromptRefine] = useState<boolean>(true);
  const [internalVideoSceneMotion, setInternalVideoSceneMotion] = useState<"camera"|"subject"|"scene">("subject");
  const [internalVideoApplyTimelineCamera, setInternalVideoApplyTimelineCamera] = useState<boolean>(savedRenderDefaults.internalVideoApplyTimelineCamera ?? true);
  const [internalVideoKeyframeRenderer, setInternalVideoKeyframeRenderer] = useState<"internal"|"tensorrt_sd15">("internal");
  const [keyframeContinuityMode, setKeyframeContinuityMode] = useState<KeyframeContinuityMode>(() => (
    normalizeKeyframeContinuityMode(
      savedRenderDefaults.keyframeContinuityMode
      ?? (savedRenderDefaults.internalMotionStrategy === "storyboard_full_motion" ? "project" : "scene"),
    )
  ));

  const [timeline, setTimeline] = useState<any>({ layers: [], camera: { keyframes: [] } });
  const [timelineDirty, setTimelineDirty] = useState<boolean>(false);

  const [selectedLayerIdxs, setSelectedLayerIdxs] = useState<number[]>([]);
  const [editMaskMode, setEditMaskMode] = useState<boolean>(false);
  const [editorBgPath, setEditorBgPath] = useState<string>("");

  const [editorTimeS, setEditorTimeS] = useState<number>(0);
  const [autoKey, setAutoKey] = useState<boolean>(true);

  const singleLayerIdx = selectedLayerIdxs.length === 1 ? selectedLayerIdxs[0] : null;

  const upsertKeyframe = (layer: any, t: number, patch: any) => {
    const kfs = Array.isArray(layer.keyframes) ? [...layer.keyframes] : [];
    const eps = 1e-6;
    const i = kfs.findIndex((k: any) => typeof k?.t === "number" && Math.abs(k.t - t) < eps);
    const kf = { ...(i >= 0 ? kfs[i] : {}), t, ...patch };
    if (i >= 0) kfs[i] = kf;
    else kfs.push(kf);
    kfs.sort((a: any, b: any) => Number(a?.t ?? 0) - Number(b?.t ?? 0));
    return kfs;
  };

  const addLayerKeyframesAtTime = (t: number, mode: "layer" | "mask") => {
    const layers = timeline?.layers || [];
    const indices = [...selectedLayerIdxs];
    if (!indices.length) return;

    const nextLayers = layers.map((l: any) => ({ ...l }));
    for (const idx of indices) {
      const l = nextLayers[idx];
      if (!l) continue;

      const patch: any =
        mode === "mask"
          ? {
              mask_x: Number(l.mask_x ?? 0),
              mask_y: Number(l.mask_y ?? 0),
              mask_scale: Number(l.mask_scale ?? 1),
              mask_rotation_deg: Number(l.mask_rotation_deg ?? 0),
              mask_asset: l.mask_asset,
              mask_invert: !!l.mask_invert,
              mask_feather_px: Number(l.mask_feather_px ?? 0),
            }
          : {
              x: Number(l.x ?? 0),
              y: Number(l.y ?? 0),
              w: Number(l.w ?? 0),
              h: Number(l.h ?? 0),
              opacity: Number(l.opacity ?? 1),
              rotation_deg: Number(l.rotation_deg ?? 0),
              blend_mode: l.blend_mode ?? "normal",
              asset: l.asset,
              text: l.text,
              color: l.color,
              stroke_color: l.stroke_color,
              stroke_width: l.stroke_width,
              size: l.size,
              mask_asset: l.mask_asset,
              mask_invert: !!l.mask_invert,
              mask_feather_px: Number(l.mask_feather_px ?? 0),
              mask_x: Number(l.mask_x ?? 0),
              mask_y: Number(l.mask_y ?? 0),
              mask_scale: Number(l.mask_scale ?? 1),
              mask_rotation_deg: Number(l.mask_rotation_deg ?? 0),
            };

      l.keyframes = upsertKeyframe(l, t, patch);
    }

    setTimeline({ ...timeline, layers: nextLayers });
    setTimelineDirty(true);
  };

  const setSelection = (indices: number[]) => {
    setSelectedLayerIdxs(indices);
    if (indices.length !== 1) setEditMaskMode(false);
  };

  const overlayAssets = project?.meta?.assets?.overlays || [];
  const maskAssets = project?.meta?.assets?.masks || [];
  const [overlayFile, setOverlayFile] = useState<File | null>(null);
  const [maskFile, setMaskFile] = useState<File | null>(null);
  const [overlayText, setOverlayText] = useState<string>("");


  const [caps, setCaps] = useState<any>(null);
  const [hardware, setHardware] = useState<any>(null);
  const [renderProviders, setRenderProviders] = useState<any>(null);
  const [videoRoute, setVideoRoute] = useState<any>(null);
  const [modelCatalog, setModelCatalog] = useState<CatalogEntry[]>([]);
  const [installedModels, setInstalledModels] = useState<Record<string, boolean>>({});
  const [projectAssets, setProjectAssets] = useState<{ refs: { path: string }[] }>({ refs: [] });
  const [projectOutputImages, setProjectOutputImages] = useState<{ path: string }[]>([]);
  const [validate, setValidate] = useState<any>(null);
  const [internalPreflight, setInternalPreflight] = useState<any>(null);
  const [internalPolling, setInternalPolling] = useState<boolean>(true);
  const [internalJobInfo, setInternalJobInfo] = useState<string | null>(null);
  const {
    jobs: projectJobs,
    selectedLog: internalSelectedLog,
    setSelectedLog: setInternalSelectedLog,
    lastRefreshAt: internalJobsLastRefreshAt,
    error: internalJobsError,
    setError: setInternalJobsError,
    refresh: refreshProjectJobs,
    loadJobLog: loadInternalJobLog,
    runJobAction: runInternalJobAction,
    resumeFromCheckpoint: resumeInternalFromCheckpoint,
    restartClean: restartInternalClean,
  } = useProjectJobs({ projectId, autoRefresh: internalPolling, refreshIntervalMs: 3000 });
  const internalJobs = useMemo(
    () =>
      projectJobs
        .filter((job) => job.type === "internal_video")
        .sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || ""))),
    [projectJobs],
  );
  const latestInternalJob = internalJobs[0] ?? null;
  const [codexStatus, setCodexStatus] = useState<any>(null);
  const [codexReview, setCodexReview] = useState<any>(null);
  const [codexBusy, setCodexBusy] = useState<boolean>(false);
  const [motionSequencer, setMotionSequencer] = useState<any>(null);
  const [motionSequencerBusy, setMotionSequencerBusy] = useState<boolean>(false);
  const [motionSequencerEnabled, setMotionSequencerEnabled] = useState<boolean>(true);

  // AI Auto-Render (preset-driven auto-configure + run)
  const [animationPresets, setAnimationPresets] = useState<any[]>([]);
  const [autoPreset, setAutoPreset] = useState<string>("full_motion");
  const [autoEngine, setAutoEngine] = useState<"auto" | "internal" | "comfyui">("auto");
  const [autoFps, setAutoFps] = useState<number>(24);
  const [autoSourceAsset, setAutoSourceAsset] = useState<string>("");
  const [autoMaskAssets, setAutoMaskAssets] = useState<string[]>([]);
  const [autoConfig, setAutoConfig] = useState<any>(null);
  const [autoBusy, setAutoBusy] = useState<boolean>(false);
  const [layeredBusy, setLayeredBusy] = useState<boolean>(false);

  const [info, setInfo] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [latestVideoMissing, setLatestVideoMissing] = useState<boolean>(false);
  const [revisionConflict, setRevisionConflict] = useState<ApiError | null>(null);
  const projectRevisionRef = useRef<number | null>(null);

  const latestInternalVideoPath = String(project?.meta?.last_internal_render?.video || "");
  const signedMediaPaths = Array.from(new Set(
    [
      latestInternalVideoPath,
      sourceAsset,
      stillMaskAsset,
      editorBgPath,
      ...controlnetUnits.map((unit) => unit.reference_asset),
      ...(Array.isArray(project?.meta?.exports?.deforum) ? project.meta.exports.deforum : []),
      ...(Array.isArray(project?.meta?.exports?.comfyui) ? project.meta.exports.comfyui : []),
    ]
      .map((path) => String(path || "").trim().replace(/\\/g, "/"))
      .filter(Boolean),
  ));
  const signedMediaRequests: SignedProjectMediaRequest[] = signedMediaPaths
    .map((path) => ({ purpose: "file", path }));
  const signedMedia = useSignedProjectMedia(projectId || "", signedMediaRequests, backendUrl);
  const fileUrl = (_pid: string, rel: string) => {
    const path = String(rel || "").trim().replace(/\\/g, "/");
    return path ? signedMedia.urlFor({ purpose: "file", path }) : "";
  };
  const latestInternalVideoUrl = fileUrl(projectId, latestInternalVideoPath);
  const editorBgUrl = fileUrl(projectId, editorBgPath);
  const effectiveInternalTemporalMode = internalMotionStrategy === "storyboard_full_motion" ? "video_model" : internalTemporalMode;

  useEffect(() => {
    setLatestVideoMissing(false);
  }, [latestInternalVideoUrl]);

  const comfyStillModels = useMemo(
    () => modelCatalog.filter((m) => (m.render?.render_modes || []).includes("stills") && m.kind === "checkpoint"),
    [modelCatalog]
  );
  const stillModels = useMemo(
    () => modelCatalog.filter((m) => (
      (m.render?.render_modes || []).includes("stills") &&
      (m.kind === "checkpoint" || m.kind === "diffusers" || (m.kind === "runtime_bundle" && m.render?.engine === "tensorrt_standalone"))
    )),
    [modelCatalog]
  );
  const controlnetModels = useMemo(
    () => modelCatalog.filter((m) => m.kind === "controlnet"),
    [modelCatalog]
  );
  const svdModels = useMemo(
    () => modelCatalog.filter((m) => (m.render?.render_modes || []).includes("motion_svd") || m.kind === "motion_module"),
    [modelCatalog]
  );
  const internalVideoModelOptions = useMemo(
    () => modelCatalog.filter((m) =>
      m.render?.engine === "internal_video_model" ||
      (m.render?.render_modes || []).includes("internal_video_model") ||
      m.kind === "video_diffusers" ||
      m.kind === "motion_adapter"
    ),
    [modelCatalog]
  );
  const canonicalInternalVideoModelIds = useMemo(() => {
    const resolveCanonical = (engine: ExplicitInternalVideoModelEngine) => {
      const compatible = internalVideoModelOptions.filter((model) => declaredInternalVideoModelEngine(model) === engine);
      return compatible.find((model) => model.id === CANONICAL_INTERNAL_VIDEO_MODEL_IDS[engine])?.id
        || compatible[0]?.id
        || "";
    };
    return {
      svd: resolveCanonical("svd"),
      animatediff: resolveCanonical("animatediff"),
    };
  }, [internalVideoModelOptions]);
  const filteredInternalVideoModelOptions = useMemo(
    () => internalVideoModelEngine === "auto"
      ? internalVideoModelOptions
      : internalVideoModelOptions.filter(
        (model) => declaredInternalVideoModelEngine(model) === internalVideoModelEngine,
      ),
    [internalVideoModelEngine, internalVideoModelOptions],
  );
  const selectInternalVideoModelEngine = (value: unknown) => {
    const engine = normalizeInternalVideoModelEngine(value);
    setInternalVideoModelEngine(engine);
    setInternalVideoModelId(engine === "auto" ? "" : canonicalInternalVideoModelIds[engine]);
  };
  const selectInternalVideoModel = (modelId: string) => {
    setInternalVideoModelId(modelId);
    if (!modelId) return;
    const model = internalVideoModelOptions.find((candidate) => candidate.id === modelId);
    const declaredEngine = declaredInternalVideoModelEngine(model);
    if (declaredEngine) setInternalVideoModelEngine(declaredEngine);
  };
  const supportedTensorRtInternalModels = useMemo(
    () => modelCatalog.filter((m) => m.id === "local_sd15_tensorrt_bundle" && m.render?.engine === "tensorrt_standalone"),
    [modelCatalog]
  );
  const internalModelOptions = useMemo(
    () => [
      ...modelCatalog.filter((m) => m.kind === "diffusers" && m.supports_internal_video !== false),
      ...supportedTensorRtInternalModels,
    ],
    [modelCatalog, supportedTensorRtInternalModels]
  );
  const tensorRtInternalModel = useMemo(
    () => supportedTensorRtInternalModels[0] || null,
    [supportedTensorRtInternalModels]
  );
  const tensorRtInternalVisible = !!tensorRtInternalModel;
  const tensorRtInternalInstalled = !!tensorRtInternalModel && installedModels[tensorRtInternalModel.id] !== false;
  const internalTensorRtRequired = internalRenderMode === "tensorrt" || (
    effectiveInternalTemporalMode === "video_model" && internalVideoKeyframeRenderer === "tensorrt_sd15"
  );
  const internalTensorRtBlocked = internalTensorRtRequired && !tensorRtInternalInstalled;
  const loraModels = useMemo(
    () => modelCatalog.filter((m) => m.kind === "lora" && installedModels[m.id] !== false),
    [modelCatalog, installedModels]
  );
  const selectedStillModel = useMemo(
    () => stillModels.find((m) => m.id === selectedStillModelId) || stillModels[0] || null,
    [stillModels, selectedStillModelId]
  );
  const selectedMotionModel = useMemo(
    () => comfyStillModels.find((m) => m.id === selectedMotionModelId) || comfyStillModels[0] || null,
    [comfyStillModels, selectedMotionModelId]
  );
  const selectedSvdModel = useMemo(
    () => svdModels.find((m) => m.id === selectedSvdModelId) || svdModels[0] || null,
    [svdModels, selectedSvdModelId]
  );
  const selectedStillEngine = String(selectedStillModel?.engine || selectedStillModel?.render?.engine || (selectedStillModel?.kind === "diffusers" ? "internal" : "comfyui"));
  const selectedStillFamily = String(selectedStillModel?.family || selectedStillModel?.render?.family || "").toLowerCase();
  const selectedStillIsTensorRT = selectedStillEngine === "tensorrt_standalone";
  const selectedStillInstalled = !!selectedStillModel && installedModels[selectedStillModel.id] !== false;
  const selectedStillTensorRtReady = selectedStillIsTensorRT && selectedStillInstalled;
  const selectedTrtProfileWidth = Number(selectedStillModel?.render?.profile_width || 0);
  const selectedTrtProfileHeight = Number(selectedStillModel?.render?.profile_height || 0);
  const selectedTrtMaxBatch = Number(selectedStillModel?.render?.max_batch || 1);
  const selectedTrtSupportsLivePreview = Boolean(selectedStillModel?.render?.live_preview);
  const canStillTxt2img = selectedStillModel?.supports_txt2img !== false;
  const canStillImg2img = !!selectedStillModel?.supports_img2img;
  const canStillInpaint = !!selectedStillModel?.supports_inpaint;
  const canStillOutpaint = !!selectedStillModel?.supports_outpaint;
  const canStillControlnet = !!selectedStillModel?.supports_controlnet;
  const isControlnetCompatible = (model: CatalogEntry) => {
    const controlEngine = String(model.engine || model.render?.engine || (model.kind === "controlnet" && model.source === "hf" ? "comfyui" : "comfyui")).toLowerCase();
    const controlFamily = String(model.family || model.render?.family || "").toLowerCase();
    if (installedModels[model.id] === false) return false;
    if (selectedStillEngine && controlEngine && selectedStillEngine !== controlEngine) return false;
    if (selectedStillEngine === "internal" && selectedStillFamily === "sd35") return false;
    if (selectedStillFamily && controlFamily && selectedStillFamily !== controlFamily) return false;
    return true;
  };
  const compatibleControlnetModels = useMemo(
    () => controlnetModels.filter((m) => isControlnetCompatible(m)),
    [controlnetModels, installedModels, selectedStillEngine, selectedStillFamily]
  );
  const modelFamilyLabel = (family?: string | null) => {
    const normalized = String(family || "").trim().toLowerCase();
    if (normalized === "sd15") return "SD1.5";
    if (normalized === "sdxl") return "SDXL";
    if (normalized === "sd35" || normalized === "sd3") return "SD3.5";
    return normalized ? normalized.toUpperCase() : "Unknown";
  };
  const modelEngineLabel = (engine?: string | null, kind?: string) => {
    const normalized = String(engine || "").trim().toLowerCase();
    if (normalized === "tensorrt_standalone" || kind === "runtime_bundle") return "TensorRT";
    if (normalized === "internal" || kind === "diffusers") return "Internal";
    return "ComfyUI";
  };
  const controlnetBlockedReason = useMemo(() => {
    if (!canStillControlnet) return "The selected base model does not advertise ControlNet support.";
    if (selectedStillEngine === "internal" && selectedStillFamily === "sd35") {
      return "Internal SD3.5 still models do not support ControlNet in this phase.";
    }
    if (!compatibleControlnetModels.length) {
      return "No compatible ControlNet models are currently installed for this base model and engine.";
    }
    return "";
  }, [canStillControlnet, compatibleControlnetModels, selectedStillEngine, selectedStillFamily]);
  const compatibleRefinerModels = useMemo(
    () => stillModels.filter((model) => {
      if (!model?.id || model.id === selectedStillModel?.id) return false;
      if (installedModels[model.id] === false) return false;
      const modelEngine = String(model.engine || model.render?.engine || (model.kind === "diffusers" ? "internal" : "comfyui")).toLowerCase();
      const modelFamily = String(model.family || model.render?.family || "").toLowerCase();
      if (selectedStillEngine && modelEngine !== selectedStillEngine) return false;
      if (selectedStillFamily && modelFamily && modelFamily !== selectedStillFamily) return false;
      if (selectedStillEngine === "comfyui") return model.kind === "checkpoint";
      return model.kind === "diffusers";
    }),
    [installedModels, selectedStillEngine, selectedStillFamily, selectedStillModel?.id, stillModels]
  );

  useEffect(() => {
    if (!selectedStillIsTensorRT) return;
    if (selectedTrtProfileWidth && renderWidth !== selectedTrtProfileWidth) {
      setRenderWidth(selectedTrtProfileWidth);
    }
    if (selectedTrtProfileHeight && renderHeight !== selectedTrtProfileHeight) {
      setRenderHeight(selectedTrtProfileHeight);
    }
    if (selectedTrtMaxBatch && trtBatchSize > selectedTrtMaxBatch) {
      setTrtBatchSize(selectedTrtMaxBatch);
    }
    if (!selectedTrtSupportsLivePreview && trtLivePreview) {
      setTrtLivePreview(false);
      setTrtPreviewImage(null);
    }
  }, [
    selectedStillIsTensorRT,
    selectedTrtProfileWidth,
    selectedTrtProfileHeight,
    selectedTrtMaxBatch,
    selectedTrtSupportsLivePreview,
    renderWidth,
    renderHeight,
    trtBatchSize,
    trtLivePreview,
  ]);

  useEffect(() => {
    if (selectedStillFamily !== "flux") return;
    setStillWorkflow("txt2img");
    setRenderSteps((current) => Math.max(1, Math.min(4, current || 4)));
    setRenderCfg(0);
    setHiresFix((current) => ({ ...current, enabled: false }));
    setRefiner((current) => ({ ...current, enabled: false }));
    setSelectedLoras([]);
  }, [selectedStillFamily]);

  useEffect(() => {
    if (internalModelId === "auto") return;
    if (!internalModelOptions.some((m) => m.id === internalModelId)) {
      setInternalModelId("auto");
    }
  }, [internalModelId, internalModelOptions]);

  useEffect(() => {
    if (!internalVideoModelId) return;
    const selectedModel = internalVideoModelOptions.find((model) => model.id === internalVideoModelId);
    if (!selectedModel) {
      if (!modelCatalog.length) return;
      setInternalVideoModelId("");
      return;
    }
    if (
      internalVideoModelEngine !== "auto"
      && declaredInternalVideoModelEngine(selectedModel) !== internalVideoModelEngine
    ) {
      setInternalVideoModelId(canonicalInternalVideoModelIds[internalVideoModelEngine]);
    }
  }, [
    canonicalInternalVideoModelIds,
    internalVideoModelEngine,
    internalVideoModelId,
    internalVideoModelOptions,
    modelCatalog.length,
  ]);

  const internalHostedVisible = !!renderProviders?.stability?.visible;
  const fireflyVisible = !!renderProviders?.firefly?.visible;
  const imagineartVisible = !!renderProviders?.imagineart?.visible;
  const cosmosReady = !!renderProviders?.cosmos?.active;
  const azureFoundryReady = !!renderProviders?.azure_foundry?.active;
  const internalDirectmlDetected = !!hardware?.hardware?.supports_directml;
  const internalDirectmlAvailable = !!renderProviders?.directml?.enabled && internalDirectmlDetected;
  const sourceImageOptions = useMemo(() => {
    const seen = new Set<string>();
    return [...projectOutputImages, ...projectAssets.refs].filter((entry) => {
      const path = String(entry?.path || "").trim();
      if (!path || seen.has(path)) return false;
      seen.add(path);
      return true;
    });
  }, [projectAssets.refs, projectOutputImages]);

  const buildInternalPayload = () => {
    const useTensorRt = internalRenderMode === "tensorrt";
    const tensorRtModelId = tensorRtInternalModel?.id || "local_sd15_tensorrt_bundle";
    const selectedVideoModel = internalVideoModelOptions.find((model) => model.id === internalVideoModelId);
    const payloadVideoModelId = internalVideoModelEngine === "auto"
      ? (selectedVideoModel?.id || "")
      : declaredInternalVideoModelEngine(selectedVideoModel) === internalVideoModelEngine
        ? (selectedVideoModel?.id || "")
        : canonicalInternalVideoModelIds[internalVideoModelEngine];
    return {
      variant_index: selectedVariant,
      fps_output: internalFpsOut,
      fps_render: internalFpsRender,
      width: renderWidth,
      height: renderHeight,
      steps: renderSteps,
      cfg: renderCfg,
      sampler: renderSampler,
      seed: renderSeed.trim() && Number.isFinite(Number(renderSeed)) ? Math.trunc(Number(renderSeed)) : undefined,
      negative_prompt: renderNegativePrompt,
      keyframe_interval_s: internalKeyInterval,
      interpolation_engine: internalInterp,
      temporal_mode: useTensorRt ? "keyframes" : effectiveInternalTemporalMode,
      temporal_strength: internalTemporalStrength,
      temporal_steps: internalTemporalSteps,
      refine_every_n_frames: internalRefineEvery,
      anchor_strength: internalAnchorStrength,
      prompt_blend: internalPromptBlend,
      motion_strategy: useTensorRt ? "manual" : internalMotionStrategy,
      storyboard_shot_max_s: internalStoryboardShotMax,
      video_model_engine: internalVideoModelEngine,
      video_model_id: payloadVideoModelId || undefined,
      video_model_max_frames_per_scene: internalVideoMaxFrames,
      video_model_motion_bucket_id: internalVideoMotionBucket,
      video_model_noise_aug_strength: internalVideoNoiseAug,
      video_model_decode_chunk_size: internalVideoDecodeChunk,
      video_model_dtype: internalVideoDtype,
      video_model_cpu_offload: internalVideoCpuOffload,
      video_model_motion_score_mode: internalVideoMotionScoreMode,
      video_model_manual_motion_score: internalVideoManualMotionScore,
      video_model_anchor_mode: internalVideoAnchorMode,
      video_model_prompt_refine: internalVideoPromptRefine,
      video_model_scene_motion: internalVideoSceneMotion,
      video_model_apply_timeline_camera: internalVideoApplyTimelineCamera,
      video_model_keyframe_renderer: internalVideoKeyframeRenderer,
      video_model_keyframe_model_id: internalVideoKeyframeRenderer === "tensorrt_sd15"
        ? (tensorRtInternalModel?.id || "local_sd15_tensorrt_bundle")
        : undefined,
      keyframe_continuity_mode: keyframeContinuityMode,
      parseq_enabled: motionSequencerEnabled,
      model_id: useTensorRt ? tensorRtModelId : internalModelId,
      render_mode: internalRenderMode,
      render_tier: internalRenderTier,
      device_preference: useTensorRt ? "cuda" : internalDevicePreference,
      allow_hosted_fallback: internalRenderMode === "diffusion" ? false : internalAllowHostedFallback,
      resume_existing_frames: useTensorRt ? false : internalResumeExisting,
      source_asset: sourceAsset || undefined,
      source_strength: sourceAsset ? denoiseStrength : undefined,
    };
  };

  const parsedRenderSeed = useMemo(() => {
    const trimmed = renderSeed.trim();
    if (!trimmed) return undefined;
    const value = Number(trimmed);
    return Number.isFinite(value) ? Math.trunc(value) : undefined;
  }, [renderSeed]);

  const buildDiffusionPayload = () => {
    const isFluxSchnell = selectedStillFamily === "flux";
    return {
      width: renderWidth,
      height: renderHeight,
      steps: isFluxSchnell ? Math.max(1, Math.min(4, renderSteps || 4)) : renderSteps,
      cfg: isFluxSchnell ? 0 : renderCfg,
      sampler: renderSampler,
      negative_prompt: isFluxSchnell ? "" : renderNegativePrompt,
      seed: parsedRenderSeed,
      loras: isFluxSchnell ? [] : selectedLoras.map((item) => ({ name: item.name, weight: item.weight })),
    };
  };

  const buildStillWorkflowPayload = () => {
    const upscaler = hiresFix.upscaler || "latent_bislerp";
    const payload: Record<string, any> = {
      workflow_family: selectedStillFamily === "flux" ? "txt2img" : stillWorkflow,
      ...buildDiffusionPayload(),
    };
    if (stillWorkflow === "img2img" || stillWorkflow === "inpaint" || stillWorkflow === "outpaint") {
      payload.source_asset = sourceAsset || undefined;
      payload.denoise_strength = denoiseStrength;
    }
    if (stillWorkflow === "inpaint") {
      payload.inpaint_mask = stillMaskAsset || undefined;
    }
    if (stillWorkflow === "outpaint") {
      if (stillMaskAsset) payload.inpaint_mask = stillMaskAsset;
      payload.outpaint = {
        top_px: Math.max(0, Math.trunc(outpaint.top_px || 0)),
        right_px: Math.max(0, Math.trunc(outpaint.right_px || 0)),
        bottom_px: Math.max(0, Math.trunc(outpaint.bottom_px || 0)),
        left_px: Math.max(0, Math.trunc(outpaint.left_px || 0)),
      };
    }
    if (stillWorkflow === "controlnet") {
      payload.controlnet_units = controlnetUnits
        .filter((unit) => unit.model && unit.reference_asset)
        .map((unit) => ({
          model: unit.model,
          reference_asset: unit.reference_asset,
          conditioning_mode: unit.conditioning_mode,
          strength: unit.strength,
          start_percent: unit.start_percent,
          end_percent: unit.end_percent,
        }));
    }
    if (selectedStillFamily !== "flux" && hiresFix.enabled) {
      payload.hires_fix = {
        enabled: true,
        scale: Math.max(1, Number(hiresFix.scale || 1.5)),
        denoise: Math.max(0, Math.min(1, Number(hiresFix.denoise || 0.35))),
        upscaler,
        ...(Number(hiresFix.steps) > 0 ? { steps: Math.trunc(Number(hiresFix.steps)) } : {}),
      };
      payload.upscaler = upscaler;
    }
    if (selectedStillFamily !== "flux" && refiner.enabled) {
      payload.refiner = {
        switch_at: Math.max(0, Math.min(1, Number(refiner.switch_at || 0.8))),
        ...(refiner.model ? { model: refiner.model } : {}),
        ...(Number(refiner.steps) > 0 ? { steps: Math.trunc(Number(refiner.steps)) } : {}),
      };
    }
    return payload;
  };

  useEffect(() => {
    const preferred = String(hardware?.hardware?.device_preference || "auto");
    if (preferred && internalDevicePreference === "auto" && preferred !== "auto") {
      if (preferred === "directml" && !internalDirectmlAvailable) return;
      if (preferred === "directml" || preferred === "cuda" || preferred === "mps" || preferred === "cpu") {
        setInternalDevicePreference(preferred as any);
      }
    }
  }, [hardware, internalDevicePreference, internalDirectmlAvailable]);

  useEffect(() => {
    if (!internalHostedVisible && internalRenderMode === "hosted") {
      setInternalRenderMode("auto");
    }
  }, [internalHostedVisible, internalRenderMode]);

  useEffect(() => {
    if (!internalDirectmlAvailable && internalDevicePreference === "directml") {
      setInternalDevicePreference("auto");
    }
  }, [internalDirectmlAvailable, internalDevicePreference]);

  const refreshReferenceAssets = async (id: string) => {
    if (!id) return;
    const [assetData, outputData] = await Promise.all([
      apiGet(`/v1/projects/${id}/assets`).catch(() => null),
      apiGet(`/v1/projects/${id}/outputs`).catch(() => null),
    ]);
    setProjectAssets({ refs: Array.isArray(assetData?.assets?.refs) ? assetData.assets.refs : [] });
    setProjectOutputImages(
      (Array.isArray(outputData?.images) ? outputData.images : [])
        .map((entry: any) => ({ path: String(entry?.path || entry || "").trim() }))
        .filter((entry: { path: string }) => Boolean(entry.path)),
    );
  };

  const refreshProjects = async () => {
    const d = await apiGet("/v1/projects");
    const ps = d.projects || [];
    setProjects(ps);
    const nextProjectId = resolveProjectId(ps, projectId);
    if (nextProjectId !== projectId) setProjectId(nextProjectId);
  };

  const refreshProject = async (id: string) => {
    if (!id) return;
    const d = await apiGet(`/v1/projects/${id}`);
    setProject(d.project);
    projectRevisionRef.current = projectRevision(d.project);
    setRevisionConflict(null);
    setVisualDnaHints(d.visual_dna_hints || null);
    setAnalysis(d.project?.meta?.analysis || null);
    setPlan(d.project?.meta?.last_plan || null);
    setTimeline(d.project?.meta?.timeline || { layers: [], camera: { keyframes: [] } });
    setTimelineDirty(false);
    const storedPlan = d.project?.meta?.last_conductor_plan;
    if (storedPlan && typeof storedPlan === "object") {
      setConductorPlan(storedPlan);
    }
  };

  const recordMutationResponse = (response: any) => {
    const revision = projectRevisionFromResponse(response);
    if (revision != null) {
      projectRevisionRef.current = revision;
      setProject((current: any) => current && typeof current === "object"
        ? { ...current, revision }
        : current);
    }
    return response;
  };

  const postProjectMutation = async (
    path: string,
    body: Record<string, unknown>,
    options: ApiRequestOptions = {},
  ) => {
    try {
      const response = await apiPost(
        path,
        expectedRevisionBody(body, { revision: projectRevisionRef.current }),
        options,
      );
      setRevisionConflict(null);
      return recordMutationResponse(response);
    } catch (error) {
      const conflict = revisionConflictFrom(error);
      if (conflict) setRevisionConflict(conflict);
      throw error;
    }
  };

  const uploadProjectAsset = async (path: string, file: File) => {
    try {
      const response = await apiUpload(path, file, {
        expectedRevision: projectRevisionRef.current ?? undefined,
      });
      setRevisionConflict(null);
      return recordMutationResponse(response);
    } catch (error) {
      const conflict = revisionConflictFrom(error);
      if (conflict) setRevisionConflict(conflict);
      throw error;
    }
  };

  const refreshValidate = async () => {
    if (!projectId) return;
    try {
      const d = await apiGet(`/v1/projects/${projectId}/pipeline/validate?variant_index=${selectedVariant}&preset=${renderPreset}`);
      setValidate(d);
    } catch {
      setValidate(null);
    }
  };

  const refreshInternalPreflight = async () => {
    if (!projectId) return;
    try {
      const d = await apiPost(`/v1/projects/${projectId}/render/internal/preflight`, buildInternalPayload());
      setInternalPreflight(d);
    } catch (e: any) {
      setInternalPreflight({ ok: false, error: String(e) });
    }
  };

  const refreshMotionSequencer = async () => {
    if (!projectId) return;
    try {
      const d = await apiGet(`/v1/projects/${projectId}/render/motion_sequencer?variant_index=${selectedVariant}&fps=${internalFpsOut || 24}`);
      setMotionSequencer(d);
    } catch {
      setMotionSequencer(null);
    }
  };

  const refreshStoredConductorPlan = async () => {
    if (!projectId) return false;
    try {
      const d = await apiGet(`/v1/projects/${projectId}/render/conductor/plan?variant_index=${selectedVariant}`);
      if (d?.plan) {
        setConductorPlan(d.plan);
        return true;
      }
    } catch {
      /* stored plan optional */
    }
    return false;
  };

  const refreshConductorPlan = async () => {
    if (!projectId || !(plan?.variants?.length || 0)) {
      setConductorPlan(null);
      setConductorEnvironment(null);
      setContinuityReport(null);
      return;
    }
    try {
      const d = await postProjectMutation(`/v1/projects/${projectId}/render/conductor/plan`, {
        ...orchestratorIntent,
        variant_index: selectedVariant,
        preset: renderPreset,
        allowed_engines: orchestratorIntent.allowed_engines.length
          ? orchestratorIntent.allowed_engines
          : REAL_CONDUCTOR_ENGINES,
      });
      setConductorPlan(d?.plan || null);
      setConductorEnvironment(d?.environment || null);
      if (d?.visual_dna_hints) setVisualDnaHints(d.visual_dna_hints);
      try {
        const continuity = await apiGet(`/v1/projects/${projectId}/render/conductor/continuity?variant_index=${selectedVariant}`);
        setContinuityReport(continuity?.continuity || null);
      } catch {
        setContinuityReport(null);
      }
    } catch {
      const loaded = await refreshStoredConductorPlan();
      if (!loaded) {
        setConductorPlan(null);
        setConductorEnvironment(null);
        setContinuityReport(null);
      }
    }
  };

  const refreshStoredPerformerPlan = async () => {
    if (!projectId) return false;
    try {
      const d = await apiGet(`/v1/projects/${projectId}/render/performer/plan?variant_index=${selectedVariant}`);
      if (d?.performer_plan) {
        setPerformerPlan(d.performer_plan);
        return true;
      }
    } catch {
      /* optional */
    }
    return false;
  };

  const refreshPerformerPlan = async () => {
    if (!projectId || !(plan?.variants?.length || 0)) {
      setPerformerPlan(null);
      setPerformerStatus("");
      return;
    }
    setPlanningPerformer(true);
    setPerformerStatus("");
    try {
      const d = await postProjectMutation(`/v1/projects/${projectId}/render/performer/plan`, {
        variant_index: selectedVariant,
      });
      setPerformerPlan(d?.performer_plan || null);
      setPerformerStatus(d?.performer_plan?.summary || "Performer lane plan ready.");
    } catch (e: any) {
      const loaded = await refreshStoredPerformerPlan();
      if (!loaded) {
        setPerformerPlan(null);
        setPerformerStatus(String(e));
      }
    } finally {
      setPlanningPerformer(false);
    }
  };

  const runPerformerWorkflow = async () => {
    if (!projectId || !performerPlan) return;
    setRunningPerformer(true);
    setPerformerStatus("");
    setErr(null);
    try {
      const d = await postProjectMutation(`/v1/projects/${projectId}/render/performer/run`, {
        variant_index: selectedVariant,
        plan_id: performerPlan.plan_id,
        provider: "auto",
        render_settings: buildInternalPayload(),
      });
      setPerformerStatus(d?.message || `Performer job ${d?.job?.id || ""} queued.`);
      await refreshProject(projectId);
      await refreshProjectJobs();
    } catch (e: any) {
      setPerformerStatus(String(e));
      setErr(String(e));
    } finally {
      setRunningPerformer(false);
    }
  };


  useEffect(() => {
    refreshProjects().catch(() => {});
  }, []);

  useEffect(() => {
    apiGet("/v1/comfyui/capabilities").then(setCaps).catch(() => {});
    apiGet("/v1/hardware").then((d) => setHardware(d)).catch(() => {});
    apiGet("/v1/settings/render_providers").then(setRenderProviders).catch(() => {});
    apiGet("/v1/render/route").then(setVideoRoute).catch(() => {});
    apiGet("/v1/models/catalog").then((d) => {
      const built = Array.isArray(d?.catalog) ? d.catalog : [];
      const user = Array.isArray(d?.user) ? d.user : [];
      setModelCatalog([...(built as CatalogEntry[]), ...(user as CatalogEntry[])]);
      setInstalledModels(d?.installed && typeof d.installed === "object" ? d.installed : {});
    }).catch(() => {});
  }, [backendUrl]);

  useEffect(() => {
    writeRenderDefaults({
      ...savedRenderDefaults,
      stillWidth: renderWidth,
      stillHeight: renderHeight,
      stillSteps: renderSteps,
      stillCfg: renderCfg,
      stillSampler: renderSampler,
      stillNegativePrompt: renderNegativePrompt,
      stillSeed: renderSeed,
      stillUpscaler: hiresFix.upscaler,
      hiresFixEnabled: hiresFix.enabled,
      hiresFixScale: hiresFix.scale,
      hiresFixSteps: hiresFix.steps,
      hiresFixDenoise: hiresFix.denoise,
      refinerEnabled: refiner.enabled,
      refinerModel: refiner.model,
      refinerSwitchAt: refiner.switch_at,
      refinerSteps: refiner.steps,
      internalMotionStrategy,
      internalStoryboardShotMaxS: internalStoryboardShotMax,
      keyframeContinuityMode,
      internalVideoApplyTimelineCamera,
    });
  }, [renderWidth, renderHeight, renderSteps, renderCfg, renderSampler, renderNegativePrompt, renderSeed, hiresFix, refiner, internalMotionStrategy, internalStoryboardShotMax, keyframeContinuityMode, internalVideoApplyTimelineCamera]);

  useEffect(() => {
    if (!loraToAdd && loraModels.length) setLoraToAdd(loraModels[0].id);
  }, [loraModels, loraToAdd]);

  useEffect(() => {
    projectRevisionRef.current = null;
    setRevisionConflict(null);
    if (projectId) {
      refreshProject(projectId).catch(() => {});
      refreshReferenceAssets(projectId).catch(() => {});
    }
  }, [backendUrl, projectId]);

  useEffect(() => {
    refreshMotionSequencer().catch(() => {});
  }, [projectId, selectedVariant, internalFpsOut, plan]);

  useEffect(() => {
    refreshValidate().catch(() => {});
  }, [projectId, selectedVariant, renderPreset]);

  useEffect(() => {
    const qualityTier: RenderOrchestratorIntentValue["quality_tier"] = renderPreset === "fast"
      ? "draft"
      : renderPreset;
    setOrchestratorIntent((current) => ({
      ...current,
      variant_index: selectedVariant,
      preset: renderPreset,
      quality_tier: qualityTier,
    }));
  }, [renderPreset, selectedVariant]);

  useEffect(() => {
    const aspectRatio = renderAspectRatioForSize(renderWidth, renderHeight);
    setOrchestratorIntent((current) => current.aspect_ratio === aspectRatio
      ? current
      : { ...current, aspect_ratio: aspectRatio });
  }, [renderHeight, renderWidth]);

  useEffect(() => {
    refreshConductorPlan().catch(() => {});
    refreshStoredPerformerPlan().catch(() => {});
  }, [plan, projectId, renderPreset, selectedVariant]);

  useEffect(() => {
    if (selectedStillModelId || !stillModels.length) return;
    setSelectedStillModelId(stillModels[0].id);
  }, [stillModels, selectedStillModelId]);

  useEffect(() => {
    if (selectedMotionModelId || !comfyStillModels.length) return;
    setSelectedMotionModelId(comfyStillModels[0].id);
  }, [comfyStillModels, selectedMotionModelId]);

  useEffect(() => {
    if (selectedSvdModelId || !svdModels.length) return;
    setSelectedSvdModelId(svdModels[0].id);
  }, [selectedSvdModelId, svdModels]);

  useEffect(() => {
    const supportedWorkflows = [
      canStillTxt2img ? "txt2img" : null,
      canStillImg2img ? "img2img" : null,
      canStillInpaint ? "inpaint" : null,
      canStillOutpaint ? "outpaint" : null,
      canStillControlnet ? "controlnet" : null,
    ].filter(Boolean) as Array<"txt2img" | "img2img" | "inpaint" | "outpaint" | "controlnet">;
    if (supportedWorkflows.length && !supportedWorkflows.includes(stillWorkflow)) {
      setStillWorkflow(supportedWorkflows[0]);
    }
  }, [stillWorkflow, canStillTxt2img, canStillImg2img, canStillInpaint, canStillOutpaint, canStillControlnet]);

  useEffect(() => {
    setControlnetUnits((current) => current.map((unit) => {
      if (isControlnetCompatible(controlnetModels.find((model) => model.id === unit.model) || { id: "", name: "", kind: "controlnet" } as CatalogEntry)) {
        return unit;
      }
      const fallback = compatibleControlnetModels[0];
      return {
        ...unit,
        model: fallback?.id || "",
        conditioning_mode: (fallback?.render?.conditioning_mode as ConditioningMode) || unit.conditioning_mode || "raw",
      };
    }));
  }, [compatibleControlnetModels, controlnetModels, selectedStillEngine, selectedStillFamily]);

  useEffect(() => {
    if (
      !trtLivePreview ||
      !selectedTrtSupportsLivePreview ||
      !selectedStillInstalled ||
      !projectId ||
      !selectedStillModelId ||
      !stillModels.some((m) => m.id === selectedStillModelId && m.render?.engine === "tensorrt_standalone")
    ) {
      setTrtPreviewImage(null);
      return;
    }
    
    setTrtPreviewLoading(true);
    const debounceTimer = setTimeout(async () => {
      try {
        const d = await postProjectMutation(`/v1/projects/${projectId}/render/tensorrt-standalone/preview`, {
          variant_index: selectedVariant,
          model_id: selectedStillModelId,
          width: Number(renderWidth) || 1024,
          height: Number(renderHeight) || 1024,
          steps: 8,
          cfg: renderCfg,
          seed: renderSeed ? Number(renderSeed) : undefined,
        });
        if (d && d.image) {
          setTrtPreviewImage(d.image);
        }
      } catch (e) {
        console.error("TRT Preview error", e);
      } finally {
        setTrtPreviewLoading(false);
      }
    }, 500); // 500ms debounce

    return () => clearTimeout(debounceTimer);
  }, [
    trtLivePreview, projectId, selectedVariant, selectedStillModelId,
    renderWidth, renderHeight, renderCfg, renderSeed, stillModels, selectedStillInstalled, selectedTrtSupportsLivePreview
  ]);
  useEffect(() => {
    if (!refiner.enabled || !refiner.model) return;
    const stillValid = compatibleRefinerModels.some((model) => model.id === refiner.model);
    if (!stillValid) {
      setRefiner((current) => ({ ...current, model: "" }));
    }
  }, [compatibleRefinerModels, refiner.enabled, refiner.model]);

  useEffect(() => {
    refreshInternalPreflight().catch(() => {});
  }, [
    projectId,
    selectedVariant,
    internalFpsOut,
    internalFpsRender,
    internalKeyInterval,
    internalInterp,
    internalModelId,
    internalRenderMode,
    internalRenderTier,
    internalDevicePreference,
    internalTemporalMode,
    internalTemporalStrength,
    internalTemporalSteps,
    internalRefineEvery,
    internalAnchorStrength,
    internalPromptBlend,
    internalResumeExisting,
    internalMotionStrategy,
    internalStoryboardShotMax,
    internalVideoModelEngine,
    internalVideoModelId,
    internalVideoMaxFrames,
    internalVideoMotionBucket,
    internalVideoNoiseAug,
    internalVideoDecodeChunk,
    internalVideoDtype,
    internalVideoCpuOffload,
    internalVideoMotionScoreMode,
    internalVideoManualMotionScore,
    internalVideoAnchorMode,
    internalVideoPromptRefine,
    internalVideoSceneMotion,
    internalVideoApplyTimelineCamera,
    internalVideoKeyframeRenderer,
    keyframeContinuityMode,
    motionSequencerEnabled,
    tensorRtInternalModel,
  ]);


  const runPipeline = async () => {
    setErr(null);
    setInfo(null);
    try {
      const d = await postProjectMutation(`/v1/projects/${projectId}/pipeline/run?variant_index=${selectedVariant}&preset=${renderPreset}&mode=auto`, {});
      setInfo(d);
      await refreshProject(projectId);
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const runInternalVideo = async () => {
    setErr(null);
    setInfo(null);
    if (internalTensorRtBlocked) {
      setErr("Install and verify the Local SD1.5 TensorRT Bundle in Models before starting this render.");
      return;
    }
    try {
      const d = await postProjectMutation(`/v1/projects/${projectId}/render/internal/video`, buildInternalPayload());
      setInfo(d);
      await refreshProject(projectId);
      await refreshProjectJobs();
      await refreshInternalPreflight();
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const runCodexRenderReview = async () => {
    if (!projectId) return;
    setErr(null);
    setCodexReview(null);
    setCodexBusy(true);
    try {
      const d = await postProjectMutation(`/v1/projects/${projectId}/codex/render-review`, {
        variant_index: selectedVariant,
      });
      setCodexReview(d);
    } catch (e: any) {
      setCodexReview({ ok: false, error: String(e) });
    } finally {
      setCodexBusy(false);
    }
  };

  const applyGeneratedMotionSequencer = async () => {
    if (!projectId) return;
    setErr(null);
    setMotionSequencerBusy(true);
    try {
      const d = await postProjectMutation(`/v1/projects/${projectId}/render/motion_sequencer/apply`, {
        variant_index: selectedVariant,
        fps: internalFpsOut || 24,
        activate: true,
      });
      setMotionSequencer({
        ...(motionSequencer || {}),
        active: d.manifest,
        summary: d.summary,
        overrides: d.overrides,
        recipe_graph: d.recipe_graph,
      });
      setMotionSequencerEnabled(true);
      setInfo(d);
      await refreshProject(projectId);
      await refreshInternalPreflight();
    } catch (e: any) {
      setErr(String(e));
    } finally {
      setMotionSequencerBusy(false);
    }
  };

  useEffect(() => {
    apiGet("/v1/render/animation_presets")
      .then((d) => setAnimationPresets(Array.isArray(d?.presets) ? d.presets : []))
      .catch(() => setAnimationPresets([]));
  }, []);

  useEffect(() => {
    apiGet("/v1/codex/status")
      .then((d) => setCodexStatus(d))
      .catch(() => setCodexStatus(null));
  }, []);

  const selectedAutoPreset = useMemo(
    () => animationPresets.find((p) => p.id === autoPreset) || null,
    [animationPresets, autoPreset],
  );
  const autoNeedsSource = Boolean(
    selectedAutoPreset?.uses_source_image || selectedAutoPreset?.animates_objects,
  );
  const autoNeedsMasks = Boolean(selectedAutoPreset?.requires_masks);
  const autoRunDisabled =
    !(plan?.variants?.length || 0) ||
    autoBusy ||
    (autoNeedsSource && !autoSourceAsset) ||
    (autoNeedsMasks && autoMaskAssets.length === 0);

  const previewAuto = async () => {
    if (!projectId) return;
    setErr(null);
    setInfo(null);
    setAutoBusy(true);
    try {
      const d = await postProjectMutation(`/v1/projects/${projectId}/render/auto`, {
        preset: autoPreset,
        engine: autoEngine,
        variant_index: selectedVariant,
        source_asset: autoSourceAsset || null,
        run: false,
        fps: autoFps,
      });
      setAutoConfig(d);
      setInfo(d);
    } catch (e: any) {
      setErr(String(e));
    } finally {
      setAutoBusy(false);
    }
  };

  const runAuto = async () => {
    if (!projectId) return;
    setErr(null);
    setInfo(null);
    setAutoBusy(true);
    try {
      let d: any;
      if (autoNeedsMasks) {
        // Masked / regional object presets need explicit masks -> layered endpoint.
        d = await postProjectMutation(`/v1/projects/${projectId}/render/animate_layers`, {
          source_asset: autoSourceAsset,
          mode: "masked",
          motion: selectedAutoPreset?.motion || "full_3d",
          masks: autoMaskAssets.map((m) => ({ mask_asset: m })),
          fps: autoFps,
        });
      } else {
        d = await postProjectMutation(`/v1/projects/${projectId}/render/auto`, {
          preset: autoPreset,
          engine: autoEngine,
          variant_index: selectedVariant,
          source_asset: autoSourceAsset || null,
          run: true,
          fps: autoFps,
        });
      }
      setAutoConfig(d);
      setInfo(d);
      await refreshProject(projectId);
      await refreshProjectJobs();
    } catch (e: any) {
      setErr(String(e));
    } finally {
      setAutoBusy(false);
    }
  };

  const queueLayeredAnimation = async (payload: LayeredAnimationPayload) => {
    if (!projectId) return;
    setErr(null);
    setInfo(null);
    setLayeredBusy(true);
    try {
      const result = await postProjectMutation(`/v1/projects/${projectId}/render/animate_layers`, payload);
      setInfo(result);
      await refreshProject(projectId);
      await refreshProjectJobs();
    } catch (error: any) {
      setErr(String(error));
    } finally {
      setLayeredBusy(false);
    }
  };

  const handleInternalJobAction = async (job: StudioJob, action: Parameters<typeof runInternalJobAction>[1]) => {
    setErr(null);
    try {
      await runInternalJobAction(job, action);
      await refreshInternalPreflight();
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const handleResumeInternalFromCheckpoint = async (job: StudioJob) => {
    setErr(null);
    try {
      await resumeInternalFromCheckpoint(job);
      await refreshInternalPreflight();
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const handleRestartInternalClean = async (job: StudioJob) => {
    setErr(null);
    try {
      await restartInternalClean(job);
      await refreshInternalPreflight();
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const clearLatestInternalCachedFrames = async () => {
    if (!projectId || !latestInternalJob?.id) return;
    setErr(null);
    try {
      const d = await postProjectMutation(`/v1/projects/${projectId}/jobs/${latestInternalJob.id}/clear_cached_frames`, {});
      setInfo(d);
      await refreshProject(projectId);
      await refreshProjectJobs();
      await refreshInternalPreflight();
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const dropLatestInternalCheckpoint = async () => {
    if (!projectId || !latestInternalJob?.id) return;
    setErr(null);
    try {
      const d = await postProjectMutation(`/v1/projects/${projectId}/jobs/${latestInternalJob.id}/drop_checkpoint`, {});
      setInfo(d);
      await refreshProject(projectId);
      await refreshProjectJobs();
      await refreshInternalPreflight();
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const openLocalPath = async (label: string, value?: string | null) => {
    if (!value) return;
    setErr(null);
    try {
      const result = await runDesktopArtifactAction(label, value, "open");
      if (!result.ok) throw new Error(result.error || `Unable to open ${label}`);
      setInfo({ ...result, label, value });
    } catch (e: any) {
      setErr(`Failed to open ${label}: ${String(e)}`);
    }
  };

  const applyLatestInternalSettings = () => {
    const jobPayload = latestInternalJob && "payload" in latestInternalJob
      ? (latestInternalJob as StudioJob & { payload?: Record<string, unknown> }).payload
      : null;
    const p = jobPayload || project?.meta?.last_internal_render || null;
    if (!p) return;
    if (p.variant_index != null) setSelectedVariant(Number(p.variant_index));
    if (p.fps_output != null) setInternalFpsOut(Number(p.fps_output));
    if (p.fps_render != null) setInternalFpsRender(Number(p.fps_render));
    if (p.keyframe_interval_s != null) setInternalKeyInterval(Number(p.keyframe_interval_s));
    if (p.interpolation_engine) setInternalInterp(String(p.interpolation_engine) as any);
    if (p.model_id) setInternalModelId(String(p.model_id));
    if (p.render_tier) setInternalRenderTier(String(p.render_tier) as any);
    if (p.temporal_mode) setInternalTemporalMode(String(p.temporal_mode) as any);
    if (p.temporal_strength != null) setInternalTemporalStrength(Number(p.temporal_strength));
    if (p.temporal_steps != null) setInternalTemporalSteps(Number(p.temporal_steps));
    if (p.refine_every_n_frames != null) setInternalRefineEvery(Number(p.refine_every_n_frames));
    if (p.anchor_strength != null) setInternalAnchorStrength(Number(p.anchor_strength));
    if (p.prompt_blend != null) setInternalPromptBlend(Boolean(p.prompt_blend));
    if (p.resume_existing_frames != null) setInternalResumeExisting(Boolean(p.resume_existing_frames));
    if (p.motion_strategy) setInternalMotionStrategy(String(p.motion_strategy) as any);
    if (p.storyboard_shot_max_s != null) setInternalStoryboardShotMax(Number(p.storyboard_shot_max_s));
    const hasRestoredVideoEngine = p.video_model_engine != null;
    const hasRestoredVideoModel = p.video_model_id != null;
    const restoredVideoEngine = normalizeInternalVideoModelEngine(p.video_model_engine);
    const restoredVideoModelId = hasRestoredVideoModel ? String(p.video_model_id || "") : "";
    const restoredVideoModel = internalVideoModelOptions.find((model) => model.id === restoredVideoModelId);
    const restoredModelEngine = declaredInternalVideoModelEngine(restoredVideoModel);
    if (hasRestoredVideoEngine) {
      setInternalVideoModelEngine(restoredVideoEngine);
      if (restoredVideoEngine === "auto") {
        setInternalVideoModelId(restoredVideoModel?.id || "");
      } else {
        setInternalVideoModelId(
          restoredModelEngine === restoredVideoEngine
            ? (restoredVideoModel?.id || "")
            : canonicalInternalVideoModelIds[restoredVideoEngine],
        );
      }
    } else if (hasRestoredVideoModel) {
      setInternalVideoModelId(restoredVideoModel?.id || "");
      if (restoredModelEngine) setInternalVideoModelEngine(restoredModelEngine);
    }
    if (p.video_model_max_frames_per_scene != null) setInternalVideoMaxFrames(Number(p.video_model_max_frames_per_scene));
    if (p.video_model_motion_bucket_id != null) setInternalVideoMotionBucket(Number(p.video_model_motion_bucket_id));
    if (p.video_model_noise_aug_strength != null) setInternalVideoNoiseAug(Number(p.video_model_noise_aug_strength));
    if (p.video_model_decode_chunk_size != null) setInternalVideoDecodeChunk(Number(p.video_model_decode_chunk_size));
    if (p.video_model_dtype) setInternalVideoDtype(String(p.video_model_dtype) as any);
    if (p.video_model_cpu_offload != null) setInternalVideoCpuOffload(Boolean(p.video_model_cpu_offload));
    if (p.video_model_motion_score_mode) setInternalVideoMotionScoreMode(String(p.video_model_motion_score_mode) as any);
    if (p.video_model_manual_motion_score != null) setInternalVideoManualMotionScore(Number(p.video_model_manual_motion_score));
    if (p.video_model_anchor_mode) setInternalVideoAnchorMode(String(p.video_model_anchor_mode) as any);
    if (p.video_model_prompt_refine != null) setInternalVideoPromptRefine(Boolean(p.video_model_prompt_refine));
    if (p.video_model_scene_motion) setInternalVideoSceneMotion(String(p.video_model_scene_motion) as any);
    if (p.video_model_apply_timeline_camera != null) setInternalVideoApplyTimelineCamera(Boolean(p.video_model_apply_timeline_camera));
    if (p.video_model_keyframe_renderer) setInternalVideoKeyframeRenderer(String(p.video_model_keyframe_renderer) as any);
    if (p.keyframe_continuity_mode) setKeyframeContinuityMode(normalizeKeyframeContinuityMode(p.keyframe_continuity_mode));
  };

  const addSelectedLora = () => {
    if (!loraToAdd) return;
    const model = loraModels.find((item) => item.id === loraToAdd);
    if (!model) return;
    setSelectedLoras((current) => {
      if (current.some((item) => item.name === model.id)) return current;
      return [...current, { name: model.id, label: model.name, weight: 1.0 }];
    });
  };

  const removeSelectedLora = (name: string) => {
    setSelectedLoras((current) => current.filter((item) => item.name !== name));
  };

  const addControlnetUnit = () => {
    const fallbackModel = compatibleControlnetModels[0];
    setControlnetUnits((current) => [
      ...current,
      {
        key: `${Date.now()}_${current.length}`,
        model: fallbackModel?.id || "",
        reference_asset: "",
        conditioning_mode: (fallbackModel?.render?.conditioning_mode as ConditioningMode) || "raw",
        strength: 0.8,
        start_percent: 0,
        end_percent: 1,
      },
    ]);
  };

  const updateControlnetUnit = (key: string, patch: Partial<ControlNetUnitDraft>) => {
    setControlnetUnits((current) => current.map((unit) => (unit.key === key ? { ...unit, ...patch } : unit)));
  };

  const duplicateControlnetUnit = (key: string) => {
    setControlnetUnits((current) => {
      const index = current.findIndex((unit) => unit.key === key);
      if (index < 0) return current;
      const next = [...current];
      const unit = current[index];
      next.splice(index + 1, 0, { ...unit, key: `${Date.now()}_${index}_dup` });
      return next;
    });
  };

  const moveControlnetUnit = (key: string, direction: -1 | 1) => {
    setControlnetUnits((current) => {
      const index = current.findIndex((unit) => unit.key === key);
      const targetIndex = index + direction;
      if (index < 0 || targetIndex < 0 || targetIndex >= current.length) return current;
      const next = [...current];
      const [unit] = next.splice(index, 1);
      next.splice(targetIndex, 0, unit);
      return next;
    });
  };

  const removeControlnetUnit = (key: string) => {
    setControlnetUnits((current) => current.filter((unit) => unit.key !== key));
  };

  const renderScenes = async () => {
    setErr(null);
    setInfo(null);
    try {
      const d = await postProjectMutation(`/v1/projects/${projectId}/render/stills/scenes`, {
        variant_index: selectedVariant,
        model_id: selectedStillModel?.id || undefined,
        checkpoint: checkpointName || undefined,
        ...buildStillWorkflowPayload(),
      });
      setInfo(d);
      await refreshProject(projectId);
      await refreshReferenceAssets(projectId);
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const renderFireflyScenes = async () => {
    setErr(null);
    setInfo(null);
    try {
      const d = await postProjectMutation(`/v1/projects/${projectId}/render/firefly/scenes`, {
        variant_index: selectedVariant,
        model_id: renderProviders?.firefly?.custom_model_id || undefined,
        width: renderWidth || undefined,
        height: renderHeight || undefined,
        seed: renderSeed ? Number(renderSeed) : undefined,
      });
      setInfo(d);
      await refreshProject(projectId);
      await refreshReferenceAssets(projectId);
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const renderImagineartScenes = async () => {
    setErr(null);
    setInfo(null);
    try {
      const d = await postProjectMutation(`/v1/projects/${projectId}/render/imagineart/scenes`, {
        variant_index: selectedVariant,
        width: renderWidth || undefined,
        height: renderHeight || undefined,
        seed: renderSeed ? Number(renderSeed) : undefined,
      });
      setInfo(d);
      await refreshProject(projectId);
      await refreshReferenceAssets(projectId);
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const renderImagineartVideo = async (sceneIndex?: number, useKeyframe = false) => {
    setErr(null);
    setInfo(null);
    try {
      const d = await postProjectMutation(`/v1/projects/${projectId}/render/imagineart/video`, {
        variant_index: selectedVariant,
        scene_index: sceneIndex,
        use_keyframe: useKeyframe,
      });
      setInfo(d);
      await refreshProject(projectId);
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const renderMotion = async () => {
    setErr(null);
    setInfo(null);
    try {
      const engine = renderMode === "motion_svd" ? "svd" : "animatediff";
      const d = await postProjectMutation(`/v1/projects/${projectId}/render/comfyui/motion_scenes`, {
        model_id: selectedMotionModel?.id || undefined,
        svd_model_id: renderMode === "motion_svd" ? selectedSvdModel?.id || undefined : undefined,
        checkpoint: checkpointName || undefined,
        variant_index: selectedVariant,
        ...buildDiffusionPayload(),
        engine,
        fps: motionFps,
        max_frames_per_scene: maxFramesPerScene,
        context_length: motionContextLength,
        context_overlap: motionContextOverlap,
      });
      setInfo(d);
      await refreshProject(projectId);
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const renderVideoSmart = async (forceRoute?: "local_gpu" | "cosmos_cloud" | "azure_foundry_cloud") => {
    setErr(null);
    setInfo(null);
    try {
      const d = await postProjectMutation(`/v1/projects/${projectId}/render/video/smart`, {
        variant_index: selectedVariant,
        preset: renderPreset,
        route: forceRoute,
      });
      setInfo(d);
      await refreshProject(projectId);
      apiGet("/v1/render/route").then(setVideoRoute).catch(() => {});
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const renderCosmosScene = async (sceneIndex: number, useKeyframe = false) => {
    setErr(null);
    setInfo(null);
    try {
      const d = await postProjectMutation(`/v1/projects/${projectId}/render/cosmos/scene`, {
        variant_index: selectedVariant,
        scene_index: sceneIndex,
        use_keyframe: useKeyframe,
        seed: renderSeed ? Number(renderSeed) : undefined,
      });
      setInfo(d);
      await refreshProject(projectId);
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const renderCosmosAll = async (useKeyframe = false) => {
    setErr(null);
    setInfo(null);
    try {
      const d = await postProjectMutation(`/v1/projects/${projectId}/render/cosmos/all_scenes`, {
        variant_index: selectedVariant,
        use_keyframe: useKeyframe,
        seed: renderSeed ? Number(renderSeed) : undefined,
      });
      setInfo(d);
      await refreshProject(projectId);
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const renderAzureFoundryScene = async (sceneIndex: number, useKeyframe = false) => {
    setErr(null);
    setInfo(null);
    try {
      const d = await postProjectMutation(`/v1/projects/${projectId}/render/azure_foundry/scene`, {
        variant_index: selectedVariant,
        scene_index: sceneIndex,
        use_keyframe: useKeyframe,
        seed: renderSeed ? Number(renderSeed) : undefined,
      });
      setInfo(d);
      await refreshProject(projectId);
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const renderAzureFoundryAll = async (useKeyframe = false) => {
    setErr(null);
    setInfo(null);
    try {
      const d = await postProjectMutation(`/v1/projects/${projectId}/render/azure_foundry/all_scenes`, {
        variant_index: selectedVariant,
        use_keyframe: useKeyframe,
        seed: renderSeed ? Number(renderSeed) : undefined,
      });
      setInfo(d);
      await refreshProject(projectId);
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const assembleFirefly = async () => {
    setErr(null);
    setInfo(null);
    try {
      const d = await postProjectMutation(`/v1/projects/${projectId}/render/firefly/assemble`, {
        variant_index: selectedVariant,
        fps: internalFpsOut,
      });
      setInfo(d);
      await refreshProject(projectId);
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const assembleImagineart = async () => {
    setErr(null);
    setInfo(null);
    try {
      const d = await postProjectMutation(`/v1/projects/${projectId}/render/imagineart/assemble`, {
        variant_index: selectedVariant,
        fps: internalFpsOut,
      });
      setInfo(d);
      await refreshProject(projectId);
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const assemble = async () => {
    setErr(null);
    setInfo(null);
    try {
      const d = await postProjectMutation(`/v1/projects/${projectId}/assemble_video`, {
        variant_index: selectedVariant,
        fps: internalFpsOut,
      });
      setInfo(d);
      await refreshProject(projectId);
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const runTensorrtStandalone = async () => {
    if (!projectId || !selectedStillModelId || !selectedStillTensorRtReady) return;
    setErr(null);
    setInfo(null);
    try {
      const d = await postProjectMutation(`/v1/projects/${projectId}/render/tensorrt-standalone`, {
        variant_index: selectedVariant,
        model_id: selectedStillModelId,
        width: Number(renderWidth) || 1024,
        height: Number(renderHeight) || 1024,
        steps: renderSteps,
        cfg: renderCfg,
        sampler: renderSampler,
        negative_prompt: renderNegativePrompt,
        seed: renderSeed ? Number(renderSeed) : undefined,
        batch_size: trtBatchSize,
      });
      setInfo(d);
      await refreshProjectJobs(); // Poll for TRT job in the generic internal status widget for now
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const tickWorker = async () => {
    setErr(null);
    setInfo(null);
    try {
      const d = await apiPost(`/v1/jobs/tick`, {});
      setInfo(d);
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const verifyEdmg = async () => {
    setErr(null);
    setInfo(null);
    try {
      const d = await apiPost(`/v1/edmg/verify`, {});
      setInfo(d);
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const exportDeforum = async () => {
    setErr(null);
    setInfo(null);
    try {
      const d = await postProjectMutation(`/v1/projects/${projectId}/export/deforum`, { variant_index: selectedVariant, fps: 30 });
      setInfo(d);
      await refreshProject(projectId);
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const exportComfyWorkflows = async () => {
    setErr(null);
    setInfo(null);
    try {
      if (selectedStillEngine === "internal") {
        throw new Error("ComfyUI workflow export is only available when the selected still model uses the ComfyUI engine.");
      }
      const params = new URLSearchParams({
        variant_index: String(selectedVariant),
        model_id: selectedStillModel?.id || "",
        workflow_family: stillWorkflow,
      });
      params.set("width", String(renderWidth));
      params.set("height", String(renderHeight));
      params.set("steps", String(renderSteps));
      params.set("cfg", String(renderCfg));
      params.set("sampler", renderSampler);
      params.set("negative_prompt", renderNegativePrompt);
      if (parsedRenderSeed != null) params.set("seed", String(parsedRenderSeed));
      if (selectedLoras.length) params.set("loras_json", JSON.stringify(selectedLoras.map((item) => ({ name: item.name, weight: item.weight }))));
      if (stillWorkflow === "img2img" || stillWorkflow === "inpaint" || stillWorkflow === "outpaint") {
        if (sourceAsset) params.set("source_asset", sourceAsset);
        params.set("denoise_strength", String(denoiseStrength));
      }
      if (stillWorkflow === "inpaint" && stillMaskAsset) {
        params.set("inpaint_mask", stillMaskAsset);
      }
      if (stillWorkflow === "outpaint") {
        if (stillMaskAsset) params.set("inpaint_mask", stillMaskAsset);
        params.set("outpaint_json", JSON.stringify(outpaint));
      }
      if (stillWorkflow === "controlnet") {
        const units = controlnetUnits
          .filter((unit) => unit.model && unit.reference_asset)
          .map((unit) => ({
            model: unit.model,
            reference_asset: unit.reference_asset,
            conditioning_mode: unit.conditioning_mode,
            strength: unit.strength,
            start_percent: unit.start_percent,
            end_percent: unit.end_percent,
          }));
        if (units.length) params.set("controlnet_units_json", JSON.stringify(units));
      }
      if (hiresFix.enabled) {
        params.set("hires_fix_json", JSON.stringify({
          enabled: true,
          scale: Math.max(1, Number(hiresFix.scale || 1.5)),
          denoise: Math.max(0, Math.min(1, Number(hiresFix.denoise || 0.35))),
          upscaler: hiresFix.upscaler || "latent_bislerp",
          ...(Number(hiresFix.steps) > 0 ? { steps: Math.trunc(Number(hiresFix.steps)) } : {}),
        }));
        params.set("upscaler", hiresFix.upscaler || "latent_bislerp");
      }
      if (refiner.enabled) {
        params.set("refiner_json", JSON.stringify({
          switch_at: Math.max(0, Math.min(1, Number(refiner.switch_at || 0.8))),
          ...(refiner.model ? { model: refiner.model } : {}),
          ...(Number(refiner.steps) > 0 ? { steps: Math.trunc(Number(refiner.steps)) } : {}),
        }));
      }
      const d = await apiGet(`/v1/projects/${projectId}/export/comfyui_workflows?${params.toString()}`);
      setInfo(d);
      await refreshProject(projectId);
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const uploadReferenceAsset = async () => {
    if (!referenceUploadFile || !projectId) return;
    setErr(null);
    try {
      await uploadProjectAsset(`/v1/projects/${projectId}/assets/refs`, referenceUploadFile);
      setReferenceUploadFile(null);
      await refreshReferenceAssets(projectId);
      await refreshProject(projectId);
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const uploadWorkflowMask = async () => {
    if (!workflowMaskUploadFile || !projectId) return;
    setErr(null);
    try {
      await uploadProjectAsset(`/v1/projects/${projectId}/assets/mask`, workflowMaskUploadFile);
      setWorkflowMaskUploadFile(null);
      await refreshProject(projectId);
    } catch (e: any) {
      setErr(String(e));
    }
  };

  
  const loadEditorBackground = async () => {
    try {
      setErr(null);
      const d = await apiGet(`/v1/projects/${projectId}/outputs`);
      const imgs: string[] = (d?.images || []).map((x: any) => x.path || x).filter(Boolean);
      if (!imgs.length) { setEditorBgPath(""); return; }
      const last = imgs[0];
      setEditorBgPath(last);
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const sourceAssetPreviewUrl = sourceAsset ? fileUrl(projectId, sourceAsset) : "";
  const maskAssetPreviewUrl = stillMaskAsset ? fileUrl(projectId, stillMaskAsset) : "";
  const deforumExports = project?.meta?.exports?.deforum || [];
  const comfyExports = project?.meta?.exports?.comfyui || [];

  const variantCount = plan?.variants?.length || 0;
  const sceneCount = plan?.variants?.[selectedVariant]?.scenes?.length || 0;
  const renderRouteOptions = [
    { value: "auto", label: "Auto · best installed route" },
    { value: "diffusion", label: "Local diffusion" },
    ...(internalHostedVisible ? [{ value: "hosted", label: "Hosted Stability" }] : []),
    ...(tensorRtInternalVisible ? [{ value: "tensorrt", label: "TensorRT SD 1.5 keyframes" }] : []),
  ];
  const quickRenderModelCatalog = quickRenderGoal === "stills"
    ? stillModels
    : quickRenderGoal === "motion_ad"
      ? comfyStillModels
      : quickRenderGoal === "motion_svd"
        ? svdModels
        : internalModelOptions;
  const quickRenderModels = quickRenderModelCatalog.map((model) => ({
    id: model.id,
    name: model.name,
    installed: installedModels[model.id] !== false,
  }));
  const quickRenderModelId = quickRenderGoal === "stills"
    ? selectedStillModelId
    : quickRenderGoal === "motion_ad"
      ? selectedMotionModelId
      : quickRenderGoal === "motion_svd"
        ? selectedSvdModelId
        : internalModelId;
  const setQuickRenderModel = (modelId: string) => {
    if (quickRenderGoal === "stills") setSelectedStillModelId(modelId);
    else if (quickRenderGoal === "motion_ad") setSelectedMotionModelId(modelId);
    else if (quickRenderGoal === "motion_svd") setSelectedSvdModelId(modelId);
    else setInternalModelId(modelId);
  };
  const quickOutputFps = quickRenderGoal === "motion_ad" || quickRenderGoal === "motion_svd"
    ? motionFps
    : internalFpsOut;
  const setQuickOutputFps = (fps: number) => {
    if (quickRenderGoal === "motion_ad" || quickRenderGoal === "motion_svd") setMotionFps(fps);
    else setInternalFpsOut(fps);
  };
  const layeredRefinementModels = internalModelOptions
    .filter((model) => model.kind === "diffusers")
    .map((model) => ({
      id: model.id,
      name: model.name,
      installed: installedModels[model.id] !== false,
    }));

  const enableStoryboardFullMotion = () => {
    setInternalMotionStrategy("storyboard_full_motion");
    setInternalTemporalMode("video_model");
    setInternalVideoMotionScoreMode("auto");
    setInternalVideoPromptRefine(true);
    setInternalVideoSceneMotion("scene");
    setKeyframeContinuityMode("project");
    setInternalVideoMaxFrames((current) => Math.max(8, current));
    setInternalFpsRender((current) => Math.max(2, current));
    setInternalFpsOut((current) => Math.max(24, current));
    setInternalInterp((current) => current === "fps" ? "auto" : current);
  };

  const applyQuickRenderGoal = (goal: RenderQuickGoal) => {
    setQuickRenderGoal(goal);
    if (goal !== "edit") {
      setOrchestratorIntent((current) => ({
        ...current,
        output_mode: goal === "auto" || goal === "full_video" ? "full_video" : "scene_batch",
      }));
    }
    if (goal === "edit") {
      onNavigate?.("timeline");
      return;
    }
    if (goal === "stills") {
      setRenderMode("stills");
      setInternalMotionStrategy("manual");
      return;
    }
    if (goal === "motion_ad") {
      setRenderMode("motion_ad");
      enableStoryboardFullMotion();
      selectInternalVideoModelEngine("animatediff");
      return;
    }
    if (goal === "motion_svd") {
      setRenderMode("motion_svd");
      enableStoryboardFullMotion();
      selectInternalVideoModelEngine("svd");
      return;
    }
    if (goal === "full_video") {
      enableStoryboardFullMotion();
      setRenderMode("motion_svd");
      return;
    }
    setInternalRenderMode("auto");
  };

  const applyQuickQuality = (quality: RenderQuickQuality) => {
    setRenderPreset(quality);
    setOrchestratorIntent((current) => ({
      ...current,
      preset: quality,
      quality_tier: quality === "fast" ? "draft" : quality,
    }));
    setInternalRenderTier(quality === "ultra" ? "quality" : quality === "fast" ? "draft" : quality);
    const stillSettings = quality === "fast"
      ? { steps: 12, cfg: 5.5, renderFps: 2 }
      : quality === "balanced"
        ? { steps: 24, cfg: 7, renderFps: 3 }
        : quality === "quality"
          ? { steps: 36, cfg: 7.5, renderFps: 4 }
          : { steps: 50, cfg: 8, renderFps: 6 };
    setRenderSteps(stillSettings.steps);
    setRenderCfg(stillSettings.cfg);
    setInternalFpsRender(stillSettings.renderFps);
    setMotionFps(quality === "fast" ? 8 : quality === "balanced" ? 12 : quality === "quality" ? 18 : 24);
    setMaxFramesPerScene(quality === "fast" ? 120 : quality === "balanced" ? 240 : quality === "quality" ? 480 : 720);
  };

  const runQuickRender = () => {
    if (quickRenderGoal === "auto") {
      void (async () => {
        await refreshConductorPlan();
        await runPipeline();
      })();
      return;
    }
    if (quickRenderGoal === "stills") {
      void renderScenes();
      return;
    }
    if (quickRenderGoal === "motion_ad" || quickRenderGoal === "motion_svd") {
      void renderMotion();
      return;
    }
    if (quickRenderGoal === "edit") {
      onNavigate?.("timeline");
      return;
    }
    void runInternalVideo();
  };

  const quickRenderDisabled = quickRenderGoal === "edit"
    ? false
    : !variantCount
      || (quickRenderGoal === "full_video" && internalTensorRtBlocked)
      || (quickRenderGoal === "stills" && !selectedStillModel)
      || (quickRenderGoal === "motion_ad" && (!selectedMotionModel || caps?.animatediff?.available === false))
      || (quickRenderGoal === "motion_svd" && (!selectedSvdModel || caps?.svd?.available === false));
  const quickRunLabel = quickRenderGoal === "auto"
    ? "Choose route + queue"
    : quickRenderGoal === "stills"
      ? "Render still scenes"
      : quickRenderGoal === "motion_ad"
        ? "Queue AnimateDiff scenes"
        : quickRenderGoal === "motion_svd"
          ? "Queue SVD scenes"
          : quickRenderGoal === "edit"
            ? "Open Timeline editor"
            : "Queue full-motion video";

  return (
    <div>
      <h1>Render</h1>
      <ProjectRevisionConflict
        conflict={revisionConflict}
        onReload={() => refreshProject(projectId)}
      />
      <RenderControlCenter
        goal={quickRenderGoal}
        onGoalChange={applyQuickRenderGoal}
        quality={renderPreset}
        onQualityChange={applyQuickQuality}
        route={internalRenderMode}
        onRouteChange={(route) => setInternalRenderMode(route as "auto" | "diffusion" | "hosted" | "tensorrt")}
        routeOptions={renderRouteOptions}
        modelId={quickRenderModelId}
        onModelChange={setQuickRenderModel}
        models={quickRenderModels}
        outputFps={quickOutputFps}
        onOutputFpsChange={setQuickOutputFps}
        width={renderWidth}
        height={renderHeight}
        onResolutionChange={(width, height) => {
          setRenderWidth(width);
          setRenderHeight(height);
          setOrchestratorIntent((current) => ({
            ...current,
            aspect_ratio: renderAspectRatioForSize(width, height),
          }));
        }}
        timelineCamera={internalVideoApplyTimelineCamera}
        onTimelineCameraChange={setInternalVideoApplyTimelineCamera}
        onRun={runQuickRender}
        runDisabled={quickRenderDisabled}
        runLabel={quickRunLabel}
        onOpenAllSettings={() => {
          setAdvancedControlsOpen(true);
          window.setTimeout(() => document.getElementById("render-all-settings")?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
        }}
        onOpenModels={() => onNavigate?.("models")}
      />
      <div style={{ marginTop: 14 }}>
        <RenderOrchestratorIntentControls
          value={orchestratorIntent}
          disabled={!variantCount}
          onChange={(nextIntent) => {
            const nextVariant = variantCount
              ? Math.min(Math.max(0, nextIntent.variant_index), variantCount - 1)
              : Math.max(0, nextIntent.variant_index);
            const normalizedIntent = { ...nextIntent, variant_index: nextVariant };
            setOrchestratorIntent(normalizedIntent);
            if (nextVariant !== selectedVariant) setSelectedVariant(nextVariant);
            if (nextIntent.preset !== renderPreset) setRenderPreset(nextIntent.preset);
          }}
        />
        <div
          className="row"
          style={{ justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap", marginTop: 10 }}
        >
          <div className="small" style={{ maxWidth: 760 }}>
            These choices are kept as the current render intent. Quick Auto and the button here both plan with this exact intent before choosing a genuine render route.
          </div>
          <button
            type="button"
            disabled={!variantCount}
            onClick={() => refreshConductorPlan().catch(() => {})}
          >
            Replan with this intent
          </button>
        </div>
      </div>
      <div className="grid2">
        <div className="card">
          <div style={{ fontWeight: 800, marginBottom: 10 }}>Project</div>
          {projects.length ? (
            <select value={projectId} onChange={(e) => setProjectId(e.target.value)}>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          ) : (
            <div className="small">No projects yet. Create one in Projects tab.</div>
          )}

          <div className="row" style={{ marginTop: 10, gap: 10, flexWrap: "wrap" }}>
            <button className="secondary" onClick={() => onNavigate?.("workspace")}>Back to Workspace</button>
            <button className="secondary" onClick={() => onNavigate?.("queue")}>Open Render Queue</button>
            <button className="secondary" onClick={() => onNavigate?.("outputs")}>Open Outputs</button>
          </div>

          <hr />
          <div style={{ fontWeight: 800, marginBottom: 10 }}>Variant</div>
          {variantCount ? (
            <select value={selectedVariant} onChange={(e) => setSelectedVariant(Number(e.target.value))}>
              {plan.variants.map((v: any, idx: number) => (
                <option key={idx} value={idx}>{idx + 1}. {v.name}</option>
              ))}
            </select>
          ) : (
            <div className="small">No plan found for this project. Generate a plan in Workspace.</div>
          )}

          <hr />
          <div style={{ fontWeight: 800, marginBottom: 10 }}>Preset + Render</div>

          <div className="row" style={{ alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <div style={{ flex: 1, minWidth: 180 }}>
              <div className="small">Preset</div>
              <select value={renderPreset} onChange={(e) => setRenderPreset(e.target.value as any)}>
                <option value="fast">Fast Preview</option>
                <option value="balanced">Balanced</option>
                <option value="quality">Quality</option>
                <option value="ultra">Ultra</option>
              </select>
            </div>
            <div style={{ flex: 2, minWidth: 220 }}>
              <div className="small">Auto mode</div>
              <div className="small" style={{ opacity: 0.85 }}>
                {validate?.recommended ? (
                  <>Will run: <b>{validate.recommended.mode}</b>{validate.recommended.engine ? <> (<b>{validate.recommended.engine}</b>)</> : null} • {validate.recommended.reason}</>
                ) : (
                  <>Will auto-select the best available pipeline.</>
                )}
              </div>
              <div className="small" style={{ marginTop: 6 }}>
                ComfyUI: AnimateDiff {caps?.animatediff?.available ? "✓" : "×"} / SVD {caps?.svd?.available ? "✓" : "×"} / ControlNet {caps?.controlnet?.available ? "✓" : "×"}
              </div>
            </div>
          </div>

          {conductorPlan ? (
            <RenderPlanPanel
              plan={conductorPlan}
              continuityReport={continuityReport}
              visualDnaHints={visualDnaHints}
              onOpenModels={onNavigate ? () => onNavigate("models") : undefined}
              onOpenSettings={onNavigate ? () => onNavigate("settings") : undefined}
              onNavigateReview={onNavigate ? () => onNavigate("review") : undefined}
              onRefresh={() => refreshConductorPlan().catch(() => {})}
            />
          ) : null}

          <div className="card" style={{ marginTop: 12, padding: 12 }}>
            <div className="row" style={{ justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <div style={{ fontWeight: 800 }}>Performer workflow (W6-05)</div>
              <button className="secondary" type="button" disabled={!variantCount || planningPerformer} onClick={() => refreshPerformerPlan().catch(() => {})}>
                {planningPerformer ? "Planning…" : "Plan performer lane"}
              </button>
            </div>
            <div className="small" style={{ marginTop: 6, opacity: 0.85 }}>
              Queue audio-driven performance scenes only when a genuine high-end performer adapter is installed and ready. Synthetic proxy output is never substituted.
            </div>
            <div className="row" style={{ marginTop: 10, gap: 10, alignItems: "center", flexWrap: "wrap" }}>
              <div className="small" style={{ opacity: 0.82 }}>
                Provider: <b>automatic real route</b>
              </div>
              <button type="button" disabled={!performerPlan?.tasks?.length || runningPerformer} onClick={() => runPerformerWorkflow().catch(() => {})}>
                {runningPerformer ? "Queueing…" : "Queue performer render"}
              </button>
            </div>
            {performerStatus ? (
              <div className="small" style={{ marginTop: 8 }}>{performerStatus}</div>
            ) : null}
            {performerPlan?.tasks?.length ? (
              <div style={{ marginTop: 10 }}>
                {performerPlan.tasks.slice(0, 6).map((task: any) => (
                  <div key={task.scene_id} className="small" style={{ marginBottom: 6 }}>
                    <b>{task.scene_id}</b> • {task.engine} • {task.model?.display_name || "Wan S2V"}
                    {task.audio_window ? <> • {Number(task.audio_window.start_s).toFixed(1)}s–{Number(task.audio_window.end_s).toFixed(1)}s</> : null}
                  </div>
                ))}
              </div>
            ) : null}
            {Array.isArray(performerPlan?.warnings) && performerPlan.warnings.length ? (
              <div className="small" style={{ marginTop: 8, opacity: 0.85 }}>
                {performerPlan.warnings.slice(0, 2).map((warning: any) => warning.message).join(" • ")}
              </div>
            ) : null}
          </div>

          <div className="row" style={{ marginTop: 10, gap: 10, flexWrap: "wrap" }}>
            <button onClick={runPipeline} disabled={!variantCount}>Preset + Render (one click)</button>
            <button
              className="secondary"
              onClick={runInternalVideo}
              disabled={!variantCount || internalTensorRtBlocked}
              title={internalTensorRtBlocked ? "Install the required TensorRT bundle in Models first" : undefined}
            >
              Internal / Hosted
            </button>
            <button className="secondary" onClick={assemble} disabled={!variantCount}>Assemble only</button>
          </div>
          {!variantCount ? (
            <div className="small" role="alert" style={{ marginTop: 8, color: "var(--danger, #ff8f8f)" }}>
              Nothing can queue for this project yet. Go to Workspace, upload audio, run Analyze + Plan, then return here. “Plan performer lane” does not replace the main project plan.
            </div>
          ) : (
            <div className="small" style={{ marginTop: 8, opacity: 0.8 }}>
              Use <b>Internal / Hosted</b> for the settings below, or <b>Preset + Render</b> to let Studio choose.
            </div>
          )}

          <div className="card" style={{ marginTop: 12, padding: 12 }}>
            <div style={{ fontWeight: 900, marginBottom: 6 }}>AI Auto-Render</div>
            <div className="small" style={{ opacity: 0.85, marginBottom: 8 }}>
              Pick a preset and the AI sets the render + motion settings and runs it. The manual controls below still work.
            </div>
            <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>
              <div style={{ flex: 2, minWidth: 220 }}>
                <div className="small">Animation preset</div>
                <select
                  value={autoPreset}
                  onChange={(e) => { setAutoPreset(e.target.value); setAutoConfig(null); }}
                >
                  {animationPresets.length ? (
                    animationPresets.map((p) => (
                      <option key={p.id} value={p.id}>{p.label}</option>
                    ))
                  ) : (
                    <option value={autoPreset}>{autoPreset}</option>
                  )}
                </select>
              </div>
              <div style={{ flex: 1, minWidth: 150 }}>
                <div className="small">Engine</div>
                <select value={autoEngine} onChange={(e) => setAutoEngine(e.target.value as any)}>
                  <option value="auto">Auto</option>
                  <option value="internal">Internal renderer</option>
                  <option value="comfyui">ComfyUI</option>
                </select>
              </div>
              <div style={{ flex: 1, minWidth: 130 }}>
                <div className="small">Output FPS</div>
                <input
                  aria-label="Auto-render output FPS"
                  type="number"
                  min={1}
                  max={60}
                  step={1}
                  value={autoFps}
                  onChange={(event) => setAutoFps(Math.max(1, Math.min(60, Number(event.target.value) || 1)))}
                />
              </div>
            </div>
            {selectedAutoPreset ? (
              <div className="small" style={{ marginTop: 6, opacity: 0.85 }}>
                {selectedAutoPreset.description} • motion: <b>{selectedAutoPreset.motion_label || selectedAutoPreset.motion}</b>
                {selectedAutoPreset.motion_strategy === "storyboard_full_motion" ? <> • route: <b>storyboard video model</b></> : null}
                {selectedAutoPreset.scene_motion ? <> • scene: <b>{selectedAutoPreset.scene_motion}</b></> : null}
                {selectedAutoPreset.is_3d ? " (3D)" : ""} • quality: <b>{selectedAutoPreset.quality}</b>
                {selectedAutoPreset.animates_objects ? " • animates objects in the image" : ""}
              </div>
            ) : null}
            {autoNeedsSource ? (
              <div style={{ marginTop: 8 }}>
                <div className="small">Source image (required)</div>
                {sourceImageOptions.length ? (
                  <select value={autoSourceAsset} onChange={(e) => setAutoSourceAsset(e.target.value)}>
                    <option value="">— select a generated output or reference —</option>
                    {sourceImageOptions.map((a) => (
                      <option key={a.path} value={a.path}>{a.path.replace(/^assets[\\/]refs[\\/]/, "")}</option>
                    ))}
                  </select>
                ) : (
                  <div className="small" style={{ opacity: 0.8 }}>Generate an image or upload one under References (Advanced) first.</div>
                )}
              </div>
            ) : null}
            {autoNeedsMasks ? (
              <div style={{ marginTop: 8 }}>
                <div className="small">Object masks (required) — select one or more</div>
                {maskAssets.length ? (
                  <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 4 }}>
                    {maskAssets.map((m: string) => (
                      <label key={m} className="small" style={{ display: "flex", gap: 4, alignItems: "center" }}>
                        <input
                          type="checkbox"
                          checked={autoMaskAssets.includes(m)}
                          onChange={(e) =>
                            setAutoMaskAssets((prev) =>
                              e.target.checked ? [...prev, m] : prev.filter((x) => x !== m),
                            )
                          }
                        />
                        {m}
                      </label>
                    ))}
                  </div>
                ) : (
                  <div className="small" style={{ opacity: 0.8 }}>Upload masks under Masks (Advanced) first.</div>
                )}
              </div>
            ) : null}
            <div className="row" style={{ marginTop: 10, gap: 10, flexWrap: "wrap" }}>
              <button onClick={runAuto} disabled={autoRunDisabled}>{autoBusy ? "Working…" : "Auto-configure & Render"}</button>
              <button className="secondary" onClick={previewAuto} disabled={!variantCount || autoBusy}>Preview config</button>
            </div>
            {autoConfig?.config ? (
              <div className="card" style={{ marginTop: 10, padding: 10 }}>
                <div className="small">
                  Engine: <b>{autoConfig.engine}</b>
                  {autoConfig.config.internal_request?.render_tier ? <> • tier: <b>{autoConfig.config.internal_request.render_tier}</b></> : null}
                  {autoConfig.config.internal_request?.temporal_mode ? <> • temporal: <b>{autoConfig.config.internal_request.temporal_mode}</b></> : null}
                  {autoConfig.config.internal_request?.motion_strategy ? <> • strategy: <b>{autoConfig.config.internal_request.motion_strategy}</b></> : null}
                  {autoConfig.config.internal_request?.video_model_scene_motion ? <> • scene: <b>{autoConfig.config.internal_request.video_model_scene_motion}</b></> : null}
                  {autoConfig.config.internal_request?.video_model_keyframe_renderer ? <> • keyframes: <b>{autoConfig.config.internal_request.video_model_keyframe_renderer}</b></> : null}
                  {autoConfig.config.animation_mode ? <> • mode: <b>{autoConfig.config.animation_mode}</b></> : null}
                </div>
                {autoEngine === "comfyui" && autoConfig.comfyui_available === false ? (
                  <div className="small" style={{ opacity: 0.8 }}>ComfyUI not reachable; the internal renderer will be used.</div>
                ) : null}
                {Array.isArray(autoConfig.config.notes) && autoConfig.config.notes.length ? (
                  <ul className="small" style={{ marginTop: 6 }}>
                    {autoConfig.config.notes.map((n: string, i: number) => (<li key={i}>{n}</li>))}
                  </ul>
                ) : null}
              </div>
            ) : null}
            {autoConfig?.job || autoConfig?.jobs?.length ? (
              <div className="small" style={{ marginTop: 8 }}>
                Launched job <b>{autoConfig?.job?.id || autoConfig?.jobs?.[0]?.id || "—"}</b>
                {" "}({autoConfig?.job?.type || autoConfig?.jobs?.[0]?.type || "render"}).{" "}
                <button className="secondary" onClick={() => onNavigate?.("queue")}>Open Render Queue</button>
              </div>
            ) : null}
          </div>

          <div className="card" style={{ marginTop: 12, padding: 12 }}>
            <div className="row" style={{ justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <div>
                <div style={{ fontWeight: 900 }}>Motion Sequencer</div>
                <div className="small" style={{ opacity: 0.85 }}>
                  Parseq-style schedules from the analysis and storyboard drive camera, diffusion controls, motion score, noise, and anchor blending.
                </div>
              </div>
              <label className="row small" style={{ gap: 6, alignItems: "center" }}>
                <input type="checkbox" checked={motionSequencerEnabled} onChange={(e) => setMotionSequencerEnabled(e.target.checked)} />
                Use active schedules
              </label>
            </div>
            <div className="small" style={{ marginTop: 8 }}>
              Active: <b>{motionSequencer?.active ? "yes" : "generated preview"}</b>
              {" "}• schedules <b>{motionSequencer?.summary?.schedules ?? 0}</b>
              {" "}• keyframes <b>{motionSequencer?.summary?.keyframes ?? 0}</b>
              {" "}• prompts <b>{motionSequencer?.summary?.prompts ?? 0}</b>
            </div>
            {Array.isArray(motionSequencer?.recipe_graph?.nodes) ? (
              <div className="small" style={{ marginTop: 6 }}>
                Recipe: {motionSequencer.recipe_graph.nodes.map((node: any) => String(node.label || node.id)).join(" -> ")}
              </div>
            ) : null}
            <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 8 }}>
              <button className="secondary" onClick={applyGeneratedMotionSequencer} disabled={!variantCount || motionSequencerBusy}>
                {motionSequencerBusy ? "Applying..." : "Generate + apply schedules"}
              </button>
              <button className="secondary" onClick={() => refreshMotionSequencer().catch(() => {})} disabled={!projectId || motionSequencerBusy}>Refresh preview</button>
            </div>
            {motionSequencer?.summary ? (
              <details style={{ marginTop: 8 }}>
                <summary className="small" style={{ cursor: "pointer" }}>Schedule details</summary>
                <StructuredSummary
                  value={{
                    summary: motionSequencer.summary,
                    overrides: motionSequencer.overrides,
                    recipe_graph: motionSequencer.recipe_graph,
                  }}
                  showJson
                  jsonLabel="Show sequencer JSON"
                  maxDepth={2}
                  maxItems={12}
                />
              </details>
            ) : null}
          </div>

          <details
            id="render-all-settings"
            style={{ marginTop: 12 }}
            open={advancedControlsOpen}
            onToggle={(event) => setAdvancedControlsOpen(event.currentTarget.open)}
          >
            <summary style={{ cursor: "pointer", fontWeight: 800 }}>Advanced routing & controls</summary>
            <div style={{ marginTop: 10 }}>
              <div className="small" style={{ marginBottom: 10 }}>
                Every specialist option remains here: object animation, genuine render routing, model/video controls,
                compositing, workflows, enhancement, diagnostics, and manual queue actions.
              </div>
              <LayeredAnimationControls
                sourceOptions={sourceImageOptions}
                maskOptions={(Array.isArray(maskAssets) ? maskAssets : []).map(String)}
                modelOptions={layeredRefinementModels}
                defaultSource={autoSourceAsset}
                defaultMotion={String(selectedAutoPreset?.motion || "full_3d")}
                busy={layeredBusy}
                disabled={!projectId}
                onQueue={(payload) => void queueLayeredAnimation(payload)}
                onOpenModels={() => onNavigate?.("models")}
              />
              <div className="card" style={{ marginTop: 10 }}>
                <div style={{ fontWeight: 900, marginBottom: 8 }}>Internal renderer (no ComfyUI)</div>
                <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>
                  <div style={{ minWidth: 170 }}>
                    <div className="small">Render mode</div>
                    <select value={internalRenderMode} onChange={(e) => setInternalRenderMode(e.target.value as any)}>
                      <option value="auto">Auto</option>
                      <option value="diffusion">Local diffusion</option>
                      {internalHostedVisible ? <option value="hosted">Hosted Stability</option> : null}
                      {tensorRtInternalVisible ? <option value="tensorrt">TensorRT SD1.5 keyframe assembly</option> : null}
                    </select>
                  </div>
                  <div style={{ minWidth: 140 }}>
                    <div className="small">FPS output</div>
                    <input type="number" value={internalFpsOut} min={1} max={60} onChange={(e) => setInternalFpsOut(Number(e.target.value))} />
                  </div>
                  <div style={{ minWidth: 140 }}>
                    <div className="small">FPS render</div>
                    <input type="number" value={internalFpsRender} min={1} max={30} onChange={(e) => setInternalFpsRender(Number(e.target.value))} />
                  </div>
                  <div style={{ minWidth: 160 }}>
                    <div className="small">Keyframe interval (s)</div>
                    <input type="number" value={internalKeyInterval} min={0.5} max={60} step={0.5} onChange={(e) => setInternalKeyInterval(Number(e.target.value))} />
                  </div>
                  <div style={{ minWidth: 170 }}>
                    <div className="small">Interpolation</div>
                    <select value={internalInterp} onChange={(e) => setInternalInterp(e.target.value as any)}>
                      <option value="auto">Auto</option>
                      <option value="minterpolate">FFmpeg minterpolate</option>
                      <option value="fps">Frame duplicate</option>
                      <option value="rife">RIFE (EDMG_RIFE_CMD)</option>
                    </select>
                  </div>
<div style={{ minWidth: 240 }}>
  <div className="small">Internal model</div>
  <select value={internalModelId} onChange={(e) => setInternalModelId(e.target.value)}>
    <option value="auto">Auto (SD3.5 on strong GPU, SDXL or SD1.5 fallback)</option>
    {internalModelOptions.map((m) => (
      <option key={m.id} value={m.id}>{m.name}</option>
    ))}
  </select>
</div>
                  <div style={{ minWidth: 180 }}>
                    <div className="small">Device</div>
                    <select value={internalDevicePreference} onChange={(e) => setInternalDevicePreference(e.target.value as any)}>
                      <option value="auto">Auto</option>
                      <option value="cpu">CPU</option>
                      {hardware?.hardware?.available_backends?.includes?.("cuda") ? <option value="cuda">CUDA</option> : null}
                      {hardware?.hardware?.available_backends?.includes?.("mps") ? <option value="mps">MPS</option> : null}
                      {internalDirectmlAvailable ? <option value="directml">DirectML</option> : null}
                    </select>
                  </div>
                  <div style={{ minWidth: 190 }}>
                    <div className="small">Render tier</div>
                    <select value={internalRenderTier} onChange={(e) => setInternalRenderTier(e.target.value as any)}>
                      <option value="auto">Auto (hardware-aware)</option>
                      <option value="draft">Draft</option>
                      <option value="balanced">Balanced</option>
                      <option value="quality">Quality</option>
                    </select>
                  </div>
                  <label className="row small" style={{ gap: 6, alignItems: "center", minWidth: 250 }}>
                    <input
                      aria-label="Allow hosted fallback"
                      type="checkbox"
                      checked={internalRenderMode === "diffusion" ? false : internalAllowHostedFallback}
                      disabled={internalRenderMode === "diffusion" || internalRenderMode === "tensorrt"}
                      onChange={(event) => setInternalAllowHostedFallback(event.target.checked)}
                    />
                    Allow configured hosted fallback
                  </label>
                </div>
                <div className="small" style={{ marginTop: 8, opacity: 0.85 }}>
                  Tip: install internal models in Models first. Auto tiering adapts the internal renderer for laptops, Apple Silicon, CPU-only systems, higher-end GPUs, and the TensorRT CUDA keyframe path.
                </div>
                <div className="small" style={{ marginTop: 6, opacity: 0.82 }}>
                  Explicit Local diffusion never leaves this machine. In Auto mode, hosted fallback is used only when this switch is on and a real provider is configured.
                </div>
                <div className="small" style={{ marginTop: 6, color: "var(--accent-soft)" }}>
                  Auto mode uses installed internal models or a configured hosted renderer. Studio will not substitute a synthetic proxy render.
                </div>
                {internalTensorRtRequired ? (
                  <div
                    className="small"
                    role="status"
                    aria-label="TensorRT bundle status"
                    style={{ marginTop: 6, opacity: 0.82 }}
                  >
                    TensorRT keyframe assembly forces CUDA, keyframe temporal mode, the local SD1.5 TensorRT bundle, and the compiled 512x512 batch-1 profile. It is still-frame assembly with interpolation, not SVD or AnimateDiff subject motion. For moving subjects, use Internal video model with TensorRT SD1.5 storyboard anchors.
                    {" "}Bundle status: <b>{tensorRtInternalInstalled ? "installed" : "missing"}</b>.
                    {!tensorRtInternalInstalled ? (
                      <span>
                        {" "}<button className="secondary" type="button" onClick={() => onNavigate?.("models")}>
                          Open Models to install TensorRT bundle
                        </button>
                      </span>
                    ) : null}
                  </div>
                ) : null}
                {internalHostedVisible ? (
                  <div className="small" style={{ marginTop: 6, opacity: 0.82 }}>
                    Hosted Stability fallback is configured: <b>{renderProviders?.stability?.service}</b>
                    {renderProviders?.stability?.service === "sd3" ? <> / <b>{renderProviders?.stability?.model}</b></> : null}
                  </div>
                ) : null}
                {internalDirectmlAvailable ? (
                  <div className="small" style={{ marginTop: 6, opacity: 0.82 }}>
                    DirectML runtime detected on <b>{hardware?.hardware?.directml_device_name || hardware?.hardware?.device_name}</b>.
                  </div>
                ) : null}
                {internalDirectmlDetected && !internalDirectmlAvailable ? (
                  <div className="small" style={{ marginTop: 6, opacity: 0.82 }}>
                    DirectML runtime is available on this machine but currently disabled in Settings.
                  </div>
                ) : null}
                {savedRenderDefaults.profileId ? (
                  <div className="small" style={{ marginTop: 6, opacity: 0.82 }}>
                    Saved defaults: <b>{String(savedRenderDefaults.profileId).replace(/_/g, " ")}</b>
                  </div>
                ) : null}

                <div className="card" style={{ marginTop: 10 }}>
                  <div style={{ fontWeight: 900, marginBottom: 8 }}>Internal render readiness</div>
                  {internalPreflight?.ok ? (
                    <div>
                      <div className="small">Mode: <b>{internalPreflight.mode || "diffusion"}</b> • Device: <b>{internalPreflight.device}</b> • Model: <b>{internalPreflight.model_id}</b></div>
                      {internalPreflight?.hosted_provider ? (
                        <div className="small" style={{ marginTop: 4 }}>
                          Hosted provider: <b>{internalPreflight.hosted_provider.provider}</b> • service <b>{internalPreflight.hosted_provider.service}</b>
                          {internalPreflight.hosted_provider.model ? <> • model <b>{internalPreflight.hosted_provider.model}</b></> : null}
                          {internalPreflight.hosted_provider.style_preset ? <> • style <b>{internalPreflight.hosted_provider.style_preset}</b></> : null}
                        </div>
                      ) : null}
                      {internalPreflight?.mode === "tensorrt" ? (
                        <div className="small" style={{ marginTop: 4 }}>
                          TensorRT profile: <b>{internalPreflight?.settings?.profile_width || 512}x{internalPreflight?.settings?.profile_height || 512}</b>
                          {" "}• max batch <b>{internalPreflight?.settings?.max_batch || 1}</b>
                          {" "}• keyframe assembly engine <b>SD1.5 TensorRT</b>
                        </div>
                      ) : null}
                      <div className="small" style={{ marginTop: 4 }}>
                        Tier: requested <b>{internalPreflight?.tier_plan?.requested_tier || internalRenderTier}</b> • applied <b>{internalPreflight?.tier_plan?.applied_tier || "auto"}</b> • recommended <b>{internalPreflight?.tier_plan?.recommended_tier || hardware?.hardware?.recommended_tier || "draft"}</b>
                      </div>
                      <div className="small" style={{ marginTop: 4 }}>
                        Estimated frames: <b>{internalPreflight.estimated_frames}</b> • Keyframes: <b>{internalPreflight.estimated_keyframes}</b> • Duration: <b>{Number(internalPreflight.duration_s || 0).toFixed(1)}s</b>
                      </div>
                      {formatDurationSources(internalPreflight) ? (
                        <div className="small" style={{ marginTop: 4 }}>
                          Duration sources: <b>{formatDurationSources(internalPreflight)}</b>
                        </div>
                      ) : null}
                      {!!internalPreflight?.prompt_preview?.length ? (
                        <div style={{ marginTop: 8 }}>
                          <div className="small" style={{ fontWeight: 800 }}>Resolved render prompts</div>
                          <div style={{ display: "grid", gap: 6, marginTop: 6 }}>
                            {internalPreflight.prompt_preview.slice(0, 4).map((item: any, idx: number) => (
                              <div key={`${item?.frame ?? idx}-${idx}`} className="small" style={{ opacity: 0.9 }}>
                                <b>{Number(item?.time_s || 0).toFixed(1)}s</b>: {String(item?.prompt || "").slice(0, 260)}
                              </div>
                            ))}
                          </div>
                        </div>
                      ) : null}
                      <div className="small" style={{ marginTop: 4 }}>
                        Resume existing frames: <b>{internalPreflight.resume_existing_frames ? "on" : "off"}</b>
                      </div>
                      {internalPreflight?.tier_plan?.chunk_plan ? (
                        <div className="small" style={{ marginTop: 4 }}>
                          Chunk plan: <b>{internalPreflight.tier_plan.chunk_plan.enabled ? `${internalPreflight.tier_plan.chunk_plan.estimated_chunks} chunks` : "single pass"}</b> • {internalPreflight.tier_plan.chunk_plan.frames_per_chunk} frames/chunk • checkpoint every {internalPreflight.tier_plan.chunk_plan.checkpoint_interval_frames} frames
                        </div>
                      ) : null}
                      <div className="small" style={{ marginTop: 4 }}>
                        Hardware: <b>{hardware?.hardware?.device_name || internalPreflight?.hardware?.device_name || internalPreflight.device}</b> • backend family <b>{hardware?.hardware?.backend_family || internalPreflight?.hardware?.backend_family || "cpu_only"}</b> • RAM <b>{Number(hardware?.hardware?.ram_gb || internalPreflight?.hardware?.ram_gb || 0).toFixed(1)} GB</b>
                      </div>
                      <div className="small" style={{ marginTop: 4 }}>
                        Internal video bases: SD 1.5 <b>{internalPreflight?.installed_internal_models?.hf_sd15_internal ? "installed" : "missing"}</b> • SDXL <b>{internalPreflight?.installed_internal_models?.hf_sdxl_internal ? "installed" : "missing"}</b> • SD3.5 <b>{internalPreflight?.installed_internal_models?.hf_sd35_medium_internal ? "installed" : "missing"}</b> • FLUX keyframes <b>{internalPreflight?.installed_internal_models?.hf_flux1_schnell_internal ? "installed" : "missing"}</b>
                      </div>
                      <div className="small" style={{ marginTop: 4 }}>
                        Internal video adapters: SVD <b>{internalPreflight?.installed_internal_video_models?.hf_svd_xt_1_1_internal ? "installed" : "missing"}</b> • AnimateDiff <b>{internalPreflight?.installed_internal_video_models?.hf_animatediff_motion_adapter_v15_2_internal ? "installed" : "missing"}</b>
                        {" "}• deps <b>{internalPreflight?.internal_video_model_dependencies?.diffusers_available ? "ready" : "missing diffusers"}</b>
                      </div>
                      {internalPreflight?.parseq_motion ? (
                        <div className="small" style={{ marginTop: 4 }}>
                          Motion sequencer: <b>{internalPreflight.parseq_motion.schedules}</b> schedule(s)
                          {" "}• keyframes <b>{internalPreflight.parseq_motion.keyframes}</b>
                          {" "}• prompts <b>{internalPreflight.parseq_motion.prompts}</b>
                        </div>
                      ) : null}
                      {internalPreflight?.resource_policy ? (
                        <div className="small" style={{ marginTop: 4 }}>
                          Resource policy: <b>{internalPreflight.resource_policy.offload_policy}</b>
                          {" "}• precision <b>{internalPreflight.resource_policy.precision_policy}</b>
                          {" "}• adapters <b>{internalPreflight.resource_policy.adapter_policy?.loras?.count ?? 0} LoRA</b>
                        </div>
                      ) : null}
                      {Array.isArray(internalPreflight?.render_recipe_graph?.nodes) ? (
                        <div className="small" style={{ marginTop: 4 }}>
                          Recipe graph: {internalPreflight.render_recipe_graph.nodes.slice(0, 7).map((node: any) => String(node.label || node.id)).join(" -> ")}
                        </div>
                      ) : null}
                      {internalPreflight?.settings?.temporal_mode === "video_model" ? (
                        <div className="small" style={{ marginTop: 4 }}>
                          Video-model motion: <b>{internalPreflight?.settings?.video_model_engine || internalVideoModelEngine}</b>
                          {internalPreflight?.settings?.video_model_id ? <> • model <b>{internalPreflight.settings.video_model_id}</b></> : null}
                          {" "}• score <b>{internalPreflight?.internal_video_model_preflight?.motion_score_mode || internalPreflight?.settings?.video_model_motion_score_mode || internalVideoMotionScoreMode}</b>
                          {" "}• anchor <b>{internalPreflight?.internal_video_model_preflight?.anchor_mode || internalPreflight?.settings?.video_model_anchor_mode || internalVideoAnchorMode}</b>
                          {" "}• scene <b>{internalPreflight?.internal_video_model_preflight?.scene_motion || internalPreflight?.settings?.video_model_scene_motion || internalVideoSceneMotion}</b>
                          {" "}• Timeline camera <b>{(internalPreflight?.settings?.video_model_apply_timeline_camera ?? internalVideoApplyTimelineCamera) ? "on" : "off"}</b>
                          {" "}• keyframes <b>{internalPreflight?.internal_video_model_preflight?.keyframe_renderer || internalPreflight?.settings?.video_model_keyframe_renderer || internalVideoKeyframeRenderer}</b>
                          {" "}• continuity <b>{normalizeKeyframeContinuityMode(internalPreflight?.settings?.keyframe_continuity_mode || keyframeContinuityMode) === "project" ? "project identity lock" : "scene resets"}</b>
                          {" "}• native cap <b>{internalPreflight?.internal_video_model_preflight?.effective_native_frame_cap ?? internalVideoMaxFrames}</b>
                          {" "}• motion gate <b>{Array.isArray(internalPreflight?.internal_video_model_preflight?.motion_frame_budgets) && internalPreflight.internal_video_model_preflight.motion_frame_budgets.every((item: any) => item?.status === "pass") ? "ready" : "blocked"}</b>
                        </div>
                      ) : null}
                      {internalPreflight?.internal_video_model_preflight?.storyboard_motion_plan ? (
                        <div className="small" style={{ marginTop: 4 }}>
                          Storyboard full motion: <b>{internalPreflight.internal_video_model_preflight.storyboard_motion_plan.shot_count}</b> generated-anchor shots
                          {" "}• max <b>{Number(internalPreflight.internal_video_model_preflight.storyboard_motion_plan.shot_max_s || internalStoryboardShotMax).toFixed(1)}s</b>
                          {" "}• anchor <b>{internalPreflight.internal_video_model_preflight.storyboard_motion_plan.anchor_source === "source_image" ? "source image" : internalPreflight.internal_video_model_preflight.storyboard_motion_plan.anchor_source === "tensorrt_sd15_keyframe" ? "TensorRT SD1.5" : "generated keyframe"}</b>
                        </div>
                      ) : null}
                      {internalPreflight?.internal_video_model_preflight?.scene_scores?.length ? (
                        <div className="small" style={{ marginTop: 4 }}>
                          Scene motion scores: {internalPreflight.internal_video_model_preflight.scene_scores.slice(0, 4).map((item: any) => (
                            <span key={item.scene_index ?? item.start_s} style={{ marginRight: 8 }}>
                              <b>{Number(item.start_s || 0).toFixed(1)}s</b> {item.motion_score ?? "off"}
                            </span>
                          ))}
                        </div>
                      ) : null}
                      {internalPreflight?.requested_model_id ? (
                        <div className="small" style={{ marginTop: 4 }}>
                          Requested model: <b>{internalPreflight.requested_model_id}</b>
                        </div>
                      ) : null}
                      {internalPreflight?.tier_plan?.defaults ? (
                        <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 8 }}>
                          <button className="secondary" onClick={() => {
                            const d = internalPreflight.tier_plan.defaults;
                            setInternalFpsOut(Number(d.fps_output ?? internalFpsOut));
                            setInternalFpsRender(Number(d.fps_render ?? internalFpsRender));
                            setInternalKeyInterval(Number(d.keyframe_interval_s ?? internalKeyInterval));
                            setInternalInterp(String(d.interpolation_engine ?? internalInterp) as any);
                            setInternalTemporalMode(String(d.temporal_mode ?? internalTemporalMode) as any);
                            setInternalTemporalSteps(Number(d.temporal_steps ?? internalTemporalSteps));
                            setInternalRefineEvery(Number(d.refine_every_n_frames ?? internalRefineEvery));
                            setInternalAnchorStrength(Number(d.anchor_strength ?? internalAnchorStrength));
                          }}>Apply tier defaults</button>
                          <div className="small" style={{ alignSelf: "center", opacity: 0.85 }}>
                            Suggested: <b>{internalPreflight.tier_plan.defaults.width}x{internalPreflight.tier_plan.defaults.height}</b> • steps <b>{internalPreflight.tier_plan.defaults.steps}</b> • fps render <b>{internalPreflight.tier_plan.defaults.fps_render}</b>
                          </div>
                        </div>
                      ) : null}
                      {internalPreflight?.cache ? (
                        <div className="small" style={{ marginTop: 6 }}>
                          Cache: <b>{internalPreflight.cache.frames_present}</b>/<b>{internalPreflight.cache.frames_expected}</b> frames
                          {" "}• raw <b>{internalPreflight.cache.raw_exists ? "yes" : "no"}</b>
                          {" "}• interp <b>{internalPreflight.cache.interp_exists ? "yes" : "no"}</b>
                          {" "}• final <b>{internalPreflight.cache.final_exists ? "yes" : "no"}</b>
                        </div>
                      ) : null}
                      {!internalHostedVisible && !internalPreflight?.installed_internal_models?.hf_sd15_internal && !internalPreflight?.installed_internal_models?.hf_sdxl_internal && !internalPreflight?.installed_internal_models?.hf_sd35_medium_internal && !internalPreflight?.installed_internal_models?.hf_flux1_schnell_internal ? (
                        <div className="row" style={{ gap: 8, marginTop: 8, flexWrap: "wrap" }}>
                          <button className="secondary" onClick={() => onNavigate?.("models")}>Open Models to install internal renderer</button>
                        </div>
                      ) : null}
                      {effectiveInternalTemporalMode === "video_model" && !internalPreflight?.installed_internal_video_models?.hf_svd_xt_1_1_internal && !internalPreflight?.installed_internal_video_models?.hf_animatediff_motion_adapter_v15_2_internal ? (
                        <div className="row" style={{ gap: 8, marginTop: 8, flexWrap: "wrap" }}>
                          <button className="secondary" onClick={() => onNavigate?.("models")}>Open Models to install internal motion adapters</button>
                        </div>
                      ) : null}
                      {!!internalPreflight?.warnings?.length && (
                        <div style={{ marginTop: 8 }}>
                          {internalPreflight.warnings.map((w: string, idx: number) => (
                            <div key={idx} className="small" style={{ color: "var(--warning, #b58900)" }}>⚠ {w}</div>
                          ))}
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="small" style={{ color: "var(--danger)" }}>
                      {internalPreflight?.error || "Preflight unavailable."}
                    </div>
                  )}
                </div>

                <ProjectJobsPanel
                  backendUrl={backendUrl}
                  jobs={internalJobs}
                  selectedLog={internalSelectedLog}
                  lastRefreshAt={internalJobsLastRefreshAt}
                  error={internalJobsError}
                  info={internalJobInfo}
                  autoRefresh={internalPolling}
                  onAutoRefreshChange={setInternalPolling}
                  onRefresh={refreshProjectJobs}
                  onViewLog={loadInternalJobLog}
                  onCloseLog={() => setInternalSelectedLog(null)}
                  onJobAction={handleInternalJobAction}
                  onResumeFromCheckpoint={handleResumeInternalFromCheckpoint}
                  onRestartClean={handleRestartInternalClean}
                  onNavigateToQueue={onNavigate ? () => onNavigate("queue") : undefined}
                  onDesktopActionMessage={setInternalJobInfo}
                  onDesktopActionError={(message) => {
                    setInternalJobInfo(null);
                    setInternalJobsError(message);
                  }}
                  continuityBlockingCount={continuityReport?.blocking_count || 0}
                  title="Internal render jobs"
                  description="Latest internal/hosted video jobs for this project with the same pause, cancel, retry, and log controls as Render Queue."
                />
                {latestInternalJob ? (
                  <div className="card" style={{ marginTop: 10 }}>
                    <div style={{ fontWeight: 900, marginBottom: 8 }}>Internal job tools</div>
                    <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                      <button className="secondary" onClick={applyLatestInternalSettings}>Use latest job settings</button>
                      <button className="secondary" onClick={clearLatestInternalCachedFrames} disabled={isJobActive(latestInternalJob.status)}>Clear cached frames</button>
                      <button className="secondary" onClick={dropLatestInternalCheckpoint} disabled={isJobActive(latestInternalJob.status)}>Drop checkpoint</button>
                      {latestInternalVideoUrl ? (
                        <button className="secondary" onClick={() => openLocalPath("latest internal video", latestInternalVideoPath)}>{desktopActionLabel("open", "latest video")}</button>
                      ) : null}
                    </div>
                  </div>
                ) : null}

                <div className="card" style={{ marginTop: 10 }}>
                  <div className="row" style={{ justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                    <div style={{ fontWeight: 900 }}>Codex render diagnosis</div>
                    <button
                      className="secondary"
                      disabled={codexBusy || !projectId || !codexStatus?.installed || !codexStatus?.enabled}
                      onClick={runCodexRenderReview}
                    >
                      {codexBusy ? "Reviewing..." : "Review current render"}
                    </button>
                  </div>
                  <div className="small" style={{ marginTop: 6, opacity: 0.82 }}>
                    SDK <b>{codexStatus?.installed ? "installed" : "not installed"}</b>
                    {" "}• enabled <b>{codexStatus?.enabled ? "yes" : "no"}</b>
                    {" "}• sandbox <b>{codexStatus?.sandbox || "read-only"}</b>
                    {" "}• model <b>{codexStatus?.model || "gpt-5.4"}</b>
                  </div>
                  {codexStatus?.hint ? (
                    <div className="small" style={{ marginTop: 4, opacity: 0.78 }}>{codexStatus.hint}</div>
                  ) : null}
                  {codexReview?.error ? (
                    <div className="small" style={{ marginTop: 8, color: "var(--danger)" }}>{codexReview.error}</div>
                  ) : null}
                  {codexReview?.final_response ? (
                    <pre style={{ marginTop: 10, maxHeight: 220, overflow: "auto" }}>{codexReview.final_response}</pre>
                  ) : null}
                </div>

                <div className="card" style={{ marginTop: 10 }}>
                  <div style={{ fontWeight: 900, marginBottom: 8 }}>Latest internal output</div>
                  {latestInternalVideoUrl ? (
                    <div>
                      <div className="small">
                        {latestInternalVideoPath}
                      </div>
                      {latestVideoMissing ? (
                        <div className="small" style={{ marginTop: 8 }}>
                          This render output is no longer on disk (it may have been cleaned up or
                          belongs to a removed project). Re-render to regenerate it.
                          <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 8 }}>
                            <button className="secondary" onClick={() => { applyLatestInternalSettings(); setInternalResumeExisting(false); }}>Reuse settings + re-render</button>
                          </div>
                        </div>
                      ) : (
                        <>
                          <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 8 }}>
                            <button className="secondary" onClick={() => openLocalPath("latest internal video", latestInternalVideoPath)}>{desktopActionLabel("open", "video")}</button>
                            <a className="secondary" href={latestInternalVideoUrl} download>Download video</a>
                            <button className="secondary" onClick={() => { applyLatestInternalSettings(); setInternalResumeExisting(true); }}>Reuse settings + resume caches</button>
                          </div>
                          <RenewingVideo
                            controls
                            style={{ width: "100%", maxWidth: 640, marginTop: 10 }}
                            sourceUrl={latestInternalVideoUrl}
                            onError={() => setLatestVideoMissing(true)}
                          />
                        </>
                      )}
                    </div>
                  ) : (
                    <div className="small">No completed internal video saved yet.</div>
                  )}
                </div>

                 <div className="card" style={{ marginTop: 10 }}>
                   <div style={{ fontWeight: 900, marginBottom: 8 }}>Temporal consistency + compositing</div>

                   <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>
                     <div style={{ minWidth: 230 }}>
                       <div className="small">Motion strategy</div>
                       <select value={internalMotionStrategy} onChange={(e) => {
                         const next = e.target.value as "manual"|"storyboard_full_motion";
                         setInternalMotionStrategy(next);
                         if (next === "storyboard_full_motion") enableStoryboardFullMotion();
                       }}>
                         <option value="manual">Manual temporal controls</option>
                         <option value="storyboard_full_motion">Storyboard full motion</option>
                       </select>
                     </div>
                     {internalMotionStrategy === "storyboard_full_motion" ? (
                       <div style={{ minWidth: 170 }}>
                         <div className="small">Max shot seconds</div>
                         <input type="number" value={internalStoryboardShotMax} min={1} max={12} step={0.5}
                           onChange={(e) => setInternalStoryboardShotMax(Number(e.target.value))} />
                       </div>
                     ) : null}
                     <div style={{ minWidth: 190 }}>
                       <div className="small">Temporal mode</div>
                       <select value={effectiveInternalTemporalMode} disabled={internalMotionStrategy === "storyboard_full_motion"} onChange={(e) => setInternalTemporalMode(e.target.value as any)}>
                         <option value="video_model">Internal video model (SVD / AnimateDiff)</option>
                         <option value="frame_img2img">Internal motion (frame img2img)</option>
                         <option value="keyframes">Keyframe assembly only</option>
                         <option value="off">Off (still keyframes)</option>
                       </select>
                     </div>
                     <div style={{ minWidth: 160 }}>
                       <div className="small">Strength</div>
                       <input type="number" value={internalTemporalStrength} min={0.05} max={0.95} step={0.05}
                         onChange={(e) => setInternalTemporalStrength(Number(e.target.value))} />
                     </div>
                     <div style={{ minWidth: 160 }}>
                       <div className="small">Steps (refine)</div>
                       <input type="number" value={internalTemporalSteps} min={1} max={80}
                         onChange={(e) => setInternalTemporalSteps(Number(e.target.value))} />
                     </div>
                     <div style={{ minWidth: 170 }}>
                       <div className="small">Refine every N frames</div>
                       <input type="number" value={internalRefineEvery} min={1} max={30}
                         onChange={(e) => setInternalRefineEvery(Number(e.target.value))} />
                     </div>
                     <div style={{ minWidth: 160 }}>
                       <div className="small">Anchor strength</div>
                       <input type="number" value={internalAnchorStrength} min={0} max={1} step={0.05}
                         onChange={(e) => setInternalAnchorStrength(Number(e.target.value))} />
                     </div>
                     <label className="row small" style={{ gap: 6, alignItems: "center" }}>
                       <input type="checkbox" checked={internalPromptBlend} onChange={(e) => setInternalPromptBlend(e.target.checked)} />
                       Prompt blend (embedding)
                     </label>
                     <label className="row small" style={{ gap: 6, alignItems: "center" }}>
                       <input type="checkbox" checked={internalResumeExisting} onChange={(e) => setInternalResumeExisting(e.target.checked)} />
                       Resume existing cached frames
                     </label>
                   </div>
                   {internalMotionStrategy === "storyboard_full_motion" ? (
                     <div className="small" style={{ marginTop: 8, opacity: 0.84 }}>
                       Uses the transcript, scene prompts, and audio energy to generate scene keyframe anchors, split long scenes into short motion shots, and stitch them into the final video.
                     </div>
                   ) : null}

                   {effectiveInternalTemporalMode === "video_model" ? (
                     <div className="card" style={{ marginTop: 10 }}>
                       <div style={{ fontWeight: 800, marginBottom: 8 }}>Internal video-model adapter</div>
                       <div className="row" style={{ gap: 10, flexWrap: "wrap", alignItems: "center" }}>
                         <div style={{ minWidth: 320, flex: 1 }}>
                           <div className="small">Source image anchor (optional)</div>
                           <select
                             aria-label="Internal video source image"
                             value={sourceAsset}
                             onChange={(e) => setSourceAsset(e.target.value)}
                           >
                             <option value="">Generate anchors from scene prompts</option>
                             {sourceImageOptions.map((asset) => (
                               <option key={asset.path} value={asset.path}>{asset.path}</option>
                             ))}
                           </select>
                         </div>
                         {sourceAsset ? (
                           <div style={{ minWidth: 170 }}>
                             <div className="small">Source strength</div>
                             <input
                               aria-label="Internal video source strength"
                               type="number"
                               min={0.05}
                               max={0.95}
                               step={0.05}
                               value={denoiseStrength}
                               onChange={(e) => setDenoiseStrength(Number(e.target.value))}
                             />
                           </div>
                         ) : null}
                         <div style={{ minWidth: 160 }}>
                           <div className="small">Adapter engine</div>
                            <select value={internalVideoModelEngine} onChange={(e) => selectInternalVideoModelEngine(e.target.value)}>
                             <option value="auto">Auto installed</option>
                             <option value="svd">SVD image-to-video</option>
                             <option value="animatediff">AnimateDiff SD1.5</option>
                           </select>
                         </div>
                        <div style={{ minWidth: 280 }}>
                          <div className="small">Video model</div>
                           <select value={internalVideoModelId} onChange={(e) => selectInternalVideoModel(e.target.value)}>
                              <option value="">Auto select installed adapter</option>
                              {filteredInternalVideoModelOptions.map((m) => (
                               <option key={m.id} value={m.id}>
                                 {m.name} {installedModels[m.id] === false ? "(not installed)" : ""}
                               </option>
                             ))}
                          </select>
                        </div>
                        <div style={{ minWidth: 230 }}>
                          <div className="small">Storyboard anchors</div>
                          <select value={internalVideoKeyframeRenderer} onChange={(e) => setInternalVideoKeyframeRenderer(e.target.value as any)}>
                            <option value="internal">Internal diffusion keyframes</option>
                            <option value="tensorrt_sd15">TensorRT SD1.5 keyframes{tensorRtInternalInstalled ? "" : " (not installed)"}</option>
                          </select>
                        </div>
                        <label style={{ minWidth: 230 }} title="Choose whether each authored scene starts from a fresh keyframe or carries the previous keyframe forward to protect subject identity across the whole movie.">
                          <div className="small">Keyframe continuity</div>
                          <select
                            aria-label="Keyframe continuity"
                            value={keyframeContinuityMode}
                            onChange={(event) => setKeyframeContinuityMode(normalizeKeyframeContinuityMode(event.target.value))}
                          >
                            <option value="scene">Scene resets</option>
                            <option value="project">Project-wide identity lock</option>
                          </select>
                        </label>
                          <div style={{ minWidth: 170 }}>
                            <div className="small">Frames per scene</div>
                            <input type="number" value={internalVideoMaxFrames} min={8} max={96}
                              onChange={(e) => setInternalVideoMaxFrames(Number(e.target.value))} />
                            <div className="small" style={{ marginTop: 3, opacity: 0.78 }}>
                              Minimum 8 for verified motion. Long scenes need Storyboard full motion.
                            </div>
                          </div>
                         <div style={{ minWidth: 170 }}>
                           <div className="small">SVD motion bucket</div>
                           <input type="number" value={internalVideoMotionBucket} min={1} max={255}
                             onChange={(e) => setInternalVideoMotionBucket(Number(e.target.value))} />
                         </div>
                         <div style={{ minWidth: 170 }}>
                           <div className="small">SVD noise aug</div>
                           <input type="number" value={internalVideoNoiseAug} min={0} max={1} step={0.01}
                             onChange={(e) => setInternalVideoNoiseAug(Number(e.target.value))} />
                         </div>
                         <div style={{ minWidth: 170 }}>
                           <div className="small">Decode chunk</div>
                           <input type="number" value={internalVideoDecodeChunk} min={1} max={64}
                             onChange={(e) => setInternalVideoDecodeChunk(Number(e.target.value))} />
                         </div>
                         <div style={{ minWidth: 160 }}>
                           <div className="small">Precision</div>
                           <select value={internalVideoDtype} onChange={(e) => setInternalVideoDtype(e.target.value as any)}>
                             <option value="auto">Auto</option>
                             <option value="float16">float16</option>
                             <option value="bfloat16">bfloat16</option>
                             <option value="float32">float32</option>
                           </select>
                         </div>
                         <label className="row small" style={{ gap: 6, alignItems: "center" }}>
                           <input type="checkbox" checked={internalVideoCpuOffload} onChange={(e) => setInternalVideoCpuOffload(e.target.checked)} />
                           CPU offload
                         </label>
                         <div style={{ minWidth: 170 }}>
                           <div className="small">Motion score</div>
                           <select value={internalVideoMotionScoreMode} onChange={(e) => setInternalVideoMotionScoreMode(e.target.value as any)}>
                             <option value="auto">Auto from scene energy</option>
                             <option value="manual">Manual</option>
                             <option value="off">Off</option>
                           </select>
                         </div>
                         {internalVideoMotionScoreMode === "manual" ? (
                           <div style={{ minWidth: 150 }}>
                             <div className="small">Manual score</div>
                             <input type="number" value={internalVideoManualMotionScore} min={1} max={7}
                               onChange={(e) => setInternalVideoManualMotionScore(Number(e.target.value))} />
                           </div>
                         ) : null}
                         <div style={{ minWidth: 170 }}>
                           <div className="small">I2V anchor</div>
                           <select value={internalVideoAnchorMode} onChange={(e) => setInternalVideoAnchorMode(e.target.value as any)}>
                             <option value="start">Start anchor</option>
                             <option value="end">End anchor</option>
                             <option value="both">Cinematic start + end</option>
                             <option value="loop">Loop anchor</option>
                           </select>
                         </div>
                         <div style={{ minWidth: 190 }}>
                           <div className="small">Scene motion</div>
                           <select value={internalVideoSceneMotion} onChange={(e) => setInternalVideoSceneMotion(e.target.value as any)}>
                             <option value="camera">Camera + atmosphere</option>
                             <option value="subject">Animate subjects</option>
                             <option value="scene">Animate whole scene</option>
                           </select>
                         </div>
                          <label className="row small" style={{ gap: 6, alignItems: "center" }}>
                            <input type="checkbox" checked={internalVideoPromptRefine} onChange={(e) => setInternalVideoPromptRefine(e.target.checked)} />
                            Prompt refine
                          </label>
                          <label className="row small" style={{ gap: 6, alignItems: "center" }} title="Apply the Timeline zoom, pan, rotation, depth, pitch, yaw, and roll after SVD or AnimateDiff generates each frame.">
                            <input
                              aria-label="Apply Timeline camera motion"
                              type="checkbox"
                              checked={internalVideoApplyTimelineCamera}
                              onChange={(e) => setInternalVideoApplyTimelineCamera(e.target.checked)}
                            />
                            Apply Timeline camera motion
                          </label>
                       </div>
                       <div className="small" style={{ marginTop: 8, opacity: 0.82 }}>
                         SVD animates from keyframes. When you select a source image, Studio uses that fitted image directly as the first video-model anchor; Storyboard full motion keeps it selected instead of discarding it. Timeline camera motion is layered over generated frames when enabled; turn it off if model-generated camera movement feels doubled. TensorRT SD1.5 can generate later storyboard anchors for SVD. AnimateDiff follows scene text directly; TensorRT anchors only guide start/end/loop blending and do not replace the SD1.5 Diffusers base.
                       </div>
                       <div className="small" style={{ marginTop: 6, opacity: 0.82 }}>
                         Keyframe continuity: <b>{keyframeContinuityMode === "project" ? "Project-wide identity lock" : "Scene resets"}</b>. Project-wide mode carries the preceding anchor across scene boundaries for stronger subject identity; scene resets allow a clean visual break between authored scenes.
                       </div>
                     </div>
                   ) : null}

                   <div style={{ marginTop: 10, fontWeight: 800 }}>Overlays</div>
                   <div className="row" style={{ gap: 10, flexWrap: "wrap", alignItems: "center", marginTop: 6 }}>
                     <input type="file" accept="image/*" onChange={(e) => setOverlayFile(e.target.files?.[0] || null)} />
                     <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                       <input type="file" accept="image/*" onChange={(e) => setMaskFile(e.target.files?.[0] || null)} />
                       <button className="secondary" disabled={!maskFile} onClick={async () => {
                         try {
                           if (!maskFile) return;
                           await uploadProjectAsset(`/v1/projects/${projectId}/assets/mask`, maskFile);
                           await refreshProject(projectId);
                           setMaskFile(null);
                         } catch (e: any) { setErr(String(e)); }
                       }}>Upload mask</button>
                     </div>

                     <button
                       className="secondary"
                       disabled={!overlayFile || !projectId}
                       onClick={async () => {
                         try {
                           setErr(null);
                           const up = await uploadProjectAsset(`/v1/projects/${projectId}/assets/overlay`, overlayFile!);
                           const duration = (plan?.variants?.[selectedVariant]?.scenes?.slice(-1)?.[0]?.end_s) ?? 60;
                           const next = {
                             ...timeline,
                             layers: [
                               ...(timeline?.layers || []),
                               { type: "image", asset: up.asset, start_s: 0, end_s: Number(duration), x: 20, y: 20, w: 220, h: 220, opacity: 0.9, blend_mode: "normal", mask_asset: "", mask_invert: false, mask_feather_px: 0, keyframes: [], z: 10 }
                             ]
                           };
                           const saved = await postProjectMutation(`/v1/projects/${projectId}/timeline`, { timeline: next });
                           setTimeline(saved.timeline);
                           await refreshProject(projectId);
                         } catch (e: any) {
                           setErr(String(e));
                         }
                       }}
                     >
                       Add image overlay
                     </button>
                   </div>

                   <div className="row" style={{ gap: 10, flexWrap: "wrap", alignItems: "center", marginTop: 8 }}>
                     <input style={{ minWidth: 320 }} value={overlayText} onChange={(e) => setOverlayText(e.target.value)} placeholder="Text overlay (e.g., Title / Artist)" />
                     <button
                       className="secondary"
                       disabled={!overlayText || !projectId}
                       onClick={async () => {
                         try {
                           setErr(null);
                           const duration = (plan?.variants?.[selectedVariant]?.scenes?.slice(-1)?.[0]?.end_s) ?? 10;
                           const next = {
                             ...timeline,
                             layers: [
                               ...(timeline?.layers || []),
                               { type: "text", text: overlayText, start_s: 0, end_s: Number(duration), x: 24, y: 24, size: 34, color: "#ffffff", stroke_color: "#000000", stroke_width: 2, opacity: 1.0, z: 20 }
                             ]
                           };
                           const saved = await postProjectMutation(`/v1/projects/${projectId}/timeline`, { timeline: next });
                           setTimeline(saved.timeline);
                           await refreshProject(projectId);
                           setOverlayText("");
                         } catch (e: any) {
                           setErr(String(e));
                         }
                       }}
                     >
                       Add text overlay
                     </button>
                   </div>

                   <div className="small" style={{ marginTop: 8, opacity: 0.85 }}>
                     Layers are applied during internal renders. Delete layers from the list below.
                   </div>


                   <div className="card" style={{ marginTop: 10 }}>
                     <OverlayStage
                       projectId={projectId}
                       backendUrl={backendUrl}
                       width={768}
                       height={432}
                       timeline={timeline}
                       selectedIndices={selectedLayerIdxs}
                       onSelect={(indices) => setSelection(indices)}
                       onChange={(tl) => { setTimeline(tl); setTimelineDirty(true); }}
                       editingMask={editMaskMode}
                       onEditingMaskChange={(v) => setEditMaskMode(v)}
                       playheadS={editorTimeS}
                       autoKey={autoKey}
                       backgroundUrl={editorBgUrl}
                     />

                     <div className="row" style={{ gap: 10, flexWrap: "wrap", alignItems: "center", marginTop: 10 }}>
                       <div className="small" style={{ fontWeight: 900 }}>Keyframes</div>
                       <label className="small row" style={{ gap: 6 }}>
                         t (s)
                         <input
                           type="number"
                           step="0.1"
                           min={0}
                           value={editorTimeS}
                           onChange={(e) => setEditorTimeS(Number(e.target.value))}
                           style={{ width: 90 }}
                         />
                       </label>
                       <label className="small row" style={{ gap: 6 }}>
                         <input type="checkbox" checked={autoKey} onChange={(e) => setAutoKey(e.target.checked)} />
                         auto-key (gizmos write keyframes)
                       </label>
                       <button className="secondary" disabled={!selectedLayerIdxs.length} onClick={() => addLayerKeyframesAtTime(editorTimeS, "layer")}>
                         Add keyframe(s)
                       </button>
                       <button
                         className="secondary"
                         disabled={singleLayerIdx == null || !editMaskMode}
                         onClick={() => addLayerKeyframesAtTime(editorTimeS, "mask")}
                       >
                         Add mask keyframe
                       </button>
                       <button className="secondary" onClick={() => setSelection([])}>Clear selection</button>
                     </div>

                     <div className="row" style={{ gap: 10, flexWrap: "wrap", alignItems: "center", marginTop: 10 }}>
                       <button className="secondary" onClick={loadEditorBackground} disabled={!projectId}>Use latest output as background</button>
                       <button className="secondary" onClick={() => setEditorBgPath("")}>Clear background</button>
                       {singleLayerIdx != null ? (
                         <>
                           <button className="secondary" onClick={() => {
                             const l = timeline.layers?.[singleLayerIdx];
                             if (!l) return;
                             const next = { ...timeline, layers: (timeline.layers || []).map((x: any, i: number) => i === singleLayerIdx ? { ...x, mask_x: 0, mask_y: 0, mask_scale: 1, mask_rotation_deg: 0 } : x) };
                             setTimeline(next); setTimelineDirty(true);
                           }}>Reset mask transform</button>
                           <button className="secondary" onClick={() => {
                             const l = timeline.layers?.[singleLayerIdx];
                             if (!l) return;
                             const next = { ...timeline, layers: (timeline.layers || []).map((x: any, i: number) => i === singleLayerIdx ? { ...x, rotation_deg: 0 } : x) };
                             setTimeline(next); setTimelineDirty(true);
                           }}>Reset rotation</button>
                         </>
                       ) : null}
                     </div>
                   </div>

                   <div style={{ marginTop: 8 }}>
                     {(timeline?.layers || []).length ? (
                       <div className="small">
                         {(timeline.layers || []).map((l: any, idx: number) => (
                           <div key={idx} style={{ border: "1px solid rgba(255,255,255,0.10)", borderRadius: 10, padding: 10, marginTop: 8, background: selectedLayerIdxs.includes(idx) ? "rgba(122,162,255,0.08)" : "transparent" }}>
                             <div className="row" style={{ gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                               <div className="row" style={{ gap: 8, alignItems: "center" }}>
                               <input
                                 type="checkbox"
                                 checked={selectedLayerIdxs.includes(idx)}
                                 onChange={(e) => {
                                   const sel = new Set<number>(selectedLayerIdxs);
                                   if (e.target.checked) sel.add(idx);
                                   else sel.delete(idx);
                                   setSelection(Array.from(sel.values()).sort((a, b) => a - b));
                                 }}
                               />
                               <div style={{ width: 70, fontWeight: 900 }}>{l.type}</div>
                             </div>

                               {l.type === "image" ? (
                                 <select
                                   value={l.asset || ""}
                                   onChange={(e) => {
                                     const next = { ...timeline, layers: (timeline.layers || []).map((x: any, i: number) => i === idx ? { ...x, asset: e.target.value } : x) };
                                     setTimeline(next); setTimelineDirty(true);
                                   }}
                                 >
                                   <option value="">(select overlay)</option>
                                   {overlayAssets.map((a: string) => <option key={a} value={a}>{a}</option>)}
                                 </select>
                               ) : (
                                 <input
                                   style={{ minWidth: 220 }}
                                   value={l.text || ""}
                                   onChange={(e) => {
                                     const next = { ...timeline, layers: (timeline.layers || []).map((x: any, i: number) => i === idx ? { ...x, text: e.target.value } : x) };
                                     setTimeline(next); setTimelineDirty(true);
                                   }}
                                   placeholder="Overlay text"
                                 />
                               )}

                               <label className="small">Blend</label>
                               <select
                                 value={l.blend_mode || "normal"}
                                 onChange={(e) => {
                                   const next = { ...timeline, layers: (timeline.layers || []).map((x: any, i: number) => i === idx ? { ...x, blend_mode: e.target.value } : x) };
                                   setTimeline(next); setTimelineDirty(true);
                                 }}
                               >
                                 {["normal","multiply","screen","overlay"].map((bm) => <option key={bm} value={bm}>{bm}</option>)}
                               </select>

                               <label className="small">Opacity</label>
                               <input
                                 type="number"
                                 min={0}
                                 max={1}
                                 step={0.05}
                                 value={Number(l.opacity ?? 1)}
                                 onChange={(e) => {
                                   const v = Math.max(0, Math.min(1, Number(e.target.value)));
                                   const next = { ...timeline, layers: (timeline.layers || []).map((x: any, i: number) => i === idx ? { ...x, opacity: v } : x) };
                                   setTimeline(next); setTimelineDirty(true);
                                 }}
                                 style={{ width: 80 }}
                               />

                               <label className="small">Mask</label>
                               <select
                                 value={l.mask_asset || ""}
                                 onChange={(e) => {
                                   const next = { ...timeline, layers: (timeline.layers || []).map((x: any, i: number) => i === idx ? { ...x, mask_asset: e.target.value } : x) };
                                   setTimeline(next); setTimelineDirty(true);
                                 }}
                               >
                                 <option value="">(none)</option>
                                 {maskAssets.map((a: string) => <option key={a} value={a}>{a}</option>)}
                               </select>

                               <label className="small" style={{ display: "flex", gap: 6, alignItems: "center" }}>
                                 <input
                                   type="checkbox"
                                   checked={!!l.mask_invert}
                                   onChange={(e) => {
                                     const next = { ...timeline, layers: (timeline.layers || []).map((x: any, i: number) => i === idx ? { ...x, mask_invert: e.target.checked } : x) };
                                     setTimeline(next); setTimelineDirty(true);
                                   }}
                                 />
                                 invert
                               </label>

                               <label className="small">Feather</label>
                               <input
                                 type="number"
                                 min={0}
                                 max={50}
                                 step={1}
                                 value={Number(l.mask_feather_px ?? 0)}
                                 onChange={(e) => {
                                   const v = Math.max(0, Math.min(50, Number(e.target.value)));
                                   const next = { ...timeline, layers: (timeline.layers || []).map((x: any, i: number) => i === idx ? { ...x, mask_feather_px: v } : x) };
                                   setTimeline(next); setTimelineDirty(true);
                                 }}
                                 style={{ width: 70 }}
                               />

                               <button className="secondary" onClick={() => { setSelection([idx]); setEditMaskMode(false); }}>
                                 Edit in gizmo
                               </button>

                               <button
                                 className="secondary"
                                 onClick={async () => {
                                   const next = { ...timeline, layers: (timeline.layers || []).filter((_: any, i: number) => i !== idx) };
                                   const saved = await postProjectMutation(`/v1/projects/${projectId}/timeline`, { timeline: next });
                                   setTimeline(saved.timeline);
                                   setTimelineDirty(false);
                                   await refreshProject(projectId);
                                 }}
                               >
                                 Remove
                               </button>
                             </div>

                             <div style={{ marginTop: 8 }}>
                               <div className="small" style={{ opacity: 0.8, marginBottom: 4 }}>
                                 {`Keyframes JSON (optional): [{"t":0,"x":20,"y":20,"opacity":1,"rotation_deg":0,"blend_mode":"overlay","mask_asset":"mask.png"}, ...]`}
                               </div>
                               <textarea
                                 style={{ width: "100%", minHeight: 70 }}
                                 value={typeof l._keyframes_text === "string" ? l._keyframes_text : JSON.stringify(l.keyframes || [], null, 2)}
                                 onChange={(e) => {
                                   try {
                                     const val = JSON.parse(e.target.value || "[]");
                                     const next = { ...timeline, layers: (timeline.layers || []).map((x: any, i: number) => i === idx ? { ...x, keyframes: Array.isArray(val) ? val : [], _keyframes_text: undefined } : x) };
                                     setTimeline(next); setTimelineDirty(true);
                                   } catch {
                                     // keep editing
                                     const next = { ...timeline, layers: (timeline.layers || []).map((x: any, i: number) => i === idx ? { ...x, _keyframes_text: e.target.value } : x) };
                                     setTimeline(next); setTimelineDirty(true);
                                   }
                                 }}
                               />
                             </div>
                           </div>
                         ))}
                       </div>
                     ) : (
                       <div className="small">No layers yet.</div>
                     )}
                   </div>
                 </div>
              </div>




              
              <div style={{ marginTop: 10, fontWeight: 800 }}>Camera track</div>
              <div className="small" style={{ opacity: 0.85 }}>
                Keyframes drive internal camera motion (zoom/pan/rotation). If empty, a safe fallback motion is used.
              </div>

              <div style={{ marginTop: 8 }}>
                {((timeline?.camera?.keyframes) || []).map((k: any, i: number) => (
                  <div key={i} className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center", marginTop: 6 }}>
                    <label className="small">t</label>
                    <input type="number" step={0.1} style={{ width: 90 }} value={Number(k.t ?? 0)} onChange={(e) => {
                      const v = Number(e.target.value);
                      const next = { ...timeline, camera: { ...(timeline.camera || {}), keyframes: (timeline.camera?.keyframes || []).map((x: any, j: number) => j === i ? { ...x, t: v } : x) } };
                      setTimeline(next); setTimelineDirty(true);
                    }} />
                    <label className="small">zoom</label>
                    <input type="number" step={0.01} style={{ width: 90 }} value={Number(k.zoom ?? 1)} onChange={(e) => {
                      const v = Number(e.target.value);
                      const next = { ...timeline, camera: { ...(timeline.camera || {}), keyframes: (timeline.camera?.keyframes || []).map((x: any, j: number) => j === i ? { ...x, zoom: v } : x) } };
                      setTimeline(next); setTimelineDirty(true);
                    }} />
                    <label className="small">pan_x</label>
                    <input type="number" step={1} style={{ width: 90 }} value={Number(k.pan_x ?? 0)} onChange={(e) => {
                      const v = Number(e.target.value);
                      const next = { ...timeline, camera: { ...(timeline.camera || {}), keyframes: (timeline.camera?.keyframes || []).map((x: any, j: number) => j === i ? { ...x, pan_x: v } : x) } };
                      setTimeline(next); setTimelineDirty(true);
                    }} />
                    <label className="small">pan_y</label>
                    <input type="number" step={1} style={{ width: 90 }} value={Number(k.pan_y ?? 0)} onChange={(e) => {
                      const v = Number(e.target.value);
                      const next = { ...timeline, camera: { ...(timeline.camera || {}), keyframes: (timeline.camera?.keyframes || []).map((x: any, j: number) => j === i ? { ...x, pan_y: v } : x) } };
                      setTimeline(next); setTimelineDirty(true);
                    }} />
                    <label className="small">rot</label>
                    <input type="number" step={0.5} style={{ width: 90 }} value={Number(k.rotation_deg ?? 0)} onChange={(e) => {
                      const v = Number(e.target.value);
                      const next = { ...timeline, camera: { ...(timeline.camera || {}), keyframes: (timeline.camera?.keyframes || []).map((x: any, j: number) => j === i ? { ...x, rotation_deg: v } : x) } };
                      setTimeline(next); setTimelineDirty(true);
                    }} />
                    <button className="secondary" onClick={() => {
                      const next = { ...timeline, camera: { ...(timeline.camera || {}), keyframes: (timeline.camera?.keyframes || []).filter((_: any, j: number) => j !== i) } };
                      setTimeline(next); setTimelineDirty(true);
                    }}>Remove</button>
                  </div>
                ))}
                <div className="row" style={{ gap: 10, flexWrap: "wrap", alignItems: "center", marginTop: 8 }}>
                  <button className="secondary" onClick={() => {
                    const next = { ...timeline, camera: { ...(timeline.camera || {}), keyframes: [ ...(timeline.camera?.keyframes || []), { t: 0, zoom: 1.0, pan_x: 0, pan_y: 0, rotation_deg: 0 } ] } };
                    setTimeline(next); setTimelineDirty(true);
                  }}>Add camera keyframe</button>

                  <button className="primary" disabled={!timelineDirty} onClick={async () => {
                    try {
                      const saved = await postProjectMutation(`/v1/projects/${projectId}/timeline`, { timeline });
                      setTimeline(saved.timeline);
                      setTimelineDirty(false);
                      await refreshProject(projectId);
                    } catch (e: any) {
                      setErr(String(e));
                    }
                  }}>Save timeline</button>

                  {timelineDirty ? <span className="small" style={{ opacity: 0.75 }}>Unsaved changes</span> : null}
                </div>
              </div>

              <div className="row" style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
                <div style={{ minWidth: 320, flex: 2 }}>
                  <div className="small" style={{ fontWeight: 800 }}>Studio still model</div>
                  <select value={selectedStillModel?.id || ""} onChange={(e) => setSelectedStillModelId(e.target.value)}>
                    {stillModels.map((m) => (
                      <option key={m.id} value={m.id}>
                        {`${m.name} • ${modelEngineLabel(m.engine || m.render?.engine, m.kind)} • ${modelFamilyLabel(m.family || m.render?.family)}${installedModels[m.id] === false ? " (not installed)" : ""}`}
                      </option>
                    ))}
                  </select>
                </div>
                <div style={{ minWidth: 260, flex: 1 }}>
                  <div className="small" style={{ fontWeight: 800 }}>Manual checkpoint override</div>
                  <input
                    value={checkpointName}
                    onChange={(e) => setCheckpointName(e.target.value)}
                    placeholder={
                      selectedStillEngine === "comfyui"
                        ? selectedStillModel?.render?.checkpoint_name || "leave blank for catalog default"
                        : "internal models use the selected diffusers asset"
                    }
                    disabled={selectedStillEngine !== "comfyui"}
                  />
                </div>
                <div className="small" style={{ opacity: 0.8, flex: 1, minWidth: 260 }}>
                  {selectedStillEngine === "internal"
                    ? "Studio routes this still model through the internal diffusers adapter and validates workflow compatibility before enqueue."
                    : selectedStillIsTensorRT
                      ? "Studio routes this still model through the standalone SD1.5 TensorRT runtime bundle and validates the 512x512 batch-1 engine profile before enqueue."
                    : "Studio routes this still model through ComfyUI checkpoints and exports matching workflows when requested."}
                </div>
              </div>
              <div className="small" style={{ marginTop: 8, opacity: 0.85 }}>
                Active still engine: <b>{modelEngineLabel(selectedStillEngine, selectedStillModel?.kind)}</b> • family <b>{modelFamilyLabel(selectedStillFamily)}</b>
                {installedModels[selectedStillModel?.id || ""] === false ? <> • <span style={{ color: "var(--warning, #b58900)" }}>not installed locally</span></> : null}
              </div>
              {selectedStillFamily === "flux" ? (
                <div className="small" role="status" style={{ marginTop: 8, color: "var(--warning, #b58900)" }}>
                  FLUX.1 Schnell is a native still/keyframe renderer. Studio limits this phase-one path to text-to-image, 1–4 steps, and guidance 0. GPUs below 16 GB VRAM use sequential CPU offload; a 6 GB GPU needs substantial system memory/pagefile and will render slowly. Animate the saved image with SVD, Wan, or layered motion.
                </div>
              ) : null}
              <div className="card" style={{ marginTop: 10 }}>
                <div style={{ fontWeight: 900, marginBottom: 8 }}>Generation settings</div>
                <div className="row" style={{ gap: 10, flexWrap: "wrap", alignItems: "center" }}>
                  <div style={{ minWidth: 120 }}>
                    <div className="small">Width</div>
                    <input type="number" min={256} max={2048} step={64} value={renderWidth} onChange={(e) => setRenderWidth(Number(e.target.value))} />
                  </div>
                  <div style={{ minWidth: 120 }}>
                    <div className="small">Height</div>
                    <input type="number" min={256} max={2048} step={64} value={renderHeight} onChange={(e) => setRenderHeight(Number(e.target.value))} />
                  </div>
                  <div style={{ minWidth: 120 }}>
                    <div className="small">Steps</div>
                    <input
                      type="number"
                      min={1}
                      max={selectedStillFamily === "flux" ? 4 : 80}
                      step={1}
                      value={renderSteps}
                      onChange={(e) => {
                        const next = Number(e.target.value);
                        setRenderSteps(selectedStillFamily === "flux" ? Math.max(1, Math.min(4, next || 1)) : next);
                      }}
                    />
                  </div>
                  <div style={{ minWidth: 120 }}>
                    <div className="small">CFG</div>
                    <input
                      type="number"
                      min={selectedStillFamily === "flux" ? 0 : 1}
                      max={selectedStillFamily === "flux" ? 0 : 20}
                      step={0.1}
                      value={selectedStillFamily === "flux" ? 0 : renderCfg}
                      disabled={selectedStillFamily === "flux"}
                      onChange={(e) => setRenderCfg(Number(e.target.value))}
                    />
                  </div>
                  <div style={{ minWidth: 180 }}>
                    <div className="small">Sampler</div>
                    <select value={renderSampler} onChange={(e) => setRenderSampler(e.target.value)}>
                      {SAMPLER_OPTIONS.map((option) => (
                        <option key={option} value={option}>{option}</option>
                      ))}
                    </select>
                  </div>
                  <div style={{ minWidth: 160 }}>
                    <div className="small">Base seed</div>
                    <input
                      value={renderSeed}
                      onChange={(e) => setRenderSeed(e.target.value)}
                      placeholder="leave blank for auto"
                    />
                  </div>
                </div>
                <div style={{ marginTop: 10 }}>
                  <div className="small" style={{ marginBottom: 4 }}>Negative prompt</div>
                  <textarea
                    style={{ width: "100%", minHeight: 72 }}
                    value={renderNegativePrompt}
                    onChange={(e) => setRenderNegativePrompt(e.target.value)}
                  />
                </div>
                <div style={{ marginTop: 10 }}>
                  <div style={{ fontWeight: 800, marginBottom: 6 }}>LoRAs</div>
                  <div className="row" style={{ gap: 10, flexWrap: "wrap", alignItems: "center" }}>
                    <select value={loraToAdd} onChange={(e) => setLoraToAdd(e.target.value)} disabled={!loraModels.length}>
                      {loraModels.length ? (
                        loraModels.map((m) => (
                          <option key={m.id} value={m.id}>{m.name}</option>
                        ))
                      ) : (
                        <option value="">No installed LoRAs</option>
                      )}
                    </select>
                    <button className="secondary" onClick={addSelectedLora} disabled={!loraModels.length}>
                      Add LoRA
                    </button>
                    <div className="small" style={{ opacity: 0.8 }}>
                      Import LoRAs from Models to make them selectable here.
                    </div>
                  </div>
                  {selectedLoras.length ? (
                    <div style={{ display: "grid", gap: 8, marginTop: 10 }}>
                      {selectedLoras.map((item) => (
                        <div key={item.name} className="row" style={{ gap: 10, flexWrap: "wrap", alignItems: "center" }}>
                          <div style={{ minWidth: 280 }}>{item.label}</div>
                          <label className="small">Weight</label>
                          <input
                            type="number"
                            min={-4}
                            max={4}
                            step={0.05}
                            value={item.weight}
                            onChange={(e) => {
                              const nextWeight = Number(e.target.value);
                              setSelectedLoras((current) => current.map((entry) => (
                                entry.name === item.name ? { ...entry, weight: nextWeight } : entry
                              )));
                            }}
                            style={{ width: 110 }}
                          />
                          <button className="secondary" onClick={() => removeSelectedLora(item.name)}>
                            Remove
                          </button>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="small" style={{ marginTop: 8, opacity: 0.82 }}>
                      No LoRAs attached. Scene prompts will run against the selected base model only.
                    </div>
                  )}
                </div>
              </div>
                <div className="row" style={{ alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <label className="small">Mode</label>
                <select value={renderMode} onChange={(e) => setRenderMode(e.target.value as any)}>
                  <option value="stills">Stills (1 image/scene)</option>
                  <option value="motion_ad">Motion (AnimateDiff)</option>
                  <option value="motion_svd">Motion (SVD img2vid)</option>
                </select>

                {renderMode !== "stills" && (
                  <>
                    <label className="small">FPS</label>
                    <input style={{ width: 80 }} type="number" value={motionFps} onChange={(e) => setMotionFps(Number(e.target.value))} />
                    <label className="small">Max frames/scene</label>
                    <input style={{ width: 110 }} type="number" value={maxFramesPerScene} onChange={(e) => setMaxFramesPerScene(Number(e.target.value))} />
                  </>
                )}

                {renderMode === "stills" && (
                  <>
                    <label className="small">Still workflow</label>
                    <select value={stillWorkflow} onChange={(e) => setStillWorkflow(e.target.value as any)}>
                      {canStillTxt2img ? <option value="txt2img">Text-to-image</option> : null}
                      {canStillImg2img ? <option value="img2img">Image-to-image</option> : null}
                      {canStillInpaint ? <option value="inpaint">Inpaint</option> : null}
                      {canStillOutpaint ? <option value="outpaint">Outpaint</option> : null}
                      {canStillControlnet ? <option value="controlnet">ControlNet</option> : null}
                    </select>
                  </>
                )}

                {renderMode === "motion_ad" && (
                  <>
                    <label className="small">Base model</label>
                    <select value={selectedMotionModel?.id || ""} onChange={(e) => setSelectedMotionModelId(e.target.value)}>
                      {comfyStillModels.map((m) => (
                        <option key={m.id} value={m.id}>{m.name}</option>
                      ))}
                    </select>
                    <label className="small">Context</label>
                    <input style={{ width: 80 }} type="number" value={motionContextLength} onChange={(e) => setMotionContextLength(Number(e.target.value))} />
                    <label className="small">Overlap</label>
                    <input style={{ width: 80 }} type="number" value={motionContextOverlap} onChange={(e) => setMotionContextOverlap(Number(e.target.value))} />
                  </>
                )}

                {renderMode === "motion_svd" && (
                  <>
                    <label className="small">Base model</label>
                    <select value={selectedMotionModel?.id || ""} onChange={(e) => setSelectedMotionModelId(e.target.value)}>
                      {comfyStillModels.map((m) => (
                        <option key={m.id} value={m.id}>{m.name}</option>
                      ))}
                    </select>
                    <label className="small">SVD model</label>
                    <select value={selectedSvdModel?.id || ""} onChange={(e) => setSelectedSvdModelId(e.target.value)}>
                      {svdModels.map((m) => (
                        <option key={m.id} value={m.id}>{m.name}</option>
                      ))}
                    </select>
                  </>
                )}
              </div>

              {renderMode === "stills" ? (
                <div className="card" style={{ marginTop: 10 }}>
                  <div style={{ fontWeight: 900, marginBottom: 8 }}>Workflow inputs</div>
                  {(stillWorkflow === "img2img" || stillWorkflow === "inpaint" || stillWorkflow === "outpaint") ? (
                    <>
                      <div className="row" style={{ gap: 10, flexWrap: "wrap", alignItems: "center" }}>
                        <div style={{ minWidth: 340, flex: 2 }}>
                          <div className="small">Source image asset</div>
                          <select value={sourceAsset} onChange={(e) => setSourceAsset(e.target.value)}>
                            <option value="">Select project reference</option>
                            {sourceImageOptions.map((asset) => (
                              <option key={asset.path} value={asset.path}>{asset.path}</option>
                            ))}
                          </select>
                        </div>
                        <div style={{ minWidth: 140 }}>
                          <div className="small">Denoise strength</div>
                          <input
                            type="number"
                            min={0}
                            max={1}
                            step={0.05}
                            value={denoiseStrength}
                            onChange={(e) => setDenoiseStrength(Number(e.target.value))}
                          />
                        </div>
                        <div style={{ minWidth: 220 }}>
                          <div className="small">Upload new source</div>
                          <input type="file" accept="image/*" onChange={(e) => setReferenceUploadFile(e.target.files?.[0] || null)} />
                        </div>
                        <button className="secondary" disabled={!referenceUploadFile || !projectId} onClick={uploadReferenceAsset}>Upload source</button>
                        <button
                          className="secondary"
                          disabled={!sourceAsset || !projectId}
                          onClick={() => setEditorBgPath(sourceAsset || "")}
                        >
                          Use source as stage background
                        </button>
                      </div>
                      {sourceAsset ? (
                        <div className="row" style={{ gap: 12, flexWrap: "wrap", alignItems: "flex-start", marginTop: 10 }}>
                          <div style={{ width: 180 }}>
                            <img src={sourceAssetPreviewUrl} style={{ width: "100%", borderRadius: 12, border: "1px solid var(--border)" }} />
                            <div className="small" style={{ marginTop: 6, opacity: 0.82 }}>Source preview</div>
                          </div>
                          <div className="small" style={{ maxWidth: 420, opacity: 0.85 }}>
                            Studio keeps this workflow asset-driven. If you need to paint or align a mask, load the source into the stage background here and use the mask tools further down the page, then come back and select the saved project mask.
                          </div>
                        </div>
                      ) : null}
                      {(stillWorkflow === "inpaint" || stillWorkflow === "outpaint") ? (
                        <div className="row" style={{ gap: 10, flexWrap: "wrap", alignItems: "center", marginTop: 10 }}>
                          <div style={{ minWidth: 340, flex: 2 }}>
                            <div className="small">{stillWorkflow === "outpaint" ? "Optional mask override" : "Inpaint mask asset"}</div>
                            <select value={stillMaskAsset} onChange={(e) => setStillMaskAsset(e.target.value)}>
                              <option value="">{stillWorkflow === "outpaint" ? "Generate mask from outpaint margins" : "Select project mask"}</option>
                              {maskAssets.map((asset: string) => (
                                <option key={asset} value={asset}>{asset}</option>
                              ))}
                            </select>
                          </div>
                          <div style={{ minWidth: 220 }}>
                            <div className="small">Upload new mask</div>
                            <input type="file" accept="image/*" onChange={(e) => setWorkflowMaskUploadFile(e.target.files?.[0] || null)} />
                          </div>
                          <button className="secondary" disabled={!workflowMaskUploadFile || !projectId} onClick={uploadWorkflowMask}>Upload mask</button>
                          <button className="secondary" onClick={loadEditorBackground} disabled={!projectId}>Use latest output as stage background</button>
                        </div>
                      ) : null}
                      {(stillWorkflow === "inpaint" || stillWorkflow === "outpaint") && stillMaskAsset ? (
                        <div className="row" style={{ gap: 12, flexWrap: "wrap", alignItems: "flex-start", marginTop: 10 }}>
                          <div style={{ width: 180 }}>
                            <img src={maskAssetPreviewUrl} style={{ width: "100%", borderRadius: 12, border: "1px solid var(--border)" }} />
                            <div className="small" style={{ marginTop: 6, opacity: 0.82 }}>
                              {stillWorkflow === "outpaint" ? "Mask override preview" : "Mask preview"}
                            </div>
                          </div>
                          <div className="small" style={{ maxWidth: 420, opacity: 0.85 }}>
                            Bright areas are preserved as editable regions in the backend inpaint pass. For outpaint, leaving this empty keeps the automatic edge-expansion mask path.
                          </div>
                        </div>
                      ) : null}
                      {stillWorkflow === "outpaint" ? (
                        <>
                          <div className="row" style={{ gap: 10, flexWrap: "wrap", alignItems: "center", marginTop: 10 }}>
                            <div style={{ minWidth: 120 }}>
                              <div className="small">Expand top</div>
                              <input type="number" min={0} max={4096} step={32} value={outpaint.top_px} onChange={(e) => setOutpaint((current) => ({ ...current, top_px: Number(e.target.value) }))} />
                            </div>
                            <div style={{ minWidth: 120 }}>
                              <div className="small">Expand right</div>
                              <input type="number" min={0} max={4096} step={32} value={outpaint.right_px} onChange={(e) => setOutpaint((current) => ({ ...current, right_px: Number(e.target.value) }))} />
                            </div>
                            <div style={{ minWidth: 120 }}>
                              <div className="small">Expand bottom</div>
                              <input type="number" min={0} max={4096} step={32} value={outpaint.bottom_px} onChange={(e) => setOutpaint((current) => ({ ...current, bottom_px: Number(e.target.value) }))} />
                            </div>
                            <div style={{ minWidth: 120 }}>
                              <div className="small">Expand left</div>
                              <input type="number" min={0} max={4096} step={32} value={outpaint.left_px} onChange={(e) => setOutpaint((current) => ({ ...current, left_px: Number(e.target.value) }))} />
                            </div>
                          </div>
                          <div className="small" style={{ marginTop: 8, opacity: 0.82 }}>
                            If no mask override is selected, the backend expands the canvas and generates an outpaint mask from these margins.
                          </div>
                        </>
                      ) : null}
                    </>
                  ) : null}

                  {stillWorkflow === "controlnet" ? (
                    <>
                      <div className="row" style={{ justifyContent: "space-between", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
                        <div>
                          <div style={{ fontWeight: 800 }}>ControlNet units</div>
                          <div className="small" style={{ opacity: 0.82 }}>
                            Attach one or more conditioning units. Studio validates engine and family compatibility before enqueue.
                          </div>
                        </div>
                        <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                          <input type="file" accept="image/*" onChange={(e) => setReferenceUploadFile(e.target.files?.[0] || null)} />
                          <button className="secondary" disabled={!referenceUploadFile || !projectId} onClick={uploadReferenceAsset}>Upload reference</button>
                          <button className="secondary" onClick={addControlnetUnit} disabled={!!controlnetBlockedReason}>Add ControlNet unit</button>
                        </div>
                      </div>
                      {controlnetBlockedReason ? (
                        <div className="small" style={{ marginTop: 8, color: "var(--warning, #b58900)" }}>
                          {controlnetBlockedReason}
                        </div>
                      ) : null}
                      {controlnetUnits.length ? (
                        <div style={{ display: "grid", gap: 10, marginTop: 10 }}>
                          {controlnetUnits.map((unit, index) => (
                            <div key={unit.key} className="card" style={{ marginTop: 0 }}>
                              <div className="row" style={{ justifyContent: "space-between", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
                                <div>
                                  <div style={{ fontWeight: 800 }}>Unit {index + 1}</div>
                                  <div className="small" style={{ opacity: 0.78 }}>
                                    {unit.reference_asset ? `${unit.conditioning_mode} • ${unit.reference_asset.split("/").slice(-1)[0]}` : "Select a conditioning reference to complete this unit."}
                                  </div>
                                </div>
                                <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                                  <button className="secondary" onClick={() => moveControlnetUnit(unit.key, -1)} disabled={index === 0}>Move up</button>
                                  <button className="secondary" onClick={() => moveControlnetUnit(unit.key, 1)} disabled={index === controlnetUnits.length - 1}>Move down</button>
                                  <button className="secondary" onClick={() => duplicateControlnetUnit(unit.key)}>Duplicate</button>
                                  <button className="secondary" onClick={() => removeControlnetUnit(unit.key)}>Remove unit</button>
                                </div>
                              </div>
                              <div className="row" style={{ gap: 10, flexWrap: "wrap", alignItems: "center", marginTop: 10 }}>
                                <div style={{ minWidth: 320, flex: 2 }}>
                                  <div className="small">ControlNet model</div>
                                  <select
                                    value={unit.model}
                                    onChange={(e) => {
                                      const nextModelId = e.target.value;
                                      const nextModel = controlnetModels.find((model) => model.id === nextModelId);
                                      updateControlnetUnit(unit.key, {
                                        model: nextModelId,
                                        conditioning_mode: (nextModel?.render?.conditioning_mode as ConditioningMode) || unit.conditioning_mode || "raw",
                                      });
                                    }}
                                  >
                                    <option value="">Select ControlNet model</option>
                                    {controlnetModels.map((m) => (
                                      <option key={m.id} value={m.id} disabled={!isControlnetCompatible(m)}>
                                        {`${m.name} • ${modelEngineLabel(m.engine || m.render?.engine, m.kind)} • ${modelFamilyLabel(m.family || m.render?.family)}${isControlnetCompatible(m) ? "" : " (incompatible)"}`}
                                      </option>
                                    ))}
                                  </select>
                                </div>
                                <div style={{ minWidth: 180 }}>
                                  <div className="small">Conditioning mode</div>
                                  <select value={unit.conditioning_mode} onChange={(e) => updateControlnetUnit(unit.key, { conditioning_mode: e.target.value as ConditioningMode })}>
                                    <option value="raw">Raw image</option>
                                    <option value="blur">Blur pass</option>
                                    <option value="edge">Edge map</option>
                                    <option value="external">External-prepared map</option>
                                  </select>
                                </div>
                                <div style={{ minWidth: 120 }}>
                                  <div className="small">Strength</div>
                                  <input type="number" min={0} max={2} step={0.05} value={unit.strength} onChange={(e) => updateControlnetUnit(unit.key, { strength: Number(e.target.value) })} />
                                </div>
                                <div style={{ minWidth: 120 }}>
                                  <div className="small">Start %</div>
                                  <input type="number" min={0} max={1} step={0.05} value={unit.start_percent} onChange={(e) => updateControlnetUnit(unit.key, { start_percent: Number(e.target.value) })} />
                                </div>
                                <div style={{ minWidth: 120 }}>
                                  <div className="small">End %</div>
                                  <input type="number" min={0} max={1} step={0.05} value={unit.end_percent} onChange={(e) => updateControlnetUnit(unit.key, { end_percent: Number(e.target.value) })} />
                                </div>
                              </div>
                              <div className="row" style={{ gap: 10, flexWrap: "wrap", alignItems: "center", marginTop: 10 }}>
                                <div style={{ minWidth: 340, flex: 2 }}>
                                  <div className="small">Reference image</div>
                                  <select value={unit.reference_asset} onChange={(e) => updateControlnetUnit(unit.key, { reference_asset: e.target.value })}>
                                    <option value="">Select project reference</option>
                                    {sourceImageOptions.map((asset) => (
                                      <option key={asset.path} value={asset.path}>{asset.path}</option>
                                    ))}
                                  </select>
                                </div>
                              </div>
                              {unit.reference_asset ? (
                                <div className="row" style={{ gap: 12, flexWrap: "wrap", alignItems: "flex-start", marginTop: 10 }}>
                                  <div style={{ width: 160 }}>
                                    <img src={fileUrl(projectId, unit.reference_asset)} style={{ width: "100%", borderRadius: 12, border: "1px solid var(--border)" }} />
                                    <div className="small" style={{ marginTop: 6, opacity: 0.8 }}>Reference preview</div>
                                  </div>
                                  <div className="small" style={{ maxWidth: 420, opacity: 0.82 }}>
                                    Conditioning runs in the listed order. Duplicate or reorder units when you want one structural pass to land before another.
                                  </div>
                                </div>
                              ) : null}
                            </div>
                          ))}
                        </div>
                      ) : (
                        <div className="small" style={{ marginTop: 10, opacity: 0.82 }}>
                          No ControlNet units attached yet.
                        </div>
                      )}
                    </>
                  ) : null}

                  <div className="card" style={{ marginTop: 12 }}>
                    <div style={{ fontWeight: 800, marginBottom: 8 }}>Enhancement passes</div>
                    <div className="row" style={{ gap: 10, flexWrap: "wrap", alignItems: "center" }}>
                      <label className="small row" style={{ gap: 6, alignItems: "center" }}>
                        <input
                          type="checkbox"
                          checked={hiresFix.enabled}
                          onChange={(e) => setHiresFix((current) => ({ ...current, enabled: e.target.checked }))}
                        />
                        Enable hires fix
                      </label>
                      <div className="small" style={{ opacity: 0.82 }}>
                        Renders the base still first, then runs a higher-resolution img2img refinement pass.
                      </div>
                    </div>
                    {hiresFix.enabled ? (
                      <div className="row" style={{ gap: 10, flexWrap: "wrap", alignItems: "center", marginTop: 10 }}>
                        <div style={{ minWidth: 120 }}>
                          <div className="small">Scale</div>
                          <input type="number" min={1} max={4} step={0.05} value={hiresFix.scale} onChange={(e) => setHiresFix((current) => ({ ...current, scale: Number(e.target.value) }))} />
                        </div>
                        <div style={{ minWidth: 120 }}>
                          <div className="small">Steps override</div>
                          <input type="number" min={0} max={80} step={1} value={hiresFix.steps} onChange={(e) => setHiresFix((current) => ({ ...current, steps: Number(e.target.value) }))} />
                        </div>
                        <div style={{ minWidth: 120 }}>
                          <div className="small">Denoise</div>
                          <input type="number" min={0} max={1} step={0.05} value={hiresFix.denoise} onChange={(e) => setHiresFix((current) => ({ ...current, denoise: Number(e.target.value) }))} />
                        </div>
                        <div style={{ minWidth: 220 }}>
                          <div className="small">Upscaler</div>
                          <select value={hiresFix.upscaler} onChange={(e) => setHiresFix((current) => ({ ...current, upscaler: e.target.value }))}>
                            {UPSCALER_OPTIONS.map((option) => (
                              <option key={option.value} value={option.value}>{option.label}</option>
                            ))}
                          </select>
                        </div>
                      </div>
                    ) : null}

                    <div className="row" style={{ gap: 10, flexWrap: "wrap", alignItems: "center", marginTop: 14 }}>
                      <label className="small row" style={{ gap: 6, alignItems: "center" }}>
                        <input
                          type="checkbox"
                          checked={refiner.enabled}
                          onChange={(e) => setRefiner((current) => ({ ...current, enabled: e.target.checked }))}
                        />
                        Enable refiner pass
                      </label>
                      <div className="small" style={{ opacity: 0.82 }}>
                        Optional second img2img pass. Leave the model empty to reuse the base still model.
                      </div>
                    </div>
                    {refiner.enabled ? (
                      <div className="row" style={{ gap: 10, flexWrap: "wrap", alignItems: "center", marginTop: 10 }}>
                        <div style={{ minWidth: 320, flex: 2 }}>
                          <div className="small">Refiner model</div>
                          <select value={refiner.model} onChange={(e) => setRefiner((current) => ({ ...current, model: e.target.value }))}>
                            <option value="">Reuse base still model</option>
                            {compatibleRefinerModels.map((model) => (
                              <option key={model.id} value={model.id}>
                                {`${model.name} • ${modelEngineLabel(model.engine || model.render?.engine, model.kind)} • ${modelFamilyLabel(model.family || model.render?.family)}`}
                              </option>
                            ))}
                          </select>
                        </div>
                        <div style={{ minWidth: 120 }}>
                          <div className="small">Switch at</div>
                          <input type="number" min={0} max={1} step={0.05} value={refiner.switch_at} onChange={(e) => setRefiner((current) => ({ ...current, switch_at: Number(e.target.value) }))} />
                        </div>
                        <div style={{ minWidth: 120 }}>
                          <div className="small">Steps override</div>
                          <input type="number" min={0} max={80} step={1} value={refiner.steps} onChange={(e) => setRefiner((current) => ({ ...current, steps: Number(e.target.value) }))} />
                        </div>
                      </div>
                    ) : null}
                  </div>
                </div>
              ) : null}

              {/* ── Unified smart video button ─────────────────────────── */}
              {videoRoute && videoRoute.route !== "none" && (
                <div style={{ marginTop: 10, padding: "10px 14px", borderRadius: 10, background: "var(--surface2,#f8f9fa)", border: "1px solid var(--line)" }}>
                  <div className="small" style={{ marginBottom: 8 }}>
                    <b>Smart video:</b>{" "}
                    {videoRoute.route === "local_gpu"
                      ? `🖥 Local GPU — ${videoRoute.local_detail?.device || "GPU"} (${videoRoute.local_detail?.vram_gb || 0} GB)`
                      : videoRoute.route === "azure_foundry_cloud"
                      ? `☁ Azure AI Foundry Cosmos3 — ${videoRoute.azure_foundry_detail?.deployment_name || "cosmos3-super"}`
                      : `☁ NVIDIA Cosmos Cloud — ${videoRoute.cosmos_detail?.model || "cosmos3"}`}
                    {" "}
                    <span style={{ opacity: 0.7 }}>({videoRoute.reason})</span>
                  </div>
                  <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                    <button onClick={() => renderVideoSmart()} disabled={!variantCount} style={{ fontWeight: 700 }}>
                      ▶ Generate Video (Auto)
                    </button>
                    {videoRoute.local_ready && (
                      <button className="secondary" onClick={() => renderVideoSmart("local_gpu")} disabled={!variantCount}>
                        Force GPU
                      </button>
                    )}
                    {videoRoute.cosmos_ready && (
                      <button className="secondary" onClick={() => renderVideoSmart("cosmos_cloud")} disabled={!variantCount}>
                        Force Cloud
                      </button>
                    )}
                    {videoRoute.azure_foundry_ready && (
                      <button className="secondary" onClick={() => renderVideoSmart("azure_foundry_cloud")} disabled={!variantCount}>
                        Force Azure Foundry
                      </button>
                    )}
                    <span className="small" style={{ opacity: 0.7, alignSelf: "center" }}>
                      Change preference in Settings → GPU / Render Runtime
                    </span>
                  </div>
                </div>
              )}
              {videoRoute?.route === "none" && (
                <div className="small" style={{ marginTop: 10, padding: "8px 12px", borderRadius: 8,
                  background: "var(--warning-bg,#fff3cd)", color: "var(--warning-text,#856404)" }}>
                  ⚠ No video generation route available. Enable CUDA in Settings, add your NVIDIA API key for Cosmos cloud, or configure Azure AI Foundry Cosmos3.
                </div>
              )}

              <div className="row" style={{ marginTop: 10, gap: 10, flexWrap: "wrap" }}>
                <button onClick={renderScenes} disabled={!variantCount || renderMode !== "stills"}>Enqueue still scenes</button>
                <button onClick={renderMotion} disabled={!variantCount || renderMode === "stills"}>Enqueue motion scenes</button>
                {cosmosReady ? (
                  <>
                    <button
                      onClick={() => renderCosmosAll(false)}
                      disabled={!variantCount}
                      title="Generate a video clip for every scene using NVIDIA Cosmos text-to-video"
                    >
                      ⚡ Cosmos: All scenes (text→video)
                    </button>
                    <button
                      className="secondary"
                      onClick={() => renderCosmosAll(true)}
                      disabled={!variantCount}
                      title="Use rendered keyframes as init images for Cosmos image-to-video"
                    >
                      Cosmos: From keyframes (img→video)
                    </button>
                    <span className="row" style={{ gap: 6, alignItems: "center" }}>
                      <label className="small" htmlFor="cosmos-scene-index">Scene #</label>
                      <input
                        id="cosmos-scene-index"
                        type="number"
                        min={0}
                        max={sceneCount ? sceneCount - 1 : 0}
                        value={cosmosSceneIndex}
                        onChange={(e) => setCosmosSceneIndex(Math.max(0, Number(e.target.value) || 0))}
                        disabled={!variantCount}
                        style={{ width: 64 }}
                      />
                      <button
                        className="secondary"
                        onClick={() => renderCosmosScene(cosmosSceneIndex, false)}
                        disabled={!variantCount || cosmosSceneIndex >= sceneCount}
                        title="Generate a Cosmos video clip for just this one scene"
                      >
                        Cosmos: This scene
                      </button>
                    </span>
                  </>
                ) : null}
                {azureFoundryReady ? (
                  <>
                    <button
                      onClick={() => renderAzureFoundryAll(false)}
                      disabled={!variantCount}
                      title="Generate a video clip for every scene using Azure AI Foundry Cosmos3 (text→video)"
                    >
                      ☁ Azure Foundry: All scenes (text→video)
                    </button>
                    <button
                      className="secondary"
                      onClick={() => renderAzureFoundryAll(true)}
                      disabled={!variantCount}
                      title="Use rendered keyframes as init images for Azure Foundry Cosmos3 image-to-video"
                    >
                      Azure Foundry: From keyframes (img→video)
                    </button>
                    <span className="row" style={{ gap: 6, alignItems: "center" }}>
                      <label className="small" htmlFor="azure-foundry-scene-index">Scene #</label>
                      <input
                        id="azure-foundry-scene-index"
                        type="number"
                        min={0}
                        max={sceneCount ? sceneCount - 1 : 0}
                        value={azureFoundrySceneIndex}
                        onChange={(e) => setAzureFoundrySceneIndex(Math.max(0, Number(e.target.value) || 0))}
                        disabled={!variantCount}
                        style={{ width: 64 }}
                      />
                      <button
                        className="secondary"
                        onClick={() => renderAzureFoundryScene(azureFoundrySceneIndex, false)}
                        disabled={!variantCount || azureFoundrySceneIndex >= sceneCount}
                        title="Generate an Azure Foundry Cosmos3 video clip for just this one scene"
                      >
                        Azure Foundry: This scene
                      </button>
                    </span>
                  </>
                ) : null}
                {fireflyVisible ? (
                  <>
                    <button
                      onClick={renderFireflyScenes}
                      disabled={!variantCount}
                      title={renderProviders?.firefly?.custom_model_id
                        ? `Generate with Adobe Firefly custom model: ${renderProviders.firefly.custom_model_id}`
                        : "Generate keyframes with Adobe Firefly Image 3"}
                    >
                      🔥 Render with Firefly
                    </button>
                    <button
                      className="secondary"
                      onClick={assembleFirefly}
                      disabled={!variantCount}
                      title="Assemble Firefly stills into a final MP4 (run Render with Firefly first)"
                    >
                      Assemble Firefly video
                    </button>
                  </>
                ) : null}
                {imagineartVisible ? (
                  <>
                    <button
                      onClick={renderImagineartScenes}
                      disabled={!variantCount}
                      title={`Generate keyframes with ImagineArt (${renderProviders?.imagineart?.image_style || "imagine-turbo"})`}
                    >
                      ✨ Render with ImagineArt
                    </button>
                    <button
                      className="secondary"
                      onClick={assembleImagineart}
                      disabled={!variantCount}
                      title="Assemble ImagineArt stills into a final MP4 (run Render with ImagineArt first)"
                    >
                      Assemble ImagineArt video
                    </button>
                    {renderProviders?.imagineart?.video_enabled ? (
                      <button
                        className="secondary"
                        onClick={() => renderImagineartVideo(undefined, false)}
                        disabled={!variantCount}
                        title={`Generate native ImagineArt video clips (${renderProviders?.imagineart?.video_style || "kling-1.0-pro"})`}
                      >
                        ImagineArt native video
                      </button>
                    ) : null}
                  </>
                ) : null}
                {selectedStillIsTensorRT ? (
                  <>
                    <div className="row" style={{ alignItems: "center", gap: 8, background: "var(--bg-card)", padding: "4px 8px", borderRadius: 4, border: "1px solid var(--border-color)" }}>
                      <label style={{ fontSize: "0.8em", fontWeight: 700 }}>TRT Batch Size:</label>
                      <select disabled={!selectedStillTensorRtReady} value={trtBatchSize} onChange={(e) => setTrtBatchSize(Number(e.target.value))} style={{ padding: "2px 4px", fontSize: "0.8em" }}>
                        {[1, 2, 4, 8].map((size) => (
                          <option key={size} value={size} disabled={selectedStillIsTensorRT && size > selectedTrtMaxBatch}>
                            {size}{selectedStillIsTensorRT && size > selectedTrtMaxBatch ? " (rebuild required)" : ""}
                          </option>
                        ))}
                      </select>
                      <label style={{ fontSize: "0.8em", fontWeight: 700, marginLeft: 8 }}>Live Preview:</label>
                      <input
                        type="checkbox"
                        checked={trtLivePreview}
                        disabled={!selectedStillTensorRtReady || !selectedTrtSupportsLivePreview}
                        title={!selectedStillInstalled ? "Install and verify this TensorRT bundle in Models first." : !selectedTrtSupportsLivePreview ? "Standalone TensorRT preview is disabled because this engine takes too long to deserialize synchronously." : undefined}
                        onChange={(e) => setTrtLivePreview(e.target.checked)}
                      />
                      {trtPreviewLoading && <span style={{ fontSize: "0.8em", opacity: 0.5 }}>...</span>}
                    </div>
                    <div className="small" style={{ width: "100%", marginTop: -4, opacity: 0.78 }}>
                      TensorRT still profile: <b>{selectedTrtProfileWidth || renderWidth}x{selectedTrtProfileHeight || renderHeight}</b>
                      {" "}• max batch <b>{selectedTrtMaxBatch || 1}</b>
                      {" "}• engine <b>{modelFamilyLabel(selectedStillFamily)}</b>
                      {" "}• bundle <b>{selectedStillInstalled ? "verified" : "not installed"}</b>
                    </div>
                    <button
                      className="secondary"
                      onClick={runTensorrtStandalone}
                      disabled={!variantCount || !selectedStillTensorRtReady}
                      title={selectedStillInstalled ? "Render one still image with the compiled SD1.5 TensorRT standalone engine" : "Install and verify this TensorRT bundle in Models first."}
                    >
                      Render TensorRT still
                    </button>
                    {trtLivePreview && trtPreviewImage && (
                      <div style={{ position: "fixed", bottom: 20, right: 20, zIndex: 9999, border: "2px solid #00f", borderRadius: 8, padding: 4, background: "#000" }}>
                        <img src={trtPreviewImage} alt="Live Preview" style={{ maxWidth: 256, maxHeight: 256, display: "block" }} />
                      </div>
                    )}
                  </>
                ) : null}
                <button className="secondary" onClick={tickWorker}>Tick worker (run 1 job)</button>
                <button className="secondary" onClick={refreshValidate}>Validate capabilities</button>
              </div>
              {cosmosReady && (
                <div className="small" style={{ marginTop: 6, opacity: 0.82 }}>
                  NVIDIA Cosmos: <b>ready</b> • model <b>{renderProviders?.cosmos?.model}</b>
                  {" "}• {renderProviders?.cosmos?.num_frames} frames @ {renderProviders?.cosmos?.fps} fps
                  {" "}• ~{Math.round((renderProviders?.cosmos?.num_frames || 121) / (renderProviders?.cosmos?.fps || 24))}s clip per scene
                </div>
              )}
              {azureFoundryReady && (
                <div className="small" style={{ marginTop: 6, opacity: 0.82 }}>
                  Azure AI Foundry Cosmos3: <b>ready</b> • deployment <b>{renderProviders?.azure_foundry?.deployment_name}</b>
                  {" "}• {renderProviders?.azure_foundry?.num_frames} frames @ {renderProviders?.azure_foundry?.fps} fps
                  {" "}• ~{Math.round((renderProviders?.azure_foundry?.num_frames || 121) / (renderProviders?.azure_foundry?.fps || 24))}s clip per scene
                </div>
              )}
              {fireflyVisible && (
                <div className="small" style={{ marginTop: 6, opacity: 0.82 }}>
                  Adobe Firefly: <b>{renderProviders?.firefly?.configured ? "configured" : "not configured"}</b>
                  {renderProviders?.firefly?.custom_model_id
                    ? <> • custom model <code>{renderProviders.firefly.custom_model_id}</code></>
                    : <> • using standard Firefly Image 3</>}
                </div>
              )}
              {imagineartVisible && (
                <div className="small" style={{ marginTop: 6, opacity: 0.82 }}>
                  ImagineArt: <b>{renderProviders?.imagineart?.configured ? "configured" : "not configured"}</b>
                  {" "}• image <b>{renderProviders?.imagineart?.image_style}</b>
                  {" "}• video <b>{renderProviders?.imagineart?.video_enabled ? renderProviders.imagineart.video_style : "disabled"}</b>
                </div>
              )}

              {validate?.recommended?.diagnostics?.length ? (
                <div className="card" style={{ marginTop: 10 }}>
                  <div style={{ fontWeight: 800, marginBottom: 8 }}>Validation</div>
                  {validate.recommended.diagnostics.map((x: any, i: number) => (
                    <div key={i} className="small">• {x}</div>
                  ))}
                </div>
              ) : null}
            </div>
          </details>

          <hr />
          <div style={{ fontWeight: 800, marginBottom: 10 }}>Exports</div>
          <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>
            <button onClick={verifyEdmg}>Verify EDMG Core</button>
            <button className="secondary" onClick={exportDeforum} disabled={!variantCount}>Export Deforum JSON</button>
            <button className="secondary" onClick={exportComfyWorkflows} disabled={!variantCount || selectedStillEngine === "internal"}>Export ComfyUI workflows</button>
          </div>
          {selectedStillEngine === "internal" ? (
            <div className="small" style={{ marginTop: 8, opacity: 0.82 }}>
              ComfyUI workflow export is disabled while an internal still model is selected.
            </div>
          ) : null}

          {deforumExports.length > 0 && (
            <div style={{ marginTop: 10 }}>
              <div className="small">Latest Deforum exports</div>
              {deforumExports.slice(-3).map((p: string) => (
                <div key={p} className="small"><a href={fileUrl(projectId, p)} target="_blank" rel="noreferrer">{p}</a></div>
              ))}
            </div>
          )}

          {comfyExports.length > 0 && (
            <div style={{ marginTop: 10 }}>
              <div className="small">Latest ComfyUI workflow exports</div>
              {comfyExports.slice(-3).map((p: string) => (
                <div key={p} className="small"><a href={fileUrl(projectId, p)} target="_blank" rel="noreferrer">{p}</a></div>
              ))}
            </div>
          )}

          {err && <div style={{ marginTop: 12, color: "var(--danger)" }}>{err}</div>}
        </div>

        <div className="card">
          <CreativeDirectionPanel
            projectId={projectId}
            analysis={analysis}
            plan={plan}
            selectedVariant={selectedVariant}
            compact
            onNavigate={onNavigate}
          />

          <hr />
          <div style={{ fontWeight: 800, marginBottom: 10 }}>Render readiness</div>
          <div className="small">
            Audio analysis: {analysis ? "✓" : "×"} • Plan variants: {variantCount ? "✓" : "×"}
          </div>
          <div className="small" style={{ marginTop: 8 }}>
            If motion isn’t available, the system will automatically fall back to stills and assemble a slideshow MP4.
          </div>

          <hr />
          <div style={{ fontWeight: 800, marginBottom: 10 }}>Capabilities</div>
          {!caps && <div className="small">Loading…</div>}
          {caps && <StructuredSummary value={caps} showJson />}
          {conductorEnvironment ? (
            <>
              <div style={{ fontWeight: 800, margin: "14px 0 10px" }}>Conductor environment</div>
              <StructuredSummary value={conductorEnvironment} showJson />
            </>
          ) : null}
          <div style={{ marginTop: 14 }}>
            <VisualDnaPanel projectId={projectId} compact />
          </div>

          <hr />
          <div style={{ fontWeight: 800, marginBottom: 10 }}>Last action result</div>
          {!info && <div className="small">No recent action.</div>}
          {info && <StructuredSummary value={info} showJson />}
        </div>
      </div>

    </div>
  );
}
type RenderProps = {
  backendUrl?: string;
  onNavigate?: PageProps["onNavigate"];
};
