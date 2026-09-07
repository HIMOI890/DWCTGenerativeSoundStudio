import React from "react";
import { isProjectRevisionConflict, type ApiError } from "./api";

export function expectedRevisionBody<T extends Record<string, unknown>>(
  body: T,
  project: unknown,
): T & { expected_revision?: number } {
  const revision = Number((project as { revision?: unknown } | null)?.revision);
  return Number.isInteger(revision) && revision >= 0
    ? { ...body, expected_revision: revision }
    : body;
}

export function revisionConflictFrom(error: unknown): ApiError | null {
  return isProjectRevisionConflict(error) ? error : null;
}

export function applyResponseRevision<T>(
  current: T,
  response: unknown,
): T {
  if (!current || typeof current !== "object" || !response || typeof response !== "object") return current;
  const payload = response as Record<string, any>;
  const revision = Number(payload.revision ?? payload.project?.revision);
  if (!Number.isInteger(revision) || revision < 0) return current;
  return { ...(current as Record<string, unknown>), revision } as T;
}

export function responseRevision(response: unknown, fallback: number | null = null): number | null {
  const payload = response && typeof response === "object" ? response as Record<string, any> : {};
  const revision = Number(payload.revision ?? payload.project?.revision);
  return Number.isInteger(revision) && revision >= 0 ? revision : fallback;
}

export function projectRevision(project: unknown): number | null {
  const revision = Number((project as { revision?: unknown } | null)?.revision);
  return Number.isInteger(revision) && revision >= 0 ? revision : null;
}

export function projectRevisionFromResponse(response: unknown): number | null {
  return responseRevision(response);
}

export function ProjectRevisionConflictNotice({
  conflict,
  onReload,
  busy = false,
}: {
  conflict: ApiError | null;
  onReload: () => void | Promise<unknown>;
  busy?: boolean;
}) {
  if (!conflict) return null;
  return (
    <div className="card" role="alert" style={{ borderColor: "var(--warning)", marginBottom: 14 }}>
      <div style={{ fontWeight: 800 }}>Project changed elsewhere</div>
      <div className="small" style={{ marginTop: 6 }}>
        Your action was not applied because this screen has revision{" "}
        <b>{conflict.expectedRevision ?? "unknown"}</b>, while the project is now at revision{" "}
        <b>{conflict.actualRevision ?? "a newer value"}</b>. Reload before retrying.
      </div>
      <button className="secondary" style={{ marginTop: 10 }} disabled={busy} onClick={() => void onReload()}>
        {busy ? "Reloading…" : "Reload project"}
      </button>
    </div>
  );
}

export const ProjectRevisionConflict = ProjectRevisionConflictNotice;
