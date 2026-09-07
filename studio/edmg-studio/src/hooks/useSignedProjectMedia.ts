import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type RefObject,
} from "react";
import {
  getBackendUrl,
  isRequestAbortError,
  issueProjectMediaUrls,
  normalizeBackendUrl,
  type SignedProjectMediaRequest,
} from "../components/api";

const RENEWAL_MARGIN_MS = 30_000;
const RENEWAL_RETRY_MS = 5_000;

function expiryTime(value: number | string): number {
  if (typeof value === "number") return value < 1_000_000_000_000 ? value * 1_000 : value;
  const numeric = Number(value);
  if (Number.isFinite(numeric)) return numeric < 1_000_000_000_000 ? numeric * 1_000 : numeric;
  return Date.parse(value);
}

export function mediaRequestKey(request: SignedProjectMediaRequest): string {
  const query = request.query
    ? Object.fromEntries(Object.entries(request.query).sort(([left], [right]) => left.localeCompare(right)))
    : undefined;
  return JSON.stringify({
    purpose: request.purpose,
    path: request.path || undefined,
    query,
  });
}

export type SignedProjectMediaState = {
  urls: ReadonlyMap<string, string>;
  expiresAt: number | null;
  loading: boolean;
  error: string;
  urlFor: (request: SignedProjectMediaRequest | null | undefined) => string;
  refresh: () => void;
};

export function useSignedProjectMedia(
  projectId: string,
  requests: SignedProjectMediaRequest[],
  backendUrl = getBackendUrl(),
): SignedProjectMediaState {
  const normalizedBackendUrl = normalizeBackendUrl(backendUrl);
  const requestSignature = JSON.stringify(requests);
  const stableRequests = useMemo(
    () => JSON.parse(requestSignature) as SignedProjectMediaRequest[],
    [requestSignature],
  );
  const [urls, setUrls] = useState<ReadonlyMap<string, string>>(() => new Map());
  const [expiresAt, setExpiresAt] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const refreshRef = useRef<() => void>(() => {});

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let controller: AbortController | null = null;

    setUrls(new Map());
    setExpiresAt(null);
    setError("");
    if (!normalizedBackendUrl || !projectId || stableRequests.length === 0) {
      setLoading(false);
      refreshRef.current = () => {};
      return () => {
        active = false;
      };
    }

    const schedule = (delay: number) => {
      if (timer != null) clearTimeout(timer);
      timer = setTimeout(run, Math.max(0, delay));
    };
    const run = () => {
      if (!active || controller) return;
      const requestController = new AbortController();
      controller = requestController;
      setLoading(true);
      void issueProjectMediaUrls(projectId, stableRequests, { signal: requestController.signal })
        .then((batch) => {
          if (!active || requestController.signal.aborted) return;
          const next = new Map<string, string>();
          stableRequests.forEach((request, index) => {
            next.set(mediaRequestKey(request), batch.urls[index]?.url || "");
          });
          const nextExpiry = expiryTime(batch.expires_at);
          if (!Number.isFinite(nextExpiry)) throw new Error("Studio returned an invalid signed media expiry.");
          setUrls(next);
          setExpiresAt(nextExpiry);
          setError("");
          schedule(Math.max(1_000, nextExpiry - Date.now() - RENEWAL_MARGIN_MS));
        })
        .catch((caught: unknown) => {
          if (!active || isRequestAbortError(caught)) return;
          setError(String((caught as { message?: unknown })?.message ?? caught));
          schedule(RENEWAL_RETRY_MS);
        })
        .finally(() => {
          if (controller === requestController) {
            controller = null;
            if (active) setLoading(false);
          }
        });
    };

    refreshRef.current = () => {
      if (timer != null) clearTimeout(timer);
      timer = null;
      if (controller) {
        controller.abort();
        controller = null;
      }
      run();
    };
    run();
    return () => {
      active = false;
      if (timer != null) clearTimeout(timer);
      controller?.abort();
      refreshRef.current = () => {};
    };
  }, [normalizedBackendUrl, projectId, requestSignature, stableRequests]);

  const urlFor = useCallback(
    (request: SignedProjectMediaRequest | null | undefined) => (
      request ? urls.get(mediaRequestKey(request)) || "" : ""
    ),
    [urls],
  );
  const refresh = useCallback(() => refreshRef.current(), []);

  return { urls, expiresAt, loading, error, urlFor, refresh };
}

/**
 * Updates a media element imperatively so signed-URL rotation does not reset
 * the user's position or turn active playback into a paused player.
 */
export function usePreservedMediaSource<T extends HTMLMediaElement>(
  mediaRef: RefObject<T | null>,
  sourceUrl: string,
): void {
  useEffect(() => {
    const media = mediaRef.current;
    if (!media || media.getAttribute("src") === sourceUrl) return;
    const currentTime = Number.isFinite(media.currentTime) ? media.currentTime : 0;
    const shouldResume = !!media.getAttribute("src") && !media.paused && !media.ended;

    const restorePlayback = () => {
      try {
        if (currentTime > 0) {
          const maximum = Number.isFinite(media.duration) ? Math.max(0, media.duration - 0.01) : currentTime;
          media.currentTime = Math.min(currentTime, maximum);
        }
      } catch {
        // Some media engines reject seeks until more data is available.
      }
      if (shouldResume) void media.play().catch(() => {});
    };

    if (!sourceUrl) {
      media.pause();
      media.removeAttribute("src");
      media.load();
      return;
    }
    media.addEventListener("loadedmetadata", restorePlayback, { once: true });
    media.src = sourceUrl;
    media.load();
    return () => media.removeEventListener("loadedmetadata", restorePlayback);
  }, [mediaRef, sourceUrl]);
}
