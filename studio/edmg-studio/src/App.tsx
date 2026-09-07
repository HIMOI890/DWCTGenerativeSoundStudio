import React, { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Sidebar from "./components/Sidebar";
import { StudioCommandPalette } from "./components/StudioCommandPalette";
import {
  BACKEND_URL_CHANGED_EVENT,
  apiGet,
  getBackendUrl,
  getBackendUrlAsync,
  isRequestAbortError,
  normalizeBackendUrl,
} from "./components/api";

import Dashboard from "./pages/Dashboard";
import Projects from "./pages/Projects";
import Setup from "./pages/Setup";
import { isStudioForgeEnabled } from "./features";
import {
  getPageLoadingDetails,
  getPageDocumentTitle,
  isPage,
  preloadLikelyNextPages,
  type Page,
} from "./pageRouting";

const Workspace = lazy(() => import("./pages/Workspace"));
const Timeline = lazy(() => import("./pages/Timeline"));
const Render = lazy(() => import("./pages/Render"));
const RenderQueue = lazy(() => import("./pages/RenderQueue"));
const Review = lazy(() => import("./pages/Review"));
const Outputs = lazy(() => import("./pages/Outputs"));
const Cloud = lazy(() => import("./pages/Cloud"));
const Settings = lazy(() => import("./pages/Settings"));
const Models = lazy(() => import("./pages/Models"));
const EdmgDirector = lazy(() => import("./pages/EdmgDirector"));
const AiPlannerLab = lazy(() => import("./pages/AiPlannerLab"));
const ReactiveLab = lazy(() => import("./pages/ReactiveLab"));
const StudioForge = lazy(() => import("./pages/StudioForge"));

function getInitialPage(): Page {
  if (typeof window === "undefined") return "dashboard";
  const raw = new URLSearchParams(window.location.search).get("page");
  return raw && isPage(raw) ? raw : "dashboard";
}

function getForcedPage(): Page | null {
  if (typeof window === "undefined") return null;
  const raw = new URLSearchParams(window.location.search).get("page");
  return raw && isPage(raw) ? raw : null;
}

function PageLoadingFallback({ page }: { page: Page }) {
  const loading = getPageLoadingDetails(page);
  return (
    <div className="card">
      <div style={{ fontWeight: 800, marginBottom: 8 }}>Loading studio screen</div>
      <div className="small">
        Preparing <b>{loading.label}</b>.
      </div>
      <div className="small" style={{ marginTop: 8, opacity: 0.84 }}>
        {loading.detail}
      </div>
    </div>
  );
}

export default function App() {
  const [page, setPage] = useState<Page>(getInitialPage);
  const [forcedPage] = useState<Page | null>(getForcedPage);
  const [backendUrl, setBackendUrl] = useState<string>("");
  const [config, setConfig] = useState<any>(null);
  const [backendConfigError, setBackendConfigError] = useState<string>("");
  const [setupChecked, setSetupChecked] = useState(false);
  const [commandPaletteOpen, setCommandPaletteOpen] = useState(false);
  const [skipLinkFocused, setSkipLinkFocused] = useState(false);
  const mainRef = useRef<HTMLElement | null>(null);
  const backendUrlRef = useRef("");
  const shouldFocusMainRef = useRef(false);
  const currentPageRef = useRef(page);
  backendUrlRef.current = normalizeBackendUrl(backendUrl);

  const focusMainContent = useCallback(() => {
    window.setTimeout(() => mainRef.current?.focus({ preventScroll: true }), 0);
  }, []);

  const navigateToPage = useCallback((nextPage: Page, historyMode: "push" | "replace" = "push") => {
    if (typeof window !== "undefined") {
      const url = new URL(window.location.href);
      const currentUrlPage = url.searchParams.get("page");
      url.searchParams.set("page", nextPage);
      if (historyMode === "replace") {
        window.history.replaceState({ edmgStudioPage: nextPage }, "", url);
      } else if (currentUrlPage !== nextPage) {
        window.history.pushState({ edmgStudioPage: nextPage }, "", url);
      }
    }
    shouldFocusMainRef.current = true;
    if (currentPageRef.current === nextPage) {
      shouldFocusMainRef.current = false;
      focusMainContent();
    } else {
      currentPageRef.current = nextPage;
      setPage(nextPage);
    }
    setCommandPaletteOpen(false);
  }, [focusMainContent]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const handleHistoryNavigation = () => {
      const nextPage = getInitialPage();
      shouldFocusMainRef.current = true;
      setCommandPaletteOpen(false);
      if (currentPageRef.current === nextPage) {
        shouldFocusMainRef.current = false;
        focusMainContent();
      } else {
        currentPageRef.current = nextPage;
        setPage(nextPage);
      }
    };
    window.addEventListener("popstate", handleHistoryNavigation);
    return () => window.removeEventListener("popstate", handleHistoryNavigation);
  }, [focusMainContent]);

  useEffect(() => {
    if (typeof document === "undefined") return;
    document.title = getPageDocumentTitle(page);
    if (!shouldFocusMainRef.current) return;
    shouldFocusMainRef.current = false;
    focusMainContent();
  }, [focusMainContent, page]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const handleCommandShortcut = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase() === "k") {
        event.preventDefault();
        setCommandPaletteOpen((current) => !current);
      }
    };
    window.addEventListener("keydown", handleCommandShortcut);
    return () => window.removeEventListener("keydown", handleCommandShortcut);
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const url = await getBackendUrlAsync();
        if (alive) setBackendUrl(url);
      } catch {
        if (alive) setBackendUrl(getBackendUrl());
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;

    const handleBackendUrlChanged = (event: Event) => {
      const detail = (event as CustomEvent<{ url?: string }>).detail;
      const nextUrl = normalizeBackendUrl(detail?.url || "");
      if (!nextUrl || nextUrl === backendUrlRef.current) return;
      backendUrlRef.current = nextUrl;
      setBackendUrl(nextUrl);
      setConfig(null);
      setBackendConfigError("");
      setSetupChecked(false);
    };

    window.addEventListener(BACKEND_URL_CHANGED_EVENT, handleBackendUrlChanged);
    return () => {
      window.removeEventListener(BACKEND_URL_CHANGED_EVENT, handleBackendUrlChanged);
    };
  }, []);

  useEffect(() => {
    if (!backendUrl) return;
    const controller = new AbortController();
    let retryTimer: number | null = null;
    let retryDelayMs = 750;

    const loadConfig = () => {
      void apiGet("/v1/config", { signal: controller.signal, timeoutMs: 10_000 })
        .then((nextConfig) => {
          if (controller.signal.aborted) return;
          setConfig(nextConfig);
          setBackendConfigError("");
        })
        .catch((error: any) => {
          if (controller.signal.aborted || isRequestAbortError(error)) return;
          setConfig(null);
          setBackendConfigError(String(error?.message ?? error));
          const delay = retryDelayMs;
          retryDelayMs = Math.min(retryDelayMs * 2, 5_000);
          retryTimer = window.setTimeout(loadConfig, delay);
        });
    };

    loadConfig();
    return () => {
      controller.abort();
      if (retryTimer != null) window.clearTimeout(retryTimer);
    };
  }, [backendUrl]);

  useEffect(() => {
    if (!backendUrl || setupChecked) return;
    apiGet("/v1/setup/status")
      .then((s) => {
        const aiConfig = s?.ai_config ?? {};
        const ollamaRequired = !!aiConfig?.ollama_required;
        const modelRequired = !!aiConfig?.model_required;
        const backendBundleOk = !!s?.backend_bundle?.ok;
        const ffmpegOk = !!s?.ffmpeg?.ok;
        const ollamaOk = !!s?.ollama?.ok;
        const modelOk = !modelRequired || !!s?.ollama?.model_present;
        const need = !(backendBundleOk && ffmpegOk && (!ollamaRequired || (ollamaOk && modelOk)));
        if (need && !forcedPage) navigateToPage("setup", "replace");
        setSetupChecked(true);
      })
      .catch(() => setSetupChecked(true));
  }, [backendUrl, forcedPage, navigateToPage, setupChecked]);

  useEffect(() => {
    if (typeof window === "undefined") return;

    let canceled = false;
    const runPreload = () => {
      if (canceled) return;
      preloadLikelyNextPages(page);
    };

    const requestIdle = window.requestIdleCallback;
    if (typeof requestIdle === "function") {
      const idleId = requestIdle(runPreload, { timeout: 1200 });
      return () => {
        canceled = true;
        window.cancelIdleCallback?.(idleId);
      };
    }

    const timeoutId = window.setTimeout(runPreload, 150);
    return () => {
      canceled = true;
      window.clearTimeout(timeoutId);
    };
  }, [page]);

  const commonProps = useMemo(() => ({ backendUrl, config }), [backendUrl, config]);

  const skipToMain = (
    <a
      href="#studio-main-content"
      onClick={(event) => {
        event.preventDefault();
        focusMainContent();
      }}
      onFocus={() => setSkipLinkFocused(true)}
      onBlur={() => setSkipLinkFocused(false)}
      style={{
        position: "absolute",
        zIndex: 10000,
        top: skipLinkFocused ? 8 : -10000,
        left: skipLinkFocused ? 8 : -10000,
        padding: "8px 12px",
        borderRadius: 8,
        background: "var(--panel, #151820)",
        color: "var(--text, #fff)",
      }}
    >
      Skip to main content
    </a>
  );

  if (!backendUrl) {
    return (
      <div className="app-shell">
        {skipToMain}
        <Sidebar page={page} onNavigate={navigateToPage} onOpenCommandPalette={() => setCommandPaletteOpen(true)} />
        <main id="studio-main-content" ref={mainRef} tabIndex={-1} className="main">
          <div className="card">
            <div style={{ fontWeight: 800, marginBottom: 8 }}>Connecting to Studio backend</div>
            <div className="small">Resolving the active backend target before loading workspace screens.</div>
          </div>
        </main>
        <StudioCommandPalette
          open={commandPaletteOpen}
          activePage={page}
          onClose={() => setCommandPaletteOpen(false)}
          onNavigate={navigateToPage}
        />
      </div>
    );
  }

  let content: React.ReactNode = null;
  if (page === "dashboard") content = <Dashboard {...commonProps} />;
  if (page === "projects") content = <Projects {...commonProps} />;
  if (page === "workspace") content = <Workspace {...commonProps} onNavigate={navigateToPage as any} />;
  if (page === "timeline") content = <Timeline {...commonProps} onNavigate={navigateToPage as any} />;
  if (page === "render") content = <Render {...commonProps} onNavigate={navigateToPage as any} />;
  if (page === "queue") content = <RenderQueue {...commonProps} onNavigate={navigateToPage as any} />;
  if (page === "review") content = <Review {...commonProps} onNavigate={navigateToPage as any} />;
  if (page === "outputs") content = <Outputs {...commonProps} onNavigate={navigateToPage as any} />;
  if (page === "cloud") content = <Cloud {...commonProps} />;
  if (page === "settings") content = <Settings {...commonProps} />;
  if (page === "setup") content = <Setup onNavigate={navigateToPage as any} />;
  if (page === "models") content = <Models {...commonProps} />;
  if (page === "directorLab")
    content = <EdmgDirector {...commonProps} onNavigate={navigateToPage as any} />;
  if (page === "plannerLab")
    content = <AiPlannerLab {...commonProps} onNavigate={navigateToPage as any} />;
  if (page === "reactiveLab")
    content = <ReactiveLab {...commonProps} onNavigate={navigateToPage as any} />;
  if (page === "studioForge" && isStudioForgeEnabled())
    content = <StudioForge {...commonProps} onNavigate={navigateToPage as any} />;
  if (page === "studioForge" && !isStudioForgeEnabled())
    content = <Dashboard {...commonProps} />;

  const mainClassName = page === "timeline" ? "main main--timeline" : "main";

  return (
    <div className="app-shell">
      {skipToMain}
      <Sidebar page={page} onNavigate={navigateToPage} onOpenCommandPalette={() => setCommandPaletteOpen(true)} />
      <main id="studio-main-content" ref={mainRef} tabIndex={-1} className={mainClassName}>
        {backendConfigError ? (
          <div className="card" style={{ marginBottom: 14, borderColor: "var(--warning, #b58900)" }}>
            <div style={{ fontWeight: 800, marginBottom: 8 }}>Backend connection needs attention</div>
            <div className="small" style={{ marginBottom: 8 }}>
              Studio resolved <b>{backendUrl}</b> but could not load `/v1/config` from it.
            </div>
            <div className="small" style={{ opacity: 0.84 }}>
              If you intended to attach the desktop GUI to an external backend, open Settings and review Desktop Backend mode, host, and port. Error: {backendConfigError}
            </div>
          </div>
        ) : null}
        <Suspense key={backendUrl} fallback={<PageLoadingFallback page={page} />}>
          {content}
        </Suspense>
      </main>
      <StudioCommandPalette
        open={commandPaletteOpen}
        activePage={page}
        onClose={() => setCommandPaletteOpen(false)}
        onNavigate={navigateToPage}
      />
    </div>
  );
}
