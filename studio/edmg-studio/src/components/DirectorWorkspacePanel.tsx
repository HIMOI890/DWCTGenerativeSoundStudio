import React, { useEffect, useMemo, useState } from "react";
import { apiGet, apiPost } from "./api";
import { expectedRevisionBody, responseRevision } from "./ProjectRevisionConflict";

type DirectorWorkspacePanelProps = {
  backendUrl?: string;
  projectId: string;
  project: any;
  analysis: any;
  plan: any;
  selectedVariant: number;
  onRefreshProject: (projectId: string) => Promise<unknown>;
  onNavigate?: (destination: string) => void;
  onMutationError?: (error: unknown) => void;
};

type DirectorDocument = {
  version: number;
  story_bible: Record<string, any>;
  scenes: Array<Record<string, any>>;
  analysis_revision?: number | null;
  [key: string]: any;
};

const SAMPLE_RATE = 48_000;

function decimalSample(seconds: unknown): string {
  const value = Number(seconds);
  if (!Number.isFinite(value)) return "0";
  return Math.max(0, Math.round(value * SAMPLE_RATE)).toString(10);
}

function textField(scene: any, aliases: string[]): string {
  const sources = [scene, scene?.storyboard, scene?.prompt_pack, scene?.promptPack].filter(
    (source) => source && typeof source === "object",
  );
  for (const source of sources) {
    for (const alias of aliases) {
      const value = source?.[alias];
      if (value !== undefined && value !== null && String(value).trim()) return String(value).trim();
    }
  }
  return "";
}

function scenesFromStoryboard(plan: any, selectedVariant: number): Array<Record<string, any>> {
  const scenes = plan?.variants?.[selectedVariant]?.scenes;
  if (!Array.isArray(scenes)) return [];
  return scenes.map((scene: any, index: number) => {
    const characterLock = textField(scene, ["character_lock", "characterLock"]);
    const location = textField(scene, ["setting", "location", "location_hint", "locationHint"]);
    const action = textField(scene, ["action", "continuous_action", "continuousAction", "motion"]);
    const camera = textField(scene, ["camera", "camera_path", "cameraPath", "movement"]);
    const sceneId = String(scene?.id || scene?.scene_id || `workspace-scene-${index + 1}`);
    return {
      scene_id: sceneId,
      start_sample: decimalSample(scene?.start_s ?? index * 5),
      end_sample: decimalSample(scene?.end_s ?? (index + 1) * 5),
      intent: String(scene?.prompt || scene?.name || `Scene ${index + 1}`).trim(),
      continuity_mode: "continuous",
      subjects: characterLock
        ? [{ id: "primary", role: "primary", appearance_lock: true, appearance_notes: [characterLock] }]
        : [],
      actions: action ? [action] : [],
      camera: camera ? { movement: camera, shot_type: textField(scene, ["shot_type", "shotType", "composition"]) } : {},
      environment: location ? { location, secondary_motion: [textField(scene, ["environment_motion", "environmentMotion"])].filter(Boolean) } : {},
      renderer_hints: {
        source: "workspace_storyboard",
        name: String(scene?.name || `Scene ${index + 1}`),
        transition: String(scene?.transition || ""),
      },
    };
  });
}

function pretty(value: unknown): string {
  try {
    return JSON.stringify(value ?? [], null, 2);
  } catch {
    return "[]";
  }
}

function projectRevision(project: any): number | null {
  const revision = Number(project?.revision);
  return Number.isInteger(revision) && revision >= 1 ? revision : null;
}

export default function DirectorWorkspacePanel({
  backendUrl,
  projectId,
  project,
  analysis,
  plan,
  selectedVariant,
  onRefreshProject,
  onNavigate,
  onMutationError,
}: DirectorWorkspacePanelProps) {
  const [document, setDocument] = useState<DirectorDocument | null>(null);
  const [documentRevision, setDocumentRevision] = useState<number | null>(projectRevision(project));
  const [theme, setTheme] = useState("");
  const [style, setStyle] = useState("");
  const [instruction, setInstruction] = useState("");
  const [scenesText, setScenesText] = useState("[]");
  const [engine, setEngine] = useState("hunyuan_video15");
  const [readinessEngine, setReadinessEngine] = useState("automatic");
  const [rendererMode, setRendererMode] = useState("automatic");
  const [readiness, setReadiness] = useState<any>(null);
  const [readinessLoading, setReadinessLoading] = useState(false);
  const [promptPreview, setPromptPreview] = useState("");
  const [draftText, setDraftText] = useState("");
  const [jobId, setJobId] = useState("");
  const [reviewedJobId, setReviewedJobId] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);

  const revision = documentRevision ?? projectRevision(project);
  const selectedStoryboardScenes = useMemo(
    () => scenesFromStoryboard(plan, selectedVariant),
    [plan, selectedVariant],
  );

  useEffect(() => {
    let cancelled = false;
    if (!projectId) {
      setReadiness(null);
      return undefined;
    }
    setReadinessLoading(true);
    apiGet(
      `/v1/projects/${encodeURIComponent(projectId)}/director/readiness?mode=${encodeURIComponent(rendererMode)}&engine=${encodeURIComponent(readinessEngine)}`,
      { backendUrl },
    )
      .then((response) => {
        if (!cancelled) setReadiness(response);
      })
      .catch((reason) => {
        if (!cancelled) {
          setReadiness(null);
          onMutationError?.(reason);
        }
      })
      .finally(() => {
        if (!cancelled) setReadinessLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [backendUrl, onMutationError, projectId, readinessEngine, rendererMode]);

  const applyDocument = (response: any) => {
    const next = (response?.document || response) as DirectorDocument;
    if (!next || typeof next !== "object") return;
    const bible = next.story_bible || {};
    setDocument(next);
    setDocumentRevision(responseRevision(response, revision));
    setTheme(String(bible.project_theme || ""));
    setStyle(String(bible.visual_style || ""));
    setScenesText(pretty(next.scenes || []));
    setPromptPreview("");
    setReviewedJobId("");
  };

  useEffect(() => {
    let cancelled = false;
    setDocument(null);
    setDocumentRevision(projectRevision(project));
    setDraftText("");
    setJobId("");
    setReviewedJobId("");
    setError("");
    setStatus("");
    if (!projectId) return undefined;
    setLoading(true);
    apiGet(`/v1/projects/${encodeURIComponent(projectId)}/director/document`, { backendUrl })
      .then((response) => {
        if (!cancelled) applyDocument(response);
      })
      .catch((reason) => {
        if (!cancelled) {
          setError(String(reason));
          onMutationError?.(reason);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // The active project is the only identity that should reload the document.
  }, [backendUrl, projectId]);

  const hasUnsavedChanges = useMemo(() => {
    if (!document) return false;
    try {
      const current = {
        ...document,
        story_bible: { ...document.story_bible, project_theme: theme, visual_style: style },
        scenes: JSON.parse(scenesText),
      };
      return JSON.stringify(current) !== JSON.stringify(document);
    } catch {
      return true;
    }
  }, [document, scenesText, style, theme]);

  const buildDocument = (): DirectorDocument => {
    if (!document) throw new Error("Director document is still loading.");
    const parsedScenes = JSON.parse(scenesText);
    if (!Array.isArray(parsedScenes)) throw new Error("SceneSpecs must be a JSON array.");
    return {
      ...document,
      story_bible: { ...document.story_bible, project_theme: theme, visual_style: style },
      scenes: parsedScenes,
      analysis_revision: Number.isInteger(Number(analysis?.revision)) ? Number(analysis.revision) : document.analysis_revision ?? null,
    };
  };

  const saveDirection = async () => {
    if (!projectId || revision == null) return;
    setBusy(true); setError(""); setStatus("");
    try {
      const next = buildDocument();
      const response = await apiPost(
        `/v1/projects/${encodeURIComponent(projectId)}/director/document`,
        expectedRevisionBody({ document: next }, { revision }),
        { backendUrl },
      );
      applyDocument(response);
      await onRefreshProject(projectId);
      setStatus("Story Bible and SceneSpecs saved to this Workspace project.");
    } catch (reason) {
      setError(String(reason));
      onMutationError?.(reason);
    } finally { setBusy(false); }
  };

  const syncStoryboard = () => {
    if (!selectedStoryboardScenes.length) {
      setError("Generate or select a storyboard variant before syncing scenes into Director.");
      return;
    }
    setScenesText(pretty(selectedStoryboardScenes));
    if (!theme) setTheme(String(project?.name || ""));
    if (!style) setStyle(String(analysis?.tags?.slice?.(0, 5)?.join(", ") || "cinematic continuity"));
    setStatus(`${selectedStoryboardScenes.length} selected storyboard scene${selectedStoryboardScenes.length === 1 ? "" : "s"} staged for Director. Save direction to commit them.`);
    setError("");
  };

  const compilePrompts = async () => {
    if (!projectId) return;
    setBusy(true); setError(""); setStatus("");
    try {
      const response = await apiGet(
        `/v1/projects/${encodeURIComponent(projectId)}/director/prompts?engine=${encodeURIComponent(engine)}`,
        { backendUrl },
      );
      const packages = Array.isArray(response?.packages) ? response.packages : [];
      setPromptPreview(packages.map((item: any) => `[${item.scene_id}] ${item.prompt}`).join("\n\n"));
      setStatus(`Prepared ${packages.length} ${engine} prompt package${packages.length === 1 ? "" : "s"}. No generation job was submitted.`);
    } catch (reason) {
      setError(String(reason));
      onMutationError?.(reason);
    } finally { setBusy(false); }
  };

  const generateDraft = async () => {
    if (!projectId || revision == null) return;
    if (hasUnsavedChanges) {
      setError("Save direction before generating a Director draft so the job has a stable Workspace revision.");
      return;
    }
    if (!instruction.trim()) {
      setError("Enter a direction instruction before generating a draft.");
      return;
    }
    setBusy(true); setError(""); setStatus("");
    try {
      const response = await apiPost(
        `/v1/projects/${encodeURIComponent(projectId)}/director/generate`,
        expectedRevisionBody({ operation_id: `workspace-director-${Date.now()}`, instruction: instruction.trim() }, { revision }),
        { backendUrl },
      );
      const nextJobId = String(response?.job_id || "");
      setJobId(nextJobId);
      setReviewedJobId("");
      setDraftText("Draft queued. Use Review draft after the Director job finishes.");
      setStatus(nextJobId ? `Director draft queued in the shared project queue (${nextJobId}).` : "Director draft queued in the shared project queue.");
    } catch (reason) {
      setError(String(reason));
      onMutationError?.(reason);
    } finally { setBusy(false); }
  };

  const reviewDraft = async () => {
    if (!projectId || !jobId) {
      setError("Generate a Director draft first, then review its queue job here.");
      return;
    }
    setBusy(true); setError(""); setStatus("");
    try {
      const response = await apiGet(
        `/v1/projects/${encodeURIComponent(projectId)}/director/drafts/${encodeURIComponent(jobId)}`,
        { backendUrl },
      );
      if (response?.status !== "succeeded") {
        setDraftText(`Job status: ${response?.status || "unknown"}${response?.error ? `\n${response.error}` : ""}`);
        setStatus("The draft is not ready yet. Review again after the queue reports completion.");
        setReviewedJobId("");
        return;
      }
      const draft = response?.result?.document || response?.result;
      setDraftText(pretty(draft));
      setReviewedJobId(jobId);
      setStatus("Draft loaded for review. Apply it only after checking the Story Bible and scene constraints.");
    } catch (reason) {
      setError(String(reason));
      onMutationError?.(reason);
    } finally { setBusy(false); }
  };

  const applyDraft = async () => {
    if (!projectId || !reviewedJobId || revision == null) return;
    setBusy(true); setError(""); setStatus("");
    try {
      const response = await apiPost(
        `/v1/projects/${encodeURIComponent(projectId)}/director/drafts/${encodeURIComponent(reviewedJobId)}/apply`,
        expectedRevisionBody({}, { revision }),
        { backendUrl },
      );
      applyDocument(response);
      await onRefreshProject(projectId);
      setDraftText("Reviewed Director draft applied to the shared Workspace direction.");
      setStatus("Director direction applied. Planner, Timeline, Render, and Electron/WinUI clients now see the same project revision.");
    } catch (reason) {
      setError(String(reason));
      onMutationError?.(reason);
    } finally { setBusy(false); }
  };

  if (!projectId) {
    return <div className="small">Choose a project in Workspace to activate Director.</div>;
  }

  return (
    <div className="workspace-directorPanel">
      <div className="workspace-panelHeader">
        <div>
          <div className="workspace-sectionTitle">EDMG Director</div>
          <div className="small">Director is embedded in this Workspace session. It reads the selected analysis and storyboard, saves into the same project revision, and hands prompts/jobs to Timeline, Render, and Queue.</div>
        </div>
        <div className="workspace-panelActions">
          <button className="secondary" type="button" onClick={() => onNavigate?.("directorLab")}>Open full Director</button>
          <button className="secondary" type="button" onClick={() => void onRefreshProject(projectId)} disabled={busy || loading}>Refresh session</button>
        </div>
      </div>

      <div className="workspace-directorMeta">
        <div className="workspace-handoffCard"><div className="workspace-handoffLabel">Project</div><strong>{project?.name || projectId}</strong></div>
        <div className="workspace-handoffCard"><div className="workspace-handoffLabel">Workspace revision</div><strong>{revision ?? "pending"}</strong></div>
        <div className="workspace-handoffCard"><div className="workspace-handoffLabel">Storyboard source</div><strong>{selectedStoryboardScenes.length ? `${selectedStoryboardScenes.length} selected scene(s)` : "not available"}</strong></div>
        <div className="workspace-handoffCard"><div className="workspace-handoffLabel">Analysis</div><strong>{analysis ? "available" : "run analysis first"}</strong></div>
      </div>

      <div className="workspace-directorReadiness">
        <div className="workspace-directorReadinessHeader">
          <div>
            <div className="workspace-directorSubhead">Internal engine readiness</div>
            <div className="small">The resolver checks the active hardware and model cache before any Director or renderer worker loads weights.</div>
          </div>
          {readinessLoading ? <span className="small">Checking…</span> : null}
        </div>
        <div className="workspace-directorReadinessControls">
          <label className="workspace-directorField"><span>Renderer mode</span><select value={rendererMode} onChange={(event) => setRendererMode(event.target.value)} disabled={busy || readinessLoading}><option value="automatic">Automatic</option><option value="fast">Fast</option><option value="quality">Quality</option><option value="maximum">Maximum</option></select></label>
          <label className="workspace-directorField"><span>Renderer override</span><select value={readinessEngine} onChange={(event) => setReadinessEngine(event.target.value)} disabled={busy || readinessLoading}><option value="automatic">Automatic engine</option><option value="hunyuan_video15">HunyuanVideo-1.5</option><option value="ltx_25">LTX-2.5</option><option value="external">External provider</option></select></label>
        </div>
        {readiness ? (
          <>
            <div className="workspace-directorReadinessGrid">
              <div className="workspace-handoffCard"><div className="workspace-handoffLabel">Resolved Director</div><strong>{readiness.director?.label || "—"}</strong><span className="small">{readiness.director?.profile || "profile pending"}</span></div>
              <div className="workspace-handoffCard"><div className="workspace-handoffLabel">Resolved renderer</div><strong>{readiness.renderer?.label || "—"}</strong><span className="small">{readiness.renderer?.profile || "profile pending"}</span></div>
              <div className="workspace-handoffCard"><div className="workspace-handoffLabel">Hardware tier</div><strong>{readiness.hardware_tier || "unknown"}</strong><span className="small">{readiness.hardware?.device_name || readiness.hardware?.backend || "probe unavailable"}</span></div>
              <div className="workspace-handoffCard"><div className="workspace-handoffLabel">Admission</div><strong className={readiness.ready ? "workspace-readinessReady" : "workspace-readinessBlocked"}>{readiness.ready ? "Ready" : "Blocked"}</strong><span className="small">No model load has been attempted.</span></div>
            </div>
            {Array.isArray(readiness.blockers) && readiness.blockers.length ? <div className="workspace-directorReadinessList"><strong>Blockers</strong><ul>{readiness.blockers.map((item: string, index: number) => <li key={`blocker-${index}`}>{item}</li>)}</ul></div> : null}
            {Array.isArray(readiness.warnings) && readiness.warnings.length ? <div className="workspace-directorReadinessList"><strong>Resolution notes</strong><ul>{readiness.warnings.map((item: string, index: number) => <li key={`warning-${index}`}>{item}</li>)}</ul></div> : null}
            {Array.isArray(readiness.actions) && readiness.actions.length ? <div className="workspace-directorReadinessList"><strong>Next actions</strong><ul>{readiness.actions.map((item: string, index: number) => <li key={`action-${index}`}>{item}</li>)}</ul></div> : null}
          </>
        ) : <div className="small">Readiness is unavailable until the Workspace backend responds.</div>}
      </div>

      <div className="workspace-directorGrid">
        <div className="workspace-directorColumn">
          <label className="workspace-directorField"><span>Project theme</span><input value={theme} onChange={(event) => setTheme(event.target.value)} disabled={loading || busy} placeholder="Theme, subject, or world" /></label>
          <label className="workspace-directorField"><span>Visual style</span><input value={style} onChange={(event) => setStyle(event.target.value)} disabled={loading || busy} placeholder="Palette, lens, texture, continuity" /></label>
          <label className="workspace-directorField"><span>Direction instruction</span><textarea value={instruction} onChange={(event) => setInstruction(event.target.value)} disabled={loading || busy} rows={4} placeholder="Describe the next approved direction pass for the selected range or storyboard." /></label>
          <div className="row workspace-actionRow" style={{ gap: 8, flexWrap: "wrap" }}>
            <button type="button" onClick={syncStoryboard} disabled={busy || loading || !selectedStoryboardScenes.length}>Use selected storyboard</button>
            <button type="button" onClick={() => void saveDirection()} disabled={busy || loading || !hasUnsavedChanges || revision == null}>Save direction</button>
          </div>
          <label className="workspace-directorField"><span>SceneSpecs (advanced JSON)</span><textarea value={scenesText} onChange={(event) => setScenesText(event.target.value)} disabled={loading || busy} rows={12} spellCheck={false} /></label>
        </div>
        <div className="workspace-directorColumn">
          <div className="workspace-directorSubhead">Prepare and generate</div>
          <div className="small">Prompt preparation is deterministic and does not submit a job. Generation stays in the backend worker and returns a reviewable draft.</div>
          <label className="workspace-directorField"><span>Prompt compiler</span><select value={engine} onChange={(event) => setEngine(event.target.value)} disabled={busy || loading}><option value="hunyuan_video15">HunyuanVideo-1.5</option><option value="ltx_25">LTX-2.5</option><option value="external">External provider</option></select></label>
          <div className="row workspace-actionRow" style={{ gap: 8, flexWrap: "wrap" }}>
            <button className="secondary" type="button" onClick={() => void compilePrompts()} disabled={busy || loading || !document?.scenes?.length}>Prepare prompts</button>
            <button type="button" onClick={() => void generateDraft()} disabled={busy || loading || !document?.scenes?.length || revision == null}>Generate draft</button>
          </div>
          <textarea className="workspace-directorOutput" aria-label="Prepared Director prompts" value={promptPreview} readOnly rows={8} placeholder="Prepared renderer-specific prompts appear here." />
          <div className="workspace-directorSubhead">Draft review</div>
          <div className="small">{jobId ? `Queue job: ${jobId}` : "No Director job selected."}</div>
          <div className="row workspace-actionRow" style={{ gap: 8, flexWrap: "wrap" }}>
            <button className="secondary" type="button" onClick={() => void reviewDraft()} disabled={busy || loading || !jobId}>Review draft</button>
            <button type="button" onClick={() => void applyDraft()} disabled={busy || loading || !reviewedJobId || revision == null}>Apply reviewed draft</button>
          </div>
          <textarea className="workspace-directorOutput" aria-label="Director draft review" value={draftText} readOnly rows={10} placeholder="A completed draft will appear here for review." />
        </div>
      </div>

      {loading ? <div className="small" style={{ marginTop: 10 }}>Loading Director document for this project…</div> : null}
      {status ? <div className="small" style={{ marginTop: 10, color: "var(--success, #6ecb8b)" }}>{status}</div> : null}
      {error ? <div role="alert" style={{ marginTop: 10, color: "var(--danger)" }}>{error}</div> : null}
    </div>
  );
}
