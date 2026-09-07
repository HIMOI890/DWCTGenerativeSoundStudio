import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import {
  BACKEND_URL_CHANGED_EVENT,
  getBackendUrl,
  normalizeBackendUrl,
} from "./api";

export type HandoffType = "planner" | "reactive";

export type SessionHandoff = {
  type: HandoffType;
  projectId: string;
  at: number;
  summary: string;
};

type StudioSessionState = {
  backendScope: string;
  projectId: string;
  selectedVariant: number;
  lastHandoff: SessionHandoff | null;
};

type StudioSessionValue = StudioSessionState & {
  setProjectId: (projectId: string) => void;
  setSelectedVariant: (variantIndex: number) => void;
  noteHandoff: (handoff: SessionHandoff) => void;
  clearHandoff: () => void;
};

const STORAGE_KEY = "edmg_studio_session_v1";

const DEFAULT_STATE: StudioSessionState = {
  backendScope: "",
  projectId: "",
  selectedVariant: 0,
  lastHandoff: null,
};

function canUseStorage() {
  return typeof window !== "undefined" && typeof window.localStorage !== "undefined";
}

function sanitizeState(raw: unknown): StudioSessionState {
  const candidate = raw && typeof raw === "object" ? (raw as Partial<StudioSessionState>) : {};
  return {
    backendScope: normalizeBackendUrl(String(candidate.backendScope || "")),
    projectId: typeof candidate.projectId === "string" ? candidate.projectId : "",
    selectedVariant:
      typeof candidate.selectedVariant === "number" && Number.isFinite(candidate.selectedVariant)
        ? Math.max(0, Math.floor(candidate.selectedVariant))
        : 0,
    lastHandoff:
      candidate.lastHandoff &&
      typeof candidate.lastHandoff === "object" &&
      typeof (candidate.lastHandoff as SessionHandoff).projectId === "string" &&
      typeof (candidate.lastHandoff as SessionHandoff).summary === "string" &&
      typeof (candidate.lastHandoff as SessionHandoff).at === "number"
        ? (candidate.lastHandoff as SessionHandoff)
        : null,
  };
}

function readState(backendScope = normalizeBackendUrl(getBackendUrl())): StudioSessionState {
  if (!canUseStorage()) return { ...DEFAULT_STATE, backendScope };
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_STATE, backendScope };
    const state = sanitizeState(JSON.parse(raw));
    return state.backendScope === backendScope
      ? state
      : { ...DEFAULT_STATE, backendScope };
  } catch {
    return { ...DEFAULT_STATE, backendScope };
  }
}

function writeState(nextState: StudioSessionState) {
  if (!canUseStorage()) return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(nextState));
  } catch {
    // Ignore storage write failures; in-memory state still works.
  }
}

const StudioSessionContext = createContext<StudioSessionValue | null>(null);

export function StudioSessionProvider(props: { children: React.ReactNode }) {
  const [state, setState] = useState<StudioSessionState>(() => readState());

  const setProjectId = useCallback((projectId: string) => {
    setState((current) => ({
      ...current,
      projectId,
      selectedVariant: projectId === current.projectId ? current.selectedVariant : 0,
      lastHandoff:
        current.lastHandoff && current.lastHandoff.projectId === projectId ? current.lastHandoff : null,
    }));
  }, []);

  const setSelectedVariant = useCallback((variantIndex: number) => {
    setState((current) => ({
      ...current,
      selectedVariant: Math.max(0, Math.floor(Number(variantIndex) || 0)),
    }));
  }, []);

  const noteHandoff = useCallback((handoff: SessionHandoff) => {
    setState((current) => ({
      ...current,
      lastHandoff: handoff,
    }));
  }, []);

  const clearHandoff = useCallback(() => {
    setState((current) => ({
      ...current,
      lastHandoff: null,
    }));
  }, []);

  useEffect(() => {
    writeState(state);
  }, [state]);

  useEffect(() => {
    if (!canUseStorage()) return undefined;
    const onStorage = (event: StorageEvent) => {
      if (event.key === STORAGE_KEY) {
        setState((current) => readState(current.backendScope));
      }
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const onBackendChanged = (event: Event) => {
      const nextScope = normalizeBackendUrl(
        (event as CustomEvent<{ url?: string }>).detail?.url || "",
      );
      if (!nextScope) return;
      setState((current) => (
        current.backendScope === nextScope
          ? current
          : { ...DEFAULT_STATE, backendScope: nextScope }
      ));
    };
    window.addEventListener(BACKEND_URL_CHANGED_EVENT, onBackendChanged);
    return () => window.removeEventListener(BACKEND_URL_CHANGED_EVENT, onBackendChanged);
  }, []);

  const value = useMemo(
    () => ({
      ...state,
      setProjectId,
      setSelectedVariant,
      noteHandoff,
      clearHandoff,
    }),
    [state, setProjectId, setSelectedVariant, noteHandoff, clearHandoff],
  );

  return <StudioSessionContext.Provider value={value}>{props.children}</StudioSessionContext.Provider>;
}

export function useStudioSession() {
  const value = useContext(StudioSessionContext);
  if (!value) throw new Error("StudioSessionProvider missing");
  return value;
}
