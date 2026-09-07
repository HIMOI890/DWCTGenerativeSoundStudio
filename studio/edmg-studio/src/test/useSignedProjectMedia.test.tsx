import React from "react";
import { act, render, renderHook, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../components/api";
import { usePreservedMediaSource, useSignedProjectMedia } from "../hooks/useSignedProjectMedia";

function Harness({ path, backend = "https://studio.example" }: { path: string; backend?: string }) {
  const request = { purpose: "file" as const, path };
  const media = useSignedProjectMedia("project_01", [request], backend);
  return <span data-testid="url">{media.urlFor(request)}</span>;
}

describe("useSignedProjectMedia", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("renews before expiry and clears stale URLs when the request scope changes", async () => {
    vi.spyOn(api, "issueProjectMediaUrls")
      .mockResolvedValueOnce({
        expires_at: (Date.now() + 60_000) / 1_000,
        urls: [{ purpose: "file", url: "https://studio.example/signed/one" }],
      })
      .mockResolvedValueOnce({
        expires_at: (Date.now() + 120_000) / 1_000,
        urls: [{ purpose: "file", url: "https://studio.example/signed/two" }],
      })
      .mockResolvedValueOnce({
        expires_at: (Date.now() + 120_000) / 1_000,
        urls: [{ purpose: "file", url: "https://studio.example/signed/other" }],
      });

    const view = render(<Harness path="outputs/one.mp4" />);
    await act(async () => Promise.resolve());
    expect(screen.getByTestId("url").textContent).toContain("/signed/one");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(screen.getByTestId("url").textContent).toContain("/signed/two");

    view.rerender(<Harness path="outputs/other.mp4" />);
    expect(screen.getByTestId("url").textContent).toBe("");
    await act(async () => Promise.resolve());
    expect(screen.getByTestId("url").textContent).toContain("/signed/other");
  });

  it("aborts issuance when unmounted", () => {
    let signal: AbortSignal | undefined;
    vi.spyOn(api, "issueProjectMediaUrls").mockImplementation((_project, _requests, options) => {
      signal = options.signal;
      return new Promise(() => {});
    });
    const view = render(<Harness path="outputs/one.mp4" />);
    expect(signal?.aborted).toBe(false);
    view.unmount();
    expect(signal?.aborted).toBe(true);
  });

  it("retains the last URL after renewal fails and retries", async () => {
    const issue = vi.spyOn(api, "issueProjectMediaUrls")
      .mockResolvedValueOnce({ expires_at: (Date.now() + 60_000) / 1000, urls: [{ purpose: "file", url: "one" }] })
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce({ expires_at: (Date.now() + 120_000) / 1000, urls: [{ purpose: "file", url: "two" }] });
    render(<Harness path="one.mp4" />);
    await act(async () => Promise.resolve());
    await act(async () => vi.advanceTimersByTimeAsync(30_000));
    expect(screen.getByTestId("url").textContent).toBe("one");
    await act(async () => vi.advanceTimersByTimeAsync(5_000));
    expect(screen.getByTestId("url").textContent).toBe("two");
    expect(issue).toHaveBeenCalledTimes(3);
  });

  it("discards a superseded backend response even when transport ignores abort", async () => {
    let finish: (value: api.SignedProjectMediaBatch) => void = () => {};
    const issue = vi.spyOn(api, "issueProjectMediaUrls")
      .mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }))
      .mockResolvedValueOnce({ expires_at: (Date.now() + 60_000) / 1000, urls: [{ purpose: "file", url: "new" }] });
    const view = render(<Harness path="one.mp4" />);
    view.rerender(<Harness path="one.mp4" backend="https://next.example" />);
    await act(async () => Promise.resolve());
    await act(async () => finish({ expires_at: (Date.now() + 60_000) / 1000, urls: [{ purpose: "file", url: "old" }] }));
    expect(screen.getByTestId("url").textContent).toBe("new");
    expect(issue.mock.calls[0][2].signal?.aborted).toBe(true);
    expect(issue.mock.calls[1][2].backendUrl).toBe("https://next.example");
  });

  it("restores active playback and position when the source renews", () => {
    const media = document.createElement("video");
    media.src = "https://studio.example/one";
    media.currentTime = 12;
    Object.defineProperty(media, "paused", { value: false });
    Object.defineProperty(media, "duration", { value: 60 });
    const play = vi.spyOn(media, "play").mockResolvedValue();
    vi.spyOn(media, "load").mockImplementation(() => { media.currentTime = 0; });
    const ref = { current: media };
    const hook = renderHook(() => usePreservedMediaSource(ref, "https://studio.example/two"));
    act(() => media.dispatchEvent(new Event("loadedmetadata")));
    expect(media.currentTime).toBe(12);
    expect(play).toHaveBeenCalledOnce();
    hook.unmount();
  });
});
