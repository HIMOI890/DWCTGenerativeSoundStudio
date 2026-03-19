import React, { useEffect, useMemo, useState } from "react";
import { apiGet, apiPost, apiUpload, getBackendUrl } from "../components/api";
import { useUiMode } from "../components/uiMode";
import type { PageProps } from "../types/pageProps";

function bytes(n: number) {
  if (!Number.isFinite(n)) return "-";
  const units = ["B", "KB", "MB", "GB"];
  let u = 0, v = n;
  while (v > 1024 && u < units.length - 1) { v /= 1024; u++; }
  return `${v.toFixed(u === 0 ? 0 : 2)} ${units[u]}`;
}

export default function Workspace({ onNavigate }: PageProps) {
  const { mode: uiMode } = useUiMode();
  const backendUrl = useMemo(() => getBackendUrl(), []);
  const [projects, setProjects] = useState<any[]>([]);
  const [projectId, setProjectId] = useState<string>("");
  const [project, setProject] = useState<any>(null);

  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [refFile, setRefFile] = useState<File | null>(null);
  const [assets, setAssets] = useState<any>(null);
  const [analysis, setAnalysis] = useState<any>(null);
  const [plan, setPlan] = useState<any>(null);
  const [selectedVariant, setSelectedVariant] = useState<number>(0);

  const [planMode, setPlanMode] = useState<"auto" | "ai" | "local">("auto");

  const [timelineZoom, setTimelineZoom] = useState<number>(60); // px per second

  const [info, setInfo] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);

  const refreshProjects = async () => {
    const d = await apiGet("/v1/projects");
    const ps = d.projects || [];
    setProjects(ps);
    if (!projectId && ps.length) setProjectId(ps[0].id);
  };

  const refreshProject = async (id: string) => {
    if (!id) return;
    const d = await apiGet(`/v1/projects/${id}`);
    setProject(d.project);
    setAnalysis(d.project?.meta?.analysis || null);
    setPlan(d.project?.meta?.last_plan || null);
    try {
      const a = await apiGet(`/v1/projects/${id}/assets`);
      setAssets(a.assets);
    } catch {
      setAssets(null);
    }
  };

  useEffect(() => { refreshProjects().catch(() => {}); }, []);

  useEffect(() => { if (projectId) refreshProject(projectId).catch(() => {}); }, [projectId]);
  // Workspace stays focused on project + timeline. Rendering lives in the Render page.

  const uploadAudio = async () => {
    if (!audioFile) return;
    setErr(null); setInfo(null);
    await apiUpload(`/v1/projects/${projectId}/assets/audio`, audioFile);
    await refreshProject(projectId);
  };

  const uploadRef = async () => {
    if (!refFile) return;
    setErr(null); setInfo(null);
    await apiUpload(`/v1/projects/${projectId}/assets/refs`, refFile);
    setRefFile(null);
    await refreshProject(projectId);
  };

  const runAnalysis = async () => {
    setErr(null); setInfo(null);
    try {
      const d = await apiPost(`/v1/projects/${projectId}/analyze_audio`, {});
      setAnalysis(d);
      await refreshProject(projectId);
    } catch (e: any) { setErr(String(e)); }
  };

  const generatePlan = async () => {
    setErr(null); setInfo(null);
    try {
      const d = await apiPost(`/v1/projects/${projectId}/plan?mode=${planMode}`, {
        title: project?.name || "Untitled",
        style_prefs: "cinematic, coherent subject, high detail, consistent style",
        num_variants: 3,
        max_scenes: 12
      });
      setPlan(d);
      setSelectedVariant(0);
      await refreshProject(projectId);
    } catch (e: any) { setErr(String(e)); }
  };

  const fileUrl = (pid: string, rel: string) => `${backendUrl}/v1/projects/${pid}/file?path=${encodeURIComponent(rel)}`;

  const variantScenes = plan?.variants?.[selectedVariant]?.scenes || [];
  const durationS = analysis?.features?.duration_s || analysis?.features?.duration || plan?.duration_s || 0;

  const Timeline = () => {
    if (!variantScenes.length) return <div className="small">No scenes. Generate a plan to see a timeline.</div>;
    const lastEnd = Number(variantScenes[variantScenes.length - 1]?.end_s ?? 60);
    const maxDur = Math.max(Number(durationS) || 0, lastEnd);
    const widthPx = Math.max(600, Math.round(maxDur * timelineZoom));
    const tickEvery = 5;
    const ticks: number[] = [];
    const maxT = Math.ceil(maxDur / tickEvery) * tickEvery;
    for (let t = 0; t <= maxT; t += tickEvery) ticks.push(t);
    return (
      <div>
        <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <div className="small">Zoom</div>
          <input style={{ width: 220 }} type="range" min={20} max={160} value={timelineZoom} onChange={(e) => setTimelineZoom(Number(e.target.value))} />
          <div className="small">{timelineZoom}px/s</div>
        </div>
        <div style={{ overflowX: "auto", border: "1px solid var(--border)", borderRadius: 12, marginTop: 10 }}>
          <div style={{ width: widthPx, padding: 12, position: "relative" }}>
            <div style={{ height: 20, position: "relative", marginBottom: 10 }}>
              {ticks.map((t) => (
                <div key={t} style={{ position: "absolute", left: t * timelineZoom, top: 0 }}>
                  <div style={{ height: 10, width: 1, background: "var(--border)" }} />
                  <div className="small" style={{ transform: "translateX(-6px)", marginTop: 2 }}>{t}s</div>
                </div>
              ))}
            </div>
            <div style={{ position: "relative", height: 120 }}>
              {variantScenes.map((sc: any, i: number) => {
                const s = Number(sc.start_s ?? (i * 5));
                const e = Number(sc.end_s ?? (s + 5));
                const left = Math.max(0, s * timelineZoom);
                const w = Math.max(10, (e - s) * timelineZoom);
                return (
                  <div
                    key={i}
                    title={sc.prompt}
                    style={{
                      position: "absolute",
                      left,
                      top: 20 + (i % 4) * 24,
                      width: w,
                      height: 20,
                      borderRadius: 10,
                      border: "1px solid var(--border)",
                      background: "rgba(255,255,255,0.06)",
                      padding: "0 8px",
                      display: "flex",
                      alignItems: "center",
                      fontSize: 12,
                      overflow: "hidden",
                      whiteSpace: "nowrap",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {i + 1}. {String(sc.name || "Scene")}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
        <div style={{ marginTop: 10 }}>
          <div style={{ fontWeight: 800, marginBottom: 8 }}>Scene list</div>
          {variantScenes.map((sc: any, i: number) => (
            <div key={i} className="card" style={{ marginBottom: 8 }}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <div style={{ fontWeight: 700 }}>{i + 1}. {sc.name || "Scene"}</div>
                <div className="small">{Number(sc.start_s ?? i * 5).toFixed(2)}s → {Number(sc.end_s ?? (i * 5 + 5)).toFixed(2)}s</div>
              </div>
              <div className="small" style={{ marginTop: 6 }}>{sc.prompt}</div>
            </div>
          ))}
        </div>
      </div>
    );
  };

  return (
    <div>
      <h1>Workspace</h1>

      <div className="grid2">
        <div className="card">
          <div style={{ fontWeight: 800, marginBottom: 10 }}>Project</div>
          {projects.length ? (
            <select value={projectId} onChange={(e) => setProjectId(e.target.value)}>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          ) : (
            <div className="small">No projects yet. Create one in Projects tab.</div>
          )}

          <hr />
          <div style={{ fontWeight: 800, marginBottom: 10 }}>Audio</div>
          <input type="file" accept="audio/*" onChange={(e) => setAudioFile(e.target.files?.[0] || null)} />
          <div className="row" style={{ marginTop: 10 }}>
            <button onClick={uploadAudio} disabled={!audioFile || !projectId}>Upload</button>
            <button className="secondary" onClick={runAnalysis} disabled={!projectId}>Analyze + Transcribe</button>
          </div>
          {project?.meta?.audio && (
            <div className="small" style={{ marginTop: 10 }}>
              uploaded: {project.meta.audio.filename} ({bytes(project.meta.audio.size_bytes)})
            </div>
          )}

          <hr />
          <div style={{ fontWeight: 800, marginBottom: 10 }}>Project Assets</div>
          <div className="small">Reference images (style/character anchors)</div>
          <input type="file" accept="image/*" onChange={(e) => setRefFile(e.target.files?.[0] || null)} />
          <div className="row" style={{ marginTop: 10 }}>
            <button onClick={uploadRef} disabled={!refFile || !projectId}>Upload ref</button>
            <button className="secondary" onClick={() => projectId && refreshProject(projectId)} disabled={!projectId}>Refresh assets</button>
          </div>
          <div className="grid3" style={{ marginTop: 10 }}>
            {(assets?.refs || []).map((r: any) => (
              <a key={r.path} href={fileUrl(projectId, r.path)} target="_blank" rel="noreferrer">
                <img src={fileUrl(projectId, r.path)} style={{ width: "100%", borderRadius: 12, border: "1px solid var(--border)" }} />
              </a>
            ))}
            {!assets?.refs?.length && <div className="small">No refs yet.</div>}
          </div>

          <hr />
          <div style={{ fontWeight: 800, marginBottom: 10 }}>AI Plan</div>
          <div className="row" style={{ gap: 10, flexWrap: "wrap", alignItems: "center" }}>
            <label className="small row" style={{ gap: 6, alignItems: "center" }}>
              Plan mode
              <select value={planMode} onChange={(e) => setPlanMode(e.target.value as any)}>
                <option value="auto">Auto</option>
                <option value="ai">AI-only</option>
                <option value="local">Local-only</option>
              </select>
            </label>
            <button onClick={generatePlan} disabled={!projectId}>Generate Plan Variants</button>
          </div>

          {plan?.variants?.length ? (<>
            <div style={{ marginTop: 12 }}>
              <div className="small">Select variant</div>
              <select value={selectedVariant} onChange={(e) => setSelectedVariant(Number(e.target.value))}>
                {plan.variants.map((v: any, idx: number) => (
                  <option key={idx} value={idx}>{idx + 1}. {v.name}</option>
                ))}
              </select>
            </div>
            <div className="row" style={{ gap: 10, marginTop: 10, flexWrap: "wrap" }}>
              <button
                onClick={async () => {
                  setErr(null);
                  try {
                    await apiPost(`/v1/projects/${projectId}/timeline/apply_plan`, { variant_index: selectedVariant, overwrite: false });
                    await refreshProject(projectId);
                  } catch (e: any) { setErr(String(e)); }
                }}
                disabled={!projectId || !plan?.variants?.length}
              >
                Apply variant to timeline
              </button>
              <button
                className="secondary"
                onClick={async () => {
                  setErr(null);
                  try {
                    await apiPost(`/v1/projects/${projectId}/timeline/apply_plan`, { variant_index: selectedVariant, overwrite: true });
                    await refreshProject(projectId);
                  } catch (e: any) { setErr(String(e)); }
                }}
                disabled={!projectId || !plan?.variants?.length}
              >
                Apply (overwrite)
              </button>
            </div>
          </>) : (
            <div className="small" style={{ marginTop: 10 }}>No plan generated yet.</div>
          )}

          <hr />
          <div style={{ fontWeight: 800, marginBottom: 10 }}>Next</div>
          <div className="small" style={{ marginBottom: 10 }}>
            Workspace is for planning. Rendering, queue control, and exports live in the Render page.
          </div>
          <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>
            <button onClick={() => onNavigate?.("render")} disabled={!plan?.variants?.length}>Go to Render</button>
            <button className="secondary" onClick={() => onNavigate?.("outputs")}>Outputs</button>
            <button className="secondary" onClick={() => onNavigate?.("queue")}>Render Queue</button>
          </div>

          {err && <div style={{ marginTop: 12, color: "var(--danger)" }}>{err}</div>}
        </div>

        <div className="card">
          <div style={{ fontWeight: 800, marginBottom: 10 }}>Timeline</div>
          <Timeline />

          <details style={{ marginTop: 12 }} open={uiMode === "advanced"}>
            <summary style={{ cursor: "pointer", fontWeight: 800 }}>Inspect</summary>
            <div style={{ marginTop: 10 }}>
              <div style={{ fontWeight: 800, marginBottom: 10 }}>Selected variant (raw)</div>
              {plan?.variants?.length ? (
                <pre>{JSON.stringify(plan.variants[selectedVariant], null, 2)}</pre>
              ) : (
                <div className="small">No plan.</div>
              )}

              <hr />
              <div style={{ fontWeight: 800, marginBottom: 10 }}>Analysis</div>
              {!analysis && <div className="small">No analysis yet.</div>}
              {analysis && <pre>{JSON.stringify(analysis, null, 2)}</pre>}

              <hr />
              <div style={{ fontWeight: 800, marginBottom: 10 }}>Last action result</div>
              {!info && <div className="small">No recent action.</div>}
              {info && <pre>{JSON.stringify(info, null, 2)}</pre>}
            </div>
          </details>
        </div>
      </div>

      <div className="small" style={{ marginTop: 14 }}>
        Use Outputs to view images/videos. The backend runs an always-on worker by default; Render Queue lets you inspect jobs/logs and retry/cancel.
      </div>
    </div>
  );
}
