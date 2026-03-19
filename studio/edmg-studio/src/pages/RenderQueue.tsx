import React, { useEffect, useMemo, useState } from "react";
import { apiGet, apiPost, getBackendUrl } from "../components/api";
import { desktopActionLabel, runDesktopArtifactAction } from "../components/desktopArtifacts";
import type { PageProps } from "../types/pageProps";

export default function RenderQueue(_props: PageProps) {
  const backendUrl = useMemo(() => getBackendUrl(), []);
  const [jobs, setJobs] = useState<any[]>([]);
  const [projects, setProjects] = useState<any[]>([]);
  const [projectId, setProjectId] = useState<string>("");
  const [selectedLog, setSelectedLog] = useState<{ job: any; log: string } | null>(null);
  const [info, setInfo] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState<boolean>(true);
  const [lastRefreshAt, setLastRefreshAt] = useState<number>(0);

  const refresh = async () => {
    const p = await apiGet("/v1/projects");
    const ps = p.projects || [];
    setProjects(ps);
    if (!projectId && ps.length) setProjectId(ps[0].id);

    const d = await apiGet("/v1/jobs");
    setJobs(d.jobs || []);
    setLastRefreshAt(Date.now());
  };

  useEffect(() => {
    refresh().catch((e) => setErr(String(e)));
  }, [backendUrl]);

  useEffect(() => {
    if (!autoRefresh) return;
    const timer = window.setInterval(async () => {
      try {
        await refresh();
        if (selectedLog?.job) {
          const d = await apiGet(`/v1/projects/${selectedLog.job.project_id}/jobs/${selectedLog.job.id}/log`);
          setSelectedLog((prev) => prev ? { ...prev, log: d.log || "" } : prev);
        }
      } catch (e: any) {
        setErr(String(e));
      }
    }, 2500);
    return () => window.clearInterval(timer);
  }, [autoRefresh, projectId, backendUrl, selectedLog?.job?.id]);

  const tick = async () => {
    setErr(null);
    try {
      await apiPost("/v1/jobs/tick", {});
      await refresh();
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const cancel = async (job: any) => {
    setErr(null);
    try {
      await apiPost(`/v1/projects/${job.project_id}/jobs/${job.id}/cancel`, {});
      await refresh();
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const retry = async (job: any) => {
    setErr(null);
    try {
      await apiPost(`/v1/projects/${job.project_id}/jobs/${job.id}/retry`, {});
      await refresh();
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const viewLog = async (job: any) => {
    setErr(null);
    try {
      const d = await apiGet(`/v1/projects/${job.project_id}/jobs/${job.id}/log`);
      setSelectedLog({ job, log: d.log || "" });
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const resumeFromCheckpoint = async (job: any) => {
    setErr(null);
    try {
      await apiPost(`/v1/projects/${job.project_id}/jobs/${job.id}/resume_from_checkpoint`, {});
      await refresh();
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const restartClean = async (job: any) => {
    setErr(null);
    try {
      await apiPost(`/v1/projects/${job.project_id}/jobs/${job.id}/restart_clean`, {});
      await refresh();
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const filtered = projectId ? jobs.filter((j) => j.project_id === projectId) : jobs;
  const fileUrl = (pid: string, rel: string) => `${backendUrl}/v1/projects/${pid}/file?path=${encodeURIComponent(rel)}`;
  const activeCount = filtered.filter((j) => ["queued", "running"].includes(j.status)).length;
  const resumableCount = filtered.filter((j) => j.type === "internal_video" && ["failed", "canceled", "succeeded"].includes(j.status)).length;
  const runtimeSummary = (job: any) => {
    const cp = job?.progress?.runtime_checkpoint;
    if (!cp) return null;
    return {
      percent: Number(cp.resume_percent ?? 0),
      chunks: `${Number(cp.completed_chunks ?? 0)}/${Number(cp.estimated_chunks ?? 1)}`,
      nextFrame: `${Math.min(Number(cp.next_frame_index ?? 0) + 1, Number(cp.total_frames ?? 0) || 0)}/${Number(cp.total_frames ?? 0)}`,
      strategy: cp.chunk_strategy || "single_pass",
      canResume: Boolean(cp.can_resume),
      checkpointPath: cp?.outputs?.checkpoint_json || "",
    };
  };

  const handleArtifactPathAction = async (label: string, value: string | null | undefined, mode: "reveal" | "open") => {
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

  return (
    <div>
      <h1>Render Queue</h1>

      <div className="card">
        <div className="row">
          <button onClick={tick}>Tick Worker (process 1 job)</button>
          <button className="secondary" onClick={refresh}>Refresh</button>
          <div style={{ flex: 1 }} />
          <div>
            <div className="small">Project</div>
            <select value={projectId} onChange={(e) => setProjectId(e.target.value)}>
              {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </div>
        </div>
        <div className="small" style={{ marginTop: 10 }}>
          This is intentionally local-first for reliability. The backend runs an always-on worker by default; use this view for logs, resume actions, and clean restarts.
        </div>
        <div className="row" style={{ gap: 12, marginTop: 10, flexWrap: "wrap", alignItems: "center" }}>
          <label className="row small" style={{ gap: 6, alignItems: "center" }}>
            <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
            Live poll queue every 2.5s
          </label>
          <div className="small" style={{ opacity: 0.8 }}>
            Active <b>{activeCount}</b> • resumable <b>{resumableCount}</b>{lastRefreshAt ? <> • updated {new Date(lastRefreshAt).toLocaleTimeString()}</> : null}
          </div>
        </div>
        {err && <div style={{ marginTop: 10, color: "var(--danger)" }}>{err}</div>}
        {!err && info ? <div className="small" style={{ marginTop: 10, opacity: 0.82 }}>Last desktop action: <b>{info.action || "ok"}</b>{info.path ? <> • {String(info.path)}</> : null}</div> : null}
      </div>

      {selectedLog && (
        <div className="card" style={{ marginTop: 14 }}>
          <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ fontWeight: 800 }}>Job log</div>
            <button className="secondary" onClick={() => setSelectedLog(null)}>Close</button>
          </div>
          <div className="small" style={{ marginTop: 6 }}>
            {selectedLog.job.id} • {selectedLog.job.type} • {selectedLog.job.status}
          </div>
          <pre style={{ marginTop: 10, maxHeight: 300, overflow: "auto" }}>{selectedLog.log || "(no log yet)"}</pre>
        </div>
      )}

      <div className="card" style={{ marginTop: 14 }}>
        <div style={{ fontWeight: 800, marginBottom: 10 }}>Jobs</div>
        {!filtered.length && <div className="small">No jobs yet.</div>}
        {filtered.length > 0 && (
          <div style={{ overflowX: "auto" }}>
            <table>
              <thead>
                <tr>
                  <th>Created</th>
                  <th>Type</th>
                  <th>Status</th>
                  <th>Progress</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((j) => (
                  <tr key={j.id}>
                    <td className="small">{j.created_at}</td>
                    <td className="small">{j.type}</td>
                    <td className="small">{j.status}</td>
                    <td className="small">
                      {j.progress ? (
                        <>
                          <div>{j.progress.percent ?? 0}% • {j.progress.stage || "running"}</div>
                          {j.progress.message ? <div style={{ opacity: 0.8 }}>{j.progress.message}</div> : null}
                          {runtimeSummary(j) ? (
                            <>
                              <div style={{ opacity: 0.85, marginTop: 4 }}>
                                Resume {runtimeSummary(j)?.percent}% • chunks {runtimeSummary(j)?.chunks} • next frame {runtimeSummary(j)?.nextFrame}
                              </div>
                              <div style={{ opacity: 0.75 }}>
                                {runtimeSummary(j)?.strategy} • {runtimeSummary(j)?.canResume ? "resume-ready" : "non-resumable"}
                              </div>
                              {runtimeSummary(j)?.checkpointPath ? <div style={{ opacity: 0.65 }}>{runtimeSummary(j)?.checkpointPath}</div> : null}
                            </>
                          ) : null}
                        </>
                      ) : "—"}
                    </td>
                    <td>
                      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                        <button className="secondary" onClick={() => viewLog(j)}>Log</button>
                        {j?.result?.video ? (
                          <>
                            <a className="secondary" href={fileUrl(j.project_id, j.result.video)} target="_blank" rel="noreferrer">Output</a>
                            <button className="secondary" onClick={() => handleArtifactPathAction("output video", j.result.video, "reveal")}>{desktopActionLabel("reveal", "output video")}</button>
                          </>
                        ) : null}
                        {runtimeSummary(j)?.checkpointPath ? <button className="secondary" onClick={() => handleArtifactPathAction("checkpoint", runtimeSummary(j)?.checkpointPath, "reveal")}>{desktopActionLabel("reveal", "checkpoint")}</button> : null}
                        {j.type === "internal_video" ? (
                          <>
                            <button className="secondary" onClick={() => resumeFromCheckpoint(j)} disabled={j.status === "queued" || j.status === "running"}>Resume from checkpoint</button>
                            <button className="secondary" onClick={() => restartClean(j)} disabled={j.status === "queued" || j.status === "running"}>Restart clean</button>
                          </>
                        ) : null}
                        <button className="secondary" onClick={() => retry(j)} disabled={j.status === "running"}>Retry</button>
                        <button className="secondary" onClick={() => cancel(j)} disabled={j.status !== "queued" && j.status !== "running"}>Cancel</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
