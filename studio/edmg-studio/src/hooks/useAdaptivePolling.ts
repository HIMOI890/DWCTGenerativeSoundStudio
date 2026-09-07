import { useCallback, useEffect, useRef, useState } from "react";

export type AdaptivePollingResult = boolean | {
  active?: boolean;
  continuePolling?: boolean;
} | void;

export type AdaptivePollingOptions = {
  poll: (signal: AbortSignal) => Promise<AdaptivePollingResult>;
  enabled?: boolean;
  initialDelayMs?: number;
  activeIntervalMs: number;
  idleIntervalMs: number;
  scopeKey?: unknown;
};

export type AdaptivePollingState = {
  isPolling: boolean;
  lastError: string;
  lastCompletedAt: number | null;
  pollNow: () => void;
};

function resultIsActive(result: AdaptivePollingResult): boolean {
  if (typeof result === "boolean") return result;
  return !!(result && result.active);
}

function resultShouldContinue(result: AdaptivePollingResult): boolean {
  return typeof result !== "object" || result == null || result.continuePolling !== false;
}

/**
 * Completion-scheduled polling.
 *
 * Unlike setInterval, the next request is not scheduled until the current
 * request settles. A manual refresh requested while a poll is running is
 * queued and runs immediately afterward, so the same poll can never overlap.
 */
export function useAdaptivePolling({
  poll,
  enabled = true,
  initialDelayMs = 0,
  activeIntervalMs,
  idleIntervalMs,
  scopeKey,
}: AdaptivePollingOptions): AdaptivePollingState {
  const pollRef = useRef(poll);
  const enabledRef = useRef(enabled);
  const activeIntervalRef = useRef(activeIntervalMs);
  const idleIntervalRef = useRef(idleIntervalMs);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const inFlightRef = useRef(false);
  const runAgainRef = useRef(false);
  const runRef = useRef<() => void>(() => {});
  const [isPolling, setIsPolling] = useState(false);
  const [lastError, setLastError] = useState("");
  const [lastCompletedAt, setLastCompletedAt] = useState<number | null>(null);

  pollRef.current = poll;
  activeIntervalRef.current = activeIntervalMs;
  idleIntervalRef.current = idleIntervalMs;

  const clearTimer = useCallback(() => {
    if (timerRef.current != null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const schedule = useCallback((delayMs: number) => {
    clearTimer();
    if (!enabledRef.current) return;
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      runRef.current();
    }, Math.max(0, delayMs));
  }, [clearTimer]);

  runRef.current = () => {
    if (!enabledRef.current) return;
    if (inFlightRef.current) {
      runAgainRef.current = true;
      return;
    }

    inFlightRef.current = true;
    const controller = new AbortController();
    controllerRef.current = controller;
    setIsPolling(true);

    void pollRef.current(controller.signal)
      .then((result) => {
        if (!enabledRef.current || controller.signal.aborted) return;
        setLastError("");
        setLastCompletedAt(Date.now());
        if (!resultShouldContinue(result)) return;
        const delay = resultIsActive(result)
          ? activeIntervalRef.current
          : idleIntervalRef.current;
        schedule(delay);
      })
      .catch((error: unknown) => {
        if (!enabledRef.current || controller.signal.aborted) return;
        setLastError(String((error as { message?: unknown })?.message ?? error));
        schedule(idleIntervalRef.current);
      })
      .finally(() => {
        if (controllerRef.current === controller) controllerRef.current = null;
        inFlightRef.current = false;
        if (!enabledRef.current) return;
        setIsPolling(false);
        if (runAgainRef.current) {
          runAgainRef.current = false;
          schedule(0);
        }
      });
  };

  const pollNow = useCallback(() => {
    clearTimer();
    if (!enabledRef.current) return;
    if (inFlightRef.current) {
      runAgainRef.current = true;
      return;
    }
    runRef.current();
  }, [clearTimer]);

  useEffect(() => {
    enabledRef.current = enabled;
    if (enabled) schedule(initialDelayMs);
    return () => {
      enabledRef.current = false;
      clearTimer();
      runAgainRef.current = false;
      controllerRef.current?.abort();
      controllerRef.current = null;
    };
  }, [clearTimer, enabled, initialDelayMs, schedule, scopeKey]);

  return { isPolling, lastError, lastCompletedAt, pollNow };
}
