import React from "react";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import Review from "../pages/Review";
import { installEdmgBridge, installFetchMock, renderWithStudio } from "./testUtils";

describe("Review page", () => {
  it("loads variant review groups and applies approval", async () => {
    installEdmgBridge();
    installFetchMock({
      "/v1/projects": { projects: [{ id: "p1", name: "Demo" }] },
      "/v1/projects/p1": { project: { id: "p1", revision: 1, meta: {} } },
      "/v1/projects/p1/variant_review": {
        variant_review: {
          artifact_count: 1,
          compare_ready: false,
          plan_variant_count: 1,
          groups: [
            {
              variant_index: 0,
              label: "Warm",
              mood: "cinematic",
              artifacts: [
                {
                  path: "outputs/videos/internal_v00_demo.mp4",
                  name: "internal_v00_demo.mp4",
                  kind: "video",
                  variant_index: 0,
                  review_state: "unreviewed",
                  engine: "internal_video",
                  seed: 42,
                },
              ],
              review_summary: { unreviewed: 1, approved: 0, rejected: 0, cherry_picked: 0 },
            },
          ],
        },
      },
      "/v1/projects/p1/render/conductor/continuity*": {
        continuity: { warning_count: 0, blocking_count: 0, ok_to_render: true, warnings: [] },
      },
      "/v1/projects/p1/live_cues/publish/status": { publish: { running: false, sent_count: 0 } },
      "/v1/projects/p1/jobs": { jobs: [] },
      "POST /v1/projects/p1/variant_review/decision": {
        ok: true,
        review: { state: "approved" },
        variant_review: { artifact_count: 1, groups: [] },
      },
    });

    renderWithStudio(<Review backendUrl="http://127.0.0.1:7863" config={{}} />);

    expect(await screen.findByText("Variant Review")).toBeTruthy();
    expect(await screen.findByText("Warm")).toBeTruthy();

    fireEvent.click(screen.getByText(/unreviewed/i));
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(() => {
      expect(screen.getByText(/approved applied/i)).toBeTruthy();
    });
  });

  it("shows unified render job controls and pauses queued work", async () => {
    installEdmgBridge();
    let status = "queued";
    const fetchMock = installFetchMock({
      "/v1/projects": { projects: [{ id: "p1", name: "Demo" }] },
      "/v1/projects/p1/variant_review": {
        variant_review: { artifact_count: 0, compare_ready: false, plan_variant_count: 1, groups: [] },
      },
      "/v1/projects/p1/render/conductor/continuity*": {
        continuity: { warning_count: 0, blocking_count: 0, ok_to_render: true, warnings: [] },
      },
      "/v1/projects/p1/live_cues/publish/status": { publish: { running: false, sent_count: 0 } },
      "/v1/projects/p1/jobs": () => ({
        jobs: [{ id: "job-1", project_id: "p1", type: "internal_video", status, created_at: "2026-07-21" }],
      }),
      "POST /v1/projects/p1/jobs/job-1/pause": () => {
        status = "paused";
        return { ok: true, job: { id: "job-1", status } };
      },
    });

    renderWithStudio(<Review backendUrl="http://127.0.0.1:7863" config={{}} onNavigate={() => {}} />);

    expect(await screen.findByText("Project render jobs")).toBeTruthy();
    fireEvent.click(await screen.findByRole("button", { name: "Pause" }));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url, init]) =>
          String(url).includes("/v1/projects/p1/jobs/job-1/pause") &&
          String(init?.method || "GET").toUpperCase() === "POST"),
      ).toBe(true);
    });
  });
});
