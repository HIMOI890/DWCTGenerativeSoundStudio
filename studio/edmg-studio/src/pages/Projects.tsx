import React, { useEffect, useState } from "react";
import { apiGet, apiPost } from "../components/api";
import type { PageProps } from "../types/pageProps";

export default function Projects(_props: PageProps) {
  const [projects, setProjects] = useState<any[]>([]);
  const [name, setName] = useState("My Project");
  const [err, setErr] = useState<string | null>(null);

  const refresh = () =>
    apiGet("/v1/projects")
      .then((d) => setProjects(d.projects || []))
      .catch((e) => setErr(String(e)));

  useEffect(() => {
    refresh();
  }, []);

  const create = async () => {
    setErr(null);
    try {
      await apiPost("/v1/projects", { name });
      await refresh();
    } catch (e: any) {
      setErr(String(e));
    }
  };

  return (
    <div>
      <h1>Projects</h1>
      <div className="card">
        <div className="row">
          <input value={name} onChange={(e) => setName(e.target.value)} />
          <button onClick={create}>Create</button>
          <button className="secondary" onClick={refresh}>
            Refresh
          </button>
        </div>
        {err && <div style={{ color: "var(--danger)", marginTop: 10 }}>{err}</div>}
      </div>

      <div className="grid2" style={{ marginTop: 14 }}>
        {projects.map((p) => (
          <div key={p.id} className="card">
            <div style={{ fontWeight: 800 }}>{p.name}</div>
            <div className="small">{p.id}</div>
            <div className="small">created: {p.created_at}</div>
          </div>
        ))}
      </div>

      <div className="small" style={{ marginTop: 14 }}>
        Use Workspace to select a project and run audio/plan/render/export.
      </div>
    </div>
  );
}
