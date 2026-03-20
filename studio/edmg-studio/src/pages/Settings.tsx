import React, { useEffect, useMemo, useState } from "react";
import { apiGet, apiPost } from "../components/api";
import { useUiMode } from "../components/uiMode";
import { clearRenderDefaults, readRenderDefaults, writeRenderDefaults } from "../components/renderDefaults";
import type { PageProps } from "../types/pageProps";

type SecretName = "hf_token" | "civitai_api_key" | "openai_compat_api_key";

type StudioAiSettings = {
  mode: string;
  provider: string;
  aiBaseUrl: string;
  ollamaUrl: string;
  ollamaModel: string;
  openaiCompatBaseUrl: string;
  openaiCompatModel: string;
  source?: string;
};

const DEFAULT_AI_SETTINGS: StudioAiSettings = {
  mode: "local",
  provider: "ollama",
  aiBaseUrl: "http://127.0.0.1:7862",
  ollamaUrl: "http://127.0.0.1:11434",
  ollamaModel: "qwen2.5:3b-instruct",
  openaiCompatBaseUrl: "http://127.0.0.1:8000",
  openaiCompatModel: "qwen2.5-7b-instruct",
  source: "default",
};

function normalizeAiSettings(payload?: Partial<StudioAiSettings> | null): StudioAiSettings {
  const current = payload ?? {};
  const mode = String(current.mode ?? DEFAULT_AI_SETTINGS.mode).trim().toLowerCase();
  const providerRaw = String(current.provider ?? DEFAULT_AI_SETTINGS.provider).trim().toLowerCase();
  const provider =
    providerRaw === "openai" || providerRaw === "openai-compatible"
      ? "openai_compat"
      : providerRaw || DEFAULT_AI_SETTINGS.provider;

  return {
    mode: mode === "http" || mode === "remote" ? "http" : "local",
    provider: provider || DEFAULT_AI_SETTINGS.provider,
    aiBaseUrl: String(current.aiBaseUrl ?? DEFAULT_AI_SETTINGS.aiBaseUrl).trim() || DEFAULT_AI_SETTINGS.aiBaseUrl,
    ollamaUrl: String(current.ollamaUrl ?? DEFAULT_AI_SETTINGS.ollamaUrl).trim() || DEFAULT_AI_SETTINGS.ollamaUrl,
    ollamaModel: String(current.ollamaModel ?? DEFAULT_AI_SETTINGS.ollamaModel).trim() || DEFAULT_AI_SETTINGS.ollamaModel,
    openaiCompatBaseUrl:
      String(current.openaiCompatBaseUrl ?? DEFAULT_AI_SETTINGS.openaiCompatBaseUrl).trim() ||
      DEFAULT_AI_SETTINGS.openaiCompatBaseUrl,
    openaiCompatModel:
      String(current.openaiCompatModel ?? DEFAULT_AI_SETTINGS.openaiCompatModel).trim() ||
      DEFAULT_AI_SETTINGS.openaiCompatModel,
    source: String(current.source ?? DEFAULT_AI_SETTINGS.source),
  };
}

function aiSettingsFingerprint(settings: Partial<StudioAiSettings> | null | undefined): string {
  const normalized = normalizeAiSettings(settings);
  return JSON.stringify({
    mode: normalized.mode,
    provider: normalized.provider,
    aiBaseUrl: normalized.aiBaseUrl,
    ollamaUrl: normalized.ollamaUrl,
    ollamaModel: normalized.ollamaModel,
    openaiCompatBaseUrl: normalized.openaiCompatBaseUrl,
    openaiCompatModel: normalized.openaiCompatModel,
  });
}

export default function Settings(_props: PageProps) {
  const { mode, setMode } = useUiMode();
  const [cfg, setCfg] = useState<any>(null);
  const [aiStatus, setAiStatus] = useState<any>(null);
  const [edmgTemplate, setEdmgTemplate] = useState<any>(null);
  const [secrets, setSecrets] = useState<any>(null);
  const [hardware, setHardware] = useState<any>(null);
  const [renderProfiles, setRenderProfiles] = useState<any>(null);
  const [savedRenderDefaults, setSavedRenderDefaults] = useState<any>(() => readRenderDefaults());
  const [studioAiSettings, setStudioAiSettings] = useState<StudioAiSettings>(DEFAULT_AI_SETTINGS);
  const [aiDraft, setAiDraft] = useState<StudioAiSettings>(DEFAULT_AI_SETTINGS);
  const [aiSettingsLoaded, setAiSettingsLoaded] = useState<boolean>(false);
  const [hfToken, setHfToken] = useState<string>("");
  const [civitaiKey, setCivitaiKey] = useState<string>("");
  const [openaiCompatApiKey, setOpenaiCompatApiKey] = useState<string>("");
  const [saving, setSaving] = useState<boolean>(false);
  const [savingAi, setSavingAi] = useState<boolean>(false);
  const [aiRestartRequired, setAiRestartRequired] = useState<boolean>(false);
  const [aiNotice, setAiNotice] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    void refreshPage();
  }, []);

  const aiSettingsDirty = useMemo(
    () => aiSettingsFingerprint(aiDraft) !== aiSettingsFingerprint(studioAiSettings),
    [aiDraft, studioAiSettings]
  );

  async function refreshPage() {
    try {
      const nextCfg = await apiGet("/v1/config");
      setCfg(nextCfg);
      await refreshAiStartupSettings(nextCfg);
    } catch (e: any) {
      setErr(String(e?.message ?? e));
      await refreshAiStartupSettings(null);
    }

    apiGet("/v1/ai/status").then(setAiStatus).catch(() => {});
    apiGet("/v1/edmg/deforum_template").then(setEdmgTemplate).catch(() => {});
    apiGet("/v1/settings/secrets/status").then(setSecrets).catch(() => {});
    apiGet("/v1/hardware").then(setHardware).catch(() => {});
    apiGet("/v1/settings/render_profiles").then(setRenderProfiles).catch(() => {});
  }

  async function refreshAiStartupSettings(nextCfg: any) {
    try {
      if (window.edmg?.getAiSettings) {
        const saved = await window.edmg.getAiSettings();
        if (saved?.ok) {
          const normalized = normalizeAiSettings(saved);
          setStudioAiSettings(normalized);
          setAiDraft(normalized);
          setAiSettingsLoaded(true);
          return;
        }
      }
    } catch {
      // fall through to backend snapshot
    }

    const fallback = normalizeAiSettings({
      mode: nextCfg?.ai_mode,
      provider: nextCfg?.ai_provider,
      aiBaseUrl: nextCfg?.ai_base_url,
      ollamaUrl: nextCfg?.ai_ollama_url,
      ollamaModel: nextCfg?.ai_ollama_model,
      openaiCompatBaseUrl: nextCfg?.ai_openai_compat_base_url,
      openaiCompatModel: nextCfg?.ai_openai_compat_model,
      source: nextCfg ? "backend" : "default",
    });
    setStudioAiSettings(fallback);
    setAiDraft(fallback);
    setAiSettingsLoaded(true);
  }

  async function refreshSecrets() {
    try {
      const s = await apiGet("/v1/settings/secrets/status");
      setSecrets(s);
    } catch {
      // ignore
    }
  }

  async function refreshBackendAiStatus() {
    try {
      const nextCfg = await apiGet("/v1/config");
      setCfg(nextCfg);
    } catch {
      // ignore
    }

    try {
      const nextStatus = await apiGet("/v1/ai/status");
      setAiStatus(nextStatus);
    } catch {
      // ignore
    }
  }

  async function saveSecret(name: SecretName, value: string) {
    setSaving(true);
    setErr(null);
    try {
      await apiPost("/v1/settings/secrets/set", { name, value });
      if (name === "hf_token") setHfToken("");
      if (name === "civitai_api_key") setCivitaiKey("");
      if (name === "openai_compat_api_key") setOpenaiCompatApiKey("");
      await refreshSecrets();
      await refreshBackendAiStatus();
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

  function updateAiDraft(patch: Partial<StudioAiSettings>) {
    setAiDraft((current) => normalizeAiSettings({ ...current, ...patch, source: current.source }));
    setAiNotice(null);
  }

  async function saveAiSettings() {
    setSavingAi(true);
    setErr(null);
    setAiNotice(null);
    try {
      if (!window.edmg?.setAiSettings) {
        throw new Error("This Studio build cannot persist AI startup settings yet.");
      }
      const response = await window.edmg.setAiSettings(normalizeAiSettings(aiDraft));
      if (!response?.ok) {
        throw new Error(response?.error || "Failed to save AI startup settings.");
      }
      const normalized = normalizeAiSettings({ ...response, source: "bootstrap" });
      setStudioAiSettings(normalized);
      setAiDraft(normalized);
      setAiRestartRequired(!!response.restartRequired);
      setAiNotice(
        response.restartRequired
          ? "Saved. Restart Studio so the backend relaunches on the new AI provider."
          : "Saved."
      );
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setSavingAi(false);
    }
  }

  async function clearSecret(name: SecretName) {
    setSaving(true);
    setErr(null);
    try {
      await apiPost("/v1/settings/secrets/clear", { name });
      await refreshSecrets();
      await refreshBackendAiStatus();
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

      <div className="card" style={{ marginTop: 14 }}>
        <div style={{ fontWeight: 800, marginBottom: 10 }}>AI Provider</div>
        <div className="small" style={{ marginBottom: 10 }}>
          These are Studio startup settings for planning and prompt generation. Save them here, then restart Studio so the backend relaunches on the selected provider.
        </div>
        <div className="small" style={{ marginBottom: 12, opacity: 0.85 }}>
          Saved startup config: <b>{aiSettingsLoaded ? `${aiDraft.mode === "http" ? "remote_ai_service" : aiDraft.provider}` : "loading"}</b>
          {studioAiSettings.source ? <span> • source <b>{studioAiSettings.source}</b></span> : null}
        </div>

        <div style={{ display: "grid", gap: 12 }}>
          <div>
            <div className="small" style={{ fontWeight: 800, marginBottom: 4 }}>Mode</div>
            <select value={aiDraft.mode} onChange={(e) => updateAiDraft({ mode: e.target.value })}>
              <option value="local">Local provider inside Studio backend</option>
              <option value="http">Remote AI service over HTTP</option>
            </select>
          </div>

          {aiDraft.mode === "local" ? (
            <>
              <div>
                <div className="small" style={{ fontWeight: 800, marginBottom: 4 }}>Local provider</div>
                <select value={aiDraft.provider} onChange={(e) => updateAiDraft({ provider: e.target.value })}>
                  <option value="ollama">Ollama</option>
                  <option value="openai_compat">OpenAI-compatible</option>
                  <option value="rule_based">Rule-based fallback</option>
                </select>
              </div>

              {aiDraft.provider === "ollama" ? (
                <>
                  <div>
                    <div className="small" style={{ fontWeight: 800, marginBottom: 4 }}>Ollama URL</div>
                    <input value={aiDraft.ollamaUrl} onChange={(e) => updateAiDraft({ ollamaUrl: e.target.value })} placeholder="http://127.0.0.1:11434" />
                  </div>
                  <div>
                    <div className="small" style={{ fontWeight: 800, marginBottom: 4 }}>Ollama model</div>
                    <input value={aiDraft.ollamaModel} onChange={(e) => updateAiDraft({ ollamaModel: e.target.value })} placeholder="qwen2.5:3b-instruct" />
                  </div>
                  <div className="small" style={{ opacity: 0.82 }}>
                    Best free/local default. Good for offline use and zero paid-provider requirements.
                  </div>
                </>
              ) : null}

              {aiDraft.provider === "openai_compat" ? (
                <>
                  <div>
                    <div className="small" style={{ fontWeight: 800, marginBottom: 4 }}>OpenAI-compatible base URL</div>
                    <input value={aiDraft.openaiCompatBaseUrl} onChange={(e) => updateAiDraft({ openaiCompatBaseUrl: e.target.value })} placeholder="http://127.0.0.1:8000" />
                  </div>
                  <div>
                    <div className="small" style={{ fontWeight: 800, marginBottom: 4 }}>Model</div>
                    <input value={aiDraft.openaiCompatModel} onChange={(e) => updateAiDraft({ openaiCompatModel: e.target.value })} placeholder="qwen2.5-7b-instruct" />
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr auto auto", gap: 8, alignItems: "center" }}>
                    <div>
                      <div className="small" style={{ fontWeight: 800 }}>API key</div>
                      <div className="small" style={{ opacity: 0.8 }}>
                        Optional for local tools like LM Studio. Required for hosted providers that expect bearer auth.
                      </div>
                      <input
                        value={openaiCompatApiKey}
                        onChange={(e) => setOpenaiCompatApiKey(e.target.value)}
                        placeholder={secrets?.has_openai_compat_api_key ? "(set) paste to replace" : "paste API key if needed"}
                      />
                    </div>
                    <button disabled={saving || !openaiCompatApiKey} onClick={() => saveSecret("openai_compat_api_key", openaiCompatApiKey)}>Save</button>
                    <button className="secondary" disabled={saving || !secrets?.has_openai_compat_api_key} onClick={() => clearSecret("openai_compat_api_key")}>Clear</button>
                  </div>
                  <div className="small" style={{ opacity: 0.82 }}>
                    Use this for OpenAI-style endpoints such as hosted gateways, self-hosted vLLM/TGI adapters, or local tools that expose <code>/v1/chat/completions</code>.
                  </div>
                </>
              ) : null}

              {aiDraft.provider === "rule_based" ? (
                <div className="small" style={{ opacity: 0.82 }}>
                  Dependency-free fallback. No external AI service is required, but planning quality will be simpler and more deterministic.
                </div>
              ) : null}
            </>
          ) : (
            <>
              <div>
                <div className="small" style={{ fontWeight: 800, marginBottom: 4 }}>Remote AI service base URL</div>
                <input value={aiDraft.aiBaseUrl} onChange={(e) => updateAiDraft({ aiBaseUrl: e.target.value })} placeholder="http://127.0.0.1:7862" />
              </div>
              <div className="small" style={{ opacity: 0.82 }}>
                Use this when you want Studio to call a separate EDMG AI service over HTTP instead of running the local planner/provider path inside the Studio backend.
              </div>
            </>
          )}

          <div className="row" style={{ gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <button disabled={savingAi || !aiSettingsDirty || !window.edmg?.setAiSettings} onClick={saveAiSettings}>Save AI startup settings</button>
            {aiRestartRequired && window.edmg?.relaunch ? (
              <button className="secondary" disabled={savingAi} onClick={() => { void window.edmg?.relaunch?.(); }}>Restart now</button>
            ) : null}
            {aiNotice ? <div className="small" style={{ opacity: 0.84 }}>{aiNotice}</div> : null}
          </div>
        </div>
      </div>

      {cfg && <div className="card"><pre>{JSON.stringify(cfg, null, 2)}</pre></div>}

      {aiStatus && (
        <div className="card" style={{ marginTop: 14 }}>
          <div style={{ fontWeight: 800, marginBottom: 10 }}>Live Backend AI Status</div>
          <div className="small" style={{ marginBottom: 10 }}>
            This is the provider the backend is using right now. If it differs from the saved startup config above, restart Studio to apply your latest change.
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
