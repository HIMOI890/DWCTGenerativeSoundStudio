import { normalizeExternalUrl } from "./externalUrl";

export const BACKEND_URL_CHANGED_EVENT = "edmg:backend-url-changed";

const BROWSER_BACKEND_URL_STORAGE_KEY = "edmg.backendUrl";
let backendAuthToken = "";
let backendAuthTokenLoaded = false;

export type ApiRequestOptions = {
  signal?: AbortSignal;
  timeoutMs?: number;
  expectedRevision?: number | null;
};

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;
  readonly code: string;
  readonly expectedRevision: number | null;
  readonly actualRevision: number | null;

  constructor(message: string, status: number, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
    const payload = body && typeof body === "object" ? body as Record<string, any> : {};
    const detail = payload.detail && typeof payload.detail === "object" ? payload.detail : {};
    const error = payload.error && typeof payload.error === "object" ? payload.error : {};
    this.code = String(error.code ?? detail.code ?? payload.code ?? "");
    this.expectedRevision = readRevision(
      error.expected_revision,
      detail.expected_revision,
      payload.expected_revision,
    );
    this.actualRevision = readRevision(
      error.actual_revision,
      error.current_revision,
      detail.actual_revision,
      detail.current_revision,
      payload.actual_revision,
      payload.current_revision,
    );
  }
}

function readRevision(...values: unknown[]): number | null {
  for (const value of values) {
    const revision = Number(value);
    if (Number.isInteger(revision) && revision >= 0) return revision;
  }
  return null;
}

export function isProjectRevisionConflict(error: unknown): error is ApiError {
  if (!(error instanceof ApiError) || error.status !== 409) return false;
  return error.actualRevision != null
    || error.expectedRevision != null
    || /revision|stale|conflict/i.test(`${error.code} ${error.message}`);
}

export type SignedProjectMediaRequest = {
  purpose: "file" | "audio" | "preview";
  path?: string;
  query?: Record<string, unknown>;
};

export type SignedProjectMediaUrl = {
  purpose: SignedProjectMediaRequest["purpose"];
  url: string;
};

export type SignedProjectMediaBatch = {
  expires_at: number | string;
  urls: SignedProjectMediaUrl[];
};

export function isRequestAbortError(error: unknown): boolean {
  const candidate = error as { name?: unknown; message?: unknown } | null;
  const name = String(candidate?.name ?? "").trim();
  const message = String(candidate?.message ?? error ?? "").trim().toLowerCase();
  return name === "AbortError"
    || message === "signal is aborted without reason"
    || message === "the operation was aborted"
    || message === "this operation was aborted";
}

export function setBackendAuthTokenForSession(value: string): string {
  backendAuthToken = String(value || "").trim();
  backendAuthTokenLoaded = true;
  return backendAuthToken;
}

export function hasBackendAuthToken(): boolean {
  return !!backendAuthToken;
}

export async function getBackendAuthTokenAsync(): Promise<string> {
  if (backendAuthTokenLoaded) return backendAuthToken;
  backendAuthTokenLoaded = true;
  if (typeof window === "undefined") return "";
  try {
    const result = await window.edmg?.getBackendAuthToken?.();
    const token = typeof result === "string" ? result : String(result?.token || "");
    backendAuthToken = token.trim();
  } catch {
    backendAuthToken = "";
  }
  return backendAuthToken;
}

export async function saveBackendAuthToken(value: string): Promise<{
  configured: boolean;
  persisted: boolean;
  secureStorageAvailable: boolean;
  note?: string;
}> {
  const token = setBackendAuthTokenForSession(value);
  if (typeof window !== "undefined" && window.edmg?.setBackendAuthToken) {
    const result = await window.edmg.setBackendAuthToken(token);
    if (result?.ok === false) {
      throw new Error(String(result.error || "Unable to save backend access token"));
    }
    return {
      configured: !!token,
      persisted: !!result?.persisted,
      secureStorageAvailable: result?.secureStorageAvailable !== false,
      note: result?.note,
    };
  }
  return {
    configured: !!token,
    persisted: false,
    secureStorageAvailable: false,
    note: "Browser mode keeps the token in memory for this tab only.",
  };
}

async function backendAuthHeaders(extra?: HeadersInit): Promise<Headers> {
  const headers = new Headers(extra || {});
  const token = await getBackendAuthTokenAsync();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return headers;
}

function readQueryBackendUrl(): string {
  if (typeof window === "undefined") return "";
  if (!window.location) return "";
  const params = new URLSearchParams(window.location.search);
  return (params.get("backendUrl") || params.get("backend") || "").trim();
}

function readEnvBackendUrl(): string {
  return String(import.meta.env.VITE_EDMG_BACKEND_URL || import.meta.env.VITE_EDMG_STUDIO_BACKEND_URL || "").trim();
}

function readStoredBackendUrl(): string {
  if (typeof window === "undefined") return "";
  try {
    return String(window.localStorage?.getItem(BROWSER_BACKEND_URL_STORAGE_KEY) || "").trim();
  } catch {
    return "";
  }
}

function readSameOriginBackendUrl(): string {
  if (typeof window === "undefined") return "";
  if (!window.location) return "";
  if (import.meta.env.DEV) return "";
  const protocol = window.location.protocol;
  if (protocol !== "http:" && protocol !== "https:") return "";
  return window.location.origin;
}

function readBridgeBackendUrl(): string {
  if (typeof window === "undefined") return "";
  try {
    return String(window.edmg?.backendUrl?.() || "").trim();
  } catch {
    return "";
  }
}

export function normalizeBackendUrl(rawUrl: string): string {
  const candidate = String(rawUrl || "").trim();
  if (!candidate) return "";
  try {
    const parsed = new URL(candidate);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return "";
    }

    let normalizedPath =
      parsed.pathname && parsed.pathname !== "/"
        ? parsed.pathname.replace(/\/+$/, "")
        : "";
    normalizedPath = normalizedPath.replace(/\/(?:health|v1)$/i, "");

    return `${parsed.origin}${normalizedPath}`;
  } catch {
    return "";
  }
}

export function buildProjectFileUrl(backendUrl: string, projectId: string, relativePath: string): string {
  const base = normalizeBackendUrl(backendUrl);
  if (!base) throw new Error("A valid HTTP(S) backend URL is required.");

  const safeProjectId = String(projectId || "").trim();
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(safeProjectId)) {
    throw new Error("Invalid project identifier.");
  }

  const safePath = String(relativePath || "").trim().replace(/\\/g, "/");
  const pathParts = safePath.split("/");
  if (!safePath || safePath.startsWith("/") || pathParts.some((part) => part === ".." || part === "")) {
    throw new Error("Invalid project-relative file path.");
  }

  const target = new URL(`${base}/v1/projects/${encodeURIComponent(safeProjectId)}/file`);
  target.searchParams.set("path", safePath);
  return target.toString();
}

function pickBackendUrl(...values: unknown[]): string {
  for (const value of values) {
    const normalized = normalizeBackendUrl(String(value || ""));
    if (normalized) return normalized;
  }
  return "";
}

function rememberBackendUrl(value: string): string {
  const resolved = normalizeBackendUrl(value);
  if (typeof window !== "undefined" && resolved) {
    window.__EDMG_BACKEND_URL__ = resolved;
    try {
      const stored = normalizeBackendUrl(window.localStorage?.getItem(BROWSER_BACKEND_URL_STORAGE_KEY) || "");
      if (stored !== resolved) {
        window.localStorage?.setItem(BROWSER_BACKEND_URL_STORAGE_KEY, resolved);
      }
    } catch {
      // Browser storage can be disabled; the in-memory value above still updates this page.
    }
  }
  return resolved;
}

function getBrowserFallbackBackendUrl(): string {
  return rememberBackendUrl(
    pickBackendUrl(
      readQueryBackendUrl(),
      window.__EDMG_BACKEND_URL__,
      readEnvBackendUrl(),
      readStoredBackendUrl(),
      readSameOriginBackendUrl(),
      "http://127.0.0.1:7863"
    )
  );
}

export function setBrowserBackendUrl(value: string): string {
  const resolved = normalizeBackendUrl(value);
  if (!resolved) return "";
  rememberBackendUrl(resolved);
  if (typeof window !== "undefined") {
    try {
      window.localStorage?.setItem(BROWSER_BACKEND_URL_STORAGE_KEY, resolved);
    } catch {
      // Browser storage can be disabled; the in-memory value above still updates this page.
    }
    window.dispatchEvent(new CustomEvent(BACKEND_URL_CHANGED_EVENT, { detail: { url: resolved } }));
  }
  return resolved;
}

export function getBackendUrl(): string {
  return rememberBackendUrl(
    pickBackendUrl(
      readQueryBackendUrl(),
      readBridgeBackendUrl(),
      window.__EDMG_BACKEND_URL__,
      readEnvBackendUrl(),
      readStoredBackendUrl(),
      readSameOriginBackendUrl(),
      "http://127.0.0.1:7863"
    )
  );
}

export async function getBackendUrlAsync(): Promise<string> {
  const explicit = pickBackendUrl(readQueryBackendUrl());
  if (explicit) return rememberBackendUrl(explicit);
  try {
    const bridged = await window.edmg?.getBackendUrl?.();
    const bridgedUrl = pickBackendUrl(bridged);
    if (bridgedUrl) return rememberBackendUrl(bridgedUrl);
  } catch {
    // Fall through to the sync fallback chain below.
  }
  return rememberBackendUrl(getBackendUrl());
}

export function ensureBrowserBridge(): void {
  if (typeof window === "undefined" || window.edmg) return;
  window.edmg = {
    backendUrl: () => getBrowserFallbackBackendUrl(),
    getBackendUrl: async () => getBrowserFallbackBackendUrl(),
    setBackendUrl: async (url: string) => setBrowserBackendUrl(url),
    openExternal: async (url: string) => {
      const externalUrl = normalizeExternalUrl(url);
      if (!externalUrl) return "";
      window.open(externalUrl, "_blank", "noopener,noreferrer");
      return externalUrl;
    },
  };
}

export async function apiFetch(
  path: string,
  init: RequestInit = {},
  options: ApiRequestOptions = {},
): Promise<Response> {
  const base = await getBackendUrlAsync();
  const headers = await backendAuthHeaders(init.headers);
  const target = new URL(/^https?:\/\//i.test(path) ? path : `${base}${path}`);
  if (target.origin !== new URL(base).origin) {
    throw new Error("Refusing to send Studio backend credentials to a different origin.");
  }

  const callerSignal = options.signal ?? init.signal ?? undefined;
  const timeoutMs = Number(options.timeoutMs ?? 0);
  if (!callerSignal && !(timeoutMs > 0)) {
    return fetch(target.toString(), { ...init, headers });
  }

  const controller = new AbortController();
  let timeoutId: ReturnType<typeof setTimeout> | null = null;
  let timedOut = false;
  const abortFromCaller = () => controller.abort(callerSignal?.reason);
  if (callerSignal?.aborted) {
    abortFromCaller();
  } else {
    callerSignal?.addEventListener("abort", abortFromCaller, { once: true });
  }
  if (timeoutMs > 0) {
    timeoutId = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);
  }

  try {
    return await fetch(target.toString(), {
      ...init,
      headers,
      signal: controller.signal,
    });
  } catch (error) {
    if (timedOut) {
      throw new Error(`Studio backend request timed out after ${timeoutMs} ms`);
    }
    throw error;
  } finally {
    if (timeoutId != null) clearTimeout(timeoutId);
    callerSignal?.removeEventListener("abort", abortFromCaller);
  }
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
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0] || {};
    const loc = Array.isArray(first.loc) ? first.loc.filter(Boolean).join(".") : "";
    const msg = first.msg || first.message || "Request validation failed";
    const suffix = detail.length > 1 ? ` (${detail.length} validation issues)` : "";
    return loc ? `${loc}: ${msg}${suffix}` : `${msg}${suffix}`;
  }
  if (detail?.message) {
    const hint = detail?.hint ? `\nFix: ${detail.hint}` : "";
    return `${detail.message}${hint}`;
  }
  if (typeof d?.error === "string") return d.error;
  return fallback;
}

function backendError(response: Response, data: unknown, fallback: string): ApiError {
  return new ApiError(formatBackendError(data, fallback), response.status, data);
}

export async function apiGet(path: string, options: ApiRequestOptions = {}) {
  const r = await apiFetch(path, {}, options);
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw backendError(r, d, `GET ${path} failed`);
  return d;
}

export async function apiPost(path: string, body: any, options: ApiRequestOptions = {}) {
  const r = await apiFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }, options);
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw backendError(r, d, `POST ${path} failed`);
  return d;
}

export async function apiPatch(path: string, body: any, options: ApiRequestOptions = {}) {
  const r = await apiFetch(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }, options);
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw backendError(r, d, `PATCH ${path} failed`);
  return d;
}

export async function apiDelete(path: string, options: ApiRequestOptions = {}) {
  const r = await apiFetch(path, { method: "DELETE" }, options);
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw backendError(r, d, `DELETE ${path} failed`);
  return d;
}

export async function apiUpload(path: string, file: File, options: ApiRequestOptions = {}) {
  const fd = new FormData();
  fd.append("file", file);
  if (Number.isInteger(options.expectedRevision) && Number(options.expectedRevision) >= 0) {
    fd.append("expected_revision", String(options.expectedRevision));
  }
  const r = await apiFetch(path, { method: "POST", body: fd }, options);
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw backendError(r, d, `UPLOAD ${path} failed`);
  return d;
}

export async function issueProjectMediaUrls(
  projectId: string,
  requests: SignedProjectMediaRequest[],
  options: ApiRequestOptions = {},
): Promise<SignedProjectMediaBatch> {
  const safeProjectId = String(projectId || "").trim();
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(safeProjectId)) {
    throw new Error("Invalid project identifier.");
  }
  if (!Array.isArray(requests) || requests.length === 0) {
    throw new Error("At least one signed media request is required.");
  }

  const normalizedRequests = requests.map((request) => {
    const purpose = String(request?.purpose || "").toLowerCase();
    if (purpose !== "file" && purpose !== "audio" && purpose !== "preview") {
      throw new Error("Signed media purpose must be file, audio, or preview.");
    }
    const path = String(request.path || "").trim().replace(/\\/g, "/");
    if ((purpose === "file" || purpose === "audio")
      && (!path || path.startsWith("/") || path.split("/").some((part) => !part || part === ".."))) {
      throw new Error("Invalid project-relative media path.");
    }
    if (request.query != null && (typeof request.query !== "object" || Array.isArray(request.query))) {
      throw new Error("Signed media query must be an object.");
    }
    return {
      purpose,
      ...(path ? { path } : {}),
      ...(request.query ? { query: request.query } : {}),
    } satisfies SignedProjectMediaRequest;
  });

  const data = await apiPost(
    `/v1/projects/${encodeURIComponent(safeProjectId)}/media-urls`,
    { requests: normalizedRequests },
    options,
  ) as SignedProjectMediaBatch;
  if (!data || !Array.isArray(data.urls) || data.urls.length !== normalizedRequests.length) {
    throw new Error("Studio returned an invalid signed media URL batch.");
  }
  const backendUrl = await getBackendUrlAsync();
  const urls = data.urls.map((item, index) => {
    if (!item || item.purpose !== normalizedRequests[index]?.purpose || !String(item.url || "").trim()) {
      throw new Error("Studio returned a signed media URL for an unexpected purpose.");
    }
    const resolved = new URL(String(item.url).trim(), `${backendUrl}/`);
    if (resolved.protocol !== "http:" && resolved.protocol !== "https:") {
      throw new Error("Studio signed media URLs must use HTTP or HTTPS.");
    }
    return { purpose: item.purpose, url: resolved.toString() };
  });
  if (Number.isNaN(new Date(data.expires_at).getTime()) && !Number.isFinite(Number(data.expires_at))) {
    throw new Error("Studio returned an invalid signed media expiry.");
  }
  return { expires_at: data.expires_at, urls };
}
