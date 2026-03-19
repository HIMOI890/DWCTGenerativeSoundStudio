import React, { useEffect, useMemo, useState } from "react";
import { apiGet, apiPost, apiUpload, getBackendUrl } from "../components/api";
import { OverlayStage } from "../components/OverlayStage";
import { useUiMode } from "../components/uiMode";
import { readRenderDefaults } from "../components/renderDefaults";
import { copyPathValue, desktopActionLabel, runDesktopArtifactAction } from "../components/desktopArtifacts";

export default function Render({ onNavigate }: { onNavigate?: (page: any) => void }) {
  const savedRenderDefaults = readRenderDefaults();
  const { mode: uiMode } = useUiMode();
  const backendUrl = useMemo(() => getBackendUrl(), []);

  const [projects, setProjects] = useState<any[]>([]);
  const [projectId, setProjectId] = useState<string>("");
  const [project, setProject] = useState<any>(null);

  const [plan, setPlan] = useState<any>(null);
  const [analysis, setAnalysis] = useState<any>(null);
  const [selectedVariant, setSelectedVariant] = useState<number>(0);

  const [renderPreset, setRenderPreset] = useState<"fast" | "balanced" | "quality" | "ultra">((savedRenderDefaults.renderPreset as any) || "balanced");
  const [checkpointName, setCheckpointName] = useState<string>("");
  const [renderMode, setRenderMode] = useState<"stills" | "motion_ad" | "motion_svd">("stills");
  const [motionFps, setMotionFps] = useState<number>(12);
  const [maxFramesPerScene, setMaxFramesPerScene] = useState<number>(240);
  const [motionContextLength, setMotionContextLength] = useState<number>(16);
  const [motionContextOverlap, setMotionContextOverlap] = useState<number>(4);

  const [internalFpsOut, setInternalFpsOut] = useState<number>(24);
  const [internalFpsRender, setInternalFpsRender] = useState<number>(2);
  const [internalKeyInterval, setInternalKeyInterval] = useState<number>(5);
  const [internalInterp, setInternalInterp] = useState<"auto"|"minterpolate"|"fps"|"rife">("auto");
  const [internalModelId, setInternalModelId] = useState<string>("auto");
  const [internalRenderTier, setInternalRenderTier] = useState<"auto"|"draft"|"balanced"|"quality">((savedRenderDefaults.internalRenderTier as any) || "auto");

  const [internalTemporalMode, setInternalTemporalMode] = useState<"off"|"keyframes"|"frame_img2img">("frame_img2img");
  const [internalTemporalStrength, setInternalTemporalStrength] = useState<number>(0.35);
  const [internalTemporalSteps, setInternalTemporalSteps] = useState<number>(12);
  const [internalRefineEvery, setInternalRefineEvery] = useState<number>(1);
  const [internalAnchorStrength, setInternalAnchorStrength] = useState<number>(0.2);
  const [internalPromptBlend, setInternalPromptBlend] = useState<boolean>(true);
  const [internalResumeExisting, setInternalResumeExisting] = useState<boolean>(savedRenderDefaults.internalResumeExisting ?? true);

  const [timeline, setTimeline] = useState<any>({ layers: [], camera: { keyframes: [] } });
  const [timelineDirty, setTimelineDirty] = useState<boolean>(false);

  const [selectedLayerIdxs, setSelectedLayerIdxs] = useState<number[]>([]);
  const [editMaskMode, setEditMaskMode] = useState<boolean>(false);
  const [editorBgUrl, setEditorBgUrl] = useState<string | null>(null);

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
  const [validate, setValidate] = useState<any>(null);
  const [internalPreflight, setInternalPreflight] = useState<any>(null);
  const [latestInternalJob, setLatestInternalJob] = useState<any>(null);
  const [latestInternalDetail, setLatestInternalDetail] = useState<any>(null);
  const [latestInternalLog, setLatestInternalLog] = useState<string>("");
  const [internalPolling, setInternalPolling] = useState<boolean>(true);

  const [info, setInfo] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);

  const latestInternalVideoPath = String(project?.meta?.last_internal_render?.video || "");
  const latestInternalVideoUrl = latestInternalVideoPath
    ? `${backendUrl}/v1/projects/${projectId}/file?path=${encodeURIComponent(latestInternalVideoPath)}`
    : "";

  const buildInternalPayload = () => ({
    variant_index: selectedVariant,
    fps_output: internalFpsOut,
    fps_render: internalFpsRender,
    keyframe_interval_s: internalKeyInterval,
    interpolation_engine: internalInterp,
    temporal_mode: internalTemporalMode,
    temporal_strength: internalTemporalStrength,
    temporal_steps: internalTemporalSteps,
    refine_every_n_frames: internalRefineEvery,
    anchor_strength: internalAnchorStrength,
    prompt_blend: internalPromptBlend,
    model_id: internalModelId,
    render_mode: "auto",
    render_tier: internalRenderTier,
    allow_proxy_fallback: true,
    resume_existing_frames: internalResumeExisting,
  });

  const refreshProjects = async () => {
    const d = await apiGet("/v1/projects");
    const ps = d.projects || [];
    setProjects(ps);
    if (!projectId && ps.length) setProjectId(ps[0].id);
  };

  const refreshProject = async (id: string) => {
    if (!id) return;
    const d = await apiGet(`/v1/projects/${id}`);
    setProject(d.project);
    setAnalysis(d.project?.meta?.analysis || null);
    setPlan(d.project?.meta?.last_plan || null);
    setTimeline(d.project?.meta?.timeline || { layers: [], camera: { keyframes: [] } });
    setTimelineDirty(false);
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

  const refreshInternalStatus = async () => {
    if (!projectId) return;
    try {
      const d = await apiGet(`/v1/projects/${projectId}/jobs`);
      const all = Array.isArray(d?.jobs) ? d.jobs : [];
      const latest = all.filter((j: any) => j?.type === "internal_video").sort((a: any, b: any) => String(b?.created_at || "").localeCompare(String(a?.created_at || "")))[0] || null;
      setLatestInternalJob(latest);
      if (latest) {
        const detail = await apiGet(`/v1/projects/${projectId}/jobs/${latest.id}?tail_lines=120`);
        setLatestInternalDetail(detail);
        setLatestInternalLog(String(detail?.log_tail || ""));
      } else {
        setLatestInternalDetail(null);
        setLatestInternalLog("");
      }
    } catch (e: any) {
      setLatestInternalJob(null);
      setLatestInternalDetail(null);
      setLatestInternalLog(String(e));
    }
  };

  useEffect(() => {
    refreshProjects().catch(() => {});
  }, []);

  useEffect(() => {
    apiGet("/v1/comfyui/capabilities").then(setCaps).catch(() => {});
    apiGet("/v1/hardware").then((d) => setHardware(d)).catch(() => {});
  }, [backendUrl]);

  useEffect(() => {
    if (projectId) refreshProject(projectId).catch(() => {});
  }, [projectId]);

  useEffect(() => {
    refreshValidate().catch(() => {});
  }, [projectId, selectedVariant, renderPreset]);

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
    internalRenderTier,
    internalTemporalMode,
    internalTemporalStrength,
    internalTemporalSteps,
    internalRefineEvery,
    internalAnchorStrength,
    internalPromptBlend,
    internalResumeExisting,
  ]);

  useEffect(() => {
    if (!projectId) return;
    refreshInternalStatus().catch(() => {});
    if (!internalPolling) return;
    const t = window.setInterval(() => {
      refreshInternalStatus().catch(() => {});
    }, 3000);
    return () => window.clearInterval(t);
  }, [projectId, internalPolling]);

  const runPipeline = async () => {
    setErr(null);
    setInfo(null);
    try {
      const d = await apiPost(`/v1/projects/${projectId}/pipeline/run?variant_index=${selectedVariant}&preset=${renderPreset}&mode=auto`, {});
      setInfo(d);
      await refreshProject(projectId);
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const runInternalVideo = async () => {
    setErr(null);
    setInfo(null);
    try {
      const d = await apiPost(`/v1/projects/${projectId}/render/internal/video`, buildInternalPayload());
      setInfo(d);
      await refreshProject(projectId);
      await refreshInternalStatus();
      await refreshInternalPreflight();
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const cancelLatestInternal = async () => {
    if (!projectId || !latestInternalJob?.id) return;
    setErr(null);
    try {
      const d = await apiPost(`/v1/projects/${projectId}/jobs/${latestInternalJob.id}/cancel`, {});
      setInfo(d);
      await refreshInternalStatus();
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const retryLatestInternal = async () => {
    if (!projectId || !latestInternalJob?.id) return;
    setErr(null);
    try {
      const d = await apiPost(`/v1/projects/${projectId}/jobs/${latestInternalJob.id}/retry`, {});
      setInfo(d);
      await refreshInternalStatus();
      await refreshInternalPreflight();
    } catch (e: any) {
      setErr(String(e));
    }
  };


  const resumeLatestInternal = async () => {
    if (!projectId || !latestInternalJob?.id) return;
    setErr(null);
    try {
      const d = await apiPost(`/v1/projects/${projectId}/jobs/${latestInternalJob.id}/resume_from_checkpoint`, {});
      setInfo(d);
      await refreshInternalStatus();
      await refreshInternalPreflight();
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const restartLatestInternalClean = async () => {
    if (!projectId || !latestInternalJob?.id) return;
    setErr(null);
    try {
      const d = await apiPost(`/v1/projects/${projectId}/jobs/${latestInternalJob.id}/restart_clean`, {});
      setInfo(d);
      await refreshInternalStatus();
      await refreshInternalPreflight();
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const clearLatestInternalCachedFrames = async () => {
    if (!projectId || !latestInternalJob?.id) return;
    setErr(null);
    try {
      const d = await apiPost(`/v1/projects/${projectId}/jobs/${latestInternalJob.id}/clear_cached_frames`, {});
      setInfo(d);
      await refreshProject(projectId);
      await refreshInternalStatus();
      await refreshInternalPreflight();
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const dropLatestInternalCheckpoint = async () => {
    if (!projectId || !latestInternalJob?.id) return;
    setErr(null);
    try {
      const d = await apiPost(`/v1/projects/${projectId}/jobs/${latestInternalJob.id}/drop_checkpoint`, {});
      setInfo(d);
      await refreshProject(projectId);
      await refreshInternalStatus();
      await refreshInternalPreflight();
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const copyPathToClipboard = async (label: string, value?: string | null) => {
    if (!value) return;
    setErr(null);
    try {
      const result = await copyPathValue(label, value);
      if (!result.ok) throw new Error(result.error || `Unable to copy ${label}`);
      setInfo({ ...result, copied: label, value });
    } catch (e: any) {
      setErr(`Failed to copy ${label}: ${String(e)}`);
    }
  };

  const revealLocalPath = async (label: string, value?: string | null, mode: "reveal" | "open" = "reveal") => {
    if (!value) return;
    setErr(null);
    try {
      const result = await runDesktopArtifactAction(label, value, mode);
      if (!result.ok) throw new Error(result.error || `Unable to ${mode} ${label}`);
      setInfo({ ...result, label, value });
    } catch (e: any) {
      setErr(`Failed to ${mode} ${label}: ${String(e)}`);
    }
  };

  const applyLatestInternalSettings = () => {
    const p = latestInternalJob?.payload || project?.meta?.last_internal_render || null;
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
  };

  const renderScenes = async () => {
    setErr(null);
    setInfo(null);
    try {
      const d = await apiPost(`/v1/projects/${projectId}/render/comfyui/scenes`, { variant_index: selectedVariant, checkpoint: checkpointName || undefined });
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
      const d = await apiPost(`/v1/projects/${projectId}/render/comfyui/motion_scenes`, {
        checkpoint: checkpointName || undefined,
        variant_index: selectedVariant,
        engine,
        fps: motionFps,
        max_frames_per_scene: maxFramesPerScene,
        context_length: motionContextLength,
        context_overlap: motionContextOverlap
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
      const d = await apiPost(`/v1/projects/${projectId}/assemble_video`, { variant_index: selectedVariant });
      setInfo(d);
      await refreshProject(projectId);
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
      const d = await apiPost(`/v1/projects/${projectId}/export/deforum`, { variant_index: selectedVariant, fps: 30 });
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
      const d = await apiGet(`/v1/projects/${projectId}/export/comfyui_workflows?variant_index=${selectedVariant}`);
      setInfo(d);
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
      if (!imgs.length) { setEditorBgUrl(null); return; }
      const last = imgs[0];
      setEditorBgUrl(fileUrl(projectId, last));
    } catch (e: any) {
      setErr(String(e));
    }
  };

const fileUrl = (pid: string, rel: string) => `${backendUrl}/v1/projects/${pid}/file?path=${encodeURIComponent(rel)}`;
  const deforumExports = project?.meta?.exports?.deforum || [];
  const comfyExports = project?.meta?.exports?.comfyui || [];

  const variantCount = plan?.variants?.length || 0;

  return (
    <div>
      <h1>Render</h1>
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
                ComfyUI: AnimateDiff {caps?.animatediff?.available ? "✓" : "×"} / SVD {caps?.svd?.available ? "✓" : "×"}
              </div>
            </div>
          </div>

          <div className="row" style={{ marginTop: 10, gap: 10, flexWrap: "wrap" }}>
            <button onClick={runPipeline} disabled={!variantCount}>Preset + Render (one click)</button>
            <button className="secondary" onClick={runInternalVideo} disabled={!variantCount}>Internal (CPU-safe)</button>
            <button className="secondary" onClick={assemble} disabled={!variantCount}>Assemble only</button>
          </div>

          <details style={{ marginTop: 12 }} open={uiMode === "advanced"}>
            <summary style={{ cursor: "pointer", fontWeight: 800 }}>Advanced routing & controls</summary>
            <div style={{ marginTop: 10 }}>
              <div className="small" style={{ marginBottom: 10 }}>
                Force stills vs motion, tune FPS/frames, debug nodes, or run manual steps.
              </div>
              <div className="card" style={{ marginTop: 10 }}>
                <div style={{ fontWeight: 900, marginBottom: 8 }}>Internal renderer (no ComfyUI)</div>
                <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>
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
    <option value="auto">Auto (SDXL on GPU, SD1.5 fallback)</option>
    <option value="hf_sd15_internal">SD 1.5 (Internal)</option>
    <option value="hf_sdxl_internal">SDXL Base 1.0 (Internal)</option>
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
                </div>
                <div className="small" style={{ marginTop: 8, opacity: 0.85 }}>
                  Tip: install internal models in Models first. Auto tiering adapts the internal renderer for laptops, Apple Silicon, CPU-only systems, and higher-end GPUs.
                </div>
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
                      <div className="small" style={{ marginTop: 4 }}>
                        Tier: requested <b>{internalPreflight?.tier_plan?.requested_tier || internalRenderTier}</b> • applied <b>{internalPreflight?.tier_plan?.applied_tier || "auto"}</b> • recommended <b>{internalPreflight?.tier_plan?.recommended_tier || hardware?.hardware?.recommended_tier || "draft"}</b>
                      </div>
                      <div className="small" style={{ marginTop: 4 }}>
                        Estimated frames: <b>{internalPreflight.estimated_frames}</b> • Keyframes: <b>{internalPreflight.estimated_keyframes}</b> • Duration: <b>{Number(internalPreflight.duration_s || 0).toFixed(1)}s</b>
                      </div>
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
                        Internal models: SD 1.5 <b>{internalPreflight?.installed_internal_models?.hf_sd15_internal ? "installed" : "missing"}</b> • SDXL <b>{internalPreflight?.installed_internal_models?.hf_sdxl_internal ? "installed" : "missing"}</b>
                      </div>
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
                      {!internalPreflight?.installed_internal_models?.hf_sd15_internal && !internalPreflight?.installed_internal_models?.hf_sdxl_internal ? (
                        <div className="row" style={{ gap: 8, marginTop: 8, flexWrap: "wrap" }}>
                          <button className="secondary" onClick={() => onNavigate?.("models")}>Open Models to install internal renderer</button>
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

                <div className="card" style={{ marginTop: 10 }}>
                  <div className="row" style={{ justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap", marginBottom: 8 }}>
                    <div style={{ fontWeight: 900 }}>Latest internal render job</div>
                    <div className="row" style={{ gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                      <label className="row small" style={{ gap: 6, alignItems: "center" }}>
                        <input type="checkbox" checked={internalPolling} onChange={(e) => setInternalPolling(e.target.checked)} />
                        Live polling
                      </label>
                      <button className="secondary" onClick={() => refreshInternalStatus().catch(() => {})}>Refresh detail</button>
                      <button className="secondary" onClick={() => onNavigate?.("queue")}>Open Render Queue</button>
                    </div>
                  </div>
                  {latestInternalJob ? (
                    <div>
                      <div className="small">
                        Status: <b>{latestInternalJob.status}</b>
                        {latestInternalJob?.progress?.percent != null ? <> • {latestInternalJob.progress.percent}%</> : null}
                        {latestInternalJob?.progress?.stage ? <> • {latestInternalJob.progress.stage}</> : null}
                        {latestInternalDetail?.job?.progress?.queue_action ? <> • action <b>{latestInternalDetail.job.progress.queue_action}</b></> : null}
                      </div>
                      {latestInternalJob?.progress?.message ? (
                        <div className="small" style={{ marginTop: 4 }}>{latestInternalJob.progress.message}</div>
                      ) : null}
                      {latestInternalDetail?.runtime_checkpoint ? (
                        <>
                          <div className="small" style={{ marginTop: 6 }}>
                            Resume <b>{latestInternalDetail.runtime_checkpoint.resume_percent ?? 0}%</b> • chunks <b>{latestInternalDetail.runtime_checkpoint.completed_chunks ?? 0}/{latestInternalDetail.runtime_checkpoint.estimated_chunks ?? 1}</b> • next frame <b>{Math.min(Number(latestInternalDetail.runtime_checkpoint.next_frame_index ?? 0) + 1, Number(latestInternalDetail.runtime_checkpoint.total_frames ?? 0) || 0)}/{latestInternalDetail.runtime_checkpoint.total_frames ?? 0}</b>
                          </div>
                          <div className="small" style={{ marginTop: 4, opacity: 0.82 }}>
                            {latestInternalDetail.runtime_checkpoint.chunk_strategy || "single_pass"} • checkpoint every {latestInternalDetail.runtime_checkpoint.checkpoint_interval_frames ?? 0} frames • {latestInternalDetail.resume_ready ? "resume-ready" : "resume-limited"}
                          </div>
                          {latestInternalDetail.runtime_checkpoint.maintenance_action ? (
                            <div className="small" style={{ marginTop: 4, opacity: 0.78 }}>
                              Maintenance: <b>{latestInternalDetail.runtime_checkpoint.maintenance_action}</b>
                            </div>
                          ) : null}
                        </>
                      ) : null}
                      <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 8 }}>
                        {(latestInternalJob.status === "queued" || latestInternalJob.status === "running") ? (
                          <button className="secondary" onClick={cancelLatestInternal}>Cancel latest internal job</button>
                        ) : null}
                        {(latestInternalJob.status === "failed" || latestInternalJob.status === "canceled") ? (
                          <>
                            <button className="secondary" onClick={retryLatestInternal}>Retry latest job</button>
                            <button className="secondary" onClick={resumeLatestInternal}>Resume from checkpoint</button>
                            <button className="secondary" onClick={restartLatestInternalClean}>Restart clean</button>
                          </>
                        ) : null}
                        <button className="secondary" onClick={applyLatestInternalSettings}>Use latest job settings</button>
                        <button className="secondary" onClick={clearLatestInternalCachedFrames} disabled={latestInternalJob.status === "queued" || latestInternalJob.status === "running"}>Clear cached frames</button>
                        <button className="secondary" onClick={dropLatestInternalCheckpoint} disabled={latestInternalJob.status === "queued" || latestInternalJob.status === "running"}>Drop checkpoint</button>
                        {latestInternalVideoUrl ? (
                          <a className="secondary" href={latestInternalVideoUrl} target="_blank" rel="noreferrer">Open latest video</a>
                        ) : null}
                      </div>
                      {latestInternalDetail?.outputs ? (
                        <div style={{ marginTop: 10 }}>
                          <div className="small" style={{ opacity: 0.82 }}>Checkpoint JSON: <b>{latestInternalDetail.outputs.checkpoint_json_relpath || latestInternalDetail.outputs.checkpoint_json_abspath || "n/a"}</b></div>
                          {latestInternalDetail.outputs.cache_paths?.frames_dir ? <div className="small" style={{ marginTop: 4, opacity: 0.78 }}>Frames dir: {latestInternalDetail.outputs.cache_paths.frames_dir}</div> : null}
                          {latestInternalDetail.outputs.cache_paths?.raw_mp4 ? <div className="small" style={{ marginTop: 4, opacity: 0.78 }}>Raw MP4: {latestInternalDetail.outputs.cache_paths.raw_mp4}</div> : null}
                          {latestInternalDetail.outputs.cache_paths?.interp_mp4 ? <div className="small" style={{ marginTop: 4, opacity: 0.78 }}>Interp MP4: {latestInternalDetail.outputs.cache_paths.interp_mp4}</div> : null}
                          {latestInternalDetail.outputs.cache_paths?.final_mp4 ? <div className="small" style={{ marginTop: 4, opacity: 0.78 }}>Final MP4: {latestInternalDetail.outputs.cache_paths.final_mp4}</div> : null}
                          <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 8 }}>
                            {(latestInternalDetail.outputs.checkpoint_json_abspath || latestInternalDetail.outputs.checkpoint_json_relpath) ? <button className="secondary" onClick={() => copyPathToClipboard("checkpoint path", latestInternalDetail.outputs.checkpoint_json_abspath || latestInternalDetail.outputs.checkpoint_json_relpath)}>Copy checkpoint path</button> : null}
                            {(latestInternalDetail.outputs.checkpoint_json_abspath || latestInternalDetail.outputs.checkpoint_json_relpath) ? <button className="secondary" onClick={() => revealLocalPath("checkpoint path", latestInternalDetail.outputs.checkpoint_json_abspath || latestInternalDetail.outputs.checkpoint_json_relpath, "reveal")}>{desktopActionLabel("reveal", "checkpoint")}</button> : null}
                            {latestInternalDetail.outputs.cache_paths?.frames_dir ? <button className="secondary" onClick={() => copyPathToClipboard("frames dir", latestInternalDetail.outputs.cache_paths.frames_dir)}>Copy frames dir</button> : null}
                            {latestInternalDetail.outputs.cache_paths?.frames_dir ? <button className="secondary" onClick={() => revealLocalPath("frames dir", latestInternalDetail.outputs.cache_paths.frames_dir, "open")}>{desktopActionLabel("open", "frames dir")}</button> : null}
                            {latestInternalDetail.outputs.cache_paths?.final_mp4 ? <button className="secondary" onClick={() => copyPathToClipboard("final mp4", latestInternalDetail.outputs.cache_paths.final_mp4)}>Copy final mp4 path</button> : null}
                            {latestInternalDetail.outputs.cache_paths?.final_mp4 ? <button className="secondary" onClick={() => revealLocalPath("final mp4", latestInternalDetail.outputs.cache_paths.final_mp4, "reveal")}>{desktopActionLabel("reveal", "final mp4")}</button> : null}
                          </div>
                        </div>
                      ) : null}
                      {latestInternalLog ? (
                        <pre style={{ marginTop: 10, maxHeight: 220, overflow: "auto" }}>{latestInternalLog}</pre>
                      ) : (
                        <div className="small" style={{ marginTop: 6 }}>No log yet.</div>
                      )}
                      {latestInternalDetail?.log_exists ? (
                        <div className="small" style={{ marginTop: 6, opacity: 0.75 }}>
                          Log lines: <b>{latestInternalDetail.log_line_count ?? 0}</b> • {latestInternalDetail.log_path}
                        </div>
                      ) : null}
                    </div>
                  ) : (
                    <div className="small">No internal render job yet for this project.</div>
                  )}
                </div>

                <div className="card" style={{ marginTop: 10 }}>
                  <div style={{ fontWeight: 900, marginBottom: 8 }}>Latest internal output</div>
                  {latestInternalVideoUrl ? (
                    <div>
                      <div className="small">
                        {latestInternalVideoPath}
                      </div>
                      <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 8 }}>
                        <a className="secondary" href={latestInternalVideoUrl} target="_blank" rel="noreferrer">Open video</a>
                        <a className="secondary" href={latestInternalVideoUrl} download>Download video</a>
                        <button className="secondary" onClick={() => { applyLatestInternalSettings(); setInternalResumeExisting(true); }}>Reuse settings + resume caches</button>
                      </div>
                      <video controls style={{ width: "100%", maxWidth: 640, marginTop: 10 }} src={latestInternalVideoUrl} />
                    </div>
                  ) : (
                    <div className="small">No completed internal video saved yet.</div>
                  )}
                </div>

                 <div className="card" style={{ marginTop: 10 }}>
                   <div style={{ fontWeight: 900, marginBottom: 8 }}>Temporal consistency + compositing</div>

                   <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>
                     <div style={{ minWidth: 190 }}>
                       <div className="small">Temporal mode</div>
                       <select value={internalTemporalMode} onChange={(e) => setInternalTemporalMode(e.target.value as any)}>
                         <option value="off">Off (keyframes only)</option>
                         <option value="keyframes">Keyframes (style-locked)</option>
                         <option value="frame_img2img">Frame img2img (best)</option>
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

                   <div style={{ marginTop: 10, fontWeight: 800 }}>Overlays</div>
                   <div className="row" style={{ gap: 10, flexWrap: "wrap", alignItems: "center", marginTop: 6 }}>
                     <input type="file" accept="image/*" onChange={(e) => setOverlayFile(e.target.files?.[0] || null)} />
                     <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                       <input type="file" accept="image/*" onChange={(e) => setMaskFile(e.target.files?.[0] || null)} />
                       <button className="secondary" disabled={!maskFile} onClick={async () => {
                         try {
                           if (!maskFile) return;
                           await apiUpload(`/v1/projects/${projectId}/assets/mask`, maskFile);
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
                           const up = await apiUpload(`/v1/projects/${projectId}/assets/overlay`, overlayFile!);
                           const duration = (plan?.variants?.[selectedVariant]?.scenes?.slice(-1)?.[0]?.end_s) ?? 60;
                           const next = {
                             ...timeline,
                             layers: [
                               ...(timeline?.layers || []),
                               { type: "image", asset: up.asset, start_s: 0, end_s: Number(duration), x: 20, y: 20, w: 220, h: 220, opacity: 0.9, blend_mode: "normal", mask_asset: "", mask_invert: false, mask_feather_px: 0, keyframes: [], z: 10 }
                             ]
                           };
                           const saved = await apiPost(`/v1/projects/${projectId}/timeline`, { timeline: next });
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
                           const saved = await apiPost(`/v1/projects/${projectId}/timeline`, { timeline: next });
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
                       <button className="secondary" onClick={() => setEditorBgUrl(null)}>Clear background</button>
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
                                   const saved = await apiPost(`/v1/projects/${projectId}/timeline`, { timeline: next });
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
                      const saved = await apiPost(`/v1/projects/${projectId}/timeline`, { timeline });
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
                <div className="small" style={{ width: 140, fontWeight: 800 }}>Checkpoint</div>
                <input
                  style={{ minWidth: 360 }}
                  value={checkpointName}
                  onChange={(e) => setCheckpointName(e.target.value)}
                  placeholder="sdxl_base_1.0.safetensors (leave blank for default)"
                />
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

                {renderMode === "motion_ad" && (
                  <>
                    <label className="small">Context</label>
                    <input style={{ width: 80 }} type="number" value={motionContextLength} onChange={(e) => setMotionContextLength(Number(e.target.value))} />
                    <label className="small">Overlap</label>
                    <input style={{ width: 80 }} type="number" value={motionContextOverlap} onChange={(e) => setMotionContextOverlap(Number(e.target.value))} />
                  </>
                )}
              </div>

              <div className="row" style={{ marginTop: 10, gap: 10, flexWrap: "wrap" }}>
                <button onClick={renderScenes} disabled={!variantCount || renderMode !== "stills"}>Enqueue still scenes</button>
                <button onClick={renderMotion} disabled={!variantCount || renderMode === "stills"}>Enqueue motion scenes</button>
                <button className="secondary" onClick={tickWorker}>Tick worker (run 1 job)</button>
                <button className="secondary" onClick={refreshValidate}>Validate capabilities</button>
              </div>

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
            <button className="secondary" onClick={exportComfyWorkflows} disabled={!variantCount}>Export ComfyUI workflows</button>
          </div>

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
          {caps && <pre>{JSON.stringify(caps, null, 2)}</pre>}

          <hr />
          <div style={{ fontWeight: 800, marginBottom: 10 }}>Last action result</div>
          {!info && <div className="small">No recent action.</div>}
          {info && <pre>{JSON.stringify(info, null, 2)}</pre>}
        </div>
      </div>
    </div>
  );
}
