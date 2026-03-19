import React, { useState } from "react";
import { apiPost } from "../components/api";
import type { PageProps } from "../types/pageProps";

export default function Cloud(_props: PageProps) {
  const [bucket, setBucket] = useState("");
  const [bundleKey, setBundleKey] = useState("edmg_project_bundle.zip");
  const [lightningOut, setLightningOut] = useState("lightning_bundle");
  const [result, setResult] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);

  const awsTest = async () => {
    setErr(null); setResult(null);
    try { setResult(await apiPost("/v1/cloud/aws/test", { bucket: bucket || null })); }
    catch (e: any) { setErr(String(e)); }
  };

  const awsBundle = async () => {
    setErr(null); setResult(null);
    try { setResult(await apiPost("/v1/cloud/aws/bundle", { bucket: bucket || null, key: bundleKey || null })); }
    catch (e: any) { setErr(String(e)); }
  };

  const lightningBundle = async () => {
    setErr(null); setResult(null);
    try { setResult(await apiPost("/v1/cloud/lightning/bundle", { output_dir: lightningOut })); }
    catch (e: any) { setErr(String(e)); }
  };

  return (
    <div>
      <h1>Cloud</h1>

      <div className="grid2">
        <div className="card">
          <div style={{ fontWeight: 800, marginBottom: 10 }}>AWS</div>
          <div className="small">Optional dependency. Install backend with: pip install -e ".[aws]"</div>
          <div style={{ marginTop: 10 }}>
            <div className="small">S3 bucket</div>
            <input value={bucket} onChange={(e) => setBucket(e.target.value)} placeholder="my-bucket" />
          </div>
          <div style={{ marginTop: 10 }}>
            <div className="small">Bundle key</div>
            <input value={bundleKey} onChange={(e) => setBundleKey(e.target.value)} />
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <button onClick={awsTest}>Test credentials</button>
            <button className="secondary" onClick={awsBundle}>Bundle + (optional) upload</button>
          </div>
        </div>

        <div className="card">
          <div style={{ fontWeight: 800, marginBottom: 10 }}>Lightning.ai</div>
          <div className="small">Generates a runnable bundle folder (backend + startup script).</div>
          <div style={{ marginTop: 10 }}>
            <div className="small">Output dir</div>
            <input value={lightningOut} onChange={(e) => setLightningOut(e.target.value)} />
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <button onClick={lightningBundle}>Generate bundle</button>
          </div>
        </div>
      </div>

      {err && <div style={{ marginTop: 14, color: "var(--danger)" }}>{err}</div>}
      {result && (
        <div className="card" style={{ marginTop: 14 }}>
          <div style={{ fontWeight: 800, marginBottom: 10 }}>Result</div>
          <pre>{JSON.stringify(result, null, 2)}</pre>
        </div>
      )}
    </div>
  );
}
