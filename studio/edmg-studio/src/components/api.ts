export function getBackendUrl(): string {
  return window.edmg?.backendUrl?.() ?? window.__EDMG_BACKEND_URL__ ?? "http://127.0.0.1:7863";
}

function formatBackendError(d: any, fallback: string): string {
  // New backend format: { error: { message, hint, code } }
  const e = d?.error;
  if (e?.message) {
    const hint = e?.hint ? `\nFix: ${e.hint}` : "";
    return `${e.message}${hint}`;
  }
  // FastAPI HTTPException: { detail: ... }
  const detail = d?.detail;
  if (typeof detail === "string") return detail;
  if (detail?.message) {
    const hint = detail?.hint ? `\nFix: ${detail.hint}` : "";
    return `${detail.message}${hint}`;
  }
  if (typeof d?.error === "string") return d.error;
  return fallback;
}

export async function apiGet(path: string) {
  const base = getBackendUrl();
  const r = await fetch(`${base}${path}`);
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(formatBackendError(d, `GET ${path} failed`));
  return d;
}

export async function apiPost(path: string, body: any) {
  const base = getBackendUrl();
  const r = await fetch(`${base}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(formatBackendError(d, `POST ${path} failed`));
  return d;
}

export async function apiDelete(path: string) {
  const base = getBackendUrl();
  const r = await fetch(`${base}${path}`, { method: "DELETE" });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(formatBackendError(d, `DELETE ${path} failed`));
  return d;
}


export async function apiUpload(path: string, file: File) {
  const base = getBackendUrl();
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch(`${base}${path}`, { method: "POST", body: fd });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(formatBackendError(d, `UPLOAD ${path} failed`));
  return d;
}
