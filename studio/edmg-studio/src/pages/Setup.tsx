import React, { useEffect, useState } from "react";
import { apiGet, apiPost } from "../components/api";

function Badge({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      style={{
        padding: "2px 8px",
        borderRadius: 999,
        fontSize: 12,
        fontWeight: 700,
        background: ok ? "#163a1f" : "#3a1616",
        color: ok ? "#b7ffcb" : "#ffb7b7",
        border: ok ? "1px solid #245b32" : "1px solid #5b2424"
      }}
    >
      {label}
    </span>
  );
}


export default function Setup({ onNavigate }: { onNavigate?: (p: any) => void }) {
  const [status, setStatus] = useState<any>(null);
  const [modelsCatalog, setModelsCatalog] = useState<any>(null);
  const [edmgVerifyResult, setEdmgVerifyResult] = useState<any>(null);
  const [studioPaths, setStudioPaths] = useState<any>(null);
  const [studioHomeInput, setStudioHomeInput] = useState<string>("");
  const [packAccept, setPackAccept] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState<string>("");
  const [err, setErr] = useState<string>("");

  async function refresh() {
    setErr("");
    try {
      const s = await apiGet("/v1/setup/status");
      setStatus(s);
      const mc = await apiGet("/v1/models/catalog");
      setModelsCatalog(mc);
      if (window.edmg?.getStudioPaths) {
        const paths = await window.edmg.getStudioPaths();
        if (paths?.ok) {
          setStudioPaths(paths);
          setStudioHomeInput((current) => current || String(paths.studioHome ?? ""));
        }
      }
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    }
  }

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 2000);
    return () => clearInterval(t);
  }, []);
async function run(action: string, path: string, body: any = {}) {
    setBusy(action);
    setErr("");
    try {
      await apiPost(path, body);
      await refresh();
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setBusy("");
    }
  }

  const ollamaOk = !!status?.ollama?.ok;
  const modelOk = !!status?.ollama?.model_present;
  const comfyOk = !!status?.comfyui?.ok;
  const ffOk = !!status?.ffmpeg?.ok;
  const edmgOk = !!status?.edmg?.available;
  const sevenOk = !!status?.sevenzip?.ok;

  async function browseStudioHome() {
    try {
      const picked = await window.edmg?.pickDirectory?.({
        title: "Select Studio Home",
        defaultPath: studioHomeInput || studioPaths?.studioHome || undefined,
      });
      if (picked?.ok && picked.path) {
        setStudioHomeInput(String(picked.path));
      }
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    }
  }

  async function applyStudioHome() {
    const target = (studioHomeInput || "").trim();
    if (!target) {
      setErr("Pick a Studio Home folder first.");
      return;
    }
    if (!window.edmg?.setStudioHome) {
      setErr("Studio Home changes are only available in the desktop app build.");
      return;
    }

    setBusy("studio_home");
    setErr("");
    try {
      const result = await window.edmg.setStudioHome(target);
      if (!result?.ok) {
        throw new Error(result?.error || "Failed to save Studio Home.");
      }
      setStudioPaths((prev: any) => ({ ...(prev || {}), ...result, source: "configured (restart pending)" }));
      setStudioHomeInput(String(result.studioHome || target));
      const confirmMsg = result?.migrationPlanned
        ? `Studio Home saved.\n\n${result?.migrationSummary || "Existing Studio data will be moved into the new home on restart."}\n\nRestart EDMG Studio now?`
        : "Studio Home saved. Restart EDMG Studio now so new downloads, projects, and caches start using that folder?";
      if (window.confirm(confirmMsg)) {
        await window.edmg?.relaunch?.();
      }
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setBusy("");
    }
  }

  async function verifyEdmgCore() {
    setBusy("verify_edmg");
    setErr("");
    try {
      const result = await apiPost("/v1/edmg/verify", {});
      setEdmgVerifyResult(result);
      await refresh();
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setBusy("");
    }
  }



  async function installPack(packId: string) {
    if (!packAccept[packId]) {
      setErr("Please accept the license terms for this pack.");
      return;
    }
    setBusy(`pack_${packId}`);
    setErr("");
    try {
      // Accept licenses for all non-ollama models in the pack before install.
      const pack = (modelsCatalog?.packs ?? []).find((p: any) => p.id === packId);
      const all = [...(modelsCatalog?.catalog ?? []), ...(modelsCatalog?.user ?? [])];
      const accepted = modelsCatalog?.accepted ?? {};
      for (const mid of (pack?.models ?? [])) {
        const m = all.find((x: any) => x.id === mid);
        if (!m) continue;
        if (m.source === "ollama") continue;
        if (accepted[mid]) continue;
        await apiPost("/v1/models/accept", { model_id: mid, license_id: m.license_id ?? "unknown" });
      }
      await apiPost("/v1/models/install_pack", { pack_id: packId });
      await refresh();
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setBusy("");
    }
  }

  return (
    <div>
      <h2>Setup Wizard</h2>
      <div className="small" style={{ marginTop: 6 }}>
        This is the "installer GUI" inside EDMG Studio. It checks required components and lets you fix issues without using the command line.
      </div>

      {err && (
        <div style={{ marginTop: 12, padding: 10, borderRadius: 10, background: "#2a1b1b", border: "1px solid #5b2424" }}>
          <div style={{ fontWeight: 800 }}>Setup error</div>
          <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>{err}</pre>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 12, marginTop: 14 }}>
        <div className="card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ fontWeight: 800 }}>Storage Location</div>
            <Badge ok={!!studioPaths?.studioHome} label={studioPaths?.studioHome ? "Configured" : "Default"} />
          </div>
          <div className="small" style={{ marginTop: 6 }}>
            Set this before running Full Setup if you want Studio projects, ComfyUI Portable, model downloads, Electron data, and caches to live on <code>D:\...</code> instead of the default app-data location on <code>C:\</code>.
          </div>
          <div style={{ marginTop: 10, display: "grid", gap: 8 }}>
            <input
              value={studioHomeInput}
              onChange={(e) => setStudioHomeInput(e.target.value)}
              placeholder="D:\\EDMG-Studio"
            />
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button className="secondary" onClick={browseStudioHome}>Browse…</button>
              <button disabled={busy === "studio_home"} onClick={applyStudioHome}>
                {busy === "studio_home" ? "Saving…" : "Save & Restart"}
              </button>
              <button
                className="secondary"
                disabled={!studioPaths?.studioHome}
                onClick={() => window.edmg?.openPath?.(studioPaths?.studioHome)}
              >
                Open Folder
              </button>
            </div>
            {studioPaths ? (
              <div className="small" style={{ display: "grid", gap: 4, opacity: 0.88 }}>
                <div>Studio home: <code>{studioPaths.studioHome}</code></div>
                <div>Project data: <code>{studioPaths.dataDir}</code></div>
                <div>Shared cache: <code>{studioPaths.cacheRoot}</code></div>
                <div>Electron data: <code>{studioPaths.electronUserData}</code></div>
                {studioPaths?.pendingMigration?.source?.studioHome ? (
                  <div style={{ color: "#ffd78c" }}>
                    Pending migration: <code>{studioPaths.pendingMigration.source.studioHome}</code> to <code>{studioPaths.pendingMigration.target?.studioHome}</code>. Restart Studio to run it.
                  </div>
                ) : null}
                {studioPaths?.lastMigration ? (
                  <div style={{ color: studioPaths.lastMigration.ok ? "#b7ffcb" : "#ffd78c" }}>
                    Last migration: {studioPaths.lastMigration.ok ? "completed" : "completed with warnings"} at{" "}
                    <code>{studioPaths.lastMigration.completedAt || "unknown time"}</code>
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        </div>

        <div className="card">
  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
    <div style={{ fontWeight: 800 }}>0) Full System Setup (One-Click)</div>
    <Badge ok={ollamaOk && comfyOk && ffOk} label={(ollamaOk && comfyOk && ffOk) ? "Ready" : "Setup"} />
  </div>
  <div className="small" style={{ marginTop: 6 }}>
    Runs the full installer pipeline: 7-Zip (if needed) → Ollama installer → pull model → ComfyUI Portable install + start.
  </div>
  <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
    <button
      disabled={busy === "full_cpu"}
      onClick={() => run("full_cpu", "/v1/setup/full/install", { flavor: "cpu" })}
    >
      {busy === "full_cpu" ? "Running…" : "Full Setup (CPU)"}
    </button>
    <button
      disabled={busy === "full_nvidia"}
      onClick={() => run("full_nvidia", "/v1/setup/full/install", { flavor: "nvidia" })}
    >
      {busy === "full_nvidia" ? "Running…" : "Full Setup (NVIDIA)"}
    </button>
  </div>
</div>

<div className="card">
  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
    <div style={{ fontWeight: 800 }}>0.5) 7-Zip (Extractor)</div>
    <Badge ok={sevenOk} label={sevenOk ? "OK" : "Missing"} />
  </div>
  <div className="small" style={{ marginTop: 6 }}>
    Required to extract some .7z archives (BCJ2), including some ComfyUI Portable releases.
  </div>
  <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
    <button
      disabled={busy === "sevenzip"}
      onClick={() => run("sevenzip", "/v1/setup/7zip/install", {})}
    >
      {busy === "sevenzip" ? "Installing…" : "Install 7-Zip"}
    </button>
  </div>
  {!sevenOk && status?.sevenzip?.hint && (
    <div className="small" style={{ marginTop: 10 }}>
      Fix: {status.sevenzip.hint}
    </div>
  )}
</div>


        <div className="card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ fontWeight: 800 }}>1) Ollama (AI)</div>
            <Badge ok={ollamaOk} label={ollamaOk ? "OK" : "Missing"} />
          </div>
          <div className="small" style={{ marginTop: 6 }}>
            EDMG uses Ollama locally at <code>http://127.0.0.1:11434</code>.
          </div>
          <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button
              className="secondary"
              onClick={() => window.edmg?.openExternal?.("https://ollama.com/download/windows")}
            >
              Open Ollama Download Page
            </button>
            <button
              disabled={busy === "ollama"}
              onClick={() => run("ollama", "/v1/setup/ollama/download_and_run", {})}
            >
              {busy === "ollama" ? "Launching…" : "Download & Run Ollama Installer"}
            </button>
          </div>
          {!ollamaOk && status?.ollama?.hint && (
            <div className="small" style={{ marginTop: 10 }}>
              Fix: {status.ollama.hint}
            </div>
          )}
        </div>

        <div className="card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ fontWeight: 800 }}>2) AI Model</div>
            <Badge ok={modelOk} label={modelOk ? "Ready" : "Not pulled"} />
          </div>
          <div className="small" style={{ marginTop: 6 }}>
            Default model: <code>{status?.ollama?.model ?? "(unknown)"}</code>
          </div>
          <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button
              disabled={!ollamaOk || busy === "pull"}
              onClick={() => run("pull", "/v1/setup/ollama/pull", { model: status?.ollama?.model })}
            >
              {busy === "pull" ? "Pulling…" : "Pull Model"}
            </button>
            <button className="secondary" onClick={refresh}>
              Refresh
            </button>
          </div>
          {status?.tasks?.length ? (
            <div style={{ marginTop: 10 }}>
              <div className="small" style={{ fontWeight: 800 }}>Installer tasks</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 6 }}>
                {status.tasks.slice(0, 5).map((t: any) => (
                  <div key={t.id} style={{ padding: 8, borderRadius: 10, background: "#121422", border: "1px solid #22263a" }}>
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <div style={{ fontWeight: 700 }}>{t.name}</div>
                      <div className="small">{t.status}{t.progress != null ? ` • ${Math.round(t.progress * 100)}%` : ""}</div>
                    </div>
                    {t.last_log && (
                      <div className="small" style={{ marginTop: 6, whiteSpace: "pre-wrap" }}>{t.last_log}</div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </div>

        <div className="card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ fontWeight: 800 }}>3) ComfyUI (Video generation)</div>
            <Badge ok={comfyOk} label={comfyOk ? "OK" : "Missing"} />
          </div>
          <div className="small" style={{ marginTop: 6 }}>
            EDMG talks to ComfyUI at <code>{status?.comfyui?.url ?? "http://127.0.0.1:8188"}</code>.
          </div>

          <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button
              className="secondary"
              onClick={() => window.edmg?.openExternal?.("https://docs.comfy.org/installation/comfyui_portable_windows")}
            >
              Open ComfyUI Portable Guide
            </button>
            <button
              disabled={busy === "comfyui"}
              onClick={() => run("comfyui", "/v1/setup/comfyui/portable/install", { flavor: "cpu" })}
            >
              {busy === "comfyui" ? "Working…" : "Install ComfyUI Portable (CPU)"}
            </button>
            <button
              disabled={busy === "comfyui_n"}
              onClick={() => run("comfyui_n", "/v1/setup/comfyui/portable/install", { flavor: "nvidia" })}
            >
              {busy === "comfyui_n" ? "Working…" : "Install ComfyUI Portable (NVIDIA)"}
            </button>
            <button
              disabled={busy === "start_comfy"}
              onClick={() => run("start_comfy", "/v1/setup/comfyui/portable/start", { flavor: "cpu" })}
            >
              {busy === "start_comfy" ? "Starting…" : "Start ComfyUI (CPU)"}
            </button>
          </div>

          {!comfyOk && status?.comfyui?.hint && (
            <div className="small" style={{ marginTop: 10 }}>
              Fix: {status.comfyui.hint}
            </div>
          )}
        </div>

        <div className="card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ fontWeight: 800 }}>4) FFmpeg (Assembly)</div>
            <Badge ok={ffOk} label={ffOk ? "OK" : "Missing"} />
          </div>
          <div className="small" style={{ marginTop: 6 }}>
            Used for stitching frames/clips into MP4 with audio.
          </div>
          <div className="small" style={{ marginTop: 10, opacity: 0.9 }}>
            Current path: {status?.ffmpeg?.path ?? "ffmpeg"}
            {status?.ffmpeg?.version ? ` • ${status.ffmpeg.version}` : ""}
          </div>
          {!ffOk && (
            <div className="small" style={{ marginTop: 10 }}>
              Fix: {status?.ffmpeg?.hint ?? "Packaged EDMG Studio should include bundled FFmpeg. If this is a dev checkout, install FFmpeg and add it to PATH, or set EDMG_FFMPEG_PATH to the ffmpeg executable."}
            </div>
          )}
        </div>

        <div className="card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ fontWeight: 800 }}>5) EDMG Core (Unified Engine)</div>
            <Badge ok={edmgOk} label={edmgOk ? "Ready" : "Optional"} />
          </div>
          <div className="small" style={{ marginTop: 6 }}>
            Studio backend installs now target EDMG Core as part of the same product surface. Use this to repair or reinstall Core if the current backend environment is missing it.
          </div>
          <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button
              disabled={busy === "install_edmg"}
              onClick={() => run("install_edmg", "/v1/setup/edmg/install", { mode: "standard", backend: "cpu" })}
            >
              {busy === "install_edmg" ? "Installing…" : (edmgOk ? "Repair EDMG Core" : "Install EDMG Core")}
            </button>
            <button
              className="secondary"
              disabled={busy === "verify_edmg"}
              onClick={verifyEdmgCore}
            >
              {busy === "verify_edmg" ? "Verifying…" : "Verify EDMG Core"}
            </button>
          </div>
          <div className="small" style={{ marginTop: 10, opacity: 0.9 }}>
            {edmgOk
              ? `Installed version: ${status?.edmg?.version || "unknown"}`
              : (status?.edmg?.hint || "Studio can run without EDMG Core, but richer analysis and full Deforum exports stay limited until it is installed.")}
          </div>
          {edmgVerifyResult ? (
            <div className="small" style={{ marginTop: 10, opacity: 0.88, whiteSpace: "pre-wrap" }}>
              Verify return code: <b>{edmgVerifyResult.returncode ?? "?"}</b>
              {edmgVerifyResult.raw ? <div style={{ marginTop: 6 }}>{String(edmgVerifyResult.raw)}</div> : null}
            </div>
          ) : null}
        </div>


        <div className="card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ fontWeight: 800 }}>6) Model Packs (Recommended)</div>
            <Badge
              ok={!!modelsCatalog?.installed?.hf_sdxl_base_1_0 || false}
              label={(modelsCatalog?.installed?.hf_sdxl_base_1_0 ? "SDXL ready" : "Optional")}
            />
          </div>
          <div className="small" style={{ marginTop: 6 }}>
            EDMG does not bundle large weights in the installer. Pick a pack to download recommended models (GUI-driven).
          </div>

          <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
            {(modelsCatalog?.packs ?? []).map((p: any) => {
              const ids = p.models ?? [];
              const all = [...(modelsCatalog?.catalog ?? []), ...(modelsCatalog?.user ?? [])];
              const models = ids.map((id: string) => all.find((m: any) => m.id === id)).filter(Boolean);
              const accepted = !!packAccept[p.id];
              return (
                <div key={p.id} style={{ padding: 10, borderRadius: 12, background: "#121422", border: "1px solid #22263a", minWidth: 420 }}>
                  <div style={{ fontWeight: 900 }}>{p.name}</div>
                  <div className="small" style={{ marginTop: 4 }}>{p.description}</div>

                  <div className="small" style={{ marginTop: 8, fontWeight: 800 }}>Included models</div>
                  <div style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 6 }}>
                    {models.map((m: any) => (
                      <div key={m.id} className="small" style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                        <div>{m.name}</div>
                        <div style={{ display: "flex", gap: 8 }}>
                          <span style={{ opacity: 0.85 }}>{m.license_id ?? "unknown"}</span>
                          {m.license_url ? (
                            <button className="secondary" onClick={() => window.edmg?.openExternal?.(m.license_url)}>License</button>
                          ) : null}
                        </div>
                      </div>
                    ))}
                  </div>

                  <div style={{ marginTop: 10, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                    <label className="small" style={{ display: "flex", gap: 8, alignItems: "center" }}>
                      <input
                        type="checkbox"
                        checked={!!packAccept[p.id]}
                        onChange={(e) => setPackAccept((prev) => ({ ...prev, [p.id]: e.target.checked }))}
                      />
                      I accept the license terms for this pack
                    </label>
                    <button
                      disabled={!accepted || busy === `pack_${p.id}`}
                      onClick={() => installPack(p.id)}
                    >
                      {busy === `pack_${p.id}` ? "Installing…" : "Install"}
                    </button>
                  </div>
                </div>
              );
            })}
            <button className="secondary" onClick={() => onNavigate?.("models" as any)}>Open Model Manager</button>
          </div>

          <div className="small" style={{ marginTop: 10, opacity: 0.9 }}>
            Licenses: You will be asked to accept each model's license terms. SDXL uses an OpenRAIL++ license; community models vary.
          </div>
        </div>

        <div className="card">
          <div style={{ fontWeight: 800 }}>Ready check</div>
          <div className="small" style={{ marginTop: 6 }}>
            When Ollama + a model + ComfyUI + FFmpeg are all OK, EDMG Studio is ready to generate. EDMG Core is optional but recommended for the fully unified workflow.
          </div>
          <div style={{ marginTop: 10, display: "flex", gap: 8 }}>
            <button
              disabled={!(ollamaOk && modelOk && comfyOk && ffOk)}
              onClick={() => onNavigate?.("workspace")}
            >
              Go to Workspace
            </button>
            <button className="secondary" onClick={refresh}>
              Re-check
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
