import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  apiGet,
  apiPost,
  getBackendUrl,
  isProjectRevisionConflict,
  isRequestAbortError,
  type ApiError,
  type SignedProjectMediaRequest,
} from "../components/api";
import { desktopActionLabel, runDesktopArtifactAction } from "../components/desktopArtifacts";
import {
  ProjectRevisionConflict,
  expectedRevisionBody,
  projectRevision,
  projectRevisionFromResponse,
} from "../components/ProjectRevisionConflict";
import { RenewingVideo } from "../components/RenewingMedia";
import { StudioLayoutCustomizer } from "../components/StudioLayoutCustomizer";
import { StructuredSummary } from "../components/StructuredSummary";
import { resolveProjectId } from "../components/projectSelection";
import { useStudioSession } from "../components/studioSession";
import { useStudioPageLayout } from "../components/studioLayout";
import { useAdaptivePolling } from "../hooks/useAdaptivePolling";
import { useSignedProjectMedia } from "../hooks/useSignedProjectMedia";
import { JobActionButtons } from "../shared/jobs/JobActionButtons";
import { postQueueJobAction, type QueueJobAction } from "../shared/jobs/jobActions";
import { JobStatusChip } from "../shared/jobs/JobStatusChip";
import { jobRecoveryHint, type StudioJob } from "../shared/jobs/jobStatus";
import type { PageProps } from "../types/pageProps";

type OutputsPanelId =
  | "controls"
  | "preview"
  | "activeRenders"
  | "latestRender"
  | "renderHistory"
  | "mediaLibrary"
  | "rawPayload";

export default function Outputs(props: PageProps) {
  const backendUrl = props.backendUrl || getBackendUrl();
  const { projectId, setProjectId } = useStudioSession();
  const [projects, setProjects] = useState<any[]>([]);
  const [outs, setOuts] = useState<any>(null);
  const [selected, setSelected] = useState<{ type: "image" | "video"; path: string } | null>(null);
  const [info, setInfo] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState<boolean>(true);
  const [lastRefreshAt, setLastRefreshAt] = useState<number>(0);
  const [unrealVariantNumber, setUnrealVariantNumber] = useState<number>(1);
  const [unrealBundleName, setUnrealBundleName] = useState<string>("");
  const [unrealIncludeZip, setUnrealIncludeZip] = useState<boolean>(true);
  const [unrealExportBusy, setUnrealExportBusy] = useState<boolean>(false);
  const [unrealPlanBusyBundle, setUnrealPlanBusyBundle] = useState<string>("");
  const [unrealImportBusyBundle, setUnrealImportBusyBundle] = useState<string>("");
  const projectRevisionRef = useRef<number | null>(null);
  const [revisionConflict, setRevisionConflict] = useState<ApiError | null>(null);

  const refreshProjects = useCallback(async () => {
    const d = await apiGet("/v1/projects");
    const ps = d.projects || [];
    setProjects(ps);
    const nextProjectId = resolveProjectId(ps, projectId);
    if (nextProjectId !== projectId) setProjectId(nextProjectId);
  }, [projectId, setProjectId]);

  const refreshOutputs = useCallback(async (pid: string, signal?: AbortSignal) => {
    const d = await apiGet(`/v1/projects/${encodeURIComponent(pid)}/outputs`, { signal });
    setOuts(d);
    setLastRefreshAt(Date.now());
    return d;
  }, []);

  const refreshProjectRevision = useCallback(async (pid: string) => {
    const data = await apiGet(`/v1/projects/${encodeURIComponent(pid)}`);
    projectRevisionRef.current = projectRevision(data?.project);
    setRevisionConflict(null);
  }, []);

  const withExpectedRevision = <T extends Record<string, unknown>,>(body: T) =>
    expectedRevisionBody(body, { revision: projectRevisionRef.current });

  const setRevisionFromResponse = (response: unknown) => {
    const revision = projectRevisionFromResponse(response);
    if (revision != null) projectRevisionRef.current = revision;
  };

  const reportMutationError = (error: unknown) => {
    if (isProjectRevisionConflict(error)) setRevisionConflict(error);
  };

  const pollOutputs = useCallback(async (signal: AbortSignal) => {
    if (!projectId) return { continuePolling: false };
    try {
      const data = await refreshOutputs(projectId, signal);
      return { active: (data?.active_internal_jobs || []).length > 0 };
    } catch (error) {
      if (!isRequestAbortError(error)) setErr(String(error));
      throw error;
    }
  }, [projectId, refreshOutputs]);

  const outputPolling = useAdaptivePolling({
    poll: pollOutputs,
    enabled: !!projectId && autoRefresh,
    activeIntervalMs: 2500,
    idleIntervalMs: 2500,
    scopeKey: `${backendUrl}:${projectId}`,
  });

  useEffect(() => { refreshProjects().catch(() => {}); }, [backendUrl, refreshProjects]);
  useEffect(() => {
    if (!projectId) {
      projectRevisionRef.current = null;
      setRevisionConflict(null);
      setOuts(null);
      return;
    }
    refreshProjectRevision(projectId).catch((e) => setErr(String(e)));
    if (!autoRefresh) refreshOutputs(projectId).catch((e) => setErr(String(e)));
  }, [autoRefresh, backendUrl, projectId, refreshOutputs, refreshProjectRevision]);

  const mediaPaths = useMemo(() => {
    const paths = new Set<string>();
    const add = (value: unknown) => {
      const path = String(value || "").trim();
      if (path) paths.add(path);
    };
    add(selected?.path);
    add(outs?.latest_internal_render?.video);
    for (const entry of outs?.internal_render_history || []) add(entry?.video);
    for (const entry of outs?.videos || []) add(entry?.path);
    for (const entry of outs?.images || []) add(entry?.path);
    for (const entry of outs?.deforum_exports || []) add(entry?.path);
    for (const bundle of outs?.unreal_exports || []) {
      add(bundle?.manifest_path);
      add(bundle?.import_plan_path);
      add(bundle?.zip_path);
    }
    return [...paths];
  }, [outs, selected?.path]);
  const mediaRequests = useMemo<SignedProjectMediaRequest[]>(
    () => mediaPaths.map((path) => ({ purpose: "file", path })),
    [mediaPaths],
  );
  const signedMedia = useSignedProjectMedia(projectId, mediaRequests, backendUrl);
  const fileUrl = useCallback(
    (rel: string) => signedMedia.urlFor({ purpose: "file", path: rel }),
    [signedMedia.urlFor],
  );
  const activeInternalJobs = (outs?.active_internal_jobs || []) as any[];

  const renderMetadataCard = (entry: any) => {
    const metadata = entry?.metadata;
    const metadataPath = entry?.metadata_path;
    const baseModel = metadata?.base_model || {};
    const output = metadata?.output || {};
    const provenance = metadata?.provenance || {};
    const resolvedEngine = metadata?.engine || baseModel?.engine;
    const resolvedFamily = metadata?.model_family || baseModel?.family;
    const resolvedCfg = metadata?.cfg_scale ?? metadata?.cfg;
    const loras = Array.isArray(metadata?.loras) ? metadata.loras : [];
    const controlnetUnits = Array.isArray(metadata?.controlnet_units) ? metadata.controlnet_units : [];
    const outpaint = metadata?.outpaint && typeof metadata.outpaint === "object" ? metadata.outpaint : null;
    const prompt = String(metadata?.prompt || "").trim();
    const negativePrompt = String(metadata?.negative_prompt || "").trim();

    if (!metadata && !metadataPath) return null;

    return (
      <div style={{ marginTop: 10, border: "1px solid var(--border)", borderRadius: 12, padding: 10 }}>
        <div className="row" style={{ justifyContent: "space-between", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <div style={{ fontWeight: 800 }}>Generation metadata</div>
          <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
            {metadataPath ? (
              <button className="secondary" onClick={(e) => { e.stopPropagation(); handleArtifactPathAction("metadata sidecar", metadataPath, "reveal"); }}>
                {desktopActionLabel("reveal", "metadata sidecar")}
              </button>
            ) : null}
          </div>
        </div>
        <div className="small" style={{ marginTop: 8, opacity: 0.9 }}>
          {metadata?.workflow_family ? <>Workflow <b>{metadata.workflow_family}</b> • </> : null}
          {resolvedEngine ? <>Engine <b>{String(resolvedEngine)}</b> • </> : null}
          {resolvedFamily ? <>Family <b>{String(resolvedFamily).toUpperCase()}</b> • </> : null}
          {baseModel?.model_id ? <>Model <b>{String(baseModel.model_id)}</b></> : baseModel?.checkpoint ? <>Model <b>{String(baseModel.checkpoint)}</b></> : null}
        </div>
        <div className="small" style={{ marginTop: 6, opacity: 0.88 }}>
          Seed <b>{metadata?.seed ?? "auto"}</b> • Sampler <b>{metadata?.sampler || "default"}</b> • Steps <b>{metadata?.steps ?? "-"}</b> • CFG <b>{resolvedCfg ?? "-"}</b>
        </div>
        {provenance?.backend || provenance?.device ? (
          <div className="small" style={{ marginTop: 6, opacity: 0.88 }}>
            Runtime <b>{String(provenance?.backend || "unknown")}</b>
            {provenance?.device ? <> • Device <b>{String(provenance.device).toUpperCase()}</b></> : null}
          </div>
        ) : null}
        {prompt ? <div className="small" style={{ marginTop: 8 }}><b>Prompt:</b> {prompt}</div> : null}
        {negativePrompt ? <div className="small" style={{ marginTop: 4, opacity: 0.84 }}><b>Negative:</b> {negativePrompt}</div> : null}
        <div className="small" style={{ marginTop: 8, opacity: 0.84 }}>
          {metadata?.source_asset ? <>Source <b>{String(metadata.source_asset)}</b> • </> : null}
          {metadata?.mask_source ? <>Mask <b>{String(metadata.mask_source)}</b> • </> : null}
          {output?.image ? <>Output <b>{String(output.image)}</b></> : output?.video ? <>Output <b>{String(output.video)}</b></> : null}
        </div>
        {outpaint ? (
          <div className="small" style={{ marginTop: 6, opacity: 0.84 }}>
            Outpaint margins <b>{`T${outpaint.top_px || 0} R${outpaint.right_px || 0} B${outpaint.bottom_px || 0} L${outpaint.left_px || 0}`}</b>
          </div>
        ) : null}
        {loras.length ? (
          <div className="small" style={{ marginTop: 6, opacity: 0.86 }}>
            LoRAs <b>{loras.map((item: any) => `${String(item.name || item.filename || "lora")}@${Number(item.weight ?? 1).toFixed(2)}`).join(", ")}</b>
          </div>
        ) : null}
        {controlnetUnits.length ? (
          <div className="small" style={{ marginTop: 6, opacity: 0.86 }}>
            ControlNet <b>{controlnetUnits.map((item: any) => `${String(item.controlnet_name || item.model || "unit")}@${Number(item.strength ?? 0.8).toFixed(2)}`).join(", ")}</b>
          </div>
        ) : null}
      </div>
    );
  };

  const handleArtifactPathAction = async (label: string, value: string | null | undefined, mode: "reveal" | "open") => {
    if (!value) return;
    try {
      setErr(null);
      const result = await runDesktopArtifactAction(label, value, mode);
      if (!result.ok) throw new Error(result.error || `Unable to ${mode} ${label}`);
      setInfo({ ...result, label, value });
    } catch (e: any) {
      setErr(`Failed to ${mode} ${label}: ${String(e)}`);
    }
  };

  const retryInternalFromHistory = async (entry: any) => {
    if (!projectId) return;
    try {
      setErr(null);
      const result = await apiPost(`/v1/projects/${encodeURIComponent(projectId)}/render/internal/video`, withExpectedRevision({
        variant_index: Number(entry?.variant_index ?? 0),
        model_id: "auto",
        fps_render: Number(entry?.fps_render ?? 2),
        fps_output: Number(entry?.fps_output ?? 24),
        temporal_mode: String(entry?.temporal_mode || "frame_img2img"),
        render_mode: "auto",
        resume_existing_frames: true,
      }));
      setRevisionFromResponse(result);
      await refreshOutputs(projectId);
    } catch (e: any) {
      reportMutationError(e);
      setErr(String(e));
    }
  };

  const resumeInternalJob = async (job: any) => {
    try {
      setErr(null);
      const result = await apiPost(
        `/v1/projects/${encodeURIComponent(job.project_id)}/jobs/${encodeURIComponent(job.id)}/resume_from_checkpoint`,
        withExpectedRevision({}),
      );
      setRevisionFromResponse(result);
      await refreshOutputs(job.project_id);
    } catch (e: any) {
      reportMutationError(e);
      setErr(String(e));
    }
  };

  const restartInternalJobClean = async (job: any) => {
    try {
      setErr(null);
      const result = await apiPost(
        `/v1/projects/${encodeURIComponent(job.project_id)}/jobs/${encodeURIComponent(job.id)}/restart_clean`,
        withExpectedRevision({}),
      );
      setRevisionFromResponse(result);
      await refreshOutputs(job.project_id);
    } catch (e: any) {
      reportMutationError(e);
      setErr(String(e));
    }
  };

  const runInternalJobAction = async (job: StudioJob, action: QueueJobAction) => {
    try {
      setErr(null);
      const result = await postQueueJobAction(job, action, withExpectedRevision({}));
      setRevisionFromResponse(result);
      await refreshOutputs(job.project_id);
    } catch (e: any) {
      reportMutationError(e);
      setErr(String(e));
    }
  };

  const exportUnrealBundle = async () => {
    if (!projectId || unrealExportBusy) return;
    try {
      setErr(null);
      setUnrealExportBusy(true);
      const result = await apiPost(`/v1/projects/${encodeURIComponent(projectId)}/export/unreal`, withExpectedRevision({
        variant_index: Math.max(0, (Number(unrealVariantNumber) || 1) - 1),
        bundle_name: unrealBundleName.trim() || null,
        include_zip: unrealIncludeZip,
      }));
      setRevisionFromResponse(result);
      await refreshOutputs(projectId);
      const bundle = result?.bundle || {};
      setInfo({
        action: "export_unreal_bundle",
        path: String(bundle.zip_path || bundle.bundle_dir || bundle.manifest_path || ""),
        label: "unreal bundle",
      });
    } catch (e: any) {
      reportMutationError(e);
      setErr(String(e));
    } finally {
      setUnrealExportBusy(false);
    }
  };

  const buildUnrealImportPlan = async (bundleDir: string) => {
    if (!projectId || !bundleDir || unrealPlanBusyBundle) return;
    try {
      setErr(null);
      setUnrealPlanBusyBundle(bundleDir);
      const result = await apiPost(`/v1/projects/${encodeURIComponent(projectId)}/unreal/import-plan`, withExpectedRevision({
        bundle_dir: bundleDir,
        content_path: null,
        asset_name: null,
      }));
      setRevisionFromResponse(result);
      await refreshOutputs(projectId);
      const plan = result?.plan || {};
      setInfo({
        action: "build_unreal_import_plan",
        path: String(result?.plan_path || plan.asset_path || bundleDir),
        label: "unreal import plan",
      });
    } catch (e: any) {
      reportMutationError(e);
      setErr(String(e));
    } finally {
      setUnrealPlanBusyBundle("");
    }
  };

  const importUnrealReturn = async (bundleDir: string) => {
    if (!projectId || !bundleDir || unrealImportBusyBundle) return;
    try {
      setErr(null);
      setUnrealImportBusyBundle(bundleDir);
      const result = await apiPost(`/v1/projects/${encodeURIComponent(projectId)}/import/unreal`, withExpectedRevision({
        bundle_dir: bundleDir,
        source_dir: null,
      }));
      setRevisionFromResponse(result);
      await refreshOutputs(projectId);
      const imported = result?.imported || {};
      setInfo({
        action: "import_unreal_return",
        path: String(imported.source_dir || imported.bundle_dir || bundleDir),
        label: "unreal return",
      });
    } catch (e: any) {
      reportMutationError(e);
      setErr(String(e));
    } finally {
      setUnrealImportBusyBundle("");
    }
  };

  const panelDefinitions = useMemo(
    () => [
      {
        id: "controls" as const,
        label: "Output controls",
        description: "Project selection, refresh controls, live polling, and desktop action status.",
      },
      {
        id: "preview" as const,
        label: "Preview",
        description: "Focused image or video preview for the currently selected artifact.",
      },
      {
        id: "activeRenders" as const,
        label: "Active renders",
        description: "Resumable internal jobs, checkpoints, and queue handoff.",
      },
      {
        id: "latestRender" as const,
        label: "Latest internal render",
        description: "Most recent internal video render plus retry and checkpoint shortcuts.",
      },
      {
        id: "renderHistory" as const,
        label: "Render history",
        description: "Recent internal render outputs with retry and checkpoint access.",
      },
      {
        id: "mediaLibrary" as const,
        label: "Media library",
        description: "Videos, images, metadata, and Deforum exports for the selected project.",
      },
      {
        id: "rawPayload" as const,
        label: "Raw payload",
        description: "Read-only debug view of the current outputs response.",
      },
    ],
    [],
  );
  const {
    profileOptions,
    activeProfile,
    setActiveProfile,
    layoutState,
    visibleOrder,
    movePanel,
    updateHidden,
    resetLayout,
  } = useStudioPageLayout<OutputsPanelId>(
    "outputs",
    panelDefinitions.map((panel) => panel.id),
  );
  const panelDefinitionById = useMemo(
    () =>
      Object.fromEntries(
        panelDefinitions.map((definition) => [definition.id, definition]),
      ) as Record<OutputsPanelId, (typeof panelDefinitions)[number]>,
    [panelDefinitions],
  );
  const panelControlItems = layoutState.order.map((panelId, index) => ({
    id: panelId,
    label: panelDefinitionById[panelId].label,
    description: panelDefinitionById[panelId].description,
    hidden: layoutState.hidden.includes(panelId),
    canMoveUp: index > 0,
    canMoveDown: index < layoutState.order.length - 1,
  }));

  const panelContent: Record<OutputsPanelId, React.ReactNode> = {
    controls: (
      <div className="card">
        <div className="row">
          <div style={{ flex: 1 }}>
            <div className="small">Project</div>
            <select value={projectId} onChange={(e) => setProjectId(e.target.value)}>
              {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </div>
          <div>
            <div className="small">Refresh</div>
            <button
              className="secondary"
              onClick={() => {
                if (!projectId) return;
                if (autoRefresh) outputPolling.pollNow();
                else void refreshOutputs(projectId).catch((e) => setErr(String(e)));
              }}
            >
              Refresh
            </button>
          </div>
        </div>
        <div className="row" style={{ gap: 12, marginTop: 10, flexWrap: "wrap", alignItems: "center" }}>
          <label className="row small" style={{ gap: 6, alignItems: "center" }}>
            <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
            Live poll outputs every 2.5s
          </label>
          <div className="small" style={{ opacity: 0.8 }}>
            Active/resumable internal jobs <b>{activeInternalJobs.length}</b>{lastRefreshAt ? <> • updated {new Date(lastRefreshAt).toLocaleTimeString()}</> : null}
          </div>
        </div>
        <div style={{ marginTop: 14, borderTop: "1px solid var(--border)", paddingTop: 12, display: "grid", gap: 10 }}>
          <div style={{ fontWeight: 800 }}>Unreal bridge bundle</div>
          <div className="small" style={{ opacity: 0.84 }}>
            Export the selected plan variant into the Unreal handoff bundle and refresh Outputs when the bundle is ready.
          </div>
          <div className="row" style={{ gap: 10, flexWrap: "wrap", alignItems: "end" }}>
            <label style={{ minWidth: 110 }}>
              <div className="small">Variant</div>
              <input
                aria-label="Unreal export variant"
                type="number"
                min={1}
                step={1}
                value={unrealVariantNumber}
                onChange={(e) => setUnrealVariantNumber(Math.max(1, Number(e.target.value) || 1))}
              />
            </label>
            <label style={{ flex: 1, minWidth: 220 }}>
              <div className="small">Bundle name (optional)</div>
              <input
                aria-label="Unreal bundle name"
                type="text"
                value={unrealBundleName}
                onChange={(e) => setUnrealBundleName(e.target.value)}
                placeholder="leave blank for timestamped default"
              />
            </label>
            <label className="row small" style={{ gap: 6, alignItems: "center", marginBottom: 6 }}>
              <input
                type="checkbox"
                checked={unrealIncludeZip}
                onChange={(e) => setUnrealIncludeZip(e.target.checked)}
              />
              Include zip
            </label>
            <button onClick={exportUnrealBundle} disabled={!projectId || unrealExportBusy}>
              {unrealExportBusy ? "Exporting Unreal Bundle..." : "Export Unreal Bundle"}
            </button>
          </div>
        </div>
        {err && <div style={{ marginTop: 10, color: "var(--danger)" }}>{err}</div>}
        {!err && info ? <div className="small" style={{ marginTop: 10, opacity: 0.82 }}>Last desktop action: <b>{info.action || "ok"}</b>{info.path ? <> • {String(info.path)}</> : null}</div> : null}
      </div>
    ),
    preview: selected ? (
      <div className="card" style={{ marginTop: 14 }}>
        <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <div style={{ fontWeight: 800 }}>Preview</div>
          <button className="secondary" onClick={() => setSelected(null)}>Close</button>
        </div>
        <div className="small" style={{ marginTop: 6 }}>{selected.path}</div>
        {selected.type === "image" ? (
          <img
            src={fileUrl(selected.path)}
            alt=""
            style={{ width: "100%", marginTop: 10, borderRadius: 12, border: "1px solid var(--border)" }}
          />
        ) : (
          <RenewingVideo
            sourceUrl={fileUrl(selected.path)}
            controls
            style={{ width: "100%", marginTop: 10, borderRadius: 12, border: "1px solid var(--border)" }}
          />
        )}
      </div>
    ) : null,
    activeRenders: activeInternalJobs.length > 0 ? (
      <div className="card" style={{ marginTop: 14 }}>
        <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <div style={{ fontWeight: 800 }}>Active / resumable internal renders</div>
          <button className="secondary" onClick={() => props.onNavigate?.("queue")}>Open Render Queue</button>
        </div>
        <div style={{ display: "grid", gap: 10, marginTop: 10 }}>
          {activeInternalJobs.map((job: any) => {
            const cp = job?.progress?.runtime_checkpoint;
          const progressMessage = job?.progress?.message ? String(job.progress.message) : null;
          const recoveryHint = jobRecoveryHint(job as StudioJob);
          return (
            <div key={job.id} style={{ border: "1px solid var(--border)", borderRadius: 12, padding: 10 }}>
              <div className="small"><JobStatusChip status={job.status} /> • {job.type} • {job.progress?.stage || "queued"}</div>
              {progressMessage ? <div className="small" style={{ marginTop: 4, opacity: 0.85 }}>{progressMessage}</div> : null}
              {recoveryHint && recoveryHint !== progressMessage ? (
                <div className="small" style={{ marginTop: 4, opacity: 0.85 }}>{recoveryHint}</div>
              ) : null}
                {cp ? (
                  <>
                    <div className="small" style={{ marginTop: 6 }}>
                      Resume <b>{cp.resume_percent ?? 0}%</b> • chunks <b>{cp.completed_chunks ?? 0}/{cp.estimated_chunks ?? 1}</b> • next frame <b>{Math.min(Number(cp.next_frame_index ?? 0) + 1, Number(cp.total_frames ?? 0) || 0)}/{cp.total_frames ?? 0}</b>
                    </div>
                    <div className="small" style={{ marginTop: 4, opacity: 0.8 }}>
                      {cp.chunk_strategy || "single_pass"} • checkpoint every {cp.checkpoint_interval_frames ?? 0} frames • {cp.can_resume ? "resume-ready" : "resume-limited"}
                    </div>
                    {cp?.outputs?.checkpoint_json ? <div className="small" style={{ marginTop: 4, opacity: 0.7 }}>{cp.outputs.checkpoint_json}</div> : null}
                  </>
                ) : null}
                <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 8 }}>
                  <button className="secondary" onClick={() => props.onNavigate?.("queue")}>Open queue</button>
                  {cp?.outputs?.checkpoint_json ? <button className="secondary" onClick={() => handleArtifactPathAction("checkpoint", cp.outputs.checkpoint_json, "reveal")}>{desktopActionLabel("reveal", "checkpoint")}</button> : null}
                  <JobActionButtons
                    job={job as StudioJob}
                    onAction={(action) => runInternalJobAction(job as StudioJob, action)}
                    onResumeFromCheckpoint={() => resumeInternalJob(job)}
                    onRestartClean={() => restartInternalJobClean(job)}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </div>
    ) : null,
    latestRender: outs?.latest_internal_render ? (
      <div className="card" style={{ marginTop: 14 }}>
        <div style={{ fontWeight: 800, marginBottom: 10 }}>Latest internal render</div>
        <div className="small">
          Mode <b>{outs.latest_internal_render.mode || "diffusion"}</b> • Model <b>{outs.latest_internal_render.model_id}</b> • variant <b>{Number(outs.latest_internal_render.variant_index ?? 0) + 1}</b>
        </div>
        <div className="small" style={{ marginTop: 4 }}>
          {outs.latest_internal_render.video}
        </div>
        {outs.latest_internal_render.runtime_checkpoint ? (
          <div className="small" style={{ marginTop: 6, opacity: 0.85 }}>
            Resume {outs.latest_internal_render.runtime_checkpoint.resume_percent ?? 0}% • chunks {outs.latest_internal_render.runtime_checkpoint.completed_chunks ?? 0}/{outs.latest_internal_render.runtime_checkpoint.estimated_chunks ?? 1} • next frame {Math.min(Number(outs.latest_internal_render.runtime_checkpoint.next_frame_index ?? 0) + 1, Number(outs.latest_internal_render.runtime_checkpoint.total_frames ?? 0) || 0)}/{outs.latest_internal_render.runtime_checkpoint.total_frames ?? 0}
          </div>
        ) : null}
        <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 8 }}>
          <a className="secondary" href={fileUrl(outs.latest_internal_render.video)} target="_blank" rel="noreferrer">Open latest internal video</a>
          <button className="secondary" onClick={() => handleArtifactPathAction("latest internal video", outs.latest_internal_render.video, "reveal")}>{desktopActionLabel("reveal", "latest internal video")}</button>
          {outs.latest_internal_render.runtime_checkpoint?.outputs?.checkpoint_json ? <button className="secondary" onClick={() => handleArtifactPathAction("checkpoint", outs.latest_internal_render.runtime_checkpoint.outputs.checkpoint_json, "reveal")}>{desktopActionLabel("reveal", "checkpoint")}</button> : null}
          <button className="secondary" onClick={() => retryInternalFromHistory(outs.latest_internal_render)}>Retry with cached frames</button>
        </div>
      </div>
    ) : null,
    renderHistory: (outs?.internal_render_history?.length ?? 0) > 0 ? (
      <div className="card" style={{ marginTop: 14 }}>
        <div style={{ fontWeight: 800, marginBottom: 10 }}>Internal render history</div>
        <div style={{ display: "grid", gap: 10 }}>
          {[...(outs.internal_render_history || [])].slice().reverse().slice(0, 8).map((entry: any, idx: number) => (
            <div key={`${entry.video || idx}-${idx}`} style={{ border: "1px solid var(--border)", borderRadius: 12, padding: 10 }}>
              <div className="small">
                <b>{entry.mode || "diffusion"}</b> • <b>{entry.model_id || "internal"}</b> • variant {Number(entry.variant_index ?? 0) + 1} • {entry.temporal_mode || "frame_img2img"}
              </div>
              <div className="small" style={{ marginTop: 4, opacity: 0.85 }}>{entry.video}</div>
              {entry.runtime_checkpoint ? (
                <div className="small" style={{ marginTop: 4, opacity: 0.8 }}>
                  Resume {entry.runtime_checkpoint.resume_percent ?? 0}% • chunks {entry.runtime_checkpoint.completed_chunks ?? 0}/{entry.runtime_checkpoint.estimated_chunks ?? 1} • checkpoint every {entry.runtime_checkpoint.checkpoint_interval_frames ?? 0} frames
                </div>
              ) : null}
              <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 8 }}>
                <a className="secondary" href={fileUrl(entry.video)} target="_blank" rel="noreferrer">Open</a>
                <button className="secondary" onClick={() => handleArtifactPathAction("history video", entry.video, "reveal")}>{desktopActionLabel("reveal", "history video")}</button>
                {entry.runtime_checkpoint?.outputs?.checkpoint_json ? <button className="secondary" onClick={() => handleArtifactPathAction("checkpoint", entry.runtime_checkpoint.outputs.checkpoint_json, "reveal")}>{desktopActionLabel("reveal", "checkpoint")}</button> : null}
                <button className="secondary" onClick={() => retryInternalFromHistory(entry)}>Retry</button>
              </div>
            </div>
          ))}
        </div>
      </div>
    ) : null,
    mediaLibrary: outs ? (
      <div className="grid2" style={{ marginTop: 14 }}>
        <div className="card">
          <div style={{ fontWeight: 800, marginBottom: 10 }}>Videos</div>
          {!outs.videos?.length && <div className="small">No videos yet.</div>}
          {outs.videos?.map((v: any) => (
            <div key={v.path} style={{ marginBottom: 14 }}>
              <div className="row" style={{ justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <div className="small" style={{ overflow: "hidden", textOverflow: "ellipsis" }}>
                  {v.path}
                  {v.kind ? <> • <b>{v.kind}</b></> : null}
                  {v.size_bytes ? <> • {(Number(v.size_bytes) / (1024 * 1024)).toFixed(1)} MB</> : null}
                </div>
                <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                  <button className="secondary" onClick={() => setSelected({ type: "video", path: v.path })}>Preview</button>
                  <button className="secondary" onClick={() => handleArtifactPathAction("video", v.path, "reveal")}>{desktopActionLabel("reveal", "video")}</button>
                </div>
              </div>
              <RenewingVideo
                sourceUrl={fileUrl(v.path)}
                controls
                style={{ width: "100%", borderRadius: 12, border: "1px solid var(--border)" }}
              />
              {renderMetadataCard(v)}
            </div>
          ))}
        </div>

        <div className="card">
          <div style={{ fontWeight: 800, marginBottom: 10 }}>Images</div>
          {!outs.images?.length && <div className="small">No images yet.</div>}
          <div className="grid3">
            {outs.images?.map((im: any) => (
              <div key={im.path} style={{ cursor: "pointer" }} onClick={() => setSelected({ type: "image", path: im.path })}>
                <img
                  src={fileUrl(im.path)}
                  alt=""
                  style={{ width: "100%", borderRadius: 12, border: "1px solid var(--border)" }}
                />
                <div className="small" style={{ marginTop: 6, opacity: 0.8, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {im.path.split("/").slice(-1)[0]}
                </div>
                <div className="row" style={{ gap: 8, marginTop: 6, flexWrap: "wrap" }}>
                  <button className="secondary" onClick={(e) => { e.stopPropagation(); handleArtifactPathAction("image", im.path, "reveal"); }}>{desktopActionLabel("reveal", "image")}</button>
                  {im.metadata_path ? (
                    <button className="secondary" onClick={(e) => { e.stopPropagation(); handleArtifactPathAction("metadata sidecar", im.metadata_path, "reveal"); }}>
                      {desktopActionLabel("reveal", "metadata")}
                    </button>
                  ) : null}
                </div>
                {renderMetadataCard(im)}
              </div>
            ))}
          </div>

          {outs.deforum_exports?.length ? (
            <>
              <hr />
              <div style={{ fontWeight: 800, marginBottom: 10 }}>Deforum exports</div>
              {outs.deforum_exports.map((p: any) => (
                <div key={p.path} className="small">
                  <a href={fileUrl(p.path)} target="_blank" rel="noreferrer">{p.path}</a>
                </div>
              ))}
            </>
          ) : null}

          {outs.unreal_exports?.length ? (
            <>
              <hr />
              <div style={{ fontWeight: 800, marginBottom: 10 }}>Unreal bridge exports</div>
              <div style={{ display: "grid", gap: 10 }}>
                {outs.unreal_exports.map((bundle: any) => (
                  <div key={bundle.manifest_path || bundle.bundle_dir} style={{ border: "1px solid var(--border)", borderRadius: 12, padding: 10 }}>
                    <div className="small">
                      <b>{bundle.sequence_name || "Unreal bundle"}</b>
                      {Number.isFinite(bundle.variant_index) ? <> â€¢ variant {Number(bundle.variant_index) + 1}</> : null}
                      {bundle.created_at ? <> â€¢ {bundle.created_at}</> : null}
                    </div>
                    {bundle.bundle_dir ? (
                      <div className="small" style={{ marginTop: 4, opacity: 0.82 }}>{bundle.bundle_dir}</div>
                    ) : null}
                    {bundle.manifest?.files?.length ? (
                      <div className="small" style={{ marginTop: 6, opacity: 0.84 }}>
                        Files <b>{bundle.manifest.files.map((item: any) => String(item?.path || item)).join(", ")}</b>
                      </div>
                    ) : null}
                    {bundle.import_plan?.asset_path ? (
                      <div className="small" style={{ marginTop: 6, opacity: 0.84 }}>
                        Unreal asset <b>{String(bundle.import_plan.asset_path)}</b>
                      </div>
                    ) : null}
                    <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 8 }}>
                      {bundle.manifest_path ? (
                        <a className="secondary" href={fileUrl(bundle.manifest_path)} target="_blank" rel="noreferrer">Open manifest</a>
                      ) : null}
                      {bundle.import_plan_path ? (
                        <a className="secondary" href={fileUrl(bundle.import_plan_path)} target="_blank" rel="noreferrer">Open import plan</a>
                      ) : null}
                      {bundle.zip_path ? (
                        <a className="secondary" href={fileUrl(bundle.zip_path)} target="_blank" rel="noreferrer">Download zip</a>
                      ) : null}
                      {bundle.bundle_dir ? (
                        <button className="secondary" onClick={() => handleArtifactPathAction("unreal bundle", bundle.bundle_dir, "reveal")}>
                          {desktopActionLabel("reveal", "unreal bundle")}
                        </button>
                      ) : null}
                      {bundle.zip_path ? (
                        <button className="secondary" onClick={() => handleArtifactPathAction("unreal zip", bundle.zip_path, "reveal")}>
                          {desktopActionLabel("reveal", "unreal zip")}
                        </button>
                      ) : null}
                      {bundle.import_plan_path ? (
                        <button className="secondary" onClick={() => handleArtifactPathAction("unreal import plan", bundle.import_plan_path, "reveal")}>
                          {desktopActionLabel("reveal", "import plan")}
                        </button>
                      ) : null}
                      {bundle.bundle_dir ? (
                        <button
                          className="secondary"
                          onClick={() => buildUnrealImportPlan(String(bundle.bundle_dir))}
                          disabled={Boolean(unrealPlanBusyBundle)}
                        >
                          {unrealPlanBusyBundle === String(bundle.bundle_dir) ? "Building import plan..." : "Build import plan"}
                        </button>
                      ) : null}
                      {bundle.bundle_dir ? (
                        <button
                          className="secondary"
                          onClick={() => importUnrealReturn(String(bundle.bundle_dir))}
                          disabled={Boolean(unrealImportBusyBundle)}
                        >
                          {unrealImportBusyBundle === String(bundle.bundle_dir) ? "Importing return..." : "Import returned media"}
                        </button>
                      ) : null}
                    </div>
                  </div>
                ))}
              </div>
            </>
          ) : null}

          {outs.unreal_returns?.length ? (
            <>
              <hr />
              <div style={{ fontWeight: 800, marginBottom: 10 }}>Unreal bridge returns</div>
              <div style={{ display: "grid", gap: 10 }}>
                {outs.unreal_returns.map((returned: any, idx: number) => (
                  <div key={`${returned.bundle_dir || "return"}-${idx}`} style={{ border: "1px solid var(--border)", borderRadius: 12, padding: 10 }}>
                    <div className="small">
                      <b>{returned.sequence_name || "Returned Unreal media"}</b>
                      {Number.isFinite(returned.variant_index) ? <> Х variant {Number(returned.variant_index) + 1}</> : null}
                      {returned.created_at ? <> Х {returned.created_at}</> : null}
                    </div>
                    {returned.source_dir ? (
                      <div className="small" style={{ marginTop: 4, opacity: 0.82 }}>{returned.source_dir}</div>
                    ) : null}
                    <div style={{ display: "grid", gap: 8, marginTop: 8 }}>
                      {(returned.media || []).map((media: any) => (
                        <div key={media.path} style={{ border: "1px solid var(--border)", borderRadius: 10, padding: 8 }}>
                          <div className="small">
                            <b>{media.kind || "artifact"}</b> Х {media.path}
                          </div>
                          {media.source_path ? (
                            <div className="small" style={{ marginTop: 4, opacity: 0.82 }}>Source {media.source_path}</div>
                          ) : null}
                          <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 8 }}>
                            <button
                              className="secondary"
                              onClick={() => setSelected({ type: media.kind === "image" ? "image" : "video", path: media.path })}
                            >
                              Preview
                            </button>
                            <button className="secondary" onClick={() => handleArtifactPathAction("unreal return", media.path, "reveal")}>
                              {desktopActionLabel("reveal", "unreal return")}
                            </button>
                            {media.metadata_path ? (
                              <button className="secondary" onClick={() => handleArtifactPathAction("metadata sidecar", media.metadata_path, "reveal")}>
                                {desktopActionLabel("reveal", "metadata")}
                              </button>
                            ) : null}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </>
          ) : null}
        </div>
      </div>
    ) : null,
    rawPayload: outs ? (
      <div className="card" style={{ marginTop: 14 }}>
        <div style={{ fontWeight: 800, marginBottom: 10 }}>Output payload</div>
        <StructuredSummary value={outs} showJson jsonMaxHeight={420} />
      </div>
    ) : null,
  };

  return (
    <div>
      <h1>Outputs</h1>
      <ProjectRevisionConflict
        conflict={revisionConflict}
        onReload={async () => {
          if (!projectId) return;
          setErr(null);
          await Promise.all([
            refreshProjectRevision(projectId),
            refreshOutputs(projectId),
          ]);
        }}
      />
      <div className="small" style={{ marginTop: 6 }}>
        Reorder or hide Outputs sections for your own review flow. This only changes the local page layout and leaves artifact actions, retries, and queue behavior untouched.
      </div>
      <StudioLayoutCustomizer
        title="Outputs layout"
        description="Reorder or hide Outputs panels without changing files, internal job control, render history, or backend polling."
        items={panelControlItems}
        profileOptions={profileOptions}
        activeProfile={activeProfile}
        onSelectProfile={setActiveProfile}
        onMove={movePanel}
        onToggleHidden={updateHidden}
        onReset={resetLayout}
      />
      {visibleOrder.map((panelId) => (
        <React.Fragment key={panelId}>{panelContent[panelId]}</React.Fragment>
      ))}
    </div>
  );
}
