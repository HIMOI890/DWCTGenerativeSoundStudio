import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  apiGet,
  apiFetch,
  buildProjectFileUrl,
  ensureBrowserBridge,
  getBackendUrl,
  getBackendUrlAsync,
  isProjectRevisionConflict,
  isRequestAbortError,
  issueProjectMediaUrls,
  setBackendAuthTokenForSession,
} from "../components/api";

const FRESH_TUNNEL = "https://equity-kilometers-periodically-floating.trycloudflare.com";
const DEAD_TUNNEL = "https://bridges-apartments-theoretical-value.trycloudflare.com";

describe("backend URL resolution", () => {
  beforeEach(() => {
    setBackendAuthTokenForSession("");
  });

  afterEach(() => {
    setBackendAuthTokenForSession("");
    vi.useRealTimers();
  });

  it("prefers the live Electron bridge URL over stale browser storage", async () => {
    window.localStorage.setItem("edmg.backendUrl", DEAD_TUNNEL);
    window.__EDMG_BACKEND_URL__ = DEAD_TUNNEL;
    window.edmg = {
      backendUrl: () => FRESH_TUNNEL,
      getBackendUrl: vi.fn(async () => `${FRESH_TUNNEL}/v1`),
    };

    await expect(getBackendUrlAsync()).resolves.toBe(FRESH_TUNNEL);
    expect(getBackendUrl()).toBe(FRESH_TUNNEL);
    expect(window.localStorage.getItem("edmg.backendUrl")).toBe(FRESH_TUNNEL);
  });

  it("uses the runtime URL in browser fallback mode without recursive bridge calls", () => {
    window.localStorage.setItem("edmg.backendUrl", DEAD_TUNNEL);
    window.__EDMG_BACKEND_URL__ = `${FRESH_TUNNEL}/health`;

    ensureBrowserBridge();

    expect(window.edmg?.backendUrl()).toBe(FRESH_TUNNEL);
    expect(getBackendUrl()).toBe(FRESH_TUNNEL);
    expect(window.localStorage.getItem("edmg.backendUrl")).toBe(FRESH_TUNNEL);
  });

  it("normalizes browser bridge external URLs before opening a new tab", async () => {
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
    ensureBrowserBridge();

    await expect(
      window.edmg?.openExternal?.(" https://example.com/docs/?feature=studio#top "),
    ).resolves.toBe("https://example.com/docs?feature=studio#top");
    await expect(window.edmg?.openExternal?.("javascript:alert(1)")).resolves.toBe("");
    expect(openSpy).toHaveBeenCalledTimes(1);
    expect(openSpy).toHaveBeenCalledWith(
      "https://example.com/docs?feature=studio#top",
      "_blank",
      "noopener,noreferrer",
    );
  });

  it("persists a resolved backend URL when localStorage is empty", () => {
    window.localStorage.removeItem("edmg.backendUrl");
    window.__EDMG_BACKEND_URL__ = `${FRESH_TUNNEL}/v1`;

    expect(getBackendUrl()).toBe(FRESH_TUNNEL);
    expect(window.localStorage.getItem("edmg.backendUrl")).toBe(FRESH_TUNNEL);
  });

  it("attaches the in-memory backend bearer token without writing it to storage", async () => {
    window.__EDMG_BACKEND_URL__ = FRESH_TUNNEL;
    setBackendAuthTokenForSession("secret-test-token");
    const fetchMock = vi.fn(async (_input: string | URL | Request, _init?: RequestInit) => ({
      ok: true,
      status: 200,
      json: async () => ({ ok: true }),
    } as Response));
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiGet("/v1/config")).resolves.toEqual({ ok: true });

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer secret-test-token");
    expect(window.localStorage.getItem("edmg.backendAuthToken")).toBeNull();
  });

  it("refuses to send backend credentials to another origin", async () => {
    window.__EDMG_BACKEND_URL__ = FRESH_TUNNEL;
    setBackendAuthTokenForSession("secret-test-token");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiFetch("https://attacker.example/v1/config")).rejects.toThrow(
      "different origin",
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("aborts a request at the configured timeout", async () => {
    vi.useFakeTimers();
    window.__EDMG_BACKEND_URL__ = FRESH_TUNNEL;
    const fetchMock = vi.fn((_input: string | URL | Request, init?: RequestInit) => (
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          reject(new DOMException("Aborted", "AbortError"));
        }, { once: true });
      })
    ));
    vi.stubGlobal("fetch", fetchMock);

    const request = apiGet("/v1/setup/tasks", { timeoutMs: 250 });
    const rejection = expect(request).rejects.toThrow("timed out after 250 ms");
    await vi.advanceTimersByTimeAsync(250);
    await rejection;
  });

  it("forwards a caller AbortSignal without waiting for a timeout", async () => {
    window.__EDMG_BACKEND_URL__ = FRESH_TUNNEL;
    const fetchMock = vi.fn((_input: string | URL | Request, init?: RequestInit) => (
      new Promise<Response>((_resolve, reject) => {
        if (init?.signal?.aborted) {
          reject(new DOMException("Aborted", "AbortError"));
          return;
        }
        init?.signal?.addEventListener("abort", () => {
          reject(new DOMException("Aborted", "AbortError"));
        }, { once: true });
      })
    ));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    const request = apiGet("/v1/setup/tasks", { signal: controller.signal });
    controller.abort();

    await expect(request).rejects.toMatchObject({ name: "AbortError" });
    expect((fetchMock.mock.calls[0]?.[1] as RequestInit).signal?.aborted).toBe(true);
  });

  it("recognizes Chromium's reasonless abort without hiding real failures", () => {
    expect(isRequestAbortError(new DOMException("signal is aborted without reason", "AbortError"))).toBe(true);
    expect(isRequestAbortError(new Error("signal is aborted without reason"))).toBe(true);
    expect(isRequestAbortError(new Error("Studio backend request timed out after 10000 ms"))).toBe(false);
    expect(isRequestAbortError(new Error("Failed to fetch"))).toBe(false);
  });

  it("builds encoded project file URLs from validated backend and project paths", () => {
    const url = new URL(buildProjectFileUrl(FRESH_TUNNEL, "project_01", "outputs/videos/hero clip.mp4"));
    expect(url.origin).toBe(FRESH_TUNNEL);
    expect(url.pathname).toBe("/v1/projects/project_01/file");
    expect(url.searchParams.get("path")).toBe("outputs/videos/hero clip.mp4");
  });

  it("rejects unsafe project identifiers and traversal paths", () => {
    expect(() => buildProjectFileUrl(FRESH_TUNNEL, "../outside", "outputs/video.mp4")).toThrow(
      "Invalid project identifier",
    );
    expect(() => buildProjectFileUrl(FRESH_TUNNEL, "project_01", "../outside.mp4")).toThrow(
      "Invalid project-relative file path",
    );
    expect(() => buildProjectFileUrl("javascript:alert(1)", "project_01", "outputs/video.mp4")).toThrow(
      "valid HTTP(S) backend URL",
    );
  });

  it("issues authenticated media URLs through the canonical batch endpoint", async () => {
    window.__EDMG_BACKEND_URL__ = FRESH_TUNNEL;
    setBackendAuthTokenForSession("signed-media-token");
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      expires_at: 2_000_000_000,
      urls: [
        { purpose: "audio", url: "/signed/audio?token=one" },
        { purpose: "preview", url: `${FRESH_TUNNEL}/signed/frame?token=two` },
      ],
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await issueProjectMediaUrls("project_01", [
      { purpose: "audio", path: "song.wav" },
      { purpose: "preview", query: { t: 1.5, w: 768, h: 432 } },
    ]);

    expect(result.urls.map((item) => item.url)).toEqual([
      `${FRESH_TUNNEL}/signed/audio?token=one`,
      `${FRESH_TUNNEL}/signed/frame?token=two`,
    ]);
    const [target, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(target).toBe(`${FRESH_TUNNEL}/v1/projects/project_01/media-urls`);
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer signed-media-token");
    expect(JSON.parse(String(init.body))).toEqual({
      requests: [
        { purpose: "audio", path: "song.wav" },
        { purpose: "preview", query: { t: 1.5, w: 768, h: 432 } },
      ],
    });
  });

  it("exposes structured revision conflicts", async () => {
    window.__EDMG_BACKEND_URL__ = FRESH_TUNNEL;
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      error: {
        code: "stale_project_revision",
        message: "Project changed in another window",
        expected_revision: 7,
        actual_revision: 8,
      },
    }), { status: 409, headers: { "Content-Type": "application/json" } })));

    const error = await apiGet("/v1/projects/project_01").catch((caught) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 409,
      code: "stale_project_revision",
      expectedRevision: 7,
      actualRevision: 8,
    });
    expect(isProjectRevisionConflict(error)).toBe(true);
  });
});
