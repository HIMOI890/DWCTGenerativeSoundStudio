import { useCallback, useEffect, useState } from "react";
import { apiGet, apiPost, isRequestAbortError } from "../../components/api";
import { useAdaptivePolling } from "../../hooks/useAdaptivePolling";
import { postQueueJobAction, type QueueJobAction } from "./jobActions";
import { countActiveJobs } from "./jobRuntime";
import type { StudioJob } from "./jobStatus";

export type JobLogSelection = {
  job: StudioJob;
  log: string;
  events: Array<Record<string, unknown>>;
};

type UseProjectJobsOptions = {
  projectId?: string;
  global?: boolean;
  autoRefresh?: boolean;
  refreshIntervalMs?: number;
};

export function useProjectJobs(options: UseProjectJobsOptions) {
  const {
    projectId = "",
    global = false,
    autoRefresh = false,
    refreshIntervalMs = 2500,
  } = options;

  const [jobs, setJobs] = useState<StudioJob[]>([]);
  const [selectedLog, setSelectedLog] = useState<JobLogSelection | null>(null);
  const [lastRefreshAt, setLastRefreshAt] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const fetchJobs = useCallback(async (signal?: AbortSignal): Promise<StudioJob[]> => {
    setError(null);
    try {
      let nextJobs: StudioJob[] = [];
      if (global) {
        const data = await apiGet("/v1/jobs", { signal });
        nextJobs = Array.isArray(data?.jobs) ? data.jobs : [];
      } else if (projectId) {
        const data = await apiGet(`/v1/projects/${projectId}/jobs`, { signal });
        nextJobs = Array.isArray(data?.jobs) ? data.jobs : [];
      }
      setJobs(nextJobs);
      setLastRefreshAt(Date.now());
      return nextJobs;
    } catch (err) {
      if (isRequestAbortError(err)) throw err;
      setError(String(err));
      throw err;
    }
  }, [global, projectId]);

  const fetchJobLog = useCallback(async (job: StudioJob, signal?: AbortSignal) => {
    setError(null);
    try {
      const [logData, eventsData] = await Promise.all([
        apiGet(`/v1/projects/${job.project_id}/jobs/${job.id}/log`, { signal }),
        apiGet(`/v1/projects/${job.project_id}/jobs/${job.id}/events`, { signal }),
      ]);
      setSelectedLog({
        job,
        log: String(logData?.log || ""),
        events: Array.isArray(eventsData?.events) ? eventsData.events : [],
      });
    } catch (err) {
      if (isRequestAbortError(err)) throw err;
      setError(String(err));
      throw err;
    }
  }, []);

  const poll = useCallback(async (signal: AbortSignal) => {
    const nextJobs = await fetchJobs(signal);
    if (selectedLog?.job) await fetchJobLog(selectedLog.job, signal);
    return countActiveJobs(nextJobs) > 0;
  }, [fetchJobLog, fetchJobs, selectedLog?.job]);

  const polling = useAdaptivePolling({
    poll,
    enabled: !!(global || projectId) && autoRefresh,
    activeIntervalMs: refreshIntervalMs,
    idleIntervalMs: refreshIntervalMs,
    scopeKey: global ? "global" : projectId,
  });

  const refresh = useCallback(async () => {
    if (autoRefresh) {
      polling.pollNow();
      return;
    }
    await fetchJobs();
  }, [autoRefresh, fetchJobs, polling.pollNow]);

  const loadJobLog = useCallback(
    async (job: StudioJob) => fetchJobLog(job),
    [fetchJobLog],
  );

  const runJobAction = useCallback(
    async (job: StudioJob, action: QueueJobAction) => {
      setError(null);
      try {
        await postQueueJobAction(job, action);
        await refresh();
        if (selectedLog?.job.id === job.id) {
          await loadJobLog(job);
        }
      } catch (err) {
        setError(String(err));
      }
    },
    [loadJobLog, refresh, selectedLog?.job.id],
  );

  const resumeFromCheckpoint = useCallback(
    async (job: StudioJob) => {
      setError(null);
      try {
        await apiPost(`/v1/projects/${job.project_id}/jobs/${job.id}/resume_from_checkpoint`, {});
        await refresh();
      } catch (err) {
        setError(String(err));
      }
    },
    [refresh],
  );

  const restartClean = useCallback(
    async (job: StudioJob) => {
      setError(null);
      try {
        await apiPost(`/v1/projects/${job.project_id}/jobs/${job.id}/restart_clean`, {});
        await refresh();
      } catch (err) {
        setError(String(err));
      }
    },
    [refresh],
  );

  const tickWorker = useCallback(async () => {
    setError(null);
    try {
      await apiPost("/v1/jobs/tick", {});
      await refresh();
    } catch (err) {
      setError(String(err));
    }
  }, [refresh]);

  useEffect(() => {
    if (autoRefresh) return;
    fetchJobs().catch(() => {});
  }, [autoRefresh, fetchJobs]);

  return {
    jobs,
    selectedLog,
    setSelectedLog,
    lastRefreshAt,
    error,
    setError,
    refresh,
    loadJobLog,
    runJobAction,
    resumeFromCheckpoint,
    restartClean,
    tickWorker,
  };
}
