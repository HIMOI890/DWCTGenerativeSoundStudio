import React from "react";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Timeline from "../pages/Timeline";
import { installEdmgBridge, installFetchMock, renderWithStudio } from "./testUtils";

describe("Timeline page", () => {
  it("updates the transport button when audio playback events fire", async () => {
    installEdmgBridge();
    installFetchMock({
      "/v1/projects": { projects: [{ id: "p1", name: "Smoothness Test" }] },
      "POST /v1/projects/p1/media-urls": (_path, init) => ({
        expires_at: Math.floor(Date.now() / 1000) + 900,
        urls: JSON.parse(String(init?.body)).requests.map((request: { purpose: string }) => ({
          purpose: request.purpose, url: `/v1/projects/p1/${request.purpose === "audio" ? "audio" : "preview/frame"}?edmg_sig=test`,
        })),
      }),
      "/v1/projects/p1": {
        project: {
          id: "p1",
          name: "Smoothness Test",
          meta: {
            audio: { filename: "track.wav", duration_s: 8 },
            analysis: { features: { duration_s: 8, bpm: 120 } },
            last_plan: {
              variants: [
                {
                  name: "Variant 1",
                  scenes: [
                    { id: "scene_0", start_s: 0, end_s: 8, prompt: "A continuous guitar performance with smooth motion." },
                  ],
                },
              ],
            },
          },
        },
      },
    });

    renderWithStudio(<Timeline backendUrl="http://127.0.0.1:7863" config={{}} />);

    expect(await screen.findByText("Timeline")).toBeTruthy();
    expect(await screen.findByRole("button", { name: "Play" })).toBeTruthy();
    expect(await screen.findByRole("button", { name: "Fit all" })).toBeTruthy();
    const selectTool = screen.getByRole("button", { name: "Select" });
    const bladeTool = screen.getByRole("button", { name: "Blade" });
    expect(selectTool.getAttribute("aria-pressed")).toBe("true");
    expect(bladeTool.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(bladeTool);
    expect(selectTool.getAttribute("aria-pressed")).toBe("false");
    expect(bladeTool.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(selectTool);

    await waitFor(() => expect(document.querySelector("audio")).toBeTruthy());
    const audio = document.querySelector("audio");
    expect(audio).toBeTruthy();
    fireEvent.play(audio as HTMLAudioElement);
    expect(await screen.findByRole("button", { name: "Pause" })).toBeTruthy();

    fireEvent.pause(audio as HTMLAudioElement);
    expect(await screen.findByRole("button", { name: "Play" })).toBeTruthy();
  });

  it("exposes sync-to-renderer and delete editing actions", async () => {
    installEdmgBridge();
    const onNavigate = vi.fn();
    installFetchMock({
      "/v1/projects": { projects: [{ id: "p1", name: "Editing Test" }] },
      "/v1/projects/p1": {
        project: {
          id: "p1",
          name: "Editing Test",
          meta: {
            audio: { filename: "track.wav", duration_s: 8 },
            analysis: { features: { duration_s: 8, bpm: 120 } },
            last_plan: {
              variants: [
                {
                  name: "Variant 1",
                  scenes: [{ id: "scene_0", start_s: 0, end_s: 8, prompt: "A calm cinematic scene." }],
                },
              ],
            },
          },
        },
      },
      "POST /v1/projects/p1/timeline": (_path: string, init?: RequestInit) => {
        const body = init?.body ? JSON.parse(String(init.body)) : {};
        return { ok: true, timeline: body.timeline || {} };
      },
    });

    renderWithStudio(
      <Timeline backendUrl="http://127.0.0.1:7863" config={{}} onNavigate={onNavigate} />,
    );

    // New editing affordances are present (Sync to renderer appears in both the
    // toolbar and the handoffs dock panel).
    const syncButtons = await screen.findAllByRole("button", { name: "Sync to renderer" });
    expect(syncButtons.length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: "Delete" }).length).toBeGreaterThan(0);

    // Sync to renderer saves the timeline then navigates to the Render page.
    fireEvent.click(syncButtons[0]);
    await waitFor(() => expect(onNavigate).toHaveBeenCalledWith("render"));

    fireEvent.click(screen.getByRole("tab", { name: /Media/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Render Queue" }));
    expect(onNavigate).toHaveBeenLastCalledWith("queue");
  });

  it("provides DAW-style grid jumps and loop locator controls", async () => {
    installEdmgBridge();
    installFetchMock({
      "/v1/projects": { projects: [{ id: "p1", name: "Locator Test" }] },
      "/v1/projects/p1": {
        project: {
          id: "p1",
          name: "Locator Test",
          meta: {
            audio: { filename: "track.wav", duration_s: 8 },
            analysis: { features: { duration_s: 8, bpm: 120, beat_times_s: [0, 0.5, 1, 1.5, 2, 2.5, 3] } },
            last_plan: {
              variants: [
                {
                  name: "Variant 1",
                  scenes: [{ id: "scene_0", start_s: 0, end_s: 8, prompt: "A calm cinematic scene." }],
                },
              ],
            },
          },
        },
      },
    });

    renderWithStudio(<Timeline backendUrl="http://127.0.0.1:7863" config={{}} />);

    const playhead = await screen.findByLabelText("Playhead time") as HTMLInputElement;
    const loopIn = await screen.findByLabelText("Loop in") as HTMLInputElement;
    const loopOut = await screen.findByLabelText("Loop out") as HTMLInputElement;

    fireEvent.click(screen.getByRole("button", { name: "Next grid" }));
    expect(playhead.value).toBe("0.5");

    fireEvent.change(screen.getByLabelText("Quantize grid"), { target: { value: "0.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Go to start" }));
    fireEvent.click(screen.getByRole("button", { name: "Next grid" }));
    expect(playhead.value).toBe("0.25");

    fireEvent.click(screen.getByRole("button", { name: "Disable snap" }));
    expect(screen.getByRole("button", { name: "Enable snap" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Show time ruler" }));
    expect(screen.getByRole("button", { name: "Show bars and beats ruler" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Set in" }));
    expect(loopIn.value).toBe("0.25");

    fireEvent.change(playhead, { target: { value: "2.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Set out" }));
    expect(loopOut.value).toBe("2.5");

    fireEvent.click(screen.getByRole("button", { name: "Enable loop" }));
    expect(screen.getByRole("button", { name: "Disable loop" })).toBeTruthy();

    fireEvent.pointerDown(await screen.findByTitle(/A calm cinematic scene/));
    fireEvent.click(screen.getByRole("button", { name: "Use selection" }));
    expect(loopIn.value).toBe("0");
    expect(loopOut.value).toBe("8");
  });

  it("collapses reference and automation lane groups without hiding edit tracks", async () => {
    installEdmgBridge();
    installFetchMock({
      "/v1/projects": { projects: [{ id: "p1", name: "Lane Visibility Test" }] },
      "/v1/projects/p1": {
        project: {
          id: "p1",
          name: "Lane Visibility Test",
          meta: {
            audio: { filename: "track.wav", duration_s: 8 },
            analysis: { features: { duration_s: 8, bpm: 120 } },
            last_plan: {
              variants: [
                {
                  name: "Variant 1",
                  scenes: [{ id: "scene_0", start_s: 0, end_s: 8, prompt: "Always visible edit clip." }],
                },
              ],
            },
            timeline: {
              duration_s: 8,
              tracks: [
                {
                  id: "track_prompt",
                  name: "Prompts",
                  type: "prompt",
                  clips: [{ id: "prompt_0", start_s: 0, end_s: 8, data: { prompt: "Always visible edit clip." } }],
                },
              ],
              layers: [{ type: "text", text: "Overlay", start_s: 0, end_s: 4 }],
              camera: { keyframes: [{ t: 2, zoom: 1 }] },
            },
          },
        },
      },
    });

    renderWithStudio(<Timeline backendUrl="http://127.0.0.1:7863" config={{}} />);

    expect(await screen.findByLabelText("Audio master reference lane header")).toBeTruthy();
    expect(screen.getByLabelText("Audio master reference lane")).toBeTruthy();
    expect(screen.getByLabelText("Overlays automation lane header")).toBeTruthy();
    expect(screen.getByLabelText("Overlays automation lane")).toBeTruthy();
    expect(screen.getByTitle("Always visible edit clip.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Hide reference lanes" }));
    expect(screen.queryByLabelText("Audio master reference lane header")).toBeNull();
    expect(screen.queryByLabelText("Audio master reference lane")).toBeNull();
    expect(screen.queryByLabelText("Music map reference lane header")).toBeNull();
    expect(screen.queryByLabelText("Music map reference lane")).toBeNull();
    expect(screen.getByRole("button", { name: "Show reference lanes" }).getAttribute("aria-pressed")).toBe("false");
    expect(screen.getByTitle("Always visible edit clip.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Hide automation lanes" }));
    expect(screen.queryByLabelText("Overlays automation lane header")).toBeNull();
    expect(screen.queryByLabelText("Overlays automation lane")).toBeNull();
    expect(screen.queryByLabelText("Camera automation lane header")).toBeNull();
    expect(screen.queryByLabelText("Camera automation lane")).toBeNull();
    expect(screen.getByRole("button", { name: "Show automation lanes" }).getAttribute("aria-pressed")).toBe("false");
    expect(screen.getByTitle("Always visible edit clip.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Show reference lanes" }));
    fireEvent.click(screen.getByRole("button", { name: "Show automation lanes" }));
    expect(screen.getByLabelText("Audio master reference lane")).toBeTruthy();
    expect(screen.getByLabelText("Camera automation lane")).toBeTruthy();
  });

  it("snaps clip moves, exposes timing fields, and honors track edit locks", async () => {
    installEdmgBridge();
    installFetchMock({
      "/v1/projects": { projects: [{ id: "p1", name: "DAW Edit Test" }] },
      "/v1/projects/p1": {
        project: {
          id: "p1",
          name: "DAW Edit Test",
          meta: {
            audio: { filename: "track.wav", duration_s: 8 },
            analysis: {
              features: { duration_s: 8, bpm: 120, beat_times_s: [0, 0.5, 1, 1.5, 2, 2.5, 3] },
            },
            last_plan: {
              variants: [
                {
                  name: "Variant 1",
                  scenes: [{ id: "scene_0", start_s: 0, end_s: 2, prompt: "Move me on the beat." }],
                },
              ],
            },
          },
        },
      },
    });

    renderWithStudio(<Timeline backendUrl="http://127.0.0.1:7863" config={{}} />);

    const clip = await screen.findByTitle("Move me on the beat.");
    fireEvent.change(screen.getByLabelText("Timeline zoom"), { target: { value: "100" } });
    fireEvent.pointerDown(clip, { clientX: 0, pointerId: 1 });

    const arrangement = document.querySelector(".timeline-arrangementCard");
    expect(arrangement).toBeTruthy();
    fireEvent.pointerMove(arrangement as Element, { clientX: 60, pointerId: 1 });
    fireEvent.pointerUp(arrangement as Element, { pointerId: 1 });
    fireEvent.click(screen.getByRole("tab", { name: /Inspector/ }));

    const snappedStart = Number((await screen.findByLabelText("Clip start") as HTMLInputElement).value);
    const snappedEnd = Number((screen.getByLabelText("Clip end") as HTMLInputElement).value);
    expect(Number.isInteger(snappedStart * 2)).toBe(true);
    expect(snappedEnd - snappedStart).toBe(2);
    expect((screen.getByLabelText("Clip length") as HTMLInputElement).value).toBe("2");

    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(Number((screen.getByLabelText("Clip start") as HTMLInputElement).value)).toBe(0);
    fireEvent.click(screen.getByRole("button", { name: "Redo" }));
    expect(Number((screen.getByLabelText("Clip start") as HTMLInputElement).value)).toBe(snappedStart);

    fireEvent.click(screen.getByRole("button", { name: "Lock Prompts track" }));
    expect(screen.getByRole("button", { name: "Unlock Prompts track" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Clip length"), { target: { value: "1" } });
    expect((screen.getByLabelText("Clip length") as HTMLInputElement).value).toBe("2");

    fireEvent.click(screen.getByRole("button", { name: "Unlock Prompts track" }));
    fireEvent.change(screen.getByLabelText("Clip length"), { target: { value: "1" } });
    expect(Number((screen.getByLabelText("Clip end") as HTMLInputElement).value)).toBe(snappedStart + 1);
  });

  it("ripples downstream clips after quantize and inspector timing edits", async () => {
    installEdmgBridge();
    let savedTimeline: any = null;
    installFetchMock({
      "/v1/projects": { projects: [{ id: "p1", name: "Ripple Timing Test" }] },
      "/v1/projects/p1": {
        project: {
          id: "p1",
          name: "Ripple Timing Test",
          meta: {
            audio: { duration_s: 8 },
            analysis: { features: { duration_s: 8, bpm: 120, beat_times_s: [0, 0.5, 1, 1.5, 2, 2.5] } },
            timeline: {
              duration_s: 8,
              tracks: [
                {
                  id: "prompts",
                  name: "Prompts",
                  type: "prompt",
                  clips: [
                    { id: "first", start_s: 0.1, end_s: 1.1, data: { prompt: "First cue" } },
                    { id: "second", start_s: 1.1, end_s: 2.1, data: { prompt: "Second cue" } },
                  ],
                },
              ],
            },
          },
        },
      },
      "POST /v1/projects/p1/timeline": (_path: string, init?: RequestInit) => {
        const body = init?.body ? JSON.parse(String(init.body)) : {};
        savedTimeline = body.timeline;
        return { ok: true, timeline: body.timeline || {} };
      },
    });

    renderWithStudio(<Timeline backendUrl="http://127.0.0.1:7863" config={{}} />);

    fireEvent.pointerDown(await screen.findByRole("button", { name: /First cue/ }));
    fireEvent.click(screen.getByRole("button", { name: "Ripple" }));
    fireEvent.click(screen.getByRole("button", { name: "Quantize" }));
    fireEvent.click(screen.getByRole("tab", { name: /Inspector/ }));
    fireEvent.change(await screen.findByLabelText("Clip end"), { target: { value: "1.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save timeline *" }));

    await waitFor(() => expect(savedTimeline).toBeTruthy());
    expect(savedTimeline.tracks[0].clips).toEqual([
      expect.objectContaining({ id: "first", start_s: 0, end_s: 1.5 }),
      expect.objectContaining({ id: "second", start_s: 1.5, end_s: 2.5 }),
    ]);
  });

  it("ripples deletion through undo and redo history", async () => {
    installEdmgBridge();
    let savedTimeline: any = null;
    installFetchMock({
      "/v1/projects": { projects: [{ id: "p1", name: "Ripple Delete Test" }] },
      "/v1/projects/p1": {
        project: {
          id: "p1",
          name: "Ripple Delete Test",
          meta: {
            audio: { duration_s: 8 },
            analysis: { features: { duration_s: 8, bpm: 120 } },
            timeline: {
              duration_s: 8,
              tracks: [
                {
                  id: "prompts",
                  name: "Prompts",
                  type: "prompt",
                  clips: [
                    { id: "first", start_s: 0, end_s: 1, data: { prompt: "First cue" } },
                    { id: "second", start_s: 1, end_s: 2, data: { prompt: "Second cue" } },
                  ],
                },
              ],
            },
          },
        },
      },
      "POST /v1/projects/p1/timeline": (_path: string, init?: RequestInit) => {
        const body = init?.body ? JSON.parse(String(init.body)) : {};
        savedTimeline = body.timeline;
        return { ok: true, timeline: body.timeline || {} };
      },
    });

    renderWithStudio(<Timeline backendUrl="http://127.0.0.1:7863" config={{}} />);

    fireEvent.pointerDown(await screen.findByRole("button", { name: /First cue/ }));
    fireEvent.click(screen.getByRole("button", { name: "Ripple" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(await screen.findByRole("button", { name: /Second cue, 0:00\.0 to 0:01\.0/ })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(await screen.findByRole("button", { name: /First cue, 0:00\.0 to 0:01\.0/ })).toBeTruthy();
    expect(await screen.findByRole("button", { name: /Second cue, 0:01\.0 to 0:02\.0/ })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Redo" }));
    fireEvent.click(screen.getByRole("button", { name: "Save timeline *" }));

    await waitFor(() => expect(savedTimeline).toBeTruthy());
    expect(savedTimeline.tracks[0].clips).toEqual([
      expect.objectContaining({ id: "second", start_s: 0, end_s: 1 }),
    ]);
  });

  it("labels scheduled motion axes and applies multi-axis camera presets", async () => {
    installEdmgBridge();
    let savedTimeline: any = null;
    installFetchMock({
      "/v1/projects": { projects: [{ id: "p1", name: "Motion Test" }] },
      "/v1/projects/p1": {
        project: {
          id: "p1",
          name: "Motion Test",
          meta: {
            audio: { filename: "track.wav", duration_s: 8 },
            analysis: { features: { duration_s: 8, bpm: 120 } },
            last_plan: {
              variants: [
                {
                  name: "Variant 1",
                  scenes: [{ id: "scene_0", start_s: 0, end_s: 8, prompt: "Move through the frame." }],
                },
              ],
            },
            timeline: {
              duration_s: 8,
              tracks: [
                {
                  id: "track_prompt",
                  name: "Prompts",
                  type: "prompt",
                  clips: [{ id: "prompt_0", start_s: 0, end_s: 8, data: { prompt: "Move through the frame." } }],
                },
                {
                  id: "track_motion",
                  name: "Motion",
                  type: "motion",
                  clips: [
                    {
                      id: "reactive_0",
                      start_s: 0,
                      end_s: 2,
                      data: { zoom: "0:(1.0), 48:(1.08)", angle: "0:(0), 48:(2.0)" },
                    },
                    {
                      id: "simple_0",
                      start_s: 2,
                      end_s: 8,
                      data: {
                        zoom_start: 1,
                        zoom_end: 1.06,
                        pan_x_start: 0,
                        pan_x_end: 0,
                        pan_y_start: 0,
                        pan_y_end: 0,
                        rotation_start: 0,
                        rotation_end: 0,
                      },
                    },
                  ],
                },
              ],
              camera: {
                keyframes: [{ t: 4, zoom: 1, pan_x: 0, pan_y: 0, rotation_deg: 0 }],
              },
            },
          },
        },
      },
      "POST /v1/projects/p1/timeline": (_path: string, init?: RequestInit) => {
        const body = init?.body ? JSON.parse(String(init.body)) : {};
        savedTimeline = body.timeline;
        return { ok: true, timeline: body.timeline || {} };
      },
    });

    renderWithStudio(<Timeline backendUrl="http://127.0.0.1:7863" config={{}} />);

    expect(await screen.findByTitle("Audio reactive · zoom + rotate")).toBeTruthy();
    fireEvent.pointerDown(await screen.findByTitle("Push in · zoom"));
    fireEvent.click(screen.getByRole("tab", { name: /Inspector/ }));

    const preset = await screen.findByLabelText("Motion preset") as HTMLSelectElement;
    expect(preset.value).toBe("custom");
    expect(screen.getByLabelText("Motion pan X end")).toBeTruthy();
    expect(screen.getByText("3D orbit + render controls")).toBeTruthy();

    fireEvent.change(preset, { target: { value: "orbit_right" } });
    expect(await screen.findByTitle("Orbit right · zoom + pan + rotate + depth + 3D orbit")).toBeTruthy();
    expect((screen.getByLabelText("Motion pan X end") as HTMLInputElement).value).toBe("8");

    fireEvent.click(screen.getByRole("button", { name: "Save timeline *" }));
    await waitFor(() => expect(savedTimeline).toBeTruthy());
    const start = savedTimeline.camera.keyframes.find((point: any) => point.t === 2);
    const middle = savedTimeline.camera.keyframes.find((point: any) => point.t === 4);
    const end = savedTimeline.camera.keyframes.find((point: any) => point.t === 8);
    expect(start.pan_x).toBe(-8);
    expect(start.rotation_3d_y).toBe(5);
    expect(middle.pan_x).toBeCloseTo(-2.67, 1);
    expect(middle.rotation_3d_y).toBeCloseTo(1.67, 1);
    expect(end.pan_x).toBe(8);
    expect(end.rotation_3d_y).toBe(-5);
  });

  it("records move, trim, split, property, camera, and curve edits in history", async () => {
    installEdmgBridge();
    installFetchMock({
      "/v1/projects": { projects: [{ id: "p1", name: "Command History Test" }] },
      "/v1/projects/p1": {
        project: {
          id: "p1",
          name: "Command History Test",
          meta: {
            audio: { filename: "track.wav", duration_s: 8 },
            analysis: { features: { duration_s: 8, bpm: 120, beat_times_s: [0, 0.5, 1, 1.5, 2] } },
            last_plan: {
              variants: [
                {
                  name: "Variant 1",
                  scenes: [{ id: "scene_0", start_s: 0, end_s: 4, prompt: "Command history prompt" }],
                },
              ],
            },
            timeline: {
              duration_s: 8,
              tracks: [
                {
                  id: "track_prompt",
                  name: "Prompts",
                  type: "prompt",
                  clips: [{ id: "prompt_0", start_s: 0, end_s: 4, data: { prompt: "Command history prompt" } }],
                },
                {
                  id: "track_motion",
                  name: "Motion",
                  type: "motion",
                  clips: [{ id: "motion_0", start_s: 0, end_s: 4, data: {} }],
                },
              ],
              camera: {
                keyframes: [{ t: 2, zoom: 1, pan_x: 0, pan_y: 0, rotation_deg: 0 }],
              },
            },
          },
        },
      },
    });

    renderWithStudio(<Timeline backendUrl="http://127.0.0.1:7863" config={{}} />);

    const clip = await screen.findByTitle("Command history prompt");
    fireEvent.pointerDown(clip);
    fireEvent.click(screen.getByRole("tab", { name: /Inspector/ }));

    const playhead = await screen.findByLabelText("Playhead time") as HTMLInputElement;
    fireEvent.change(playhead, { target: { value: "1" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Nudge to playhead" })[0]);
    expect((screen.getByLabelText("Clip start") as HTMLInputElement).value).toBe("1");
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect((screen.getByLabelText("Clip start") as HTMLInputElement).value).toBe("0");

    fireEvent.change(screen.getByLabelText("Clip length"), { target: { value: "2" } });
    expect((screen.getByLabelText("Clip length") as HTMLInputElement).value).toBe("2");
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect((screen.getByLabelText("Clip length") as HTMLInputElement).value).toBe("4");
    fireEvent.click(screen.getByRole("button", { name: "Redo" }));
    expect((screen.getByLabelText("Clip length") as HTMLInputElement).value).toBe("2");

    fireEvent.click(screen.getByRole("button", { name: "Split" }));
    expect(screen.getAllByTitle("Command history prompt")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(screen.getAllByTitle("Command history prompt")).toHaveLength(1);

    const prompt = screen.getByLabelText("Prompt text") as HTMLTextAreaElement;
    fireEvent.change(prompt, { target: { value: "Updated command history prompt" } });
    expect(prompt.value).toBe("Updated command history prompt");
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect((screen.getByLabelText("Prompt text") as HTMLTextAreaElement).value).toBe("Command history prompt");

    fireEvent.pointerDown(screen.getByLabelText(/Camera keyframe at/));
    const zoom = await screen.findByLabelText("Camera zoom") as HTMLInputElement;
    fireEvent.change(zoom, { target: { value: "1.5" } });
    expect(zoom.value).toBe("1.5");
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect((screen.getByLabelText("Camera zoom") as HTMLInputElement).value).toBe("1");

    fireEvent.click(screen.getByRole("tab", { name: /Curves/ }));
    const strengthSchedule = await screen.findByLabelText("Strength schedule") as HTMLTextAreaElement;
    fireEvent.change(strengthSchedule, { target: { value: "0:(0.45)" } });
    expect(strengthSchedule.value).toBe("0:(0.45)");
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect((screen.getByLabelText("Strength schedule") as HTMLTextAreaElement).value).toBe("");
  });

  it("queues edited masters with full schema settings and valid codec pairs", async () => {
    installEdmgBridge();
    const renderRequests: any[] = [];
    installFetchMock({
      "/v1/projects": { projects: [{ id: "p1", name: "Export Contract" }] },
      "/v1/projects/p1": {
        project: {
          id: "p1",
          name: "Export Contract",
          meta: {
            audio: { duration_s: 8 },
            analysis: { features: { duration_s: 8, bpm: 120 } },
            timeline: {
              duration_s: 8,
              tracks: [
                {
                  id: "track_video",
                  name: "Video Edit",
                  type: "video",
                  clips: [
                    {
                      id: "video_1",
                      start_s: 0,
                      end_s: 8,
                      data: {
                        source_path: "outputs/videos/source.mp4",
                        source_in_s: 0,
                        source_out_s: 8,
                        speed: 1,
                        volume: 1,
                      },
                    },
                  ],
                },
              ],
            },
          },
        },
      },
      "POST /v1/projects/p1/timeline": (_path: string, init?: RequestInit) => {
        const body = init?.body ? JSON.parse(String(init.body)) : {};
        return { ok: true, timeline: body.timeline || {} };
      },
      "POST /v1/projects/p1/timeline/render": (_path: string, init?: RequestInit) => {
        renderRequests.push(init?.body ? JSON.parse(String(init.body)) : {});
        return { ok: true, job: { id: `timeline-job-${renderRequests.length}` } };
      },
    });

    renderWithStudio(<Timeline backendUrl="http://127.0.0.1:7863" config={{}} />);

    fireEvent.click(await screen.findByRole("tab", { name: /Media/ }));
    const outputName = await screen.findByLabelText("Edited master output name") as HTMLInputElement;
    fireEvent.change(outputName, { target: { value: "  Final: Cut?  " } });
    fireEvent.blur(outputName);
    expect(outputName.value).toBe("Final_ Cut_");

    fireEvent.change(screen.getByLabelText("Output size preset"), { target: { value: "4320x7680" } });
    expect((screen.getByLabelText("Output width") as HTMLInputElement).value).toBe("4320");
    expect((screen.getByLabelText("Output height") as HTMLInputElement).value).toBe("7680");

    fireEvent.change(screen.getByLabelText("Output FPS"), { target: { value: "119.88" } });
    expect((screen.getByLabelText("Frame-rate preset") as HTMLSelectElement).value).toBe("custom");

    fireEvent.change(screen.getByLabelText("Edited master video codec"), { target: { value: "prores" } });
    expect(screen.getByText("PCM 16-bit")).toBeTruthy();
    expect((screen.getByLabelText("Edited master CRF") as HTMLInputElement).disabled).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Render edited master" }));
    await waitFor(() => expect(renderRequests).toHaveLength(1));
    expect(renderRequests[0]).toMatchObject({
      name: "Final_ Cut_",
      width: 4320,
      height: 7680,
      fps: 119.88,
      video_codec: "prores",
      audio_codec: "pcm_s16le",
    });
    expect(renderRequests[0]).not.toHaveProperty("quality");

    fireEvent.change(screen.getByLabelText("Edited master video codec"), { target: { value: "hevc" } });
    expect(screen.getByText("AAC 192 kbps")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Edited master quality preset"), { target: { value: "low" } });
    fireEvent.change(screen.getByLabelText("Edited master CRF"), { target: { value: "31" } });
    fireEvent.click(screen.getByRole("button", { name: "Render edited master" }));
    await waitFor(() => expect(renderRequests).toHaveLength(2));
    expect(renderRequests[1]).toMatchObject({
      video_codec: "hevc",
      audio_codec: "aac",
      quality: 31,
    });
  });

  it("validates edited-master dimensions, FPS, quality, and name inline", async () => {
    installEdmgBridge();
    installFetchMock({
      "/v1/projects": { projects: [{ id: "p1", name: "Validation Test" }] },
      "/v1/projects/p1": {
        project: {
          id: "p1",
          name: "Validation Test",
          meta: {
            audio: { duration_s: 8 },
            analysis: { features: { duration_s: 8, bpm: 120 } },
            timeline: {
              duration_s: 8,
              tracks: [
                {
                  id: "track_video",
                  name: "Video Edit",
                  type: "video",
                  clips: [{ id: "video_1", start_s: 0, end_s: 8, data: { source_path: "outputs/videos/source.mp4" } }],
                },
              ],
            },
          },
        },
      },
    });

    renderWithStudio(<Timeline backendUrl="http://127.0.0.1:7863" config={{}} />);
    fireEvent.click(await screen.findByRole("tab", { name: /Media/ }));
    const renderButton = await screen.findByRole("button", { name: "Render edited master" }) as HTMLButtonElement;
    const width = screen.getByLabelText("Output width") as HTMLInputElement;
    const height = screen.getByLabelText("Output height") as HTMLInputElement;
    const fps = screen.getByLabelText("Output FPS") as HTMLInputElement;
    const quality = screen.getByLabelText("Edited master CRF") as HTMLInputElement;
    const name = screen.getByLabelText("Edited master output name") as HTMLInputElement;

    fireEvent.change(width, { target: { value: "1919" } });
    expect((await screen.findAllByText("Width must be even for video encoding.")).length).toBeGreaterThan(0);
    expect(renderButton.disabled).toBe(true);
    fireEvent.change(width, { target: { value: "7680" } });

    fireEvent.change(height, { target: { value: "7681" } });
    expect((await screen.findAllByText("Height must be between 256 and 7680 pixels.")).length).toBeGreaterThan(0);
    fireEvent.change(height, { target: { value: "7680" } });

    fireEvent.change(fps, { target: { value: "121" } });
    expect((await screen.findAllByText("FPS must be between 1 and 120. Decimal frame rates are supported.")).length).toBeGreaterThan(0);
    fireEvent.change(fps, { target: { value: "120" } });

    fireEvent.change(quality, { target: { value: "52" } });
    expect((await screen.findAllByText("CRF must be a whole number between 1 and 51.")).length).toBeGreaterThan(0);
    fireEvent.change(screen.getByLabelText("Edited master video codec"), { target: { value: "prores" } });
    expect(screen.queryByText("CRF must be a whole number between 1 and 51.")).toBeNull();
    expect(renderButton.disabled).toBe(false);
    fireEvent.change(screen.getByLabelText("Edited master video codec"), { target: { value: "h264" } });
    fireEvent.change(quality, { target: { value: "51" } });

    fireEvent.change(name, { target: { value: "..." } });
    fireEvent.blur(name);
    expect((await screen.findAllByText("Enter a filename-safe output name.")).length).toBeGreaterThan(0);
    expect(renderButton.disabled).toBe(true);

    fireEvent.change(name, { target: { value: "portrait_8k_master" } });
    expect(renderButton.disabled).toBe(false);
  });
});
