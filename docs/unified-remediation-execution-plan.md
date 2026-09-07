# Combined remediation and unified Studio workflow plan

This is the working plan for the remediation already in progress and the unified-planner workstream added by the user on 2026-09-07. The source documents remain unchanged:

- [Whole-project remediation plan](../%23%20Whole-project%20remediation%20plan.md).
- [Unified AI Planner, Automatic Keyframe Scheduling, and Adaptive Render Engine](../%23%20Unified%20AI%20Planner,%20Automatic%20Key.md).
- [Remediation implementation and test evidence](remediation-status.md).

The user's request to add the second document changes this plan. It does not mark the new features implemented or turn the document's instructions into an independent execution request. Existing remediation implementation remains authorized. The unified-planner phases below are planned work; their acceptance criteria must be met before claiming completion.

## Requirements carried across both workstreams

- Preserve existing storyboard save/reload behavior, structured setting and shot type, character/style locks, exact end-state/start-state handoffs, and all user-authored files and timeline content.
- Keep backend scheduling and renderer selection canonical. Electron and WinUI consume the same contracts and must agree for the same project, revision, and intent.
- Reuse existing renderers, stores, queues, model integrations, continuity helpers, transport, polling, signing, and revision mechanisms.
- Keep the WinUI default spool limit at 512 MiB with its configurable override.
- A generated schedule is a draft until the user approves it. Regeneration and variant switching do not modify the active timeline.
- Internal temporal rendering must never silently switch to hosted, proxy, mock, CPU, cached-output substitution, or still-slideshow generation. Expose the actual route and provenance.
- Preserve specialist backends and advanced features when consolidating UI. Style is creative direction, not a separate execution route.
- No large model downloads, pagefile changes, installer execution, or release publication without a separate request. Checkpoint commits and pushes have been explicitly requested during remediation; record each checkpoint accurately.

## Delivery order and finding-to-test checklist

| Phase | Deliverable | Depends on | Verification and acceptance |
|---|---|---|---|
| R1 | Authenticated signed-media issuance, key rotation, canonical containment, preview budgets, safe metadata autosave/recovery | Existing signed-media clients and store helpers | Tampering, expiry, malformed values, rotation, GET/HEAD/ranges, symlinks, standard/diffusion bounds, reserved fields, bounded nested merges |
| R2 | Revision preconditions, migration preservation, owned worker updates, immutable terminal jobs, cancellation/publication coordination | R1 and synchronized stores | Missing/stale revisions, multipart and JSON contracts, cross-store concurrency, ownership conflicts, cancellation before/after publication |
| R3 | Electron/WinUI integration and diagnostics | R1–R2 | Media renewal and retry, playback position/state, unmount/backend-switch cancellation, scoped state, picker and polling, token races, bounded spool cleanup, Setup classifications, settings diagnostics |
| R4 | Operational and installer verification | Existing supervisor/dispatcher/pin/interpreter implementations | Go readiness and exact-child cleanup; Tk dispatcher/disposal; checksum-before-execution and owned PID policy; dependency inputs; one target interpreter; non-mutating shell syntax |
| U1 | Canonical structured production packets and unified Analyze and Build Plan operation | R2–R3 | Analysis-to-visual rationale, backward-compatible scene fields, concise operational prompts regenerated from structured edits, both client contracts |
| U2 | Backend schedule compiler and versioned per-variant drafts | U1, shared transport, existing analysis | Deterministic anchors/motion/camera/markers; musical event selection; smoothing, clamps and deduplication; non-24-FPS transport; source revisions and provenance; absent/unstable inputs |
| U3 | Draft review, approval and atomic application to owned timeline objects | U2, R2 | Draft generation never changes timeline; stale project/schedule revisions conflict; stable IDs; user-owned and locked keys preserved; explicit reset confirmation |
| U4 | Adaptive Studio Engine intent and resolved execution plan | U2–U3, verified model/device capabilities | Genuine temporal eligibility, internal-only restrictions, incompatible combinations, explicit selected stages/models/reasons, style affecting prompts rather than route selection |
| U5 | Electron and WinUI planner/engine UI parity | U1–U4 | Shared schedule summaries, review and apply, save/reload/reopen, identical resolved route, one shared engine state, Advanced stage controls, preserved specialist capabilities |
| V1 | Focused regressions followed by full validation ladder | Changed workstreams | Frozen backend and repository Python scopes with unique temporary directories; Electron lint/typecheck/full suite; WinUI Core and application build with actual SDK recorded; Go and operations tests |
| V2 | Isolated internal FLUX → temporal CUDA motion proof | CUDA-ready current-source runtime and installed models | New two-scene MP4, fixed seeds, project continuity, generated test-tone mux, codec/resolution/frame/duration/audio evidence, sampled-frame motion analysis, exact handoffs/keyframe continuity, hash/jobs/device/model/cache provenance |

R1–R4 implementation and verification are in progress; their exact evidence and exceptions live in the remediation status document. U1–U5 have been added to the plan and are not yet implemented. V2 is currently blocked: the tested source runtime has CPU-only PyTorch and reports `cuda_runtime_ready=false`. A visible GPU and model folders do not satisfy that prerequisite.

## Unified planner and schedule contract

Each scene's production packet includes timing and musical-section identity; creative intent and rationale; subject, setting, action, shot, camera and environmental motion; transition and continuity locks; start/end states; a concise operational positive prompt; negative prompt; engine/model hints; anchor instructions and motion intensity. Structured fields remain the source of truth, and legacy fields remain readable.

Each canonical plan variant gains a versioned `schedule_draft` with:

- Source project, analysis and plan revisions; schedule revision; schema version; selected output FPS; canonical duration; generation timestamp and provenance.
- Prompt anchors, visual/key-image anchors, camera keys, subject/environment motion and engine-parameter keys, musical markers and transition cues.
- Stable point identifiers, ownership/source identifiers, analysis cue and human-readable reason for each point, continuity constraints and validation warnings.

Compile or regenerate the draft after generation, regeneration, Planner Lab import and material scene edits. Always anchor scene boundaries. Add meaningful section/event anchors, use lower density in stable passages, and apply normalization, attack/release smoothing, trigger thresholds, artistic remapping and parameter clamps. Use the shared seconds/frame/beat transport instead of hardcoded 24 FPS. Propagate exact scene handoffs and character, setting, palette, screen-direction and style locks. Incomplete analysis produces a valid section/energy draft with explicit missing-input warnings; never invent transcript evidence.

Plan generation/import responses include schedule summaries so neither client duplicates scheduling logic. Schedule regeneration changes only the selected variant's unapproved draft. Legacy projects normalize into drafts on load or regeneration without modifying their active timelines.

## Review and approval behavior

AI Planner Lab and Workspace show enhanced analysis, structured prompts, schedule summary counts, a readable musical timeline, warnings and expandable cue-to-visual explanations. The primary action is **Approve Plan and Apply Schedule**. It identifies the schedule revision and expected project revision and atomically updates planner-owned prompt, visual anchor, motion/parameter, camera and marker/transition content.

Preserve user tracks and manually authored or explicitly locked keys using stable ownership/source IDs. Provide **Regenerate Draft**, **Reset Planner-Owned Schedule** with confirmation, and **Open in Timeline**. Reactive Lab remains an optional advanced remapping editor. Switching variants changes the preview only. Saved native scene edits regenerate the backend draft.

## Adaptive Studio Engine behavior

Expose one render-intent contract: desired result (full-motion video, still sequence, edit existing output), quality, aspect ratio/resolution, output FPS, optional model/route overrides and approved schedule revision. Preflight resolves eligible execution stages from scene requirements, true-motion needs, installed models, verified device/VRAM capabilities and quality/speed/continuity priorities. The response reports stages, models, keyframe renderer, temporal renderer, capability evidence, allowed fallbacks, incompatibilities and understandable selection reasons.

The primary Render surface has one preflight and Render action. Advanced controls are grouped by keyframe/image generation, temporal motion, camera/audio modulation, interpolation/finishing and model/runtime overrides. Existing internal, TensorRT, SVD, AnimateDiff, ComfyUI, hosted, still, timeline, conductor and sequencer capabilities remain supported under shared state. If no draft is approved, preflight offers approval/application instead of silently choosing a default timeline.

## Added acceptance cases

- Deterministic schedules from sections, beats, energy and scene boundaries; ordered/deduplicated points within duration; valid exact handoffs; seconds/frame consistency at multiple output frame rates.
- Missing transcript/BPM, unstable BPM, silence, short/long audio, overlapping scenes and malformed provider output yield safe drafts and actionable warnings.
- Regeneration changes only draft state. Approval updates only planner-owned objects. Stale draft/project revisions cannot overwrite later timeline edits.
- Full-motion intent selects a real temporal engine when eligible; unavailable combinations fail clearly. Still/keyframe assembly is never reported as temporal motion. Internal-only intent never silently selects hosted/proxy/mock/synthetic execution.
- Both clients show drafts without making them active, approve the selected variant in one action, retain manual content, and show one consistent Studio Engine/Advanced state. Both reopen the same saved project/revision and display the same resolved engine.
- End-to-end: analyze a real project, generate variants, review one and apply all tracks without Reactive Lab or manual keyframe entry; inspect prompt/anchor/motion/camera/marker tracks; render and verify approved schedule revision, actual engine/models, cache status and provenance in artifact metadata.
- Verify Electron and packaged WinUI against the same project and revision. Unit tests and source builds do not substitute for live packaged-client or CUDA artifact acceptance.

## Completion reporting

Keep implementation, passing automated checks, live verification and blocked prerequisites separate. Report exact changed files, commands/results, actual SDK, preserved pre-existing work, checkpoint SHAs, proof paths and every unverified behavior. A blocked CUDA proof remains blocked even if all non-GPU tests pass. Adding this workstream to the plan is not an implementation-complete claim.
