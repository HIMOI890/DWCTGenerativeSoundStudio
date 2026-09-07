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
