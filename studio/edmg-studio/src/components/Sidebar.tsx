import React from "react";

export type Page =
  | "dashboard"
  | "projects"
  | "workspace"
  | "timeline"
  | "render"
  | "queue"
  | "outputs"
  | "cloud"
  | "settings"
  | "setup"
  | "models";

export default function Sidebar({
  page,
  onNavigate
}: {
  page: Page;
  onNavigate: (p: Page) => void;
}) {
  const items: Array<[Page, string]> = [
    ["dashboard", "Dashboard"],
    ["projects", "Projects"],
    ["workspace", "Workspace"],
    ["timeline", "Timeline"],
    ["render", "Render"],
    ["queue", "Render Queue"],
    ["outputs", "Outputs"],
    ["cloud", "Cloud"],
    ["models", "Models"],
    ["settings", "Settings"],
    ["setup", "Setup"],
  ];

  return (
    <div className="sidebar">
      <div style={{ fontSize: 18, fontWeight: 800 }}>EDMG Studio</div>
      <div className="small" style={{ marginTop: 6 }}>
        Desktop UI + local backend + ComfyUI + AI + EDMG Core
      </div>

      <div style={{ marginTop: 14, display: "flex", flexDirection: "column", gap: 8 }}>
        {items.map(([k, label]) => (
          <button
            key={k}
            onClick={() => onNavigate(k)}
            style={{
              textAlign: "left",
              background: page === k ? "#1b1d2b" : "#141623"
            }}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="small" style={{ marginTop: 14 }}>
        backend: 7863 • Ollama: 11434 • ComfyUI: 8188
      </div>
    </div>
  );
}
