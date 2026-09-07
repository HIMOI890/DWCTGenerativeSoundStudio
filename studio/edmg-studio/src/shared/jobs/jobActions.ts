import { apiPost } from "../../components/api";
import type { StudioJob } from "./jobStatus";

export type QueueJobAction = "pause" | "resume" | "retry" | "cancel";

export async function postQueueJobAction(
  job: Pick<StudioJob, "id" | "project_id">,
  action: QueueJobAction,
  body: Record<string, unknown> = {},
): Promise<unknown> {
  const projectId = encodeURIComponent(job.project_id);
  const jobId = encodeURIComponent(job.id);
  return apiPost(`/v1/projects/${projectId}/jobs/${jobId}/${action}`, body);
}
