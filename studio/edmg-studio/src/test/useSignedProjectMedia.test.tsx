import React from "react";
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../components/api";
import { useSignedProjectMedia } from "../hooks/useSignedProjectMedia";

function Harness({ path }: { path: string }) {
  const request = { purpose: "file" as const, path };
  const media = useSignedProjectMedia("project_01", [request], "https://studio.example");
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
    const issue = vi.spyOn(api, "issueProjectMediaUrls")
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
});
