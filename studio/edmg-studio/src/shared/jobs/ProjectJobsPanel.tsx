import React from "react";
import { desktopActionLabel, runDesktopArtifactAction } from "../../components/desktopArtifacts";
import { useSignedProjectMedia } from "../../hooks/useSignedProjectMedia";
import { JobActionButtons } from "./JobActionButtons";
import { JobStatusChip } from "./JobStatusChip";
import {
  countActiveJobs,
  countPausedJobs,
  countResumableInternalJobs,
  jobRuntimeSummary,
} from "./jobRuntime";
import type { QueueJobAction } from "./jobActions";
import { jobRecoveryHint, type StudioJob } from "./jobStatus";
import type { JobLogSelection } from "./useProjectJobs";

type ProjectJobsPanelProps = {
  backendUrl: string;
  jobs: StudioJob[];
  selectedLog: JobLogSelection | null;
  lastRefreshAt?: number;
  error?: string | null;
  info?: string | null;
  autoRefresh?: boolean;
  onAutoRefreshChange?: (enabled: boolean) => void;
  onRefresh: () => void | Promise<unknown>;
  onViewLog: (job: StudioJob) => void | Promise<unknown>;
  onCloseLog?: () => void;
  onJobAction: (job: StudioJob, action: QueueJobAction) => void | Promise<unknown>;
  onDesktopActionMessage?: (message: string) => void;
  onDesktopActionError?: (message: string) => void;
  onResumeFromCheckpoint?: (job: StudioJob) => void | Promise<unknown>;
  onRestartClean?: (job: StudioJob) => void | Promise<unknown>;
  onTickWorker?: () => void | Promise<unknown>;
  onNavigateToQueue?: () => void;
  continuityBlockingCount?: number;
  showWorkerControls?: boolean;
  title?: string;
  description?: string;
};

function SignedOutputLink({
  backendUrl,
  projectId,
  path,
}: {
  backendUrl: string;
  projectId: string;
  path: string;
}) {
  const request = { purpose: "file" as const, path };
  const media = useSignedProjectMedia(projectId, [request], backendUrl);
  const url = media.urlFor(request);
  return url ? (
    <a className="secondary" href={url} target="_blank" rel="noreferrer">Output</a>
  ) : (
    <button className="secondary" disabled title={media.error || "Preparing secure output link"}>Output</button>
  );
}

export function ProjectJobsPanel({
  backendUrl,
  jobs,
  selectedLog,
  lastRefreshAt = 0,
  error = null,
  info = null,
  autoRefresh = false,
  onAutoRefreshChange,
  onRefresh,
  onViewLog,
  onCloseLog,
  onJobAction,
  onResumeFromCheckpoint,
  onRestartClean,
  onTickWorker,
  onNavigateToQueue,
  onDesktopActionMessage,
  onDesktopActionError,
  continuityBlockingCount = 0,
  showWorkerControls = false,
  title = "Render jobs",
  description = "Pause, cancel, retry, reveal outputs, and inspect logs with the same controls as Render Queue.",
}: ProjectJobsPanelProps) {
  const handleArtifactPathAction = async (label: string, value: string | null | undefined, mode: "reveal" | "open") => {
    if (!value) return;
    try {
      const result = await runDesktopArtifactAction(label, value, mode);
      if (!result.ok) throw new Error(result.error || `Unable to ${mode} ${label}`);
      onDesktopActionMessage?.(`Last desktop action: ${result.action || "ok"} • ${value}`);
    } catch (err) {
      onDesktopActionError?.(`Failed to ${mode} ${label}: ${String(err)}`);
    }
  };

  return (
    <>
      <div className="card" style={{ marginTop: 14 }}>
        <div className="row" style={{ justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <div>
            <div style={{ fontWeight: 800 }}>{title}</div>
            <div className="small" style={{ marginTop: 4 }}>{description}</div>
          </div>
          <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
            {showWorkerControls && onTickWorker ? (
              <button onClick={() => void onTickWorker()}>Tick Worker (process 1 job)</button>
            ) : null}
            <button className="secondary" onClick={() => void onRefresh()}>Refresh</button>
            {onNavigateToQueue ? (
              <button className="secondary" onClick={onNavigateToQueue}>Open Render Queue</button>
            ) : null}
          </div>
        </div>
        <div className="row" style={{ gap: 12, marginTop: 10, flexWrap: "wrap", alignItems: "center" }}>
          {onAutoRefreshChange ? (
            <label className="row small" style={{ gap: 6, alignItems: "center" }}>
              <input type="checkbox" checked={autoRefresh} onChange={(event) => onAutoRefreshChange(event.target.checked)} />
              Live poll every 2.5s
            </label>
          ) : null}
          <div className="small" style={{ opacity: 0.8 }}>
            Active <b>{countActiveJobs(jobs)}</b> • paused <b>{countPausedJobs(jobs)}</b> • resumable <b>{countResumableInternalJobs(jobs)}</b>
            {continuityBlockingCount ? <> • blocking <b>{continuityBlockingCount}</b></> : null}
            {lastRefreshAt ? <> • updated {new Date(lastRefreshAt).toLocaleTimeString()}</> : null}
          </div>
        </div>
        <div className="small" style={{ marginTop: 8, opacity: 0.8 }}>
          Pause holds queued jobs only; cancel stops active work; retry and checkpoint recovery match Render Queue behavior.
        </div>
        {error ? <div style={{ marginTop: 10, color: "var(--danger)" }}>{error}</div> : null}
        {!error && info ? <div className="small" style={{ marginTop: 10, opacity: 0.82 }}>{info}</div> : null}
      </div>

      {selectedLog ? (
        <div className="card" style={{ marginTop: 14 }}>
          <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ fontWeight: 800 }}>Job log</div>
            {onCloseLog ? (
              <button className="secondary" onClick={onCloseLog}>Close</button>
            ) : null}
          </div>
          <div className="small" style={{ marginTop: 6 }}>
            {selectedLog.job.id} • {selectedLog.job.type} • <JobStatusChip status={selectedLog.job.status} />
          </div>
          {selectedLog.events.length ? (
            <div className="small" style={{ marginTop: 8, opacity: 0.85 }}>
              Events: {selectedLog.events.map((event) => String(event.event_type || "event")).join(" → ")}
            </div>
          ) : null}
          <pre style={{ marginTop: 10, maxHeight: 300, overflow: "auto" }}>{selectedLog.log || "(no log yet)"}</pre>
        </div>
      ) : null}

      <div className="card" style={{ marginTop: 14 }}>
        <div style={{ fontWeight: 800, marginBottom: 10 }}>Jobs</div>
        {!jobs.length && <div className="small">No jobs yet.</div>}
        {jobs.length > 0 && (
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
                {jobs.map((job) => {
                  const runtime = jobRuntimeSummary(job);
                  const recoveryHint = jobRecoveryHint(job);
                  const videoPath = typeof job.result?.video === "string" ? job.result.video : null;
                  return (
                    <tr key={job.id}>
                      <td className="small">{job.created_at || "—"}</td>
                      <td className="small">{job.type}</td>
                      <td className="small"><JobStatusChip status={job.status} /></td>
                      <td className="small">
                        {job.progress ? (
                          <>
                            <div>{job.progress.percent ?? 0}% • {job.progress.stage || "running"}</div>
                            {job.progress.message ? <div style={{ opacity: 0.8 }}>{job.progress.message}</div> : null}
                            {recoveryHint && recoveryHint !== job.progress.message ? (
                              <div style={{ opacity: 0.8, marginTop: 4 }}>{recoveryHint}</div>
                            ) : null}
                            {runtime ? (
                              <>
                                <div style={{ opacity: 0.85, marginTop: 4 }}>
                                  Resume {runtime.percent}% • chunks {runtime.chunks} • next frame {runtime.nextFrame}
                                </div>
                                <div style={{ opacity: 0.75 }}>
                                  {runtime.strategy} • {runtime.canResume ? "resume-ready" : "non-resumable"}
                                </div>
                                {runtime.checkpointPath ? <div style={{ opacity: 0.65 }}>{runtime.checkpointPath}</div> : null}
                              </>
                            ) : null}
                          </>
                        ) : (
                          recoveryHint || "—"
                        )}
                      </td>
                      <td>
                        <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                          <button className="secondary" onClick={() => void onViewLog(job)}>Log</button>
                          {videoPath ? (
                            <>
                              <SignedOutputLink
                                backendUrl={backendUrl}
                                projectId={job.project_id}
                                path={videoPath}
                              />
                              <button
                                className="secondary"
                                onClick={() => void handleArtifactPathAction("output video", videoPath, "reveal")}
                              >
                                {desktopActionLabel("reveal", "output video")}
                              </button>
                            </>
                          ) : null}
                          {runtime?.checkpointPath ? (
                            <button
                              className="secondary"
                              onClick={() => void handleArtifactPathAction("checkpoint", runtime.checkpointPath, "reveal")}
                            >
                              {desktopActionLabel("reveal", "checkpoint")}
                            </button>
                          ) : null}
                          {job.type === "internal_video" ? (
                            <JobActionButtons
                              job={job}
                              onAction={(action) => void onJobAction(job, action)}
                              onResumeFromCheckpoint={
                                onResumeFromCheckpoint ? () => void onResumeFromCheckpoint(job) : undefined
                              }
                              onRestartClean={onRestartClean ? () => void onRestartClean(job) : undefined}
                            />
                          ) : (
                            <JobActionButtons job={job} onAction={(action) => void onJobAction(job, action)} />
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
