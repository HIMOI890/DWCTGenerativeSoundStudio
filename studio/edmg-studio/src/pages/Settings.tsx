import React, { useEffect, useState } from "react";
import { apiGet, apiPost } from "../components/api";
import { useUiMode } from "../components/uiMode";
import { clearRenderDefaults, readRenderDefaults, writeRenderDefaults } from "../components/renderDefaults";
import type { PageProps } from "../types/pageProps";

export default function Settings(_props: PageProps) {
  const { mode, setMode } = useUiMode();
  const [cfg, setCfg] = useState<any>(null);
  const [aiStatus, setAiStatus] = useState<any>(null);
  const [edmgTemplate, setEdmgTemplate] = useState<any>(null);
  const [secrets, setSecrets] = useState<any>(null);
  const [hardware, setHardware] = useState<any>(null);
  const [renderProfiles, setRenderProfiles] = useState<any>(null);
  const [savedRenderDefaults, setSavedRenderDefaults] = useState<any>(() => readRenderDefaults());
  const [hfToken, setHfToken] = useState<string>("");
  const [civitaiKey, setCivitaiKey] = useState<string>("");
  const [saving, setSaving] = useState<boolean>(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    apiGet("/v1/config").then(setCfg).catch((e) => setErr(String(e)));
    apiGet("/v1/ai/status").then(setAiStatus).catch(() => {});
    apiGet("/v1/edmg/deforum_template").then(setEdmgTemplate).catch(() => {});
    apiGet("/v1/settings/secrets/status").then(setSecrets).catch(() => {});
    apiGet("/v1/hardware").then(setHardware).catch(() => {});
    apiGet("/v1/settings/render_profiles").then(setRenderProfiles).catch(() => {});
  }, []);

  async function refreshSecrets() {
    try {
      const s = await apiGet("/v1/settings/secrets/status");
      setSecrets(s);
    } catch {
      // ignore
    }
  }

  async function saveSecret(name: "hf_token" | "civitai_api_key", value: string) {
    setSaving(true);
    setErr(null);
    try {
      await apiPost("/v1/settings/secrets/set", { name, value });
      if (name === "hf_token") setHfToken("");
      if (name === "civitai_api_key") setCivitaiKey("");
      await refreshSecrets();
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setSaving(false);
    }
  }


  function applyRenderProfile(profileId: "laptop_safe" | "balanced_auto" | "high_quality") {
    const p = renderProfiles?.profiles?.[profileId];
    if (!p) return;
    const next = {
      profileId,
      renderPreset: p.render_preset,
      internalRenderTier: p.internal_render_tier,
      internalResumeExisting: !!p.resume_existing_frames,
    };
    writeRenderDefaults(next);
    setSavedRenderDefaults(next);
  }

  function resetRenderProfile() {
    clearRenderDefaults();
    setSavedRenderDefaults({});
  }

  async function clearSecret(name: "hf_token" | "civitai_api_key") {
    setSaving(true);
    setErr(null);
    try {
      await apiPost("/v1/settings/secrets/clear", { name });
      await refreshSecrets();
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <h1>Settings</h1>
      {err && <div style={{ color: "var(--danger)" }}>{err}</div>}

      <div className="card" style={{ marginTop: 14 }}>
        <div style={{ fontWeight: 800, marginBottom: 10 }}>UI Mode</div>
        <div className="small" style={{ marginBottom: 10 }}>
          Simple keeps the day-to-day workflow one-click. Advanced exposes every knob (routing, parameters, debugging).
        </div>
        <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>
          <button className={mode === "simple" ? "" : "secondary"} onClick={() => setMode("simple")}>Simple</button>
          <button className={mode === "advanced" ? "" : "secondary"} onClick={() => setMode("advanced")}>Advanced</button>
          <div className="small" style={{ opacity: 0.8 }}>current: <b>{mode}</b></div>
        </div>
      </div>


      <div className="card" style={{ marginTop: 14 }}>
        <div style={{ fontWeight: 800, marginBottom: 10 }}>Render defaults</div>
        <div className="small" style={{ marginBottom: 10 }}>
          Save a hardware-aware quick profile here and the Render page will pick it up automatically. This is the safest place to tune laptop vs workstation behavior without changing project content.
        </div>
        <div className="small" style={{ marginBottom: 10, opacity: 0.9 }}>
          Hardware: <b>{hardware?.hardware?.device_name || "unknown"}</b> • backend family <b>{hardware?.hardware?.backend_family || "cpu_only"}</b> • recommended tier <b>{hardware?.hardware?.recommended_tier || "draft"}</b>
        </div>
        {renderProfiles ? (
          <div style={{ display: "grid", gap: 10 }}>
            {Object.entries(renderProfiles.profiles || {}).map(([id, profile]: any) => (
              <div key={id} style={{ border: "1px solid var(--line)", borderRadius: 10, padding: 12 }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
                  <div>
                    <div style={{ fontWeight: 800 }}>{profile.label} {renderProfiles.recommended_profile === id ? <span className="small" style={{ marginLeft: 6, opacity: 0.8 }}>(recommended)</span> : null}</div>
                    <div className="small" style={{ opacity: 0.85 }}>{profile.description}</div>
                    <div className="small" style={{ marginTop: 4, opacity: 0.82 }}>Preset <b>{profile.render_preset}</b> • internal tier <b>{profile.internal_render_tier}</b> • resume caches <b>{profile.resume_existing_frames ? "on" : "off"}</b></div>
                  </div>
                  <button className={savedRenderDefaults?.profileId === id ? "" : "secondary"} onClick={() => applyRenderProfile(id as any)}>Use on Render page</button>
                </div>
              </div>
            ))}
            <div className="row" style={{ gap: 10, alignItems: "center", flexWrap: "wrap" }}>
              <button className="secondary" onClick={resetRenderProfile}>Clear saved defaults</button>
              <div className="small" style={{ opacity: 0.82 }}>Current saved profile: <b>{savedRenderDefaults?.profileId || "none"}</b></div>
            </div>
          </div>
        ) : (
          <div className="small" style={{ opacity: 0.75 }}>Loading render profiles…</div>
        )}
      </div>

      {cfg && <div className="card"><pre>{JSON.stringify(cfg, null, 2)}</pre></div>}

      {aiStatus && (
        <div className="card" style={{ marginTop: 14 }}>
          <div style={{ fontWeight: 800, marginBottom: 10 }}>AI Status</div>
          <div className="small" style={{ marginBottom: 10 }}>
            Default is <b>local Ollama</b>. You can also point to OpenAI-compatible providers or a remote AI service.
          </div>
          <pre>{JSON.stringify(aiStatus, null, 2)}</pre>
        </div>
      )}

      <div className="card" style={{ marginTop: 14 }}>
        <div style={{ fontWeight: 800, marginBottom: 10 }}>Tokens</div>
        <div className="small" style={{ marginBottom: 10 }}>
          Optional. Needed only for gated Hugging Face downloads and some Civitai downloads. Stored in OS keychain when available; otherwise stored locally under the Studio data directory.
        </div>
        {secrets ? (
          <div className="small" style={{ marginBottom: 10, opacity: 0.9 }}>
            Storage: <b>{secrets.store}</b>
            {secrets.note ? <span style={{ marginLeft: 10, opacity: 0.85 }}>{secrets.note}</span> : null}
          </div>
        ) : (
          <div className="small" style={{ marginBottom: 10, opacity: 0.75 }}>Loading token status…</div>
        )}

        <div style={{ display: "grid", gridTemplateColumns: "1fr auto auto", gap: 8, alignItems: "center" }}>
          <div>
            <div className="small" style={{ fontWeight: 800 }}>Hugging Face token</div>
            <div className="small" style={{ opacity: 0.8 }}>Used for gated HF models/checkpoints.</div>
            <input
              value={hfToken}
              onChange={(e) => setHfToken(e.target.value)}
              placeholder={secrets?.has_hf_token ? "(set) paste to replace" : "paste token"}
            />
          </div>
          <button disabled={saving || !hfToken} onClick={() => saveSecret("hf_token", hfToken)}>Save</button>
          <button className="secondary" disabled={saving || !secrets?.has_hf_token} onClick={() => clearSecret("hf_token")}>Clear</button>

          <div>
            <div className="small" style={{ fontWeight: 800 }}>Civitai API key</div>
            <div className="small" style={{ opacity: 0.8 }}>Used for some Civitai imports/downloads.</div>
            <input
              value={civitaiKey}
              onChange={(e) => setCivitaiKey(e.target.value)}
              placeholder={secrets?.has_civitai_api_key ? "(set) paste to replace" : "paste API key"}
            />
          </div>
          <button disabled={saving || !civitaiKey} onClick={() => saveSecret("civitai_api_key", civitaiKey)}>Save</button>
          <button className="secondary" disabled={saving || !secrets?.has_civitai_api_key} onClick={() => clearSecret("civitai_api_key")}>Clear</button>
        </div>
      </div>

      <div className="card" style={{ marginTop: 14 }}>
        <div style={{ fontWeight: 800, marginBottom: 10 }}>ComfyUI workflow</div>
        <div className="small">
          Studio uses a built-in ComfyUI workflow template (CheckpointLoaderSimple → CLIPTextEncode → KSampler → VAEDecode → SaveImage).
          Ensure your checkpoint name matches <code>EDMG_COMFYUI_CHECKPOINT</code>.
        </div>
      </div>

      {edmgTemplate && (
        <div className="card" style={{ marginTop: 14 }}>
          <div style={{ fontWeight: 800, marginBottom: 10 }}>Deforum template (from EDMG Core)</div>
          <div className="small">This is an editing surface / reference for Deforum exports.</div>
          <pre>{JSON.stringify(edmgTemplate, null, 2)}</pre>
        </div>
      )}
    </div>
  );
}
