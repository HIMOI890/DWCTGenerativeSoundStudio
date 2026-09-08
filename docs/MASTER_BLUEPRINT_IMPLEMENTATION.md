# Master Blueprint implementation evidence

Status: partial implementation; no master-blueprint milestone is complete.

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
| Director / renderers | Typed persistent Story Bible/SceneSpecs; revision-checked saves; separate Hunyuan/LTX/external prompt compilation with source hashes; WinUI theme/style, Advanced scenes editor and prompt preview | Eight new regression tests; native save and prompt preparation exercised. Qwen3-VL, qualified Hunyuan/LTX runtime integration and fresh artifact proof remain open |
| DAW / production | Existing functionality retained | Mixer, isolated VST3, recording, automation and later production milestones remain open |

## Verification on 2026-09-08

- Native Core: **280 passed**. Existing MSTest analyzer warnings remain in the test project.
- WinUI Release x64: **build succeeded, zero warnings and errors**.
- Full backend scopes after Director additions: **603 passed, 3 skipped**. Skips document Windows FFmpeg/NumPy analysis instead of Librosa JIT. A first rerun failed fixture setup because an old pytest temp directory was inaccessible; a fresh explicit `--basetemp` resolved that environmental error.
- Earlier same implementation pass: frontend lint/typecheck and **173 tests in 41 files** passed; repository scope runner exited zero.
- Packaged `winapp run` produced a responsive window titled `EDMG Studio`; controls were inspected with Windows UI Automation. The current validation backend is isolated on port 7889 with a temporary Studio home, separate from user projects.
- Native project creation, audio/video track insertion, undo/redo, reopen and exact clip move to sample `24001` were observed in persisted backend state. This is editor interaction evidence, not synchronized playback or generated-video evidence.
- Latest native regression project `7d9baac638254819ab56d705c9e5aa87`: Add Clip preserved the audio track at revision 4. Native Director controls saved the Story Bible and scene `arrival` at project revision 5 / Story Bible revision 2, then displayed a prepared Hunyuan prompt. App PID 25932 was responsive with title `EDMG Studio`.
- The three task-created `.test-master-editor-01`, `-02`, `-03` folders remain untracked because automatic approval review rejected their cleanup as blocked by policy. No cleanup workaround was attempted.
- No model/profile is qualified by these tests. No new generated temporal artifact, GPU memory tier, external provider submission or release installation has been accepted.

## Next internal-engine work

Qwen3-VL dense-model adapter implemented in `services/qwen_director.py`: lazy local-only Transformers inference, explicit memory limits, deterministic generation, schema validation, and draft-only output. Proposal validation rejects scene-set/timing, Story Bible, analysis-revision and locked-appearance changes. The generation route now submits persistent idempotent `qwen_director` jobs; dispatch forces subprocess execution and fails rather than falling back into the API process. The worker rechecks weight shards and available CPU/CUDA memory before loading. Seventeen focused Director/worker tests pass, including queue replay, conflicting operation IDs, missing-model rejection and no project mutation on submission.

The Qwen catalog/install entry is now pinned to official repository revision `0c351dd01ed87e9c1b53cbc748cba10e6187ff3b`. It uses the existing internal snapshot download flow and the `director` model folder. Transformers installed-state validation requires nonempty metadata, the expected model type, and structurally valid safetensors shards. Missing chat templates and missing weights are rejected. All 17 model download tests pass; the complete backend rerun passes **611 tests with 3 documented Windows skips**.

Remaining immediate integration: native Generate/review/apply controls, serialize competing model loads, and verify cancellation and inference with actual installed weights. The pinned model has not yet been downloaded or qualified. The new worker memory admission check is conservative and is not hardware qualification evidence. The current running backend has not been restarted for this catalog change yet.

Persist typed Story Bible and SceneSpecs, compile distinct renderer prompt packages, then integrate Qwen3-VL-8B and HunyuanVideo-1.5 into existing workers/model installation/conductor contracts. Surface the work in WinUI. Keep external providers available and require explicit fallback policy. Never infer 6 GB generation support from a preflight or substitute still assembly for temporal model output.
