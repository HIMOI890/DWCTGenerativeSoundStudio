import React from "react";
import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import Workspace from "../pages/Workspace";
import { installEdmgBridge, installFetchMock, renderWithStudio } from "./testUtils";

describe("Workspace page", () => {
  it("integrates creative direction from the real project analysis and plan", async () => {
    window.localStorage.clear();
    installEdmgBridge();
    installFetchMock({
      "/v1/projects": { projects: [{ id: "p1", name: "Demo Project" }] },
      "/v1/projects/p1": {
        project: {
          id: "p1",
          name: "Demo Project",
          meta: {
            audio: { filename: "track.wav", size_bytes: 1024 * 1024, duration_s: 48 },
            analysis: {
              features: {
                duration_s: 48,
                bpm: 122,
                energy: 0.68,
                bass_energy: 0.55,
                mid_energy: 0.49,
                brightness: 0.42,
              },
              transcript: {
                text: "Neon streets open into a dawn skyline while the chorus lifts the whole crowd forward.",
              },
            },
            last_plan: {
              variants: [
                {
                  name: "Variant 1",
                  scenes: [
                    {
                      name: "Neon arrival",
                      start_s: 0,
                      end_s: 12,
                      prompt: "Neon streets with rain reflections and kinetic camera drift.",
                      setting: "Rain-soaked transit plaza",
                      shot_type: "wide tracking profile",
                      character_lock: "Same lead performer in a charcoal coat",
                      style_lock: "Nocturnal 35mm realism with cyan and amber practicals",
                      start_state: "Performer waits at frame left facing screen right",
                      end_state: "Performer reaches frame center facing screen right",
                      action: "Performer crosses the plaza in one continuous walk",
                      camera: "Measured lateral tracking move",
                      motion: "Natural left-to-right walk with coherent anatomy",
                      environment_motion: "Rain falls while neon reflections move across the pavement",
                      continuity_note: "Preserve landmarks, wardrobe, palette, and screen direction",
                      transition: "Continue the walk into the next scene",
                    },
                    {
                      name: "Skyline lift",
                      start_s: 12,
                      end_s: 24,
                      prompt: "Dawn skyline bloom with silhouettes and stronger motion parallax.",
                      setting: "Rain-soaked transit plaza",
                      shot_type: "hero medium tracking shot",
                      character_lock: "Same lead performer in a charcoal coat",
                      style_lock: "Nocturnal 35mm realism with cyan and amber practicals",
                      start_state: "Performer reaches frame center facing screen right",
                      end_state: "Performer settles at frame right facing screen right",
                      action: "Performer completes one uninterrupted turn toward the skyline",
                      camera: "Continue laterally into a restrained push",
                      environment_motion: "Rain and skyline haze maintain continuous motion",
                      transition: "Resolve on the skyline glow",
                    },
                  ],
                },
              ],
            },
          },
        },
      },
      "/v1/projects/p1/assets": { assets: { refs: [] } },
      "/v1/projects/p1/director/document": {
        ok: true,
        revision: 1,
        document: {
          version: 1,
          story_bible: {
            revision: 1,
            project_theme: "Neon arrival",
            visual_style: "Nocturnal 35mm realism",
            characters: {},
            locations: {},
            continuity_rules: [],
            forbidden_changes: [],
          },
          scenes: [],
          analysis_revision: 1,
        },
      },
      "POST /v1/projects/p1/director/document": (path, init) => ({
        ok: true,
        revision: 2,
        document: JSON.parse(String(init?.body || "{}")).document,
      }),
      "/v1/projects/p1/audio": {},
      "/v1/projects/p1/creative_direction*": {
        creative_direction: {
          preset: "cinematic",
          sensitivity: 1,
          metrics: { energy: 0.68, bass: 0.55, mid: 0.49, treble: 0.42, duration_s: 48, source: "analysis" },
          waveform: [0.2, 0.45, 0.8, 0.5],
          motifs: ["neon", "skyline", "dawn"],
          transcript_text: "Neon streets open into a dawn skyline while the chorus lifts the whole crowd forward.",
          transcript_summary: "Neon streets open into a dawn skyline while the chorus lifts the whole crowd forward.",
          status: "Creative direction is being derived on the backend from the saved project analysis and plan.",
          export_text: "1. Neon arrival (0.00s - 12.00s)\nNeon streets with rain reflections and kinetic camera drift.",
          scenes: [
            {
              index: 0,
              name: "Neon arrival",
              start_s: 0,
              end_s: 12,
              duration_s: 12,
              energy: 0.64,
              energy_label: "lift",
              prompt: "Neon streets with rain reflections and kinetic camera drift.",
              transcript_cue: "Neon streets open into a dawn skyline while the chorus lifts the whole crowd forward.",
              camera_hint: "Tracking medium shot with progressive push, controlled drift, and bolder edge lighting.",
              motion_hint: "Zoom 1.14, cfg 7.9, strength 0.68, Z travel -16.6.",
              prompt_pack: "Neon streets with rain reflections and kinetic camera drift.",
            },
          ],
        },
      },
    });

    renderWithStudio(<Workspace backendUrl="http://127.0.0.1:7863" config={{}} />);

    expect(await screen.findByRole("heading", { name: "Workspace" })).toBeTruthy();
    expect(await screen.findByText("Creative direction")).toBeTruthy();
    expect(await screen.findByText("Scene prompt pack")).toBeTruthy();
    expect(await screen.findByRole("button", { name: "Copy prompt pack" })).toBeTruthy();
    expect(await screen.findByRole("button", { name: "Apply direction to timeline" })).toBeTruthy();
    expect(await screen.findByRole("button", { name: "Timeline patch" })).toBeTruthy();
    expect(await screen.findByRole("button", { name: "LLM contract" })).toBeTruthy();
    expect(await screen.findByText(/Transcript anchor/i)).toBeTruthy();

    fireEvent.click(await screen.findByRole("tab", { name: /Storyboard/i }));
    expect((await screen.findAllByText("Rain-soaked transit plaza")).length).toBe(2);
    expect(await screen.findByText("wide tracking profile")).toBeTruthy();
    expect((await screen.findAllByText("Same lead performer in a charcoal coat")).length).toBe(2);
    expect((await screen.findAllByText("Nocturnal 35mm realism with cyan and amber practicals")).length).toBe(2);
    expect(await screen.findByText("Performer waits at frame left facing screen right")).toBeTruthy();
    expect((await screen.findAllByText("Performer reaches frame center facing screen right")).length).toBe(2);
    expect(await screen.findByText("Rain falls while neon reflections move across the pavement")).toBeTruthy();

    fireEvent.click(await screen.findByRole("tab", { name: /Reactive Lab/i }));
    expect(await screen.findByText("Reactive Lab + Renderer Handoff")).toBeTruthy();

    fireEvent.click(await screen.findByRole("tab", { name: /Director/i }));
    expect(await screen.findByText("EDMG Director")).toBeTruthy();
    expect(await screen.findByText(/Director is embedded in this Workspace session/i)).toBeTruthy();
    fireEvent.click(await screen.findByRole("button", { name: "Use selected storyboard" }));
    expect(await screen.findByText(/selected storyboard scene/i)).toBeTruthy();
    fireEvent.click(await screen.findByRole("button", { name: "Save direction" }));
    expect(await screen.findByText(/saved to this Workspace project/i)).toBeTruthy();
  });

  it("reconciles a stale stored project id before loading project detail routes", async () => {
    window.localStorage.clear();
    window.localStorage.setItem("edmg_studio_session_v1", JSON.stringify({
      projectId: "stale-project",
      selectedVariant: 0,
      lastHandoff: null,
    }));
    installEdmgBridge();
    const fetchMock = installFetchMock({
      "/v1/projects": { projects: [{ id: "p1", name: "Recovered Project" }] },
      "/v1/projects/p1": {
        project: {
          id: "p1",
          name: "Recovered Project",
          meta: {
            last_plan: {
              variants: [{ name: "Variant 1", scenes: [] }],
            },
          },
        },
      },
      "/v1/projects/p1/assets": { assets: { refs: [] } },
      "/v1/projects/p1/creative_direction*": {
        creative_direction: {
          status: "Recovered selection.",
          export_text: "",
          scenes: [],
          metrics: { energy: 0, bass: 0, mid: 0, treble: 0, duration_s: 0, source: "analysis" },
          missing: [],
          waveform: [],
          motifs: [],
          transcript_text: "",
          transcript_summary: "",
          preset: "cinematic",
          sensitivity: 1,
          provider_mode: "local",
          scene_source: "plan",
          ready: false,
        },
      },
    });

    renderWithStudio(<Workspace backendUrl="http://127.0.0.1:7863" config={{}} />);

    expect(await screen.findByText("Recovered Project")).toBeTruthy();
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/v1/projects/stale-project"))).toBe(false);
  });

  it("surfaces a precise no-speech-after-vad status when no transcript is available", async () => {
    window.localStorage.clear();
    installEdmgBridge();
    installFetchMock({
      "/v1/projects": { projects: [{ id: "p1", name: "Instrumental Project" }] },
      "/v1/projects/p1": {
        project: {
          id: "p1",
          name: "Instrumental Project",
          meta: {
            analysis: {
              summary: "Transcription unavailable. Using audio-only analysis from rhythm, energy, and spectral movement.",
              transcript: {
                text: "",
                note: "No speech detected after VAD.",
                duration_after_vad_s: 0,
                source: "faster_whisper",
              },
              features: {
                duration_s: 374.8,
                bpm: 60,
                energy: 0.41,
              },
            },
            last_plan: {
              variants: [{ name: "Variant 1", scenes: [] }],
            },
          },
        },
      },
      "/v1/projects/p1/assets": { assets: { refs: [] } },
      "/v1/projects/p1/creative_direction*": {
        creative_direction: {
          status: "Audio-only creative direction is ready.",
          export_text: "",
          scenes: [],
          metrics: { energy: 0.41, bass: 0.28, mid: 0.35, treble: 0.22, duration_s: 374.8, source: "analysis" },
          missing: [],
          waveform: [],
          motifs: [],
          transcript_text: "",
          transcript_summary: "No speech detected after VAD. Studio is still able to build audio-reactive sections and a first creative direction from rhythm, energy, and spectral movement.",
          preset: "cinematic",
          sensitivity: 1,
          provider_mode: "local",
          scene_source: "analysis_fallback",
          ready: true,
        },
      },
    });

    renderWithStudio(<Workspace backendUrl="http://127.0.0.1:7863" config={{}} />);

    expect((await screen.findAllByText(/No speech detected after VAD/i)).length).toBeGreaterThan(0);
    expect(await screen.findByText("No speech after VAD")).toBeTruthy();
  });
});
