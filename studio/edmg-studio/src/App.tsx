import React, { useEffect, useMemo, useState } from "react";
import Sidebar, { Page } from "./components/Sidebar";
import { apiGet } from "./components/api";

import Dashboard from "./pages/Dashboard";
import Projects from "./pages/Projects";
import Workspace from "./pages/Workspace";
import Timeline from "./pages/Timeline";
import Render from "./pages/Render";
import RenderQueue from "./pages/RenderQueue";
import Outputs from "./pages/Outputs";
import Cloud from "./pages/Cloud";
import Settings from "./pages/Settings";
import Setup from "./pages/Setup";
import Models from "./pages/Models";

export default function App() {
  const [page, setPage] = useState<Page>("dashboard");
  const [backendUrl, setBackendUrl] = useState<string>("");
  const [config, setConfig] = useState<any>(null);
  const [setupChecked, setSetupChecked] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const url = window.edmg?.getBackendUrl
          ? await window.edmg.getBackendUrl()
          : (window.edmg?.backendUrl?.() ?? window.__EDMG_BACKEND_URL__ ?? "http://127.0.0.1:7863");
        if (alive) setBackendUrl(url);
      } catch {
        const url = window.edmg?.backendUrl?.() ?? window.__EDMG_BACKEND_URL__ ?? "http://127.0.0.1:7863";
        if (alive) setBackendUrl(url);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!backendUrl) return;
    apiGet("/v1/config").then(setConfig).catch(() => {});
  }, [backendUrl]);

  useEffect(() => {
    if (!backendUrl || setupChecked) return;
    apiGet("/v1/setup/status")
      .then((s) => {
        const need = !(s?.ollama?.ok && s?.ollama?.model_present && s?.comfyui?.ok && s?.ffmpeg?.ok);
        if (need) setPage("setup" as any);
        setSetupChecked(true);
      })
      .catch(() => setSetupChecked(true));
  }, [backendUrl, setupChecked]);

  const commonProps = useMemo(() => ({ backendUrl, config }), [backendUrl, config]);

  let content: React.ReactNode = null;
  if (page === "dashboard") content = <Dashboard {...commonProps} />;
  if (page === "projects") content = <Projects {...commonProps} />;
  if (page === "workspace") content = <Workspace {...commonProps} onNavigate={setPage as any} />;
  if (page === "timeline") content = <Timeline {...commonProps} onNavigate={setPage as any} />;
  if (page === "render") content = <Render {...commonProps} onNavigate={setPage as any} />;
  if (page === "queue") content = <RenderQueue {...commonProps} onNavigate={setPage as any} />;
  if (page === "outputs") content = <Outputs {...commonProps} onNavigate={setPage as any} />;
  if (page === "cloud") content = <Cloud {...commonProps} />;
  if (page === "settings") content = <Settings {...commonProps} />;
  if (page === "setup") content = <Setup onNavigate={setPage as any} />;
  if (page === "models") content = <Models {...commonProps} />;

  return (
    <div style={{ display: "flex" }}>
      <Sidebar page={page} onNavigate={setPage} />
      <div className="main">{content}</div>
    </div>
  );
}
