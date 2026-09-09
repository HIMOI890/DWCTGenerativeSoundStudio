# Master Blueprint implementation evidence

Status: partial implementation; no master-blueprint milestone is complete.

## Workspace integration checkpoint — 2026-09-08

The user approved one connected Overview + Director, AI Planner, and Reactive Lab workflow, with automatically prepared downstream keyframes and one shared review/apply boundary. WinUI is the first implementation. This checkpoint is committed and pushed before launching the new UI, as explicitly requested; interactive acceptance is still pending at this checkpoint.

- Audio analysis prepares a persistent Workspace direction draft using the existing local planner and schedule compiler. It does not submit inference or provider jobs. Upload/reanalysis retains scene plans and authored timeline content; replaced analysis is archived and source fingerprints prevent stale draft application.
- Planner generation, scene edits, Planner imports, saved Director documents, and accepted Qwen proposals refresh the shared draft. Director, Planner, and Reactive views receive one scene/schedule projection with linked analysis/plan revisions, rational frame-rate metadata, sample strings, camera keys, motion keys, and cues.
- WinUI combines Overview and Director. Native scene fields edit the complete underlying document while retaining extension fields. Advanced saved-direction and Qwen controls remain available. Reactive Lab opens on automatically populated keyframes with native strength/zoom refinement, local recovery of unsaved refinements, and shared save/apply. Planner can open the generated Workspace plan immediately after analysis.
- Reactive refinements retain source IDs and timing, validate finite bounded values, reject locked-scene changes, preserve unknown extension fields, and survive reanalysis for matching point IDs. Unmatched refinements remain recorded with a placement-review warning. Applying the shared draft preserves user/locked timeline content through the existing ownership rules and records the timeline change in editor undo history.
- Queued local model workers now serialize their process lifetimes through one cross-process model-load gate. Lease renewal, cancellation while waiting/running, attempt fencing, process reaping, and prevention of duplicate inline fallback are covered. Qwen inference has cooperative stopping and live memory budgets. This gate covers dispatcher jobs, not unrelated GPU consumers or direct preview calls.
- A prior native validation of the worker slice observed cancellation of a validation-only Qwen queue item without project mutation. This was not model inference. The new merged Workspace controls have not yet been launched at this checkpoint.

Verification before push: WinUI Release x64 build passes with zero warnings/errors; native Core **283 passed** (existing MSTest analyzer warnings); repository **149 passed, 4 opt-in live-model skips**; backend **662 passed, 3 Windows analyzer skips**; frontend **173 tests in 41 files**, lint and typecheck pass. New Workspace domain/API files and integration tests pass targeted Ruff checks. Broad legacy backend Ruff findings remain outside this targeted lint claim. Endpoint integration tests substitute expensive audio feature extraction/transcription, so actual analyzed audio and native interaction still require live validation.

New Electron presentation, actual Qwen/Hunyuan temporal artifacts, model/hardware qualification, synchronized native audio, and the remaining master-blueprint milestones are still open. Existing Electron compatibility checks pass. This checkpoint does not claim completed editor or internal-engine milestones.

## Scope and provenance

- Working branch: `codex/master-blueprint-winui-first`.
- Inspected committed baseline: `51c6b7ec86c6207de32a1e54a634a41a16c660fd`.
- Existing unstaged native playback, navigation, preview, cancellation and timeline work was preserved. No reset, clean, staging or commit was performed.
- Governing specification: `E:/DWCTGenerativeSoundStudio/EDMG Studio Master Blueprint.md`.
- On 2026-09-08 the user requested finishing the current editor changes and then prioritizing the internal Director/renderer using both the master blueprint and `C:/Users/lanak/Downloads/EDMG_Studio_Unified_Renderer_Director_Blueprint.md`. The master blueprint and approved proven-hardware policy resolve conflicts. Remaining editor milestones are not waived or marked complete.
- WinUI remains the first client; new Electron presentation follows native acceptance.

## Acceptance matrix

| Area | Implemented | Evidence / remaining gate |
| --- | --- | --- |
| Project migration | Additive schema 3 migration through registry, existing backup preservation | Migration fixtures pass; comprehensive older-client rejection remains partial |
| Exact time | Decimal-string signed int64 samples; rational frame rates; ties-away rounding; drop-frame timecode | Golden unit tests pass; tempo/signature maps remain open |
| Commands | Atomic revision-checked grouped edits, operation receipts, lock checks, persisted 200-transaction undo/redo, legacy save adapter | Backend tests cover replay, conflict, reload, offsets, invalid groups and extension fields |
| Native controls | Shared undo/redo, audio/video tracks, add clip, exact sample move/split | Packaged app launched and responsive; native controls exercised against isolated backend |
| Media offsets | Audio/video split/trim preserve source offsets and rates; fractional resampling phase retained | Native and backend regression tests pass |
| Timeline viewport | Clip/camera event culling with overscan | Unit coverage includes 10,000 events; full 64-track p95 benchmark is not measured |
| Recovery | Atomic project persistence and existing recovery infrastructure retained | Timed/20-edit snapshots and ten-backup policy remain open |
| Native audio | Existing preview remains | C++ worker, WASAPI clock, synchronized audio/video and ten-minute continuity gate remain open |
| Director / renderers | Typed persistent Story Bible/SceneSpecs; revision-checked saves; separate Hunyuan/LTX/external prompt compilation with source hashes; WinUI theme/style, Advanced scenes editor and prompt preview; Director is now a Workspace mode in WinUI and Electron alongside AI Planner, Reactive Lab, and Storyboard; typed hardware-aware readiness resolution for Automatic/Fast/Quality/Maximum | Backend and UI regression suites pass; packaged WinUI Workspace exposes Director, AI Planner, and Reactive Lab modes; Director stages the selected storyboard, saves the shared project revision, prepares prompts, and reviews/applies drafts. Readiness now resolves Director/renderer/profile and lists installation, policy, and qualification blockers before model load; Director jobs persist the resolved mode, renderer policy, selected model, and readiness snapshot. The internal video boundary now has a lazy HunyuanVideo-1.5 Diffusers adapter contract for T2V/I2V, CPU offload, VAE tiling, guider-based CFG, frame normalization, and CUDA OOM reporting; queue-time admission rechecks the same readiness contract before any Hunyuan load. Qwen3-VL, qualified Hunyuan/LTX runtime integration and fresh artifact proof remain open |
| DAW / production | Existing functionality retained | Mixer, isolated VST3, recording, automation and later production milestones remain open |

## Verification on 2026-09-08

- Native Core: **280 passed**. Existing MSTest analyzer warnings remain in the test project.
- WinUI Release x64: **build succeeded, zero warnings and errors**.
- Full backend scopes after Director additions: **616 passed, 3 skipped**. Skips document Windows FFmpeg/NumPy analysis instead of Librosa JIT. A first rerun failed fixture setup because an old pytest temp directory was inaccessible; the successful rerun used a fresh isolated pytest temp root and bundled FFmpeg paths.
- Frontend lint, typecheck, and **173 tests in 41 files** passed after the integrated Workspace Director surface was added.
- Internal video model and selection regression slice: **22 passed** using fake Diffusers T2V/I2V pipeline boundaries; this verifies layout validation, distinct Hunyuan T2V/I2V loading, guider configuration, VAE tiling, frame normalization, and queue-time admission rejection. It is not model-generation evidence.
- Packaged `winapp run` produced a responsive window titled `EDMG Studio`; Windows UI Automation inspected the Workspace modes and found live `Director`, `AI Planner`, and `Reactive Lab` controls. The current validation backend is isolated on port 7889 with a temporary Studio home, separate from user projects.
- Native project creation, audio/video track insertion, undo/redo, reopen and exact clip move to sample `24001` were observed in persisted backend state. This is editor interaction evidence, not synchronized playback or generated-video evidence.
- Latest native regression project `7d9baac638254819ab56d705c9e5aa87`: Add Clip preserved the audio track at revision 4. Native Director controls saved the Story Bible and scene `arrival` at project revision 5 / Story Bible revision 2, then displayed a prepared Hunyuan prompt. App PID 25932 was responsive with title `EDMG Studio`.
- The three task-created `.test-master-editor-01`, `-02`, `-03` folders remain untracked because automatic approval review rejected their cleanup as blocked by policy. No cleanup workaround was attempted.
- No model/profile is qualified by these tests. No new generated temporal artifact, GPU memory tier, external provider submission or release installation has been accepted.

## Next internal-engine work

Qwen3-VL dense-model adapter implemented in `services/qwen_director.py`: lazy local-only Transformers inference, explicit memory limits, deterministic generation, schema validation, and draft-only output. Proposal validation rejects scene-set/timing, Story Bible, analysis-revision and locked-appearance changes. The generation route now submits persistent idempotent `qwen_director` jobs; dispatch forces subprocess execution and fails rather than falling back into the API process. The worker rechecks weight shards and available CPU/CUDA memory before loading. Seventeen focused Director/worker tests pass, including queue replay, conflicting operation IDs, missing-model rejection and no project mutation on submission.

The Qwen catalog/install entry is now pinned to official repository revision `0c351dd01ed87e9c1b53cbc748cba10e6187ff3b`. It uses the existing internal snapshot download flow and the `director` model folder. Transformers installed-state validation requires nonempty metadata, the expected model type, and structurally valid safetensors shards. Missing chat templates and missing weights are rejected. All 17 model download tests pass; the complete backend rerun passes **611 tests with 3 documented Windows skips**.

The next internal-engine gate is to serialize competing model loads and verify cancellation and inference with actual installed weights. The readiness resolver is now shared by the project API and both Workspace clients; it reports hardware tier, Director model/profile, renderer model/profile, blockers, warnings, and next actions without loading weights. Queue-time Hunyuan admission re-resolves that contract server-side, so a crafted request cannot bypass the Workspace state. Qwen3-VL-8B remains the only installable Director model. Qwen3-VL-30B-A3B, HunyuanVideo-1.5, and LTX-2.5 are visible as discovery-only catalog entries until their pinned install contracts and runtime qualification are complete. The pinned Qwen3-VL-8B model has not yet been downloaded or qualified. The worker memory admission check is conservative and is not hardware qualification evidence. The adapter tests use fake pipeline boundaries only; no real Hunyuan weights or temporal artifact are claimed.

Persist typed Story Bible and SceneSpecs, compile distinct renderer prompt packages, then integrate Qwen3-VL-8B and HunyuanVideo-1.5 into existing workers/model installation/conductor contracts. Surface the work in WinUI. Keep external providers available and require explicit fallback policy. Never infer 6 GB generation support from a preflight or substitute still assembly for temporal model output.
