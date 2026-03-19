import React, { useEffect, useState } from "react";
import { apiGet } from "../components/api";

export default function Dashboard({ backendUrl, config }: { backendUrl: string; config: any }) {
  const [health, setHealth] = useState<any>(null);
  const [edmg, setEdmg] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    apiGet("/health").then(setHealth).catch((e) => setErr(String(e)));
    apiGet("/v1/edmg/status").then(setEdmg).catch(() => {});
  }, [backendUrl]);

  return (
    <div>
      <h1>Dashboard</h1>
      <div className="grid2">
        <div className="card">
          <div style={{ fontWeight: 800, marginBottom: 8 }}>Backend</div>
          <div className="small">{backendUrl}</div>
          <hr />
          {err && <div style={{ color: "var(--danger)" }}>{err}</div>}
          {health && <pre>{JSON.stringify(health, null, 2)}</pre>}
        </div>

        <div className="card">
          <div style={{ fontWeight: 800, marginBottom: 8 }}>Config</div>
          {!config && <div className="small">Loading…</div>}
          {config && <pre>{JSON.stringify(config, null, 2)}</pre>}
        </div>
      </div>

      <div className="grid2" style={{ marginTop: 14 }}>
        <div className="card">
          <div style={{ fontWeight: 800, marginBottom: 8 }}>EDMG Core</div>
          {!edmg && <div className="small">Not detected (optional).</div>}
          {edmg && <pre>{JSON.stringify(edmg, null, 2)}</pre>}
          <div className="small" style={{ marginTop: 10 }}>
            Studio backend installs now target EDMG Core by default. If it is missing here, repair it from Setup.
          </div>
        </div>

        <div className="card">
          <div style={{ fontWeight: 800, marginBottom: 8 }}>Workflow</div>
          <ol style={{ margin: 0, paddingLeft: 18, color: "var(--text)" }}>
            <li>Create a project</li>
            <li>Upload audio</li>
            <li>Analyze/transcribe</li>
            <li>Generate plan variants</li>
            <li>Render scenes (ComfyUI)</li>
            <li>Assemble MP4 (FFmpeg)</li>
            <li>Export Deforum JSON (optional)</li>
          </ol>
        </div>
      </div>
    </div>
  );
}
