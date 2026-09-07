import React, { useEffect, useMemo, useState } from "react";
import {
  apiGet,
  getBackendAuthTokenAsync,
  hasBackendAuthToken,
  saveBackendAuthToken,
} from "./api";

type SecurityStatus = {
  auth_mode?: string;
  auth_required?: boolean;
  auth_configured?: boolean;
  configured_host?: string | null;
  remote_without_auth?: boolean;
  cors_origins?: string[];
  cors_origin_regex_configured?: boolean;
  public_media_gets?: boolean;
  media_url_ttl_s?: number;
  preview_limits?: { standard?: { max_duration_s: number; max_fps: number }; diffusion?: { max_duration_s: number; max_fps: number } };
  transport?: string;
  transport_secure?: boolean;
  note?: string;
};

function describeTarget(rawUrl: string) {
  try {
    const parsed = new URL(rawUrl);
    const loopback = ["127.0.0.1", "localhost", "::1"].includes(parsed.hostname);
    return {
      loopback,
      secure: parsed.protocol === "https:",
      warning: !loopback && parsed.protocol !== "https:",
    };
  } catch {
    return { loopback: false, secure: false, warning: false };
  }
}

export default function BackendSecurityPanel({ backendUrl }: { backendUrl: string }) {
  const [status, setStatus] = useState<SecurityStatus | null>(null);
  const [tokenDraft, setTokenDraft] = useState("");
  const [tokenConfigured, setTokenConfigured] = useState(false);
  const [persisted, setPersisted] = useState(false);
  const [secureStorageAvailable, setSecureStorageAvailable] = useState(false);
  const [allowPrivateHttp, setAllowPrivateHttp] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const target = useMemo(() => describeTarget(backendUrl), [backendUrl]);

  async function refreshStatus() {
    try {
      const next = await apiGet("/v1/security/status");
      setStatus(next || null);
    } catch (error: any) {
      setNotice(String(error?.message || error));
    }
  }

  useEffect(() => {
    let active = true;
    void (async () => {
      const token = await getBackendAuthTokenAsync();
      if (!active) return;
      setTokenConfigured(!!token || hasBackendAuthToken());
      if (window.edmg?.getBackendAuthToken) {
        try {
          const saved = await window.edmg.getBackendAuthToken();
          if (!active) return;
          setPersisted(!!saved?.persisted);
          setSecureStorageAvailable(saved?.secureStorageAvailable !== false);
        } catch {
          setSecureStorageAvailable(false);
        }
      }
      await refreshStatus();
    })();
    return () => {
      active = false;
    };
  }, [backendUrl]);

  async function saveToken() {
    if (!tokenDraft.trim()) return;
    setBusy(true);
    setNotice("");
    try {
      const result = await saveBackendAuthToken(tokenDraft);
      setTokenDraft("");
      setTokenConfigured(result.configured);
      setPersisted(result.persisted);
      setSecureStorageAvailable(result.secureStorageAvailable);
      await apiGet("/v1/config");
      await refreshStatus();
      setNotice(result.note || "Backend access token saved and verified.");
    } catch (error: any) {
      setNotice(String(error?.message || error));
    } finally {
      setBusy(false);
    }
  }

  async function clearToken() {
    setBusy(true);
    setNotice("");
    try {
      const result = await saveBackendAuthToken("");
      setTokenDraft("");
      setTokenConfigured(false);
      setPersisted(false);
      setSecureStorageAvailable(result.secureStorageAvailable);
      await refreshStatus();
      setNotice(result.note || "Backend access token cleared.");
    } catch (error: any) {
      setNotice(String(error?.message || error));
    } finally {
      setBusy(false);
    }
  }

  async function testConnection() {
    setBusy(true);
    setNotice("");
    try {
      await refreshStatus();
      await apiGet("/v1/config");
      setNotice("Authenticated backend connection verified.");
    } catch (error: any) {
      setNotice(String(error?.message || error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ marginTop: 14, padding: 12, border: "1px solid var(--line)", borderRadius: 10 }}>
      <div style={{ fontWeight: 800, marginBottom: 6 }}>Backend Access Security</div>
      <div className="small" style={{ marginBottom: 10, opacity: 0.84 }}>
        Remote control and metadata routes use a bearer token. Electron stores it with OS-backed encryption;
        browser-only Studio keeps it in memory for the current tab and never writes it to localStorage.
      </div>

      {target.warning ? (
        <div className="small" style={{ marginBottom: 10, padding: 8, borderRadius: 8, background: "var(--warning-bg, #fff3cd)", color: "var(--warning-text, #856404)" }}>
          <div>This non-loopback backend uses plain HTTP. Keep it on a private network or put it behind HTTPS before entering provider keys or running production work.</div>
          <label style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8 }}>
            <input
              type="checkbox"
              checked={allowPrivateHttp}
              onChange={(event) => setAllowPrivateHttp(event.target.checked)}
            />
            Allow this private HTTP target for the current Studio session
          </label>
        </div>
      ) : null}

      <div className="small" style={{ display: "grid", gap: 4, marginBottom: 10 }}>
        <div>Target: <b>{backendUrl || "not resolved"}</b></div>
        <div>Transport: <b>{target.secure ? "HTTPS" : target.loopback ? "local HTTP" : "HTTP"}</b></div>
        <div>Backend authentication: <b>{status?.auth_required ? "required" : "optional for this target"}</b></div>
        <div>Client token: <b>{tokenConfigured ? (persisted ? "saved securely" : "active for this session") : "not configured"}</b></div>
        <div>Encrypted persistence: <b>{window.edmg?.setBackendAuthToken ? (secureStorageAvailable ? "available" : "session-only") : "browser session-only"}</b></div>
        <div>CORS origins: <b>{status?.cors_origins?.length ? status.cors_origins.join(", ") : "status unavailable"}</b></div>
        <div>Native media compatibility: <b>{status?.public_media_gets ? "read-only project media URLs enabled" : "authentication required"}</b></div>
        <div>Signed media lifetime: <b>{status?.media_url_ttl_s ? `${status.media_url_ttl_s / 60} minutes` : "status unavailable"}</b></div>
        {status?.preview_limits?.standard && <div>Standard previews: up to {status.preview_limits.standard.max_duration_s}s at {status.preview_limits.standard.max_fps} FPS</div>}
        {status?.preview_limits?.diffusion && <div>Diffusion previews: up to {status.preview_limits.diffusion.max_duration_s}s at {status.preview_limits.diffusion.max_fps} FPS</div>}
      </div>

      <div style={{ display: "grid", gap: 8 }}>
        <input
          aria-label="Backend access token"
          type="password"
          autoComplete="off"
          value={tokenDraft}
          onChange={(event) => setTokenDraft(event.target.value)}
          placeholder={tokenConfigured ? "Paste to replace the current token" : "Paste backend access token"}
        />
        <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <button disabled={busy || !tokenDraft.trim() || (target.warning && !allowPrivateHttp)} onClick={saveToken}>
            {busy ? "Working…" : window.edmg?.setBackendAuthToken ? "Save encrypted token" : "Use token for this tab"}
          </button>
          <button className="secondary" disabled={busy || !tokenConfigured} onClick={clearToken}>Clear token</button>
          <button className="secondary" disabled={busy || (target.warning && !allowPrivateHttp)} onClick={testConnection}>Test authenticated connection</button>
        </div>
        {status?.note ? <div className="small" style={{ opacity: 0.8 }}>{status.note}</div> : null}
        {notice ? <div className="small" style={{ opacity: 0.88 }}>{notice}</div> : null}
      </div>
    </div>
  );
}
