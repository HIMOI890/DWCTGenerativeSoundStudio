import React, { useEffect, useMemo, useState } from "react";
import { apiGet, apiPost } from "../components/api";
import { useUiMode } from "../components/uiMode";
import type { PageProps } from "../types/pageProps";

type CatalogPayload = {
  catalog: any[];
  user: any[];
  packs: any[];
  accepted: Record<string, any>;
  installed: Record<string, boolean>;
};

function ModelCard({
  m,
  installed,
  accepted,
  onAccept,
  onInstall,
  onOpen
}: {
  m: any;
  installed: boolean;
  accepted: boolean;
  onAccept: () => void;
  onInstall: () => void;
  onOpen: (u: string) => void;
}) {
  const needsAccept = m?.source !== "ollama" && !accepted;
  const canInstall = !needsAccept;

  return (
    <div className="card" style={{ marginTop: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
        <div>
          <div style={{ fontWeight: 900 }}>{m?.name}</div>
          <div className="small" style={{ marginTop: 4 }}>
            <span style={{ opacity: 0.85 }}>
              {m?.kind} • {m?.source}
              {m?.recommended ? ` • ${m.recommended}` : ""}
            </span>
          </div>
          <div className="small" style={{ marginTop: 6 }}>
            License: <b>{m?.license_id ?? "unknown"}</b>
          </div>
          {m?.notes ? <div className="small" style={{ marginTop: 6 }}>{m.notes}</div> : null}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8, alignItems: "flex-end" }}>
          <div className="small" style={{ fontWeight: 800 }}>
            {installed ? "Installed" : "Not installed"}
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "flex-end" }}>
            {m?.license_url ? (
              <button className="secondary" onClick={() => onOpen(m.license_url)}>View license</button>
            ) : null}
            {needsAccept ? (
              <button onClick={onAccept}>Accept license</button>
            ) : null}
            <button disabled={!canInstall || installed} onClick={onInstall}>
              {installed ? "Installed" : "Install"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function Models(props: PageProps) {
  const { mode } = useUiMode();
  const [data, setData] = useState<CatalogPayload | null>(null);
  const [tasks, setTasks] = useState<any[]>([]);
  const [err, setErr] = useState<string>("");

  const [civitaiUrl, setCivitaiUrl] = useState("");
  const [importing, setImporting] = useState(false);

  const [localFolder, setLocalFolder] = useState("checkpoints");

  async function refresh() {
    setErr("");
    try {
      const d = await apiGet("/v1/models/catalog");
      setData(d as any);
      const t = await apiGet("/v1/models/tasks");
      setTasks((t as any)?.tasks ?? []);
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    }
  }

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 2000);
    return () => clearInterval(t);
  }, []);

  const merged = useMemo(() => {
    const built = data?.catalog ?? [];
    const user = data?.user ?? [];
    return { built, user };
  }, [data]);

  async function accept(m: any) {
    await apiPost("/v1/models/accept", { model_id: m.id, license_id: m.license_id ?? "unknown" });
    await refresh();
  }

  async function install(m: any) {
    await apiPost("/v1/models/install", { model_id: m.id });
    await refresh();
  }

  async function installPack(packId: string) {
    await apiPost("/v1/models/install_pack", { pack_id: packId });
    await refresh();
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
    if (!picked) return;
    await apiPost("/v1/models/import/local", { file_path: picked, folder: localFolder });
    await refresh();
  }

  const acceptedMap = data?.accepted ?? {};
  const installedMap = data?.installed ?? {};

  const internalSummary = useMemo(() => {
    const built = merged.built ?? [];
    const sd15 = built.find((m: any) => m.id === "hf_sd15_internal");
    const sdxl = built.find((m: any) => m.id === "hf_sdxl_internal");
    const installedInternal = {
      sd15: !!installedMap["hf_sd15_internal"],
      sdxl: !!installedMap["hf_sdxl_internal"],
    };
    const preferred = installedInternal.sdxl ? "SDXL" : installedInternal.sd15 ? "SD 1.5" : "none";
    return { sd15, sdxl, installedInternal, preferred };
  }, [merged, installedMap]);


  return (
    <div>
      <h2>Model Manager</h2>
      <div className="small" style={{ marginTop: 6 }}>
        EDMG ships with a curated model catalog, but does <b>not</b> bundle large weights in the installer. Use this page to install recommended defaults, add community models (Civitai), or bring your own.
      </div>

      {err && (
        <div style={{ marginTop: 12, padding: 10, borderRadius: 10, background: "#2a1b1b", border: "1px solid #5b2424" }}>
          <div style={{ fontWeight: 800 }}>Error</div>
          <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>{err}</pre>
        </div>
      )}

      <div className="card" style={{ marginTop: 14 }}>
        <div style={{ fontWeight: 900 }}>First-run packs</div>
        <div className="small" style={{ marginTop: 6 }}>
          Pick a pack to get started. You can still install/uninstall individual models below.
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
            Tip: Packs only enqueue installs. Track progress in the tasks list below.
          </div>
        ) : null}
      </div>

      <div className="card" style={{ marginTop: 14 }}>
        <div style={{ fontWeight: 900 }}>Internal render readiness</div>
        <div className="small" style={{ marginTop: 6 }}>
          Internal video rendering is ready when at least one diffusers model is installed. SDXL is preferred on stronger GPUs; SD 1.5 is the safer fallback.
        </div>
        <div className="small" style={{ marginTop: 8 }}>
          SD 1.5: <b>{internalSummary.installedInternal.sd15 ? "installed" : "missing"}</b> • SDXL: <b>{internalSummary.installedInternal.sdxl ? "installed" : "missing"}</b> • Preferred: <b>{internalSummary.preferred}</b>
        </div>
        <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
          {!internalSummary.installedInternal.sd15 && internalSummary.sd15 ? (
            <button onClick={() => install(internalSummary.sd15)}>Install SD 1.5 internal</button>
          ) : null}
          {!internalSummary.installedInternal.sdxl && internalSummary.sdxl ? (
            <button onClick={() => install(internalSummary.sdxl)}>Install SDXL internal</button>
          ) : null}
          <button className="secondary" onClick={() => props.onNavigate?.("render")}>Open Render</button>
        </div>
      </div>

      <div className="card" style={{ marginTop: 14 }}>
        <div style={{ fontWeight: 900 }}>Add community model (Civitai)</div>
        <div className="small" style={{ marginTop: 6 }}>
          Paste a Civitai model URL (optionally with <code>modelVersionId=…</code>) or a numeric model ID. You'll be prompted to review license/terms.
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
          Add a local checkpoint/LoRA/etc. EDMG will copy it into the ComfyUI models folder (Portable if installed; otherwise under <code>data/models</code>).
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

      <h3 style={{ marginTop: 18 }}>Recommended defaults</h3>
      {(merged.built ?? []).filter((m: any) => m.recommended === "default").map((m: any) => (
        <ModelCard
          key={m.id}
          m={m}
          installed={!!installedMap[m.id]}
          accepted={!!acceptedMap[m.id]}
          onAccept={() => accept(m)}
          onInstall={() => install(m)}
          onOpen={(u) => window.edmg?.openExternal?.(u)}
        />
      ))}

      {mode === "advanced" ? (
        <>
          <h3 style={{ marginTop: 18 }}>Advanced / optional</h3>
          {(merged.built ?? []).filter((m: any) => m.recommended !== "default").map((m: any) => (
            <ModelCard
              key={m.id}
              m={m}
              installed={!!installedMap[m.id]}
              accepted={!!acceptedMap[m.id]}
              onAccept={() => accept(m)}
              onInstall={() => install(m)}
              onOpen={(u) => window.edmg?.openExternal?.(u)}
            />
          ))}

          <h3 style={{ marginTop: 18 }}>User models</h3>
          {(merged.user ?? []).length ? (
            (merged.user ?? []).map((m: any) => (
              <div key={m.id}>
                <ModelCard
                  m={m}
                  installed={!!installedMap[m.id]}
                  accepted={!!acceptedMap[m.id]}
                  onAccept={() => accept(m)}
                  onInstall={() => install(m)}
                  onOpen={(u) => window.edmg?.openExternal?.(u)}
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

          <h3 style={{ marginTop: 18 }}>Install tasks</h3>
          {(tasks ?? []).length ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {(tasks ?? []).slice(0, 12).map((t: any) => (
                <div key={t.id} style={{ padding: 10, borderRadius: 12, background: "#121422", border: "1px solid #22263a" }}>
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <div style={{ fontWeight: 800 }}>{t.name}</div>
                    <div className="small">{t.status}{t.progress != null ? ` • ${Math.round(t.progress * 100)}%` : ""}</div>
                  </div>
                  {t.last_log ? <div className="small" style={{ marginTop: 6, whiteSpace: "pre-wrap" }}>{t.last_log}</div> : null}
                </div>
              ))}
            </div>
          ) : (
            <div className="small" style={{ opacity: 0.8 }}>No active tasks.</div>
          )}
        </>
      ) : null}
    </div>
  );
}
