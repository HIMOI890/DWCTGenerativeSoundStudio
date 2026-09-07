import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiGet, apiPost, isRequestAbortError } from "../components/api";
import { StudioLayoutCustomizer } from "../components/StudioLayoutCustomizer";
import { useStudioPageLayout } from "../components/studioLayout";
import { useUiMode } from "../components/uiMode";
import { useAdaptivePolling } from "../hooks/useAdaptivePolling";
import { buildInternalModelReadiness } from "../shared/internalModelReadiness";
import type { PageProps } from "../types/pageProps";

type CatalogEntry = {
  id: string;
  name: string;
  kind: string;
  source: string;
  recommended?: string;
  lane?: string;
  lane_gates?: { promotable_to?: string[]; stable_requires?: string[] };
  benchmark?: { present?: boolean; summary?: string; updated_at?: number };
  notes?: string;
  license_id?: string;
  license_url?: string;
  installable?: boolean;
  hf_repo_id?: string;
  hf_url?: string;
  filename?: string;
  tags?: string[];
  hardware_targets?: string[];
  collections?: string[];
};

type CatalogPayload = {
  catalog: CatalogEntry[];
  user: CatalogEntry[];
  packs: any[];
  accepted: Record<string, any>;
  installed: Record<string, boolean>;
  cloud?: Record<string, any>;
  storage_mode?: string;
  model_cache?: string | null;
  tensorrt_migration?: TensorRtMigrationStatus;
};

type InFlightRequest = {
  promise: Promise<void>;
  signal?: AbortSignal;
};

type TensorRtEngineFileStatus = {
  role: string;
  name?: string | null;
  present: boolean;
  non_empty: boolean;
  safe_regular_file: boolean;
  size_bytes: number;
  sha256?: string | null;
  hash_state: string;
};

type TensorRtMigrationStatus = {
  legacy: {
    detected: boolean;
    status: "absent" | "partial" | "ready_to_import";
    expected_file_count: number;
    usable_file_count: number;
    total_bytes: number;
    files: TensorRtEngineFileStatus[];
    missing_roles: string[];
    unusable_roles: string[];
    source_preserved: boolean;
  };
  canonical: {
    exists: boolean;
    status: string;
    manifest: { valid: boolean; schema_version?: number | null };
    engine_files_verified: boolean;
    unet_engine_ready: boolean;
    onnx_ready: boolean;
    profile_metadata_ready: boolean;
    base_model_metadata_ready: boolean;
    renderer_ready: boolean;
    gaps: string[];
  };
  migration: {
    available: boolean;
    blocked_reason?: string | null;
    copy_only: boolean;
    source_will_be_preserved: boolean;
    disk: {
      required_free_bytes: number;
      available_free_bytes?: number | null;
      enough_space: boolean;
    };
  };
};

type HubResult = {
  id: string;
  downloads?: number;
  likes?: number;
  lastModified?: string;
  pipeline_tag?: string;
  tags?: string[];
};

type HubCollection = {
  id: string;
  label: string;
  search: string;
  note: string;
  url: string;
};

type ModelsPanelId =
  | "packs"
  | "internal"
  | "runtime"
  | "discovery"
  | "imports"
  | "defaults"
  | "advanced";

const HUB_COLLECTIONS: HubCollection[] = [
  {
    id: "stable-diffusion-35",
    label: "SD3.5 Image",
    search: "stable-diffusion-3.5",
    note: "Curated Stability image models, including SD3.5 large, medium, turbo, and controlnets.",
    url: "https://huggingface.co/collections/stabilityai/stable-diffusion-35",
  },
  {
    id: "video",
    label: "Video",
    search: "stable-video-diffusion",
    note: "Stable Video Diffusion image-to-video checkpoints and related motion models.",
    url: "https://huggingface.co/collections/stabilityai/video",
  },
  {
    id: "nvidia-optimized",
    label: "NVIDIA",
    search: "tensorrt stable-diffusion-3.5",
    note: "TensorRT-oriented Stability bundles for NVIDIA-specific fast paths.",
    url: "https://huggingface.co/collections/stabilityai/nvidia-optimized",
  },
  {
    id: "amd-optimized",
    label: "AMD",
    search: "amdgpu stable-diffusion-3.5",
    note: "AMDGPU / DirectML-oriented Stability bundles for Windows and AMD hardware paths.",
    url: "https://huggingface.co/collections/stabilityai/amd-optimized",
  },
];

const STABILITY_LINKS = [
  { label: "SD3.5 collection", url: "https://huggingface.co/collections/stabilityai/stable-diffusion-35" },
  { label: "Video collection", url: "https://huggingface.co/collections/stabilityai/video" },
  { label: "NVIDIA collection", url: "https://huggingface.co/collections/stabilityai/nvidia-optimized" },
  { label: "AMD collection", url: "https://huggingface.co/collections/stabilityai/amd-optimized" },
  { label: "Stability GitHub", url: "https://github.com/Stability-AI" },
  { label: "generative-models", url: "https://github.com/Stability-AI/generative-models" },
  { label: "sd3.5 repo", url: "https://github.com/Stability-AI/sd3.5" },
  { label: "stable-audio-tools", url: "https://github.com/Stability-AI/stable-audio-tools" },
  { label: "Stability Platform", url: "https://platform.stability.ai/" },
];

function formatDate(value?: string) {
  if (!value) return "unknown";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleDateString();
}

function repoIdFromEntry(model: CatalogEntry) {
  if (model.hf_repo_id) return model.hf_repo_id;
  if (model.hf_url) {
    const match = model.hf_url.match(/huggingface\.co\/([^/]+\/[^/]+)\/resolve\//i);
    if (match) return match[1];
  }
  return "";
}

function ModelCard({
  m,
  installed,
  cloudRecord,
  storageMode,
  cacheLabel,
  accepted,
  onAccept,
  onInstall,
  onRestore,
  onOpen,
  onPromote,
  onBenchmark,
}: {
  m: CatalogEntry;
  installed: boolean;
  cloudRecord?: any;
  storageMode?: string;
  cacheLabel?: string;
  accepted: boolean;
  onAccept: () => void;
  onInstall: () => void;
  onRestore: () => void;
  onOpen: (u: string) => void;
  onPromote?: (lane: string) => void;
  onBenchmark?: () => void;
}) {
  const installable = m.installable !== false;
  const needsAccept = installable && m.source !== "ollama" && !accepted;
  const canInstall = installable && !needsAccept;
  const cloudStored = !!cloudRecord;
  const cloudOnly = storageMode === "cloud_only";
  const cloudProvider = cloudRecord?.provider || cacheLabel || "cloud cache";
  const statusLabel = installed ? "Installed locally" : cloudStored ? `Stored in ${cloudProvider}` : installable ? "Not installed" : "Browser only";
  const installLabel = installed
    ? "Installed"
    : cloudStored
      ? `Stored in ${cloudProvider}`
      : !installable
        ? "Install unavailable"
        : cloudOnly
          ? `Store in ${cacheLabel || "cloud"}`
          : "Install";
  const lane = String(m.lane || "experimental");
  const promotable = Array.isArray(m.lane_gates?.promotable_to) ? m.lane_gates!.promotable_to! : [];

  return (
    <div className="card" style={{ marginTop: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
        <div>
          <div style={{ fontWeight: 900 }}>{m.name}</div>
          <div className="small" style={{ marginTop: 4 }}>
            <span style={{ opacity: 0.85 }}>
              {m.kind} • {m.source}
              {m.recommended ? ` • ${m.recommended}` : ""}
              {` • lane ${lane}`}
            </span>
          </div>
          <div className="small" style={{ marginTop: 6 }}>
            License: <b>{m.license_id ?? "unknown"}</b>
            {m.benchmark?.present ? <> • benchmark <b>recorded</b></> : <> • benchmark <b>missing</b></>}
          </div>
          {m.tags?.length ? (
            <div className="small" style={{ marginTop: 6, opacity: 0.88 }}>
              Tags: <b>{m.tags.join(", ")}</b>
            </div>
          ) : null}
          {m.hardware_targets?.length ? (
            <div className="small" style={{ marginTop: 4, opacity: 0.82 }}>
              Best on: <b>{m.hardware_targets.join(", ")}</b>
            </div>
          ) : null}
          {m.notes ? <div className="small" style={{ marginTop: 6 }}>{m.notes}</div> : null}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8, alignItems: "flex-end" }}>
          <div className="small" style={{ fontWeight: 800 }}>
            {statusLabel}
          </div>
          {cloudStored ? (
            <div className="small" style={{ maxWidth: 260, textAlign: "right", opacity: 0.82 }}>
              {String(cloudRecord.object || cloudRecord.key || "")}
            </div>
          ) : null}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "flex-end" }}>
            {m.license_url ? (
              <button className="secondary" onClick={() => onOpen(m.license_url || "")}>View license</button>
            ) : null}
            {(m.hf_repo_id || m.hf_url) ? (
              <button className="secondary" onClick={() => onOpen(`https://huggingface.co/${repoIdFromEntry(m) || ""}`)}>
                Open model page
              </button>
            ) : null}
            {needsAccept ? (
              <button onClick={onAccept}>Accept license</button>
            ) : null}
            {cloudStored && !installed ? (
              <button className="secondary" onClick={onRestore}>Restore local</button>
            ) : null}
            <button disabled={!canInstall || installed || cloudStored} onClick={onInstall}>
              {installLabel}
            </button>
            {onBenchmark ? (
              <button className="secondary" onClick={onBenchmark}>Record benchmark</button>
            ) : null}
            {onPromote && promotable.includes("recommended") && lane !== "recommended" && lane !== "stable" ? (
              <button className="secondary" onClick={() => onPromote("recommended")}>Promote → recommended</button>
            ) : null}
            {onPromote && promotable.includes("stable") && lane === "recommended" ? (
              <button className="secondary" onClick={() => onPromote("stable")}>Promote → stable</button>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}

function HubResultCard({
  result,
  matchedCatalog,
  installed,
  cloudStored,
  cacheLabel,
  accepted,
  onAccept,
  onInstall,
  onOpen,
}: {
  result: HubResult;
  matchedCatalog: CatalogEntry | null;
  installed: boolean;
  cloudStored?: boolean;
  cacheLabel?: string;
  accepted: boolean;
  onAccept: (model: CatalogEntry) => void;
  onInstall: (model: CatalogEntry) => void;
  onOpen: (url: string) => void;
}) {
  const modelUrl = `https://huggingface.co/${result.id}`;
  return (
    <div className="card" style={{ marginTop: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
        <div>
          <div style={{ fontWeight: 900 }}>{result.id}</div>
          <div className="small" style={{ marginTop: 4, opacity: 0.85 }}>
            {(result.pipeline_tag || "model")} • {result.downloads ?? 0} downloads • {result.likes ?? 0} likes
          </div>
          <div className="small" style={{ marginTop: 4, opacity: 0.82 }}>
            Updated: <b>{formatDate(result.lastModified)}</b>
          </div>
          {result.tags?.length ? (
            <div className="small" style={{ marginTop: 6, opacity: 0.82 }}>
              {result.tags.slice(0, 6).join(", ")}
            </div>
          ) : null}
          {matchedCatalog ? (
            <div className="small" style={{ marginTop: 8 }}>
              Studio match: <b>{matchedCatalog.name}</b>
            </div>
          ) : (
            <div className="small" style={{ marginTop: 8, opacity: 0.8 }}>
              No direct Studio install mapping yet.
            </div>
          )}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8, alignItems: "flex-end" }}>
          <button className="secondary" onClick={() => onOpen(modelUrl)}>Open on Hugging Face</button>
          {matchedCatalog && matchedCatalog.installable !== false && !accepted ? (
            <button onClick={() => onAccept(matchedCatalog)}>Accept license</button>
          ) : null}
          {matchedCatalog ? (
            <button
              disabled={matchedCatalog.installable === false || installed || cloudStored || (matchedCatalog.source !== "ollama" && !accepted)}
              onClick={() => onInstall(matchedCatalog)}
            >
              {installed
                ? "Installed in Studio"
                : cloudStored
                  ? `Stored in ${cacheLabel || "cloud cache"}`
                  : matchedCatalog.installable === false
                    ? "Browser only"
                    : "Install in Studio"}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function taskStage(task: any): string {
  const explicit = String(task?.stage || task?.phase || "").trim();
  if (explicit) return explicit;
  const log = String(task?.last_log || "").trim();
  if (!log) return "";
  return log.split(/\r?\n/).filter(Boolean).at(-1) || "";
}

function formatTaskBytes(value: unknown): string {
  if (value == null || value === "") return "";
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${Math.round(bytes)} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let size = bytes / 1024;
  let unit = units[0];
  for (let index = 1; index < units.length && size >= 1024; index += 1) {
    size /= 1024;
    unit = units[index];
  }
  return `${size >= 10 ? size.toFixed(1) : size.toFixed(2)} ${unit}`;
}

function tensorRtRoleLabel(role: string): string {
  return {
    text_encoder: "Text encoder",
    unet: "UNet",
    vae_decoder: "VAE decoder",
    vae_encoder: "VAE encoder",
  }[role] || role;
}

function LegacyTensorRtCard({
  status,
  activeTask,
  busy,
  message,
  onImport,
  onCancel,
}: {
  status?: TensorRtMigrationStatus;
  activeTask?: any;
  busy: boolean;
  message: string;
  onImport: () => void;
  onCancel: (taskId: string) => void;
}) {
  const legacy = status?.legacy;
  const canonical = status?.canonical;
  const disk = status?.migration?.disk;
  const blockedRoles = [...(legacy?.missing_roles ?? []), ...(legacy?.unusable_roles ?? [])];
  const importActive = !!activeTask && (activeTask.status === "queued" || activeTask.status === "running");

  let summary = "Checking the Studio-managed legacy engine folder…";
  if (legacy?.status === "absent") {
    summary = "No root-level legacy TensorRT engine set was detected in the current Studio Home.";
  } else if (legacy?.status === "partial") {
    summary = "A partial legacy engine set was found. Studio will not copy it until all four expected engines are non-empty and safe to read.";
  } else if (legacy?.status === "ready_to_import") {
    summary = `Found ${legacy.usable_file_count} safe, non-empty engine candidates (${formatTaskBytes(legacy.total_bytes)}). SHA-256 verification occurs during the copy task.`;
  }

  return (
    <div className="card" style={{ marginTop: 14 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <div style={{ fontWeight: 900 }}>Legacy TensorRT engine migration</div>
        <div className="small">
          {canonical?.renderer_ready
            ? "Renderer ready"
            : canonical?.exists
              ? "Engine copy present — setup incomplete"
              : legacy?.status === "ready_to_import"
                ? "Ready to verify and copy"
                : "Not ready to copy"}
        </div>
      </div>
      <div className="small" style={{ marginTop: 6 }}>{summary}</div>
      <div className="small" style={{ marginTop: 6, opacity: 0.88 }}>
        This is a copy-only migration into the canonical <code>local_sd15_tensorrt_bundle</code> layout. Studio never moves, renames, or deletes the legacy engines.
      </div>

      {legacy?.detected ? (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 8, marginTop: 10 }}>
          {legacy.files.map((file) => (
            <div key={file.role} style={{ padding: 8, borderRadius: 10, background: "#121422", border: "1px solid #22263a" }}>
              <div className="small" style={{ fontWeight: 800 }}>{tensorRtRoleLabel(file.role)}</div>
              <div className="small" style={{ marginTop: 3, opacity: 0.84 }}>
                {file.present && file.non_empty && file.safe_regular_file
                  ? `${file.name} • ${formatTaskBytes(file.size_bytes)}`
                  : file.present
                    ? `${file.name || "Engine"} • unusable`
                    : "Missing"}
              </div>
              {file.sha256 ? (
                <div className="small" title={file.sha256} style={{ marginTop: 3, opacity: 0.7 }}>
                  SHA-256 {file.sha256.slice(0, 12)}…
                </div>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}

      {blockedRoles.length ? (
        <div className="small" role="alert" style={{ marginTop: 8, color: "#ffcf9f" }}>
          Repair required: {Array.from(new Set(blockedRoles)).map(tensorRtRoleLabel).join(", ")}.
        </div>
      ) : null}

      {canonical?.exists && !canonical.renderer_ready ? (
        <div style={{ marginTop: 10, padding: 10, borderRadius: 10, background: "#241f13", border: "1px solid #5b4b24" }}>
          <div className="small" style={{ fontWeight: 800 }}>Canonical bundle is intentionally not marked ready</div>
          <div className="small" style={{ marginTop: 4 }}>
            The engine copy is preserved, but Studio will not advertise it as installed or renderer-ready until all compatibility requirements are verified.
          </div>
          {canonical.gaps.length ? (
            <ul className="small" style={{ marginTop: 6, marginBottom: 0, paddingLeft: 20 }}>
              {canonical.gaps.map((gap) => <li key={gap}>{gap}</li>)}
            </ul>
          ) : null}
        </div>
      ) : null}

      {status?.migration?.blocked_reason === "insufficient_disk_space" && disk ? (
        <div className="small" role="alert" style={{ marginTop: 8, color: "#ffcf9f" }}>
          Free space required: {formatTaskBytes(disk.required_free_bytes)}. Available: {formatTaskBytes(disk.available_free_bytes)}.
        </div>
      ) : null}

      {message ? <div className="small" role="status" aria-live="polite" style={{ marginTop: 8 }}>{message}</div> : null}
      <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
        {importActive ? (
          <button className="secondary" disabled={busy} onClick={() => onCancel(String(activeTask.id))}>
            {busy ? "Requesting cancellation…" : "Cancel safe copy"}
          </button>
        ) : (
          <button disabled={!status?.migration?.available || busy} onClick={onImport}>
            {busy ? "Starting verification…" : "Verify and copy engines"}
          </button>
        )}
      </div>
    </div>
  );
}

function ModelTaskProgress({
  tasks,
  isPolling,
  cancellingTaskId,
  onCancel,
}: {
  tasks: any[];
  isPolling: boolean;
  cancellingTaskId: string;
  onCancel: (taskId: string) => void;
}) {
  return (
    <section className="card" style={{ marginTop: 14 }} aria-labelledby="model-install-progress-heading">
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center" }}>
        <div id="model-install-progress-heading" style={{ fontWeight: 900 }}>Model install progress</div>
        <div className="small" role="status" aria-live="polite">
          {isPolling ? "Checking tasks…" : tasks.some((task) => task.status === "queued" || task.status === "running") ? "Install active" : "No active install"}
        </div>
      </div>
      {tasks.length ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 10 }}>
          {tasks.slice(0, 12).map((task: any) => {
            const progress = Number(task.progress);
            const hasProgress = task.progress != null && Number.isFinite(progress);
            const stage = taskStage(task);
            const bytesCompleted = formatTaskBytes(task.bytes_completed);
            const bytesTotal = formatTaskBytes(task.bytes_total);
            const hasFilesCompleted = task.files_completed != null && Number.isFinite(Number(task.files_completed));
            const hasFilesTotal = task.files_total != null && Number.isFinite(Number(task.files_total));
            const filesCompleted = hasFilesCompleted ? Number(task.files_completed) : null;
            const filesTotal = hasFilesTotal ? Number(task.files_total) : null;
            const hasFileCounts = hasFilesCompleted || hasFilesTotal;
            return (
              <div key={task.id} style={{ padding: 10, borderRadius: 12, background: "#121422", border: "1px solid #22263a" }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                  <div style={{ fontWeight: 800 }}>{task.name}</div>
                  <div className="small">
                    {task.status}{hasProgress ? ` • ${Math.round(Math.max(0, Math.min(1, progress)) * 100)}%` : ""}
                  </div>
                </div>
                {(task.status === "queued" || task.status === "running") ? (
                  <>
                    <progress
                      aria-label={`${task.name} progress`}
                      value={hasProgress ? Math.max(0, Math.min(1, progress)) : undefined}
                      max={1}
                      style={{ width: "100%", marginTop: 8 }}
                    />
                    <div style={{ marginTop: 8 }}>
                      <button
                        className="secondary"
                        disabled={task.cancel_requested || cancellingTaskId === String(task.id)}
                        onClick={() => onCancel(String(task.id))}
                      >
                        {task.cancel_requested || cancellingTaskId === String(task.id)
                          ? "Cancellation requested…"
                          : `Cancel ${task.name}`}
                      </button>
                    </div>
                  </>
                ) : null}
                {stage ? <div className="small" style={{ marginTop: 6, whiteSpace: "pre-wrap" }}>Stage: {stage}</div> : null}
                {(bytesCompleted || bytesTotal || hasFileCounts) ? (
                  <div className="small" style={{ marginTop: 6 }}>
                    {bytesCompleted ? <>Downloaded <b>{bytesCompleted}</b>{bytesTotal ? <> of <b>{bytesTotal}</b></> : null}</> : null}
                    {(bytesCompleted || bytesTotal) && hasFileCounts ? " • " : null}
                    {hasFileCounts ? (
                      <>Files <b>{filesCompleted ?? "?"}</b> of <b>{filesTotal ?? "?"}</b></>
                    ) : null}
                  </div>
                ) : null}
                {task.error ? (
                  <div className="small" role="alert" style={{ marginTop: 6, color: "#ffb7b7" }}>
                    <div>{task.error}</div>
                    {task.error_hint ? <div style={{ marginTop: 4 }}>Next: {task.error_hint}</div> : null}
                    {task.error_code ? <div style={{ marginTop: 4, opacity: 0.78 }}>Code: {task.error_code}</div> : null}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : (
        <div className="small" style={{ marginTop: 8, opacity: 0.8 }}>No model install tasks yet.</div>
      )}
    </section>
  );
}

export default function Models(props: PageProps) {
  const { mode } = useUiMode();
  const [data, setData] = useState<CatalogPayload | null>(null);
  const [tasks, setTasks] = useState<any[]>([]);
  const [renderProviders, setRenderProviders] = useState<any>(null);
  const [err, setErr] = useState<string>("");
  const [catalogLoading, setCatalogLoading] = useState(false);
  const catalogRequestRef = useRef<InFlightRequest | null>(null);
  const providersRequestRef = useRef<InFlightRequest | null>(null);
  const previousTaskStatesRef = useRef<string | null>(null);

  const [civitaiUrl, setCivitaiUrl] = useState("");
  const [importing, setImporting] = useState(false);
  const [localFolder, setLocalFolder] = useState("checkpoints");
  const [tensorRtBusy, setTensorRtBusy] = useState(false);
  const [tensorRtMessage, setTensorRtMessage] = useState("");
  const [cancellingTaskId, setCancellingTaskId] = useState("");

  const [hubCollectionId, setHubCollectionId] = useState<string>(HUB_COLLECTIONS[0].id);
  const [hubQuery, setHubQuery] = useState<string>("");
  const [hubLoading, setHubLoading] = useState<boolean>(false);
  const [hubResults, setHubResults] = useState<HubResult[]>([]);
  const [hubError, setHubError] = useState<string>("");

  const loadCatalog = useCallback((signal?: AbortSignal) => {
    const currentRequest = catalogRequestRef.current;
    if (currentRequest && !currentRequest.signal?.aborted) return currentRequest.promise;
    setCatalogLoading(true);
    const request = apiGet("/v1/models/catalog", { signal, timeoutMs: 30_000 })
      .then((payload) => {
        setData(payload as CatalogPayload);
      })
      .finally(() => {
        if (catalogRequestRef.current?.promise === request) {
          catalogRequestRef.current = null;
          setCatalogLoading(false);
        }
      });
    catalogRequestRef.current = { promise: request, signal };
    return request;
  }, []);

  const loadRenderProviders = useCallback((signal?: AbortSignal) => {
    const currentRequest = providersRequestRef.current;
    if (currentRequest && !currentRequest.signal?.aborted) return currentRequest.promise;
    const request = apiGet("/v1/settings/render_providers", { signal, timeoutMs: 15_000 })
      .then((payload) => {
        setRenderProviders(payload);
      })
      .finally(() => {
        if (providersRequestRef.current?.promise === request) {
          providersRequestRef.current = null;
        }
      });
    providersRequestRef.current = { promise: request, signal };
    return request;
  }, []);

  const pollModelTasks = useCallback(async (signal: AbortSignal) => {
    const payload = await apiGet("/v1/models/tasks", { signal, timeoutMs: 10_000 });
    const nextTasks = Array.isArray((payload as any)?.tasks) ? (payload as any).tasks : [];
    const nextStates = nextTasks.map((task: any) => `${task.id}:${task.status}`).join("|");
    const previousStates = previousTaskStatesRef.current;
    previousTaskStatesRef.current = nextStates;
    setTasks(nextTasks);

    if (previousStates != null && previousStates !== nextStates) {
      void loadCatalog().catch((error: any) => {
        setErr(String(error?.message ?? error));
      });
    }

    const active = nextTasks.some((task: any) => task.status === "queued" || task.status === "running");
    return { active, continuePolling: active };
  }, [loadCatalog]);

  const {
    isPolling: tasksPolling,
    lastError: taskPollingError,
    pollNow: pollModelTasksNow,
  } = useAdaptivePolling({
    poll: pollModelTasks,
    activeIntervalMs: 1_000,
    idleIntervalMs: 15_000,
  });

  async function refresh() {
    setErr("");
    pollModelTasksNow();
    try {
      await Promise.all([loadCatalog(), loadRenderProviders()]);
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    }
  }

  async function promoteModel(modelId: string, lane: string) {
    setErr("");
    try {
      await apiPost("/v1/models/promote", { model_id: modelId, lane, reason: `UI promote to ${lane}` });
      await refresh();
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    }
  }

  async function recordBenchmark(modelId: string) {
    setErr("");
    try {
      await apiPost("/v1/models/benchmark", {
        model_id: modelId,
        summary: "manual_ui_benchmark",
        passed: true,
        metrics: { source: "models_page" },
      });
      await refresh();
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([
      loadCatalog(controller.signal),
      loadRenderProviders(controller.signal),
    ]).catch((error: any) => {
      if (!controller.signal.aborted && !isRequestAbortError(error)) {
        setErr(String(error?.message ?? error));
      }
    });
    return () => controller.abort();
  }, [loadCatalog, loadRenderProviders]);

  const merged = useMemo(() => {
    const built = data?.catalog ?? [];
    const user = data?.user ?? [];
    return { built, user };
  }, [data]);

  const hubCollection = useMemo(
    () => HUB_COLLECTIONS.find((item) => item.id === hubCollectionId) ?? HUB_COLLECTIONS[0],
    [hubCollectionId]
  );

  const builtByRepoId = useMemo(() => {
    const mapping = new Map<string, CatalogEntry>();
    for (const item of merged.built ?? []) {
      const repoId = repoIdFromEntry(item);
      if (repoId) mapping.set(repoId, item);
    }
    return mapping;
  }, [merged.built]);

  useEffect(() => {
    let cancelled = false;
    const loadHub = async () => {
      setHubLoading(true);
      setHubError("");
      try {
        const query = (hubQuery || hubCollection.search).trim() || hubCollection.search;
        const { listModels } = await import("@huggingface/hub");
        const nextResults: HubResult[] = [];
        for await (const item of listModels({
          search: query,
          author: "stabilityai",
          limit: 8,
        } as any)) {
          nextResults.push({
            id: String((item as any).id || ""),
            downloads: Number((item as any).downloads || 0),
            likes: Number((item as any).likes || 0),
            lastModified: String((item as any).lastModified || ""),
            pipeline_tag: String((item as any).pipeline_tag || ""),
            tags: Array.isArray((item as any).tags) ? (item as any).tags.slice(0, 8) : [],
          });
        }
        if (!cancelled) setHubResults(nextResults);
      } catch (e: any) {
        if (!cancelled) {
          setHubError(String(e?.message ?? e));
          setHubResults([]);
        }
      } finally {
        if (!cancelled) setHubLoading(false);
      }
    };
    void loadHub();
    return () => {
      cancelled = true;
    };
  }, [hubCollection, hubQuery]);

  async function accept(m: CatalogEntry) {
    setErr("");
    try {
      await apiPost("/v1/models/accept", { model_id: m.id, license_id: m.license_id ?? "unknown" });
      await refresh();
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    }
  }

  async function install(m: CatalogEntry) {
    setErr("");
    try {
      // Match Setup: accept license before install so one-click Install works.
      if (m.source !== "ollama" && !(data?.accepted || {})[m.id]) {
        await apiPost("/v1/models/accept", { model_id: m.id, license_id: m.license_id ?? "unknown" });
      }
      await apiPost("/v1/models/install", { model_id: m.id });
      await refresh();
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    }
  }

  async function cancelTask(taskId: string) {
    setErr("");
    setCancellingTaskId(taskId);
    try {
      const payload = await apiPost("/v1/models/tasks/cancel", { task_id: taskId });
      const task = (payload as any)?.task;
      if (task?.id) {
        setTasks((current) => [task, ...current.filter((item) => item.id !== task.id)]);
      }
      pollModelTasksNow();
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setCancellingTaskId("");
    }
  }

  async function restoreLocal(m: CatalogEntry) {
    setErr("");
    try {
      await apiPost("/v1/models/restore_local", { model_id: m.id });
      await refresh();
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    }
  }

  async function installPack(packId: string) {
    setErr("");
    try {
      const pack = (data?.packs ?? []).find((p: any) => p.id === packId);
      const all = [...(data?.catalog ?? []), ...(data?.user ?? [])];
      const accepted = data?.accepted ?? {};
      for (const mid of (pack?.models ?? [])) {
        const m = all.find((x: any) => x.id === mid);
        if (!m || m.source === "ollama" || accepted[mid]) continue;
        await apiPost("/v1/models/accept", { model_id: mid, license_id: m.license_id ?? "unknown" });
      }
      await apiPost("/v1/models/install_pack", { pack_id: packId });
      await refresh();
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    }
  }

  async function importCivitai() {
    setImporting(true);
    setErr("");
    try {
      await apiPost("/v1/models/import/civitai", { url: civitaiUrl });
      setCivitaiUrl("");
      await refresh();
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setImporting(false);
    }
  }

  async function importLocal() {
    setErr("");
    const picked = await window.edmg?.pickFile?.({
      title: "Select model file",
      filters: [{ name: "Model files", extensions: ["safetensors", "ckpt", "pt", "bin"] }]
    });
    const filePath = picked?.ok && !picked.canceled
      ? String(picked.paths?.[0] || "").trim()
      : "";
    if (!filePath) return;
    await apiPost("/v1/models/import/local", { file_path: filePath, folder: localFolder });
    await refresh();
  }

  async function importLegacyTensorRt() {
    const confirmed = window.confirm(
      "Verify and copy the four legacy TensorRT engines into the canonical Studio bundle? " +
      "This can take several minutes and requires enough free space for a complete second copy. " +
      "The original files will remain unchanged."
    );
    if (!confirmed) return;
    setTensorRtBusy(true);
    setTensorRtMessage("");
    setErr("");
    try {
      const payload = await apiPost("/v1/models/tensorrt/import-legacy", {}, { timeoutMs: 30_000 });
      const task = (payload as any)?.task;
      if (task?.id) {
        setTasks((current) => [task, ...current.filter((item) => item.id !== task.id)]);
      }
      setTensorRtMessage(
        "Verification and safe copy started. The legacy source will remain in place, and Studio will publish only a fully hash-verified engine copy."
      );
      pollModelTasksNow();
      await loadCatalog();
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setTensorRtBusy(false);
    }
  }

  async function cancelLegacyTensorRtImport(taskId: string) {
    setTensorRtBusy(true);
    setTensorRtMessage("");
    setErr("");
    try {
      const payload = await apiPost(
        "/v1/models/tensorrt/cancel-import",
        { task_id: taskId },
        { timeoutMs: 15_000 },
      );
      const task = (payload as any)?.task;
      if (task?.id) {
        setTasks((current) => [task, ...current.filter((item) => item.id !== task.id)]);
      }
      setTensorRtMessage("Cancellation requested. Studio will remove only its temporary copy and leave every legacy engine unchanged.");
      pollModelTasksNow();
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setTensorRtBusy(false);
    }
  }

  const acceptedMap = data?.accepted ?? {};
  const installedMap = data?.installed ?? {};
  const cloudMap = data?.cloud ?? {};
  const storageMode = data?.storage_mode ?? "local_cache";
  const cacheLabel = data?.model_cache || "cloud cache";
  const activeTensorRtTask = tasks.find(
    (task: any) => task.model_id === "local_sd15_tensorrt_bundle"
      && (task.status === "queued" || task.status === "running"),
  );

  const internalSummary = useMemo(() => {
    return buildInternalModelReadiness({
      catalog: merged.built,
      installed: installedMap,
      cloud: cloudMap,
      modelCache: cacheLabel,
      tasks,
    });
  }, [merged.built, installedMap, cloudMap, cacheLabel, tasks]);

  const defaultModels = (merged.built ?? []).filter((m) => m.recommended === "default" && m.installable !== false);
  const advancedModels = (merged.built ?? []).filter((m) => m.recommended !== "default" && m.installable !== false);
  const browserOnlyModels = (merged.built ?? []).filter((m) => m.installable === false);

  const panelDefinitions = useMemo(
    () => [
      {
        id: "packs" as const,
        label: "First-run packs",
        description: "Studio-supported starter packs and quick install actions.",
      },
      {
        id: "internal" as const,
        label: "Internal render readiness",
        description: "Preferred internal diffusion models and readiness summary.",
      },
      {
        id: "runtime" as const,
        label: "Runtime bridges",
        description: "Optional hosted Stability and DirectML-specific runtime surfaces.",
      },
      {
        id: "discovery" as const,
        label: "Discovery",
        description: "Hub browsing and source collections for supported models.",
      },
      {
        id: "imports" as const,
        label: "Imports",
        description: "Safely migrate legacy runtimes or bring community and local models into Studio.",
      },
      {
        id: "defaults" as const,
        label: "Recommended defaults",
        description: "Studio-ready recommended models surfaced for day-to-day use.",
      },
      {
        id: "advanced" as const,
        label: "Advanced inventory",
        description: "Optional models, user models, and browser-only bundles.",
      },
    ],
    [],
  );
  const { profileOptions, activeProfile, setActiveProfile, layoutState, visibleOrder, movePanel, updateHidden, resetLayout } =
    useStudioPageLayout<ModelsPanelId>(
      "models",
      panelDefinitions.map((panel) => panel.id),
    );
  const panelDefinitionById = useMemo(
    () =>
      Object.fromEntries(
        panelDefinitions.map((definition) => [definition.id, definition]),
      ) as Record<ModelsPanelId, (typeof panelDefinitions)[number]>,
    [panelDefinitions],
  );
  const panelControlItems = layoutState.order.map((panelId, index) => ({
    id: panelId,
    label: panelDefinitionById[panelId].label,
    description: panelDefinitionById[panelId].description,
    hidden: layoutState.hidden.includes(panelId),
    canMoveUp: index > 0,
    canMoveDown: index < layoutState.order.length - 1,
  }));

  const panelContent: Record<ModelsPanelId, React.ReactNode> = {
    packs: (
      <div className="card" style={{ marginTop: 14 }}>
        <div style={{ fontWeight: 900 }}>First-run packs</div>
        <div className="small" style={{ marginTop: 6 }}>
          Pick a pack to get started. Packs install Studio-supported defaults only; browser-only runtime bundles stay separate until their execution path is ready.
        </div>
        <div style={{ marginTop: 10, display: "flex", gap: 10, flexWrap: "wrap" }}>
          {(data?.packs ?? []).map((p: any) => (
            <button key={p.id} onClick={() => installPack(p.id)}>
              Install: {p.name}
            </button>
          ))}
        </div>
        {mode === "advanced" && (data?.packs ?? []).length ? (
          <div className="small" style={{ marginTop: 10, opacity: 0.85 }}>
            Packs only enqueue installs. Track progress in the tasks list below.
          </div>
        ) : null}
      </div>
    ),
    internal: (
      <div className="card" style={{ marginTop: 14 }}>
        <div style={{ fontWeight: 900 }}>Internal render readiness</div>
        <div className="small" style={{ marginTop: 6 }}>
          Internal still/keyframe rendering is ready when at least one diffusers model is installed. Full internal motion needs an internal video adapter: SVD for keyframe image-to-video, or AnimateDiff for SD1.5 prompt motion.
        </div>
        <div className="small" style={{ marginTop: 8 }}>
          SD 1.5: <b>{internalSummary.status("sd15")}</b>
          {" "}• SDXL: <b>{internalSummary.status("sdxl")}</b>
          {" "}• SD3.5 Medium: <b>{internalSummary.status("sd35")}</b>
          {" "}• FLUX.1 Schnell: <b>{internalSummary.status("flux")}</b>
          {" "}• Preferred: <b>{internalSummary.preferred}</b>
        </div>
        <div className="small" style={{ marginTop: 6 }}>
          Internal motion: SVD <b>{internalSummary.status("svd")}</b>
          {" "}• AnimateDiff <b>{internalSummary.status("animatediff")}</b>
        </div>
        <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
          {!internalSummary.availableInternal.sd15 && internalSummary.sd15 ? (
            <button onClick={() => install(internalSummary.sd15)}>Install SD 1.5 internal</button>
          ) : null}
          {internalSummary.cloudInternal.sd15 && !internalSummary.installedInternal.sd15 && internalSummary.sd15 ? (
            <button className="secondary" onClick={() => restoreLocal(internalSummary.sd15)}>Restore SD 1.5 internal</button>
          ) : null}
          {!internalSummary.availableInternal.sdxl && internalSummary.sdxl ? (
            <button onClick={() => install(internalSummary.sdxl)}>Install SDXL internal</button>
          ) : null}
          {internalSummary.cloudInternal.sdxl && !internalSummary.installedInternal.sdxl && internalSummary.sdxl ? (
            <button className="secondary" onClick={() => restoreLocal(internalSummary.sdxl)}>Restore SDXL internal</button>
          ) : null}
          {!internalSummary.availableInternal.sd35 && internalSummary.sd35 ? (
            <button onClick={() => install(internalSummary.sd35)}>Install SD3.5 internal</button>
          ) : null}
          {internalSummary.cloudInternal.sd35 && !internalSummary.installedInternal.sd35 && internalSummary.sd35 ? (
            <button className="secondary" onClick={() => restoreLocal(internalSummary.sd35)}>Restore SD3.5 internal</button>
          ) : null}
          {!internalSummary.availableInternal.flux && internalSummary.flux ? (
            <button onClick={() => install(internalSummary.flux)}>Install FLUX.1 Schnell internal</button>
          ) : null}
          {internalSummary.cloudInternal.flux && !internalSummary.installedInternal.flux && internalSummary.flux ? (
            <button className="secondary" onClick={() => restoreLocal(internalSummary.flux)}>Restore FLUX.1 Schnell internal</button>
          ) : null}
          {!internalSummary.availableInternal.svd && internalSummary.svd ? (
            <button onClick={() => install(internalSummary.svd)}>Install internal SVD motion</button>
          ) : null}
          {internalSummary.cloudInternal.svd && !internalSummary.installedInternal.svd && internalSummary.svd ? (
            <button className="secondary" onClick={() => restoreLocal(internalSummary.svd)}>Restore internal SVD motion</button>
          ) : null}
          {!internalSummary.availableInternal.animatediff && internalSummary.animatediff ? (
            <button onClick={() => install(internalSummary.animatediff)}>Install internal AnimateDiff</button>
          ) : null}
          {internalSummary.cloudInternal.animatediff && !internalSummary.installedInternal.animatediff && internalSummary.animatediff ? (
            <button className="secondary" onClick={() => restoreLocal(internalSummary.animatediff)}>Restore internal AnimateDiff</button>
          ) : null}
          <button className="secondary" onClick={() => props.onNavigate?.("render")}>Open Render</button>
        </div>
      </div>
    ),
    runtime: (
      <>
        {renderProviders?.stability?.visible ? (
          <div className="card" style={{ marginTop: 14 }}>
            <div style={{ fontWeight: 900 }}>Hosted Stability fallback</div>
            <div className="small" style={{ marginTop: 6 }}>
              Hosted keyframe rendering is configured and will appear as an internal render option in Render. Studio keeps assembly, caching, history, retry, and resume local while using Stability&apos;s hosted image API for keyframes.
            </div>
            <div className="small" style={{ marginTop: 8 }}>
              Service: <b>{renderProviders?.stability?.service}</b>
              {renderProviders?.stability?.service === "sd3" ? <> • model <b>{renderProviders?.stability?.model}</b></> : null}
              {" "}• style <b>{renderProviders?.stability?.style_preset || "none"}</b>
            </div>
            <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button className="secondary" onClick={() => props.onNavigate?.("render")}>Open Render</button>
              <button className="secondary" onClick={() => props.onNavigate?.("settings")}>Open Settings</button>
            </div>
          </div>
        ) : null}

        {renderProviders?.directml?.available ? (
          <div className="card" style={{ marginTop: 14 }}>
            <div style={{ fontWeight: 900 }}>AMD / DirectML runtime</div>
            <div className="small" style={{ marginTop: 6 }}>
              DirectML is available on <b>{renderProviders?.directml?.device_name || "this Windows GPU"}</b>. Studio&apos;s internal renderer can now use SDXL and SD 1.5 through ONNX Runtime on supported AMD / Windows machines.
            </div>
            <div className="small" style={{ marginTop: 8 }}>
              Preferred DirectML model: <b>{renderProviders?.directml?.preferred_model || "auto"}</b> • active backend <b>{renderProviders?.directml?.active ? "yes" : "no"}</b>
            </div>
            <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button className="secondary" onClick={() => props.onNavigate?.("render")}>Open Render</button>
              <button className="secondary" onClick={() => props.onNavigate?.("settings")}>Tune runtime</button>
            </div>
          </div>
        ) : null}

        {!renderProviders?.stability?.visible && !renderProviders?.directml?.available ? (
          <div className="card" style={{ marginTop: 14 }}>
            <div style={{ fontWeight: 900 }}>Runtime bridges</div>
            <div className="small" style={{ marginTop: 6 }}>
              No optional hosted or hardware-specific runtime bridge is active yet. The default internal render path and model install flow remain unchanged.
            </div>
          </div>
        ) : null}
      </>
    ),
    discovery: (
      <>
        <div className="card" style={{ marginTop: 14 }}>
          <div style={{ fontWeight: 900 }}>Stability quick links</div>
          <div className="small" style={{ marginTop: 6 }}>
            These are the source collections and repos Studio now uses for curated model discovery.
          </div>
          <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
            {STABILITY_LINKS.map((link) => (
              <button key={link.url} className="secondary" onClick={() => window.edmg?.openExternal?.(link.url)}>
                {link.label}
              </button>
            ))}
          </div>
        </div>

        <div className="card" style={{ marginTop: 14 }}>
          <div style={{ fontWeight: 900 }}>Stability Hub browser</div>
          <div className="small" style={{ marginTop: 6 }}>
            Powered by Hugging Face Hub search for a richer in-app browse flow. Studio only enables one-click install when a result maps cleanly to a supported local runtime path.
          </div>
          <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <select value={hubCollectionId} onChange={(e) => setHubCollectionId(e.target.value)}>
              {HUB_COLLECTIONS.map((item) => (
                <option key={item.id} value={item.id}>{item.label}</option>
              ))}
            </select>
            <input
              style={{ minWidth: 320 }}
              value={hubQuery}
              onChange={(e) => setHubQuery(e.target.value)}
              placeholder={hubCollection.search}
            />
            <button className="secondary" onClick={() => window.edmg?.openExternal?.(hubCollection.url)}>Open official collection</button>
          </div>
          <div className="small" style={{ marginTop: 8, opacity: 0.84 }}>{hubCollection.note}</div>
          {hubError ? <div className="small" style={{ marginTop: 8, color: "var(--danger)" }}>{hubError}</div> : null}
          {hubLoading ? <div className="small" style={{ marginTop: 8, opacity: 0.8 }}>Loading Hub results…</div> : null}
          {!hubLoading && !hubResults.length && !hubError ? (
            <div className="small" style={{ marginTop: 8, opacity: 0.8 }}>No results yet.</div>
          ) : null}
          {hubResults.map((result) => {
            const matchedCatalog = builtByRepoId.get(result.id) ?? null;
            return (
              <HubResultCard
                key={result.id}
                result={result}
                matchedCatalog={matchedCatalog}
                installed={matchedCatalog ? !!installedMap[matchedCatalog.id] : false}
                cloudStored={matchedCatalog ? !!cloudMap[matchedCatalog.id] : false}
                cacheLabel={cacheLabel}
                accepted={matchedCatalog ? !!acceptedMap[matchedCatalog.id] : false}
                onAccept={accept}
                onInstall={install}
                onOpen={(u) => window.edmg?.openExternal?.(u)}
              />
            );
          })}
        </div>
      </>
    ),
    imports: (
      <>
        <LegacyTensorRtCard
          status={data?.tensorrt_migration}
          activeTask={activeTensorRtTask}
          busy={tensorRtBusy}
          message={tensorRtMessage}
          onImport={() => void importLegacyTensorRt()}
          onCancel={(taskId) => void cancelLegacyTensorRtImport(taskId)}
        />

        <div className="card" style={{ marginTop: 14 }}>
          <div style={{ fontWeight: 900 }}>Add community model (Civitai)</div>
          <div className="small" style={{ marginTop: 6 }}>
            Paste a Civitai model URL (optionally with <code>modelVersionId=…</code>) or a numeric model ID. You&apos;ll be prompted to review license/terms.
          </div>
          <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
            <input
              style={{ minWidth: 420 }}
              value={civitaiUrl}
              onChange={(e) => setCivitaiUrl(e.target.value)}
              placeholder="https://civitai.com/models/12345/…?modelVersionId=67890"
            />
            <button disabled={!civitaiUrl || importing} onClick={importCivitai}>
              {importing ? "Importing…" : "Import"}
            </button>
            <button className="secondary" onClick={() => window.edmg?.openExternal?.("https://civitai.com/")}>
              Open Civitai
            </button>
          </div>
        </div>

        <div className="card" style={{ marginTop: 14 }}>
          <div style={{ fontWeight: 900 }}>Bring your own</div>
          <div className="small" style={{ marginTop: 6 }}>
            Add a local checkpoint, LoRA, or ControlNet. EDMG copies it into the Studio-managed ComfyUI models folder.
          </div>
          <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <label className="small" style={{ fontWeight: 800 }}>Type:</label>
            <select value={localFolder} onChange={(e) => setLocalFolder(e.target.value)}>
              <option value="checkpoints">Checkpoint</option>
              <option value="loras">LoRA</option>
              <option value="embeddings">Embedding</option>
              <option value="vae">VAE</option>
              <option value="controlnet">ControlNet</option>
            </select>
            <button onClick={importLocal}>Pick file…</button>
          </div>
        </div>
      </>
    ),
    defaults: (
      <>
        <h3 style={{ marginTop: 18 }}>Recommended defaults</h3>
        {defaultModels.map((m) => (
          <ModelCard
            key={m.id}
            m={m}
            installed={!!installedMap[m.id]}
            cloudRecord={cloudMap[m.id]}
            storageMode={storageMode} cacheLabel={cacheLabel}
            accepted={!!acceptedMap[m.id]}
            onAccept={() => accept(m)}
            onInstall={() => install(m)}
            onRestore={() => restoreLocal(m)}
            onOpen={(u) => window.edmg?.openExternal?.(u)}
            onPromote={(lane) => promoteModel(m.id, lane)}
            onBenchmark={() => recordBenchmark(m.id)}
          />
        ))}
      </>
    ),
    advanced: mode === "advanced" ? (
      <>
        <h3 style={{ marginTop: 18 }}>Advanced / optional</h3>
        {advancedModels.map((m) => (
          <ModelCard
            key={m.id}
            m={m}
            installed={!!installedMap[m.id]}
            cloudRecord={cloudMap[m.id]}
            storageMode={storageMode} cacheLabel={cacheLabel}
            accepted={!!acceptedMap[m.id]}
            onAccept={() => accept(m)}
            onInstall={() => install(m)}
            onRestore={() => restoreLocal(m)}
            onOpen={(u) => window.edmg?.openExternal?.(u)}
            onPromote={(lane) => promoteModel(m.id, lane)}
            onBenchmark={() => recordBenchmark(m.id)}
          />
        ))}

        {browserOnlyModels.length ? (
          <>
            <h3 style={{ marginTop: 18 }}>Discovery-only runtime bundles</h3>
            <div className="small" style={{ opacity: 0.82 }}>
              These vendor runtime bundles are listed for discovery and future adapters. Studio currently executes internal TensorRT video only through the local SD1.5 bundle; SVD/SD3.5 TensorRT bundles are not selectable render engines yet.
            </div>
            {browserOnlyModels.map((m) => (
              <ModelCard
                key={m.id}
                m={m}
                installed={!!installedMap[m.id]}
                cloudRecord={cloudMap[m.id]}
                storageMode={storageMode} cacheLabel={cacheLabel}
                accepted={!!acceptedMap[m.id]}
                onAccept={() => accept(m)}
                onInstall={() => install(m)}
                onRestore={() => restoreLocal(m)}
                onOpen={(u) => window.edmg?.openExternal?.(u)}
                onPromote={(lane) => promoteModel(m.id, lane)}
                onBenchmark={() => recordBenchmark(m.id)}
              />
            ))}
          </>
        ) : null}

        <h3 style={{ marginTop: 18 }}>User models</h3>
        {(merged.user ?? []).length ? (
          (merged.user ?? []).map((m) => (
            <div key={m.id}>
              <ModelCard
                m={m}
                installed={!!installedMap[m.id]}
                cloudRecord={cloudMap[m.id]}
                storageMode={storageMode} cacheLabel={cacheLabel}
                accepted={!!acceptedMap[m.id]}
                onAccept={() => accept(m)}
                onInstall={() => install(m)}
                onRestore={() => restoreLocal(m)}
                onOpen={(u) => window.edmg?.openExternal?.(u)}
                onPromote={(lane) => promoteModel(m.id, lane)}
                onBenchmark={() => recordBenchmark(m.id)}
              />
              <div style={{ marginTop: 6, display: "flex", gap: 8 }}>
                <button className="secondary" onClick={() => apiPost("/v1/models/remove_user", { model_id: m.id }).then(refresh)}>
                  Remove from list
                </button>
              </div>
            </div>
          ))
        ) : (
          <div className="small" style={{ opacity: 0.8 }}>No user models yet.</div>
        )}

      </>
    ) : (
      <div className="card" style={{ marginTop: 14 }}>
        <div style={{ fontWeight: 900 }}>Advanced inventory</div>
        <div className="small" style={{ marginTop: 6 }}>
          Switch Settings to <b>Advanced</b> UI mode when you want optional models, user-model management, and install task details.
        </div>
      </div>
    ),
  };

  return (
    <div>
      <h2>Model Manager</h2>
      <div className="small" style={{ marginTop: 6 }}>
        EDMG ships with a curated model catalog, but does <b>not</b> bundle large weights in the installer. Use this page to install Studio-ready defaults, add community models, or browse curated Stability model families.
      </div>
      <div style={{ marginTop: 8, display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <div className="small" style={{ opacity: 0.86 }}>
          Storage mode: <b>{storageMode === "cloud_only" ? "cloud-only" : "local + cache"}</b>
          {data?.model_cache ? <> • Cache: <b>{data.model_cache}</b> (priority over S3/Azure)</> : null}
        </div>
        <button className="secondary" disabled={catalogLoading} onClick={() => void refresh()}>
          {catalogLoading ? "Refreshing models…" : "Refresh models"}
        </button>
      </div>

      {catalogLoading ? (
        <div className="small" role="status" aria-live="polite" style={{ marginTop: 8 }}>Loading model catalog…</div>
      ) : null}
      {(err || taskPollingError) && (
        <div role="alert" style={{ marginTop: 12, padding: 10, borderRadius: 10, background: "#2a1b1b", border: "1px solid #5b2424" }}>
          <div style={{ fontWeight: 800 }}>Error</div>
          <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>{err || taskPollingError}</pre>
        </div>
      )}
      <ModelTaskProgress
        tasks={tasks}
        isPolling={tasksPolling}
        cancellingTaskId={cancellingTaskId}
        onCancel={(taskId) => void cancelTask(taskId)}
      />
      <StudioLayoutCustomizer
        title="Model Manager layout"
        description="Reorder or hide major model-management sections without changing install queues, runtime choices, or the catalog itself."
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
