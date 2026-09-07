# Whole-project remediation plan

## Objective

Implement all confirmed findings from the completed repository review across the FastAPI
backend, React/Electron Studio, WinUI Studio, process supervisors, Linux setup scripts, and
installer tooling. Preserve existing user-owned working-tree changes, keep the Studio UI as the
primary control surface, and add focused regression coverage for every changed contract.

## Confirmed decisions

- Replace unauthenticated native media access with authenticated issuance of short-lived,
  HMAC-SHA256-signed media URLs shared by Electron, browser Vite, and WinUI.
- Default signed URL lifetime to 15 minutes, make it configurable from 1 to 60 minutes, and have
  clients renew URLs before expiry. Sign the canonical path and sorted query parameters so a URL
  cannot be repurposed for another project, resource, path, preview size, or render workload.
- Use `EDMG_MEDIA_SIGNING_SECRET` as the preferred signing key. If absent, derive a
  domain-separated key from the configured backend bearer token; if authentication is explicitly
  disabled and neither exists, use a process-local random key. Issue with only the current key and
  optionally accept `EDMG_MEDIA_SIGNING_SECRET_PREVIOUS` during rotation.
- Disable legacy `EDMG_BACKEND_PUBLIC_MEDIA_GETS` behavior by default. Keep an explicit
  compatibility opt-in, but never let it bypass path containment or preview-budget validation.
- Add durable integer project revisions and compare-and-set persistence. Stale interactive writes
  return HTTP 409 with the current revision; background jobs merge only the fields they own and
  retry bounded compare-and-set conflicts instead of replacing newer project state.
- Enforce conservative configurable preview budgets:
  - All previews: each dimension at most 2048 and at most 4,194,304 pixels per frame.
  - Standard segments: at most 10 seconds, 12 FPS, 120 frames, and 125,829,120 pixel-frames.
  - Diffusion segments: at most 5 seconds, 8 FPS, 40 frames, 1024 per dimension, 30 steps, and
    41,943,040 pixel-frames.
- Lower WinUI's temporary video spool limit from 20 GiB to a configurable 4 GiB default, reject
  oversized known lengths before copying, and abort bounded copies when the length is unknown.

## Workstreams

### 1. Secure media, metadata, previews, and cancellation

- Add a small signing/verification component to backend security configuration:
  - Generate relative signed URLs through an authenticated project-scoped batch endpoint.
  - Accept signatures only on allowlisted project media routes and only for GET/HEAD.
  - Use URL-safe signatures, constant-time comparison, integer expirations, canonical query
    serialization, strict clock/TTL validation, and identical authorization for byte ranges.
  - Apply `Cache-Control: no-store` and existing security headers to signed responses.
- Harden every media route independently of middleware:
  - Resolve uploaded audio and requested project files against the canonical project directory.
  - Reject absolute paths, traversal, symlink escapes, malformed encodings, and metadata-derived
    paths outside the project.
  - Keep range, HEAD, audio, output, frame-preview, and segment-preview behavior intact.
- Replace arbitrary autosave/recovery metadata replacement with bounded, validated patches:
  - Reserve server-owned identity, revision, audio filename, artifact, and path-bearing fields.
  - Allow only JSON-compatible user-editable metadata with depth and serialized-size limits.
  - Merge approved fields without deleting unrelated current metadata.
- Centralize preview validation and reject requests that exceed dimension, duration, frame-count,
  step-count, or aggregate pixel-work budgets before decoding or rendering begins.
- Change terminal job updates to conditional active-state transitions so canceled jobs cannot
  later become succeeded or failed. Prevent canceled render workers from publishing final project
  mutations or output registration.

### 2. Revisioned project persistence

- Extend project documents with a backward-compatible revision field; migrate legacy documents to
  the initial revision on read without losing unknown data.
- Add keyed per-project synchronization around read/migrate/compare/write sequences while retaining
  atomic file replacement.
- Make mutations pass an expected revision, increment exactly once on success, and raise a typed
  conflict containing the current revision when compare-and-set fails.
- Map stale mutations to a stable HTTP 409 response and expose the new revision in project payloads
  and mutation responses.
- Update autosave, recovery, project editing, uploads, plans, renders, and other state-changing
  routes to use the revision contract.
- Replace stale whole-project background saves with ownership-aware merge functions for job status,
  generated artifacts, analysis, plans, and render outputs. Use bounded CAS retries only where the
  merge is safe and deterministic.

### 3. Studio client reliability and contract adoption

#### React/Electron

- Add authenticated signed-media URL helpers and an abort-aware renewal hook. Migrate Timeline,
  OverlayStage, Outputs, Review, Render, Workspace, ProjectJobsPanel, and the audio-reactive
  workbench away from unsigned URLs and full-file audio blob downloads.
- Preserve playback position and play/pause state when a long-lived media element renews its URL;
  revoke temporary object URLs on replacement and unmount.
- Carry project revisions through save/autosave/recovery calls, surface 409 conflicts as an explicit
  reload-or-retry state, and refresh the current project after successful writes.
- Replace overlapping `setInterval` job polling with the existing abort-aware,
  completion-scheduled adaptive polling primitive.
- Correct `Models.tsx` to consume Electron's `{ ok, canceled, paths }` picker result and reject
  empty selections without coercing the result object into a path.
- Key backend-scoped providers/state by the normalized backend URL so switching endpoints aborts
  old requests and clears projects, jobs, media URLs, health, and cached credentials from the
  previous backend.

#### WinUI

- Consume the same signed-media issuance contract for native playback and preview requests.
- Enforce the configurable spool ceiling using early length checks and bounded streaming, delete
  partial temporary files on every failure/cancellation path, and keep byte-range playback working.
- Make backend-token cache invalidation process-wide so `Save`/clear operations immediately affect
  existing providers; preserve the current user-owned edits in
  `WindowsBackendTokenProvider.cs`.
- Distinguish authentication, transport, cancellation, and backend-health failures on Setup and
  marshal all resulting UI state changes onto the UI dispatcher.

### 4. Process, supply-chain, and installer hardening

- Go supervisor:
  - Write managed-backend state only after readiness succeeds.
  - On timeout or readiness failure, terminate the exact child process tree, await exit, remove
    only matching state, and return the original readiness error with cleanup context.
- Tk launcher:
  - Keep process and network work on worker threads, but route every control mutation and dialog
    through `root.after`.
  - Guard callbacks after window disposal and restore button/busy state on success and failure.
- Linux setup:
  - Replace Ollama `curl | sh` with a pinned release download verified by SHA-256 before execution.
  - Track the exact launched Ollama PID rather than using broad `pkill -f`.
  - Pin ComfyUI and custom-node repositories to reviewed commits, install from checked-in
    constraints/lock inputs rather than mutable live requirements, and require checksums for model
    and bootstrap downloads.
  - Download to temporary files, verify before extraction/execution, atomically publish successful
    artifacts, and provide explicit version/checksum overrides for intentional upgrades.
- Python installer:
  - Resolve the requested target interpreter once and use it for venv creation, package
    installation, version verification, imports, and smoke checks.
  - Fail clearly if the requested interpreter or resulting environment does not match the
    requested version.

## Files and tests

Primary implementation surfaces include:

- Backend: `security.py`, `app.py`, `api/routers.py`, `schemas.py`,
  `store/projects.py`, and `store/jobs.py`.
- React/Electron: `src/components/api.ts`, project/media hooks and providers, direct media
  consumers, `Models.tsx`, and `App.tsx`.
- WinUI: media pipeline/session classes, backend API/token services, and Setup page code.
- Operations: `tools/edmgctl/internal/support/support.go`, `tools/launcher_gui.py`,
  `scripts/setup_linux_ollama.sh`, `scripts/setup_linux_comfyui.sh`, pinned dependency metadata,
  and `scripts/edmg_installer.py`.

Add or extend focused tests for signature issuance and tampering, traversal/symlink rejection,
preview budgets, metadata reservations, revision conflicts and migrations, safe job cancellation,
media renewal, backend switching, polling serialization, picker behavior, bounded WinUI spooling,
token invalidation, supervisor cleanup, UI-thread marshaling, setup checksum enforcement, and
target-interpreter verification.

## Validation

1. Run focused backend security, autosave/recovery, project-store, job-control, and preview tests.
2. Run focused Vitest coverage for API/media hooks, polling, Models, backend switching, and media
   consumers; then run frontend lint, typecheck, and the full UI suite from `studio\edmg-studio`.
3. Run WinUI media, token, and Setup tests plus the existing .NET build/test targets.
4. Run Go support-package tests, launcher tests, installer tests, and non-mutating shell syntax or
   repository-provided lint checks.
5. Run the pinned full backend pytest scope and repository Python scope. Treat only the previously
   documented incomplete local Director pnpm store as an environment limitation if it remains
   reproducible and unrelated to the changes.

## Execution notes

- Inspect the working tree before each workstream and patch around user-owned changes; never revert,
  overwrite, stage, or commit unrelated work.
- Reuse current response/error helpers, adaptive polling, atomic writes, and conditional job
  transitions rather than introducing duplicate infrastructure.
- Keep all new backend capabilities reachable from Studio UI clients; do not leave a curl-only
  implementation.
- Do not run host-mutating Linux installers, download large models, publish releases, or invoke
  cloud/GPU rendering during validation.




## Also include

Using the current Studio code and without any hosted, proxy, mock, or still-slideshow fallback, run one short low-resolution internal CUDA storyboard-full-motion proof shot that uses FLUX for keyframes and project-wide continuity. Recheck the active hardware/pagefile headroom, backend command line, selected project, and model readiness first. Validate the resulting MP4 with codec, duration, audio mux, sampled-frame motion, hash, and provenance evidence; state clearly whether temporal motion was actually produced and preserve all existing worktree changes.
