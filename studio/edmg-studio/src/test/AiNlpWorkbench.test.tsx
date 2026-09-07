import React from "react";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import AiNlpWorkbench from "../workbenches/AiNlpWorkbench";
import { installEdmgBridge, installFetchMock, renderWithStudio } from "./testUtils";

const TRANSCRIPT = "A late night drive through the rain soaked city.";

function studioProject() {
  return {
    id: "p1",
    name: "Handoff Demo",
    meta: {
      audio: { filename: "track.wav", duration_s: 12 },
      analysis: {
        timestamp: 123,
        summary: TRANSCRIPT,
        transcript: { text: TRANSCRIPT },
        features: { duration_s: 12, bpm: 120 },
        sections: [
          { start_s: 0, end_s: 6, energy: 0.3, label: "intro" },
          { start_s: 6, end_s: 12, energy: 0.8, label: "drop" },
        ],
      },
      last_plan: {
        variants: [
          {
            name: "Variant 1",
            scenes: [
              {
                start_s: 0,
                end_s: 6,
                name: "Intro",
                prompt: "rain on glass, neon reflections",
                setting: "Rain-soaked transit plaza",
                shot_type: "wide tracking profile",
                character_lock: "Same lead driver in a charcoal coat",
                style_lock: "Grounded nocturnal 35mm realism",
                start_state: "Driver waits at frame left facing screen right",
                end_state: "Driver reaches frame center facing screen right",
                action: "Driver crosses toward the idling car in one continuous walk",
                camera: "Measured lateral tracking move",
                motion: "Coherent left-to-right walk with natural coat movement",
                environment_motion: "Rain falls while neon reflections slide across wet pavement",
                continuity_note: "Preserve the plaza landmarks and screen direction",
                transition: "Continue the walk into the next beat",
              },
              {
                start_s: 6,
                end_s: 12,
                name: "Drop",
                prompt: "city lights rushing past",
                setting: "Rain-soaked transit plaza",
                shot_type: "hero medium tracking shot",
                character_lock: "Different character that must be rejected",
                style_lock: "Different style that must be rejected",
                start_state: "Mismatched start state that must be repaired",
                end_state: "Driver settles behind the wheel facing screen right",
                action: "Driver opens the car door and sits in one continuous action",
                camera: "Continue the lateral move into a restrained push",
                environment_motion: "Rain and passing headlights maintain continuous motion",
                transition: "Resolve on the dashboard glow",
              },
            ],
          },
        ],
      },
    },
  };
}

describe("AiNlpWorkbench shared-session hydration", () => {
  it("pre-fills the creative brief and subject focus from the analyzed transcript", async () => {
    installEdmgBridge();
    // Audio endpoint returns a non-blob response; the workbench handles that
    // gracefully and still hydrates the brief/prompts from the session.
    installFetchMock({ "/v1/projects/p1/audio": {} });

    renderWithStudio(
      <AiNlpWorkbench
        compact
        studioProjectId="p1"
        studioProjectName="Handoff Demo"
        studioProject={studioProject()}
        studioSelectedVariant={0}
        onSyncToStudio={vi.fn()}
      />,
    );

    // Wait for hydration to finish (it auto-opens the Prompt Pack once the
    // saved storyboard scenes are loaded from the shared session).
    await waitFor(() => expect(screen.getByText(/Executive AI plan/i)).toBeTruthy());

    // Open Setup to inspect the seeded brief/subject fields.
    fireEvent.click(screen.getByRole("tab", { name: /Setup/i }));

    // Creative brief is seeded from the transcript instead of the generic default.
    expect(screen.getByDisplayValue(TRANSCRIPT)).toBeTruthy();
    // Subject focus is derived from the transcript too.
    expect(screen.getByDisplayValue(/embodying A late night drive/i)).toBeTruthy();
    // Handoff message tells the user no re-upload is required.
    expect(screen.getByText(/no audio download is needed/i)).toBeTruthy();
    // With session analysis hydrated, planning works without a local file.
    fireEvent.click(screen.getByRole("tab", { name: /^Setup/i }));
    expect(screen.getByRole("button", { name: /Regenerate plan/i })).toBeTruthy();
  });

  it("surfaces structured continuity fields and syncs exact end-to-start handoffs", async () => {
    installEdmgBridge();
    installFetchMock({ "/v1/projects/p1/audio": {} });
    const onSyncToStudio = vi.fn().mockResolvedValue("Continuity plan synced.");

    renderWithStudio(
      <AiNlpWorkbench
        compact
        studioProjectId="p1"
        studioProjectName="Handoff Demo"
        studioProject={studioProject()}
        studioSelectedVariant={0}
        onSyncToStudio={onSyncToStudio}
      />,
    );

    await waitFor(() => expect(screen.getByText(/Executive AI plan/i)).toBeTruthy());
    expect(screen.getAllByText(/Storyboard continuity contract/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Same lead driver in a charcoal coat/i).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("tab", { name: /Storyboard/i }));
    expect(await screen.findByText(/Storyboard reading order/i)).toBeTruthy();
    expect(screen.getAllByText(/Rain-soaked transit plaza/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Driver reaches frame center facing screen right/i).length).toBeGreaterThan(1);

    fireEvent.click(screen.getByRole("button", { name: /Sync to internal renderer/i }));
    await waitFor(() => expect(onSyncToStudio).toHaveBeenCalledTimes(1));

    const payload = onSyncToStudio.mock.calls[0][0] as any;
    expect(payload.plan.scenes[1].startState).toBe(payload.plan.scenes[0].endState);
    expect(payload.plan.scenePlan[1].startState).toBe(payload.plan.scenePlan[0].endState);
    expect(payload.plan.scenes[1].characterLock).toBe(payload.plan.scenes[0].characterLock);
    expect(payload.plan.scenes[1].styleLock).toBe(payload.plan.scenes[0].styleLock);
    expect(payload.plan.scenes[1].startState).not.toContain("Mismatched start state");
  });

  it("edits all continuity fields, syncs them, and reloads the saved canonical scenes", async () => {
    installEdmgBridge();
    installFetchMock({ "/v1/projects/p1/audio": {} });
    const onSyncToStudio = vi.fn().mockResolvedValue("Saved");
    const props = { compact: true, studioProjectId: "p1", studioSelectedVariant: 0, onSyncToStudio };
    const view = renderWithStudio(<AiNlpWorkbench {...props} studioProject={studioProject()} />);
    await screen.findByText(/Executive AI plan/i);
    const values = { setting: "East station platform", shotType: "Close tracking shot", characterLock: "Driver with a red scarf", styleLock: "Silver grain", startState: "Driver beside the gate", endState: "Driver touches the door" };
    for (const [field, value] of Object.entries(values)) {
      fireEvent.change(screen.getByLabelText(`Scene 1 ${field}`), { target: { value } });
    }
    values.endState = "Driver holds the door open";
    fireEvent.change(screen.getByLabelText("Scene 2 startState"), { target: { value: values.endState } });
    expect((screen.getByLabelText("Scene 1 endState") as HTMLInputElement).value).toBe(values.endState);
    fireEvent.click(screen.getByRole("button", { name: /Sync to internal renderer/i }));
    await waitFor(() => expect(onSyncToStudio).toHaveBeenCalledTimes(1));
    const scenes = onSyncToStudio.mock.calls[0][0].plan.scenes;
    expect(scenes[0]).toMatchObject(values);
    expect(scenes[1].startState).toBe(values.endState);
    expect(scenes[1].characterLock).toBe(values.characterLock);
    expect(scenes[1].styleLock).toBe(values.styleLock);
    const project = studioProject();
    project.meta.last_plan.variants[0].scenes = scenes.map((scene: any) => ({
      ...scene, prompt: scene.text, setting: scene.setting, shot_type: scene.shotType,
      character_lock: scene.characterLock, style_lock: scene.styleLock,
      start_state: scene.startState, end_state: scene.endState,
    }));
    view.unmount();
    renderWithStudio(<AiNlpWorkbench {...props} studioProject={project} />);
    await screen.findByText(/Executive AI plan/i);
    for (const [field, value] of Object.entries(values)) {
      expect((screen.getByLabelText(`Scene 1 ${field}`) as HTMLInputElement).value).toBe(value);
    }
  });

  it("keeps locked-scene contracts coherent when regenerating the surrounding storyboard", async () => {
    installEdmgBridge();
    installFetchMock({ "/v1/projects/p1/audio": {} });
    const onSyncToStudio = vi.fn().mockResolvedValue("Regenerated continuity plan synced.");

    renderWithStudio(
      <AiNlpWorkbench
        compact
        studioProjectId="p1"
        studioProjectName="Handoff Demo"
        studioProject={studioProject()}
        studioSelectedVariant={0}
        onSyncToStudio={onSyncToStudio}
      />,
    );

    await waitFor(() => expect(screen.getByText(/Executive AI plan/i)).toBeTruthy());
    fireEvent.click(screen.getAllByRole("button", { name: /^Lock$/i })[0]);
    fireEvent.click(screen.getByRole("tab", { name: /^Setup/i }));
    fireEvent.click(screen.getByRole("button", { name: /Regenerate plan/i }));
    fireEvent.click(screen.getByRole("button", { name: /Sync to internal renderer/i }));
    await waitFor(() => expect(onSyncToStudio).toHaveBeenCalledTimes(1));

    const payload = onSyncToStudio.mock.calls[0][0] as any;
    const [first, second] = payload.plan.scenes;
    expect(second.startState).toBe(first.endState);
    expect(second.characterLock).toBe(first.characterLock);
    expect(second.styleLock).toBe(first.styleLock);
    expect(payload.plan.scenePlan[1].startState).toBe(first.endState);
    expect(payload.plan.scenePlan[1].characterLock).toBe(first.characterLock);
    expect(payload.plan.scenePlan[1].styleLock).toBe(first.styleLock);
  });
});
