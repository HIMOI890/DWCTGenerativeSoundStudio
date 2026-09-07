import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  apiGet,
  apiPost,
  getBackendUrl,
  isProjectRevisionConflict,
  type ApiError,
  type SignedProjectMediaRequest,
} from "../components/api";
import {
  ProjectRevisionConflict,
  expectedRevisionBody,
  projectRevision,
  projectRevisionFromResponse,
} from "../components/ProjectRevisionConflict";
import { RenewingVideo } from "../components/RenewingMedia";
import { StudioLayoutCustomizer } from "../components/StudioLayoutCustomizer";
import { useStudioPageLayout } from "../components/studioLayout";
import { resolveProjectId } from "../components/projectSelection";
import { useStudioSession } from "../components/studioSession";
import { useSignedProjectMedia } from "../hooks/useSignedProjectMedia";
import { ProjectJobsPanel } from "../shared/jobs/ProjectJobsPanel";
import { useProjectJobs } from "../shared/jobs/useProjectJobs";
import type { PageProps } from "../types/pageProps";

type ReviewPanelId = "controls" | "compare" | "continuity" | "renderJobs" | "livePublish";

type VariantArtifact = {
  path: string;
  name: string;
  kind: string;
  variant_index: number;
  review_state: string;
  review_notes?: string;
  engine?: string;
  model_id?: string;
  seed?: number | null;
  content_hash?: string | null;
};

type VariantGroup = {
  variant_index: number;
  label: string;
  mood?: string;
  artifacts: VariantArtifact[];
  review_summary: Record<string, number>;
};

export default function Review(props: PageProps) {
  const backendUrl = props.backendUrl || getBackendUrl();
  const { projectId, setProjectId } = useStudioSession();
  const [projects, setProjects] = useState<any[]>([]);
  const [review, setReview] = useState<any>(null);
  const [continuity, setContinuity] = useState<any>(null);
  const [publishStatus, setPublishStatus] = useState<any>(null);
  const [selectedPaths, setSelectedPaths] = useState<string[]>([]);
  const [notes, setNotes] = useState("");
  const [traits, setTraits] = useState("palette, motion");
  const [variantIndex, setVariantIndex] = useState(0);
  const [oscHost, setOscHost] = useState("127.0.0.1");
  const [oscPort, setOscPort] = useState(9000);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [jobInfo, setJobInfo] = useState<string | null>(null);
  const [autoRefreshJobs, setAutoRefreshJobs] = useState(true);
  const projectRevisionRef = useRef<number | null>(null);
  const [revisionConflict, setRevisionConflict] = useState<ApiError | null>(null);

  const {
    jobs,
    selectedLog,
    setSelectedLog,
    lastRefreshAt,
    error: jobError,
    setError: setJobError,
    refresh: refreshJobs,
    loadJobLog,
    runJobAction,
    resumeFromCheckpoint,
    restartClean,
  } = useProjectJobs({
    projectId,
    autoRefresh: autoRefreshJobs && Boolean(projectId),
    refreshIntervalMs: 2500,
  });

  const panelDefinitions = useMemo(
    () => [
      { id: "controls" as const, label: "Project", description: "Pick a project and refresh review groups." },
      { id: "compare" as const, label: "Variant compare", description: "Synchronized artifact compare and approval actions." },
      { id: "continuity" as const, label: "Continuity", description: "Pre-render continuity warnings from Visual DNA and scenes." },
      { id: "renderJobs" as const, label: "Render jobs", description: "Pause, cancel, retry, logs, and recovery parity with Render Queue." },
      { id: "livePublish" as const, label: "Live publish", description: "Experimental OSC/MIDI/WebSocket cue playback." },
    ],
    [],
  );
  const {
    visibleOrder,
    layoutState,
    movePanel,
    updateHidden,
    resetLayout,
    profileOptions,
    activeProfile,
    setActiveProfile,
  } = useStudioPageLayout<ReviewPanelId>("review", panelDefinitions.map((panel) => panel.id));

  const panelControlItems = layoutState.order.map((panelId, index) => ({
    id: panelId,
    label: panelDefinitions.find((panel) => panel.id === panelId)?.label || panelId,
    description: panelDefinitions.find((panel) => panel.id === panelId)?.description || "",
    hidden: layoutState.hidden.includes(panelId),
    canMoveUp: index > 0,
    canMoveDown: index < layoutState.order.length - 1,
  }));

  const refreshProjects = async () => {
    const data = await apiGet("/v1/projects");
    const items = data.projects || [];
    setProjects(items);
    const nextProjectId = resolveProjectId(items, projectId);
    if (nextProjectId !== projectId) setProjectId(nextProjectId);
  };

  const refreshReview = async (pid: string) => {
    const encodedProjectId = encodeURIComponent(pid);
    const [projectData, reviewData, continuityData, publishData] = await Promise.all([
      apiGet(`/v1/projects/${encodedProjectId}`),
      apiGet(`/v1/projects/${encodedProjectId}/variant_review`),
      apiGet(`/v1/projects/${encodedProjectId}/render/conductor/continuity?variant_index=${variantIndex}`),
      apiGet(`/v1/projects/${encodedProjectId}/live_cues/publish/status`),
    ]);
    projectRevisionRef.current = projectRevision(projectData?.project);
    setRevisionConflict(null);
    setReview(reviewData.variant_review || null);
    setContinuity(continuityData.continuity || null);
    setPublishStatus(publishData.publish || null);
  };

  useEffect(() => {
    refreshProjects().catch(() => {});
  }, [backendUrl]);

  useEffect(() => {
    if (!projectId) {
      projectRevisionRef.current = null;
      setRevisionConflict(null);
      return;
    }
    refreshReview(projectId).catch((error) => setErr(String(error)));
  }, [projectId, variantIndex, backendUrl]);

  const groups = (review?.groups || []) as VariantGroup[];
  const mediaRequests = useMemo<SignedProjectMediaRequest[]>(
    () => Array.from(new Set(
      groups.flatMap((group) => group.artifacts.map((artifact) => String(artifact.path || "").trim())),
    ))
      .filter(Boolean)
      .map((path) => ({ purpose: "file", path })),
    [review],
  );
  const signedMedia = useSignedProjectMedia(projectId, mediaRequests, backendUrl);
  const fileUrl = (rel: string) => signedMedia.urlFor({ purpose: "file", path: rel });
  const selectedArtifacts = groups
    .flatMap((group) => group.artifacts)
    .filter((artifact) => selectedPaths.includes(artifact.path));

  const toggleSelect = (path: string) => {
    setSelectedPaths((current) =>
      current.includes(path) ? current.filter((item) => item !== path) : [...current, path].slice(-4),
    );
  };

  const withExpectedRevision = <T extends Record<string, unknown>,>(body: T) =>
    expectedRevisionBody(body, { revision: projectRevisionRef.current });

  const setRevisionFromResponse = (response: unknown) => {
    const revision = projectRevisionFromResponse(response);
    if (revision != null) projectRevisionRef.current = revision;
  };

  const reportMutationError = (error: unknown) => {
    if (isProjectRevisionConflict(error)) setRevisionConflict(error);
  };

  const applyDecision = async (decision: "approved" | "rejected" | "cherry_picked") => {
    if (!projectId || !selectedPaths.length) {
      setErr("Select at least one artifact to review.");
      return;
    }
    setBusy(true);
    setErr(null);
    setInfo(null);
    try {
      for (const path of selectedPaths) {
      const result = await apiPost(`/v1/projects/${encodeURIComponent(projectId)}/variant_review/decision`, withExpectedRevision({
          artifact_path: path,
          decision,
          notes,
          cherry_pick_traits: decision === "cherry_picked"
            ? traits.split(",").map((item) => item.trim()).filter(Boolean)
            : [],
          lock_fields: decision === "approved" ? ["timing", "reference"] : [],
        }));
        setRevisionFromResponse(result);
      }
      setInfo(`${decision} applied to ${selectedPaths.length} artifact(s).`);
      setSelectedPaths([]);
      await refreshReview(projectId);
    } catch (error: any) {
      reportMutationError(error);
      setErr(String(error));
    } finally {
      setBusy(false);
    }
  };

  const startPublish = async () => {
    if (!projectId) return;
    setBusy(true);
    setErr(null);
    try {
      const result = await apiPost(`/v1/projects/${encodeURIComponent(projectId)}/live_cues/publish/start`, withExpectedRevision({
        osc_host: oscHost,
        osc_port: oscPort,
        midi_enabled: true,
        websocket_enabled: true,
        playback_speed: 4,
      }));
      setRevisionFromResponse(result);
      setPublishStatus(result.publish || null);
      setInfo("Live cue publish started (experimental).");
    } catch (error: any) {
      reportMutationError(error);
      setErr(String(error));
    } finally {
      setBusy(false);
    }
  };

  const stopPublish = async () => {
    if (!projectId) return;
    setBusy(true);
    setErr(null);
    try {
      const result = await apiPost(
        `/v1/projects/${encodeURIComponent(projectId)}/live_cues/publish/stop`,
        withExpectedRevision({}),
      );
      setRevisionFromResponse(result);
      setPublishStatus(result.publish || null);
      setInfo("Live cue publish stopped.");
    } catch (error: any) {
      reportMutationError(error);
      setErr(String(error));
    } finally {
      setBusy(false);
    }
  };

  const exportAdapter = async (adapter: "touchdesigner" | "unreal") => {
    if (!projectId) return;
    setBusy(true);
    setErr(null);
    try {
      const result = await apiPost(`/v1/projects/${encodeURIComponent(projectId)}/world_adapters/export`, withExpectedRevision({
        adapter,
        variant_index: variantIndex,
        sequence_name: "EDMG_LiveSet",
      }));
      setRevisionFromResponse(result);
      setInfo(`${adapter} adapter export ready (${result.simulation?.simulated_events ?? 0} events).`);
    } catch (error: any) {
      reportMutationError(error);
      setErr(String(error));
    } finally {
      setBusy(false);
    }
  };

  const panelContent: Record<ReviewPanelId, React.ReactNode> = {
    controls: (
      <div className="card">
        <div className="timeline-kicker">Review</div>
        <h2>Variant Review</h2>
        <div className="small">Compare rendered variants, approve results, cherry-pick traits, and inspect provenance before final export.</div>
        <div className="row" style={{ marginTop: 12, gap: 12, flexWrap: "wrap" }}>
          <label>
            Project
            <select value={projectId} onChange={(event) => setProjectId(event.target.value)}>
              {!projects.length && <option value="">No projects</option>}
              {projects.map((item) => (
                <option key={item.id} value={item.id}>{item.name}</option>
              ))}
            </select>
          </label>
          <label>
            Plan variant
            <select value={variantIndex} onChange={(event) => setVariantIndex(Number(event.target.value))}>
              {Array.from({ length: Math.max(review?.plan_variant_count || 1, 1) }).map((_, index) => (
                <option key={index} value={index}>Variant {index + 1}</option>
              ))}
            </select>
          </label>
          <button className="secondary" onClick={() => projectId && refreshReview(projectId)} disabled={!projectId}>Refresh</button>
        </div>
        {review ? (
          <div className="small" style={{ marginTop: 10 }}>
            {review.artifact_count} artifact(s) • compare ready: <b>{review.compare_ready ? "yes" : "no"}</b>
          </div>
        ) : null}
        {info ? <div className="small" style={{ marginTop: 8, color: "var(--success-text,#16a34a)" }}>{info}</div> : null}
        {err ? <div className="small" style={{ marginTop: 8, color: "var(--danger)" }}>{err}</div> : null}
      </div>
    ),
    compare: (
      <div className="card">
        <div style={{ fontWeight: 800 }}>Synchronized compare</div>
        <div className="small" style={{ marginTop: 6 }}>Select up to four artifacts. Approval writes review state to artifact manifests.</div>
        <label style={{ display: "block", marginTop: 12 }}>
          Review notes
          <input value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="Why this variant should survive" />
        </label>
        <label style={{ display: "block", marginTop: 8 }}>
          Cherry-pick traits
          <input value={traits} onChange={(event) => setTraits(event.target.value)} placeholder="palette, motion, reference frame" />
        </label>
        <div className="row" style={{ marginTop: 12, gap: 8, flexWrap: "wrap" }}>
          <button onClick={() => applyDecision("approved")} disabled={busy || !selectedPaths.length}>Approve</button>
          <button className="secondary" onClick={() => applyDecision("cherry_picked")} disabled={busy || !selectedPaths.length}>Cherry-pick traits</button>
          <button className="secondary" onClick={() => applyDecision("rejected")} disabled={busy || !selectedPaths.length}>Reject</button>
        </div>
        <div style={{ display: "grid", gap: 12, marginTop: 16, gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))" }}>
          {groups.map((group) => (
            <div key={group.variant_index} style={{ border: "1px solid var(--border)", borderRadius: 12, padding: 10 }}>
              <div style={{ fontWeight: 700 }}>{group.label || `Variant ${group.variant_index + 1}`}</div>
              <div className="small">{group.mood || "No mood"} • {group.artifacts.length} artifact(s)</div>
              {group.artifacts.map((artifact) => {
                const selected = selectedPaths.includes(artifact.path);
                return (
                  <div
                    key={artifact.path}
                    onClick={() => toggleSelect(artifact.path)}
                    style={{
                      marginTop: 8,
                      padding: 8,
                      borderRadius: 10,
                      border: selected ? "2px solid var(--accent,#6366f1)" : "1px solid var(--border)",
                      cursor: "pointer",
                    }}
                  >
                    {artifact.kind === "video" ? (
                      <RenewingVideo sourceUrl={fileUrl(artifact.path)} controls style={{ width: "100%", borderRadius: 8 }} />
                    ) : (
                      <img src={fileUrl(artifact.path)} alt={artifact.name} style={{ width: "100%", borderRadius: 8 }} />
                    )}
                    <div className="small" style={{ marginTop: 6 }}>
                      <b>{artifact.review_state}</b> • {artifact.engine || "engine?"} • {artifact.model_id || "model?"} • seed {artifact.seed ?? "auto"}
                    </div>
                    {artifact.content_hash ? <div className="small" style={{ opacity: 0.8 }}>{artifact.content_hash.slice(0, 16)}…</div> : null}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
        {selectedArtifacts.length >= 2 ? (
          <div className="small" style={{ marginTop: 12 }}>
            Comparing {selectedArtifacts.length} artifacts side-by-side for timing, provenance, and review state.
          </div>
        ) : null}
      </div>
    ),
    continuity: (
      <div className="card">
        <div style={{ fontWeight: 800 }}>Continuity validation</div>
        <div className="small" style={{ marginTop: 6 }}>
          Early warnings from Visual DNA anchors, scene prompts, and Render Conductor risk scores.
        </div>
        {continuity ? (
          <>
            <div className="small" style={{ marginTop: 10 }}>
              {continuity.warning_count} warning(s) • blocking: <b>{continuity.blocking_count}</b> • ok to render: <b>{continuity.ok_to_render ? "yes" : "no"}</b>
            </div>
            <div style={{ display: "grid", gap: 8, marginTop: 12 }}>
              {(continuity.warnings || []).map((warning: any, index: number) => (
                <div key={`${warning.code}-${index}`} style={{ border: "1px solid var(--border)", borderRadius: 10, padding: 8 }}>
                  <div style={{ fontWeight: 700 }}>{warning.code} • {warning.severity}</div>
                  <div className="small">{warning.scene_id}: {warning.message}</div>
                </div>
              ))}
            </div>
          </>
        ) : (
          <div className="small" style={{ marginTop: 10 }}>Run analysis and generate a plan to populate continuity checks.</div>
        )}
      </div>
    ),
    renderJobs: (
      <ProjectJobsPanel
        backendUrl={backendUrl}
        jobs={jobs}
        selectedLog={selectedLog}
        lastRefreshAt={lastRefreshAt}
        error={jobError}
        info={jobInfo}
        autoRefresh={autoRefreshJobs}
        onAutoRefreshChange={setAutoRefreshJobs}
        onRefresh={refreshJobs}
        onViewLog={loadJobLog}
        onCloseLog={() => setSelectedLog(null)}
        onJobAction={runJobAction}
        onResumeFromCheckpoint={resumeFromCheckpoint}
        onRestartClean={restartClean}
        onNavigateToQueue={() => props.onNavigate?.("queue")}
        onDesktopActionMessage={setJobInfo}
        onDesktopActionError={(message) => {
          setJobInfo(null);
          setJobError(message);
        }}
        continuityBlockingCount={Number(continuity?.blocking_count || 0)}
        title="Project render jobs"
        description="Same pause, cancel, retry, log, reveal, and checkpoint recovery controls as Render Queue while reviewing variants."
      />
    ),
    livePublish: (
      <div className="card">
        <div style={{ fontWeight: 800 }}>Live cue publishers (Labs)</div>
        <div className="small" style={{ marginTop: 6 }}>Experimental OSC/MIDI/WebSocket playback compiled from the Music Graph.</div>
        <div className="row" style={{ marginTop: 12, gap: 12, flexWrap: "wrap" }}>
          <label>
            OSC host
            <input value={oscHost} onChange={(event) => setOscHost(event.target.value)} />
          </label>
          <label>
            OSC port
            <input type="number" value={oscPort} onChange={(event) => setOscPort(Number(event.target.value))} />
          </label>
        </div>
        <div className="row" style={{ marginTop: 12, gap: 8, flexWrap: "wrap" }}>
          <button onClick={startPublish} disabled={busy || !projectId}>Start publish</button>
          <button className="secondary" onClick={stopPublish} disabled={busy || !projectId}>Stop</button>
          <button className="secondary" onClick={() => exportAdapter("touchdesigner")} disabled={busy || !projectId}>Export TouchDesigner</button>
          <button className="secondary" onClick={() => exportAdapter("unreal")} disabled={busy || !projectId}>Export Unreal</button>
        </div>
        {publishStatus ? (
          <div className="small" style={{ marginTop: 10 }}>
            Running: <b>{publishStatus.running ? "yes" : "no"}</b> • sent {publishStatus.sent_count ?? 0} • target {publishStatus.osc_target || "n/a"}
          </div>
        ) : null}
      </div>
    ),
  };

  return (
    <div className="page-stack">
      <ProjectRevisionConflict
        conflict={revisionConflict}
        onReload={async () => {
          if (!projectId) return;
          setErr(null);
          await refreshReview(projectId);
        }}
      />
      <StudioLayoutCustomizer
        title="Review layout"
        description="Reorder or hide variant compare, continuity, and live publish panels."
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
