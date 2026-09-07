# Unified AI Planner, Automatic Keyframe Scheduling, and Adaptive Render Engine

## Summary

Turn the current multi-step workflow into one coherent production pipeline:

**Audio analysis → AI creative plan → enriched scene prompts → proposed keyframe schedule → user review → one-click timeline application → adaptive render engine**

The AI Planner will automatically create a complete keyframe-schedule draft for every generated plan variant. Users will no longer need to open the Reactive Lab or manually build keyframes just to turn the analysis into a renderable timeline. The schedule remains a draft until the user selects a variant and applies it, protecting existing timeline work.

The Render page will present one adaptive “Studio Engine” instead of several competing top-level render workflows. Existing internal, TensorRT, SVD, AnimateDiff, ComfyUI, hosted, still-image, and timeline-rendering capabilities will remain available as engine components and expert overrides.

## Implementation Changes

### 1. Make the planner output production-ready

- Expand each planned scene into a structured production packet containing:
  - Scene timing and musical-section identity.
  - Human-readable creative intent and rationale.
  - Subject, setting, action, shot composition, camera path, environmental motion, transition, continuity, and start/end-state locks.
  - A concise renderer-safe positive prompt derived from those structured fields.
  - Negative prompt, model/engine hints, keyframe-anchor instructions, motion intensity, and continuity requirements.
- Treat structured scene fields as the source of truth. Regenerate the concise operational prompt whenever a structured field is edited instead of forcing users to edit one oversized prompt string.
- Improve the Planner UI’s analysis output so it explains:
  - What the music is doing at each section.
  - What visual decision the planner made because of that analysis.
  - Which prompt, keyframe, motion, and transition behaviors will be generated.
- Keep style—cinematic, music-video, experimental, documentary, palette, lens language, texture, and similar choices—as creative direction inside the plan. Style will no longer behave like a separate rendering path.
- Use one “Analyze and Build Plan” workflow in both clients. Audio-only analysis may still be available for diagnostics, but the normal user action produces the analysis, plan variants, enhanced prompts, and keyframe drafts together.

### 2. Generate a keyframe-schedule draft automatically

- Add a backend-owned schedule compiler that runs whenever a plan variant is generated, regenerated, imported from Planner Lab, or materially edited.
- Compile the saved analysis and structured scenes into a schedule containing:
  - Scene-boundary prompt keyframes.
  - Visual anchor/key-image keyframes.
  - Camera position and movement keyframes.
  - Subject and environmental motion envelopes.
  - Beat, onset, phrase, section, and transition markers.
  - Engine parameters such as motion score, anchor strength, denoise/strength, guidance, steps, and interpolation cues where supported.
- Derive timing from the canonical project duration and shared transport: seconds, output FPS, frame index, BPM/beat grid, and analysis sections. Avoid the current hardcoded 24-FPS conversion when the selected output transport differs.
- Use the existing analysis smoothing principles—normalization, attack/release smoothing, trigger thresholds, artistic remapping, and parameter clamps—so raw audio fluctuations do not create noisy or excessive keyframes.
- Use musical importance rather than fixed intervals alone:
  - Always anchor scene starts and ends.
  - Add anchors at major section changes and high-confidence musical events.
  - Use lower-density motion points through stable passages.
  - Increase meaningful motion detail around rises, impacts, hooks, and transitions.
- Preserve explicit scene continuity:
  - The prior scene’s end state becomes the next scene’s starting anchor.
  - Character, setting, palette, screen direction, and style locks propagate through adjacent keyframes.
  - Each schedule point records its source and reason so the UI can explain why it exists.
- Store the generated schedule as a versioned draft attached to its plan variant, including analysis revision, plan revision, selected FPS, duration, provenance, validation warnings, and generation timestamp.
- Regenerating a plan replaces only that variant’s unapproved draft. It does not alter the active timeline.
- If analysis is incomplete, BPM is unstable, or no transcript exists, generate a valid section/energy-based draft and visibly report which inputs were unavailable rather than failing or inventing transcript evidence.

### 3. Add review and one-click application

- Show the proposed schedule directly in AI Planner Lab after each variant:
  - Summary counts for scenes, prompt anchors, image anchors, camera keys, motion events, and warnings.
  - A readable musical timeline with scene and keyframe markers.
  - Expandable details showing the analysis cue, visual decision, prompt, motion values, and continuity handoff behind each keyframe.
- Provide a single primary action: **Approve Plan and Apply Schedule**.
- Applying the draft will atomically create/update planner-owned timeline content:
  - Prompt track.
  - Visual/key-image anchor track.
  - Motion/parameter track.
  - Camera track.
  - Musical markers and transition cues.
- Mark generated timeline objects with stable ownership and source identifiers. Subsequent applications update planner-owned objects while preserving user-created tracks and manually authored or explicitly locked keyframes.
- Provide explicit secondary actions:
  - “Regenerate Draft” changes only the proposed schedule.
  - “Reset Planner-Owned Schedule” rebuilds generated content after confirmation.
  - “Open in Timeline” takes the user to the applied result for optional manual refinement.
- Do not require a separate Reactive Lab pass. Keep Reactive Lab as an advanced editor for deliberately remapping or rebuilding audio-reactive motion.
- Use project revision checks so applying a stale draft cannot overwrite timeline changes made after the draft was generated.

### 4. Unify rendering as the Studio Engine

- Replace the overlapping top-level Render workflows with one adaptive Studio Engine surface:
  - Desired result: full-motion video, still sequence, or edit existing output.
  - Quality target.
  - Aspect ratio/resolution and output FPS.
  - Optional model preference.
  - A single preflight and Render action.
- Make “Auto” the normal route. The engine selects an eligible real renderer using:
  - Planner scene requirements and schedule features.
  - Whether true temporal subject motion is required.
  - Installed models and verified runtime capabilities.
  - GPU/VRAM and render-tier constraints.
  - Quality, speed, and continuity priorities.
- Keep engine selection truthful:
  - Distinguish still/keyframe generation from temporal video generation.
  - Distinguish internal, hosted, ComfyUI, and TensorRT execution.
  - Never silently substitute a proxy, mock, cached artifact, still assembly, or hosted provider for a requested genuine/internal temporal render.
  - Surface the chosen route, model, keyframe renderer, temporal renderer, and reason before execution.
- Treat style as input to prompt compilation and renderer settings, not as a separate “style render” button or pipeline.
- Move specialist controls into one Advanced Engine section grouped by pipeline stage:
  - Keyframe/image generation.
  - Temporal motion generation.
  - Camera and audio-reactive modulation.
  - Interpolation and finishing.
  - Model/runtime overrides.
- Retain all existing backends and specialist capabilities. Consolidation changes orchestration and presentation rather than deleting working renderers.
- Have the adaptive engine consume the approved planner schedule directly. If no schedule has been approved, preflight should offer to apply the selected variant’s draft rather than rendering an unexplained default timeline.

## Public Interfaces and Data Contracts

- Extend canonical plan variants with a versioned `schedule_draft` containing transport metadata, prompt/image anchors, camera keys, motion/parameter keys, musical markers, warnings, provenance, and the source project/analysis/plan revisions.
- Extend structured scenes with explicit operational prompt output and scheduling hints while retaining current fields for backward compatibility.
- Return schedule summaries with plan-generation and Planner Lab import responses so React and WinUI can render the same result without reimplementing scheduling logic.
- Extend the plan-application contract to identify the schedule revision being approved and apply planner-owned timeline content atomically.
- Add a schedule-regeneration operation that creates a new draft without touching the active timeline.
- Expose one adaptive render-intent contract covering desired result, quality, output transport, optional model/route overrides, and schedule revision.
- Return a resolved engine plan from preflight containing the selected execution stages, models, capability evidence, fallbacks allowed, incompatibilities, and human-readable selection reasons.
- Continue accepting existing plan, timeline, and renderer request shapes during migration. Normalize legacy projects into the new schedule-draft representation when they are loaded or next regenerated.

## Client Integration

- React/Electron:
  - Update AI Planner Lab and Workspace to show enhanced analysis, structured prompts, schedule preview, warnings, and the one-click approval/application action.
  - Rework Render Control Center into the primary Studio Engine and move the existing Preset, Auto-Render, Internal/Hosted, still, AnimateDiff, SVD, conductor, and motion-sequencer controls under its shared state and Advanced disclosure.
  - Avoid maintaining independent render-setting states that can disagree with the visible Studio Engine selection.
- WinUI:
  - Add the same schedule summary, draft status, approval/application action, revision protection, and structured prompt information to AI Planner Lab.
  - Give Render the same adaptive intent and resolved-engine information as React rather than creating a separate WinUI routing algorithm.
  - Preserve current native scene editing and make saved edits trigger backend schedule-draft regeneration.
- Keep scheduling and engine resolution in the FastAPI backend. Both clients will consume the same canonical plan, schedule, and preflight contracts to prevent parity drift.

## Test and Acceptance Plan

- Backend unit tests:
  - Analysis sections, beats, energy, and scene boundaries produce deterministic schedule drafts.
  - Every scene has prompt and continuity anchors at valid times.
  - Adjacent scene handoffs preserve exact end-state/start-state continuity.
  - Keyframes are ordered, deduplicated, clamped to project duration, and converted consistently between seconds and frames.
  - Missing transcript, missing BPM, short audio, long audio, silent passages, overlapping scenes, and malformed provider output produce safe drafts and useful warnings.
  - Regeneration changes the draft but leaves the active timeline untouched.
  - Applying a draft updates only planner-owned objects and preserves user-owned or locked keys.
  - Stale schedule/project revisions are rejected with an actionable conflict response.
- Engine-selection tests:
  - Full-motion intent selects a genuine available temporal engine.
  - Still/keyframe assembly is never reported as true temporal motion.
  - Internal-only requests never route to hosted, proxy, mock, or synthetic substitutes.
  - Unsupported model/engine combinations fail preflight clearly.
  - Style changes modify creative direction and prompt compilation without creating a separate execution path.
- React tests:
  - Analyze/plan results automatically include a schedule preview.
  - The schedule is not active until approval.
  - One-click approval applies the selected variant.
  - Existing manual timeline content remains visible after reapplication.
  - The unified Studio Engine and Advanced disclosure reflect one consistent state.
- WinUI tests:
  - Deserialize and display the same enhanced plan and schedule contracts.
  - Generate, review, approve, save, reload, and reopen the same project revision successfully.
  - Match React’s resolved engine choice for the same project and intent.
- Integration acceptance:
  - Analyze a real project, generate multiple variants, select one, and apply its complete schedule without visiting Reactive Lab or manually entering keyframes.
  - Verify prompt, anchor, motion, camera, and marker tracks in Timeline.
  - Render through the adaptive engine and confirm artifact metadata records the approved schedule revision, selected real engine, models, cache status, and execution provenance.
  - Validate both Electron/React and packaged WinUI against the same project/revision.
  - For genuine CUDA motion acceptance, inspect worker-level CUDA provenance and a real motion artifact; UI/preflight success alone is insufficient.

## Assumptions and Safeguards

- “Automatic keyframes” means automatic draft generation followed by one user approval/apply action, per the selected preference.
- Planner-generated content is non-destructive by default. Existing user-authored or locked timeline work is preserved.
- The selected plan variant owns its own schedule draft; switching variants switches the preview but does not alter the active timeline.
- React/Electron and WinUI parity are both required.
- Existing renderers, model integrations, exports, queues, and advanced controls remain supported.
- Style is unified into creative direction and prompt compilation, while engine choice remains based on capability and render intent.
- No implementation, test execution, dependency change, service restart, or modification to the currently dirty worktree is included in this planning step.
