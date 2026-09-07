# Whole-project remediation status

Baseline: `codex/Unified`, `b2d8ac4d9b92aef297b055b4cb4d1e6a629992ff`.
Pre-existing work: root `# Whole-project remediation plan.md`, preserved byte-for-byte (SHA256 F098BD31BF81F708E23BF028A33CEC43D192764E4D3A30B18F4276282E6E8255).
The user subsequently authorized a checkpoint commit and push before continuing implementation. This checkpoint is not full acceptance. No installers, large model downloads, or pagefile changes have been run.

| Finding | Implementation / validation status |
|---|---|
| WinUI token merge damage and in-flight invalidation | Repaired; Core suite 258 passed; native application builds |
| Signed media issuance, authentication, rotation, containment | Integrated; focused media/security 21 passed; broader edge audit pending |
| Preview budgets and metadata patch validation | Integrated; additional boundary/containment audit pending |
| Revision enforcement and background ownership merges | Integrated; focused revision/publication tests pass after legacy-field repair; full suite rerun pending |
| Cancellation versus terminal publication | Conditional terminal writes and guarded publication added; broader race audit pending |
| Electron media renewal, switching, polling, revisions, picker | Integrated; full UI suite 170 passed; auth race audit pending |
| WinUI media, spooling, Setup and status surfaces | Core suite passes; remaining integration audit pending; retained 512 MiB default |
| Go supervisor | Platform-specific process cleanup split; updated readiness/child-tree regressions; rerun pending |
| Tk dispatcher, Linux pins, installer interpreter | Focused operations baseline 25 passed / 2 interpreter failures; interpreter fix applied; rerun pending |
| Full client and Python suites | UI 41 files / 170 tests passed; native build succeeded; full backend and repository acceptance pending |
| Isolated FLUX + internal temporal CUDA proof | Not run; readiness inspection started; no artifact acceptance claimed |

Baseline focused backend security/autosave/job-store tests: 18 passed.
Baseline WinUI Core token/media selection: 24 passed; those tests do not compile the Windows credential provider.
Installed validation SDK: .NET 10.0.400, invoked directly; repository SDK pin unchanged.
Native application build: succeeded, zero warnings and errors.
Current full UI command: `pnpm run test:ui`, 170 passed.
Full backend first pass: 34 failed / 498 passed; revision and legacy-schema fixtures corrected; rerun still in progress. FFmpeg-dependent environment failures require investigation.
Focused revision/timeline/TensorRT rerun: 84 passed / 2 old cancellation expectation failures; tests now assert immutable terminal state and no canceled worker project publication. Further rerun pending.

Fresh initial hardware inspection: RTX 4050 Laptop GPU, 6141 MiB VRAM (10 MiB used), NVIDIA driver 610.88. Pagefile allocation 65536 MiB; no backend process was observed. Model readiness and actual CUDA temporal generation remain unverified.

Detailed temporary test logs are under `C:\DWCT-Temp\remediation-*.log`. Final report will distinguish passed checks, baseline/environment failures, introduced regressions, and unverified acceptance criteria.

## Second checkpoint (user requested commit and push, then continue)

First checkpoint `f902a28fad6e988444edfa37cd8180a3d7bbff8e` was pushed and verified equal to the remote branch.

Additional integration: native signed-media origin and backend-switch checks; preview-kind contract; response-header revision capture; native security/limit diagnostics; Electron shared token lookup with invalidation protection; recursive metadata merging; compositor asset containment; finite diffusion scalar validation; preview cache and timeline containment; canceled terminal responses; concrete planner-text fallback.

Verification since first checkpoint:
- Backend full package: 532 passed with installed FFmpeg 8.1.1 on test-process PATH (`remediation-backend-ffmpeg.log`).
- Repository scope: exit 0, 149 passed / 4 intentionally gated live-model tests skipped (`remediation-repo-final.log`).
- WinUI Core: 262 passed, including unknown-length spool bounds, playback replacement cleanup, cross-origin issuance, and backend switching (`remediation-native-expanded.log`).
- Electron focused media/API: 18 passed, including failed renewal/retry, active playback restoration, unmount cancellation, and superseded backend responses.
- Electron lint and typecheck: exit 0.
- Go support: exit 0.
- Git Bash syntax checks: ComfyUI, Ollama, HF bucket, S3 cache setup scripts exit 0; installers were not executed.
- Latest backend contract selection: 23 passed, including symlink escape rejection and nested metadata preservation. Additional preview entrypoint wiring after that selection requires a final rerun.

The repository scope helper performed its documented frozen CPU sync and removed the optional ruff package from that test environment. No dependency inputs were changed to suppress a failure. The source runtime reports torch 2.11.0+cpu and CUDA unavailable; installed model folders alone do not establish CUDA proof readiness. Real temporal MP4 proof remains unverified, with no generation claimed.

## Acceptance rerun after second checkpoint

- Backend: 538 passed, one third-party Starlette deprecation warning (`C:\DWCT-Temp\remediation-backend-acceptance.log`).
- Electron: 41 test files / 173 tests passed; lint and typecheck exit 0 (`remediation-ui-acceptance.log`, `remediation-lint-acceptance.log`, `remediation-types-acceptance.log`).
- WinUI application: SDK 10.0.400, build succeeded with zero warnings and errors (`remediation-winui-build-final.log`). Repository SDK pin unchanged.
- WinUI Core: 262 passed in the expanded regression run.

Isolated readiness evidence: `C:\DWCT-Temp\cuda-proof-20260907-063356\readiness.json`. Source backend launched on loopback port 17863 with a separate data directory, model downloads disabled, worker autostart disabled, and configured concurrency 1. Proof project `91022ecab05c44c9ae09acf717f5d184`, revision 1. Hardware endpoint reports `backend=cpu`, `available_backends=[cpu]`, and `cuda_runtime_ready=false`; the source interpreter reports `torch 2.11.0+cpu`. No inference job was queued. The internal CUDA proof is blocked on a CUDA-capable runtime for the current source. FLUX/SVD folder presence does not verify model load, temporal motion, MP4 muxing, handoffs, or cache provenance.

External shared-worktree commit `6e73ff151eeed4735be3bdaf49915d6822d09d69` (`CUDA work`) added `# Unified AI Planner, Automatic Key.md` and was already pushed. It was preserved; its contents are a separate document, not newly adopted implementation scope.
