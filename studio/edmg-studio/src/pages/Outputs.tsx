import React, { useEffect, useMemo, useState } from "react";
import { apiGet, apiPost, getBackendUrl } from "../components/api";
import { desktopActionLabel, runDesktopArtifactAction } from "../components/desktopArtifacts";
import type { PageProps } from "../types/pageProps";

export default function Outputs(props: PageProps) {
  const backendUrl = useMemo(() => getBackendUrl(), []);
  const [projects, setProjects] = useState<any[]>([]);
  const [projectId, setProjectId] = useState<string>("");
  const [outs, setOuts] = useState<any>(null);
  const [selected, setSelected] = useState<{ type: "image" | "video"; path: string } | null>(null);
  const [info, setInfo] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState<boolean>(true);
  const [lastRefreshAt, setLastRefreshAt] = useState<number>(0);

  const refreshProjects = async () => {
    const d = await apiGet("/v1/projects");
    const ps = d.projects || [];
    setProjects(ps);
    if (!projectId && ps.length) setProjectId(ps[0].id);
  };

  const refreshOutputs = async (pid: string) => {
    const d = await apiGet(`/v1/projects/${pid}/outputs`);
    setOuts(d);
    setLastRefreshAt(Date.now());
  };

  useEffect(() => { refreshProjects().catch(() => {}); }, [backendUrl]);
  useEffect(() => { if (projectId) refreshOutputs(projectId).catch((e) => setErr(String(e))); }, [projectId]);
  useEffect(() => {
    if (!projectId || !autoRefresh) return;
    const timer = window.setInterval(() => {
      refreshOutputs(projectId).catch((e) => setErr(String(e)));
    }, 2500);
    return () => window.clearInterval(timer);
  }, [projectId, autoRefresh, backendUrl]);

  const fileUrl = (pid: string, rel: string) => `${backendUrl}/v1/projects/${pid}/file?path=${encodeURIComponent(rel)}`;
  const activeInternalJobs = (outs?.active_internal_jobs || []) as any[];

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
      await apiPost(`/v1/projects/${projectId}/render/internal/video`, {
        variant_index: Number(entry?.variant_index ?? 0),
        model_id: String(entry?.mode === "proxy" ? "auto" : (entry?.model_id || "auto")),
        fps_render: Number(entry?.fps_render ?? 2),
        fps_output: Number(entry?.fps_output ?? 24),
        temporal_mode: String(entry?.temporal_mode || "frame_img2img"),
        render_mode: String(entry?.mode || "auto"),
        allow_proxy_fallback: true,
        resume_existing_frames: true,
      });
      await refreshOutputs(projectId);
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const resumeInternalJob = async (job: any) => {
    try {
      setErr(null);
      await apiPost(`/v1/projects/${job.project_id}/jobs/${job.id}/resume_from_checkpoint`, {});
      await refreshOutputs(job.project_id);
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const restartInternalJobClean = async (job: any) => {
    try {
      setErr(null);
      await apiPost(`/v1/projects/${job.project_id}/jobs/${job.id}/restart_clean`, {});
      await refreshOutputs(job.project_id);
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const cancelInternalJob = async (job: any) => {
    try {
      setErr(null);
      await apiPost(`/v1/projects/${job.project_id}/jobs/${job.id}/cancel`, {});
      await refreshOutputs(job.project_id);
    } catch (e: any) {
      setErr(String(e));
    }
  };


  return (
    <div>
      <h1>Outputs</h1>

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
            <button className="secondary" onClick={() => projectId && refreshOutputs(projectId)}>Refresh</button>
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
        {err && <div style={{ marginTop: 10, color: "var(--danger)" }}>{err}</div>}
        {!err && info ? <div className="small" style={{ marginTop: 10, opacity: 0.82 }}>Last desktop action: <b>{info.action || "ok"}</b>{info.path ? <> • {String(info.path)}</> : null}</div> : null}
      </div>

      {outs && (
        <div style={{ marginTop: 14 }}>
          {selected && (
            <div className="card" style={{ marginBottom: 14 }}>
              <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
                <div style={{ fontWeight: 800 }}>Preview</div>
                <button className="secondary" onClick={() => setSelected(null)}>Close</button>
              </div>
              <div className="small" style={{ marginTop: 6 }}>{selected.path}</div>
              {selected.type === "image" ? (
                <img
                  src={fileUrl(projectId, selected.path)}
                  style={{ width: "100%", marginTop: 10, borderRadius: 12, border: "1px solid var(--border)" }}
                />
              ) : (
                <video controls style={{ width: "100%", marginTop: 10, borderRadius: 12, border: "1px solid var(--border)" }}>
                  <source src={fileUrl(projectId, selected.path)} />
                </video>
              )}
            </div>
          )}

          {activeInternalJobs.length > 0 ? (
            <div className="card" style={{ marginBottom: 14 }}>
              <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
                <div style={{ fontWeight: 800 }}>Active / resumable internal renders</div>
                <button className="secondary" onClick={() => props.onNavigate?.("queue")}>Open Render Queue</button>
              </div>
              <div style={{ display: "grid", gap: 10, marginTop: 10 }}>
                {activeInternalJobs.map((job: any) => {
                  const cp = job?.progress?.runtime_checkpoint;
                  return (
                    <div key={job.id} style={{ border: "1px solid var(--border)", borderRadius: 12, padding: 10 }}>
                      <div className="small"><b>{job.status}</b> • {job.type} • {job.progress?.stage || "queued"}</div>
                      {job.progress?.message ? <div className="small" style={{ marginTop: 4, opacity: 0.85 }}>{job.progress.message}</div> : null}
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
                        <button className="secondary" onClick={() => resumeInternalJob(job)} disabled={job.status === "queued" || job.status === "running"}>Resume from checkpoint</button>
                        <button className="secondary" onClick={() => restartInternalJobClean(job)} disabled={job.status === "queued" || job.status === "running"}>Restart clean</button>
                        <button className="secondary" onClick={() => cancelInternalJob(job)} disabled={job.status !== "queued" && job.status !== "running"}>Cancel</button>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          ) : null}

          {outs.latest_internal_render ? (
            <div className="card" style={{ marginBottom: 14 }}>
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
                <a className="secondary" href={fileUrl(projectId, outs.latest_internal_render.video)} target="_blank" rel="noreferrer">Open latest internal video</a>
                <button className="secondary" onClick={() => handleArtifactPathAction("latest internal video", outs.latest_internal_render.video, "reveal")}>{desktopActionLabel("reveal", "latest internal video")}</button>
                {outs.latest_internal_render.runtime_checkpoint?.outputs?.checkpoint_json ? <button className="secondary" onClick={() => handleArtifactPathAction("checkpoint", outs.latest_internal_render.runtime_checkpoint.outputs.checkpoint_json, "reveal")}>{desktopActionLabel("reveal", "checkpoint")}</button> : null}
                <button className="secondary" onClick={() => retryInternalFromHistory(outs.latest_internal_render)}>Retry with cached frames</button>
              </div>
            </div>
          ) : null}

          {(outs.internal_render_history?.length ?? 0) > 0 ? (
            <div className="card" style={{ marginBottom: 14 }}>
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
                      <a className="secondary" href={fileUrl(projectId, entry.video)} target="_blank" rel="noreferrer">Open</a>
                      <button className="secondary" onClick={() => handleArtifactPathAction("history video", entry.video, "reveal")}>{desktopActionLabel("reveal", "history video")}</button>
                      {entry.runtime_checkpoint?.outputs?.checkpoint_json ? <button className="secondary" onClick={() => handleArtifactPathAction("checkpoint", entry.runtime_checkpoint.outputs.checkpoint_json, "reveal")}>{desktopActionLabel("reveal", "checkpoint")}</button> : null}
                      <button className="secondary" onClick={() => retryInternalFromHistory(entry)}>Retry</button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          <div className="grid2">
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
                  <video controls style={{ width: "100%", borderRadius: 12, border: "1px solid var(--border)" }}>
                    <source src={fileUrl(projectId, v.path)} />
                  </video>
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
                      src={fileUrl(projectId, im.path)}
                      style={{ width: "100%", borderRadius: 12, border: "1px solid var(--border)" }}
                    />
                    <div className="small" style={{ marginTop: 6, opacity: 0.8, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {im.path.split("/").slice(-1)[0]}
                    </div>
                    <div className="row" style={{ gap: 8, marginTop: 6, flexWrap: "wrap" }}>
                      <button className="secondary" onClick={(e) => { e.stopPropagation(); handleArtifactPathAction("image", im.path, "reveal"); }}>{desktopActionLabel("reveal", "image")}</button>
                    </div>
                  </div>
                ))}
              </div>

              {outs.deforum_exports?.length ? (
                <>
                  <hr />
                  <div style={{ fontWeight: 800, marginBottom: 10 }}>Deforum exports</div>
                  {outs.deforum_exports.map((p: any) => (
                    <div key={p.path} className="small">
                      <a href={fileUrl(projectId, p.path)} target="_blank" rel="noreferrer">{p.path}</a>
                    </div>
                  ))}
                </>
              ) : null}
            </div>
          </div>

          <div className="card" style={{ marginTop: 14 }}>
            <div style={{ fontWeight: 800, marginBottom: 10 }}>Metadata</div>
            <pre>{JSON.stringify(outs, null, 2)}</pre>
          </div>
        </div>
      )}
    </div>
  );
}
