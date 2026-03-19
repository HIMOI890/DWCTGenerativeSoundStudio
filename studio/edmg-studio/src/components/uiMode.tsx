import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

export type UiMode = "simple" | "advanced";

const KEY = "edmg_ui_mode";

function readMode(): UiMode {
  const v = (localStorage.getItem(KEY) || "simple").toLowerCase();
  return v === "advanced" ? "advanced" : "simple";
}

function writeMode(m: UiMode) {
  localStorage.setItem(KEY, m);
}

const Ctx = createContext<{ mode: UiMode; setMode: (m: UiMode) => void } | null>(null);

export function UiModeProvider(props: { children: React.ReactNode }) {
  const [mode, _setMode] = useState<UiMode>(() => readMode());

  const setMode = useCallback((m: UiMode) => {
    _setMode(m);
    writeMode(m);
  }, []);

  useEffect(() => {
    // Keep in sync if another window/tab updates.
    const onStorage = (e: StorageEvent) => {
      if (e.key === KEY) _setMode(readMode());
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const value = useMemo(() => ({ mode, setMode }), [mode, setMode]);
  return <Ctx.Provider value={value}>{props.children}</Ctx.Provider>;
}

export function useUiMode() {
  const v = useContext(Ctx);
  if (!v) throw new Error("UiModeProvider missing");
  return v;
}
