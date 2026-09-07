# EDMG Studio (v1.2.0)

A desktop-style "studio" application:

- **Electron** shell + **React** UI
- Local **FastAPI** backend for projects, assets, planning, rendering, and outputs
- Bundled **EDMG Director** MCP sidecar for ChatGPT connector workflows and Studio-native directing handoff
- Integrates with:
  - **Studio internal renderer** as the default built-in render path
  - **ComfyUI** as an optional still/motion render sidecar (local or remote)
  - In-process **AI providers** for planning/transcription/features (`nemotron_cloud` by default,
    with optional local Ollama fallback using `qwen3:8b`)
  - Optional external **AI service** over HTTP when you want to separate that workload
  - **OpenClaw** only as an optional operator/automation shell around Studio, not as a required runtime dependency
  - **EDMG Core** (enhanced-deforum-music-generator) for Deforum template/export (optional but recommended)
  - **AWS** + **Lightning.ai** bundle scaffolding

## Quick start

### Prereqs
- Node.js `20.19+` or `22.12+` (Node 22 LTS recommended; `.node-version` is provided)
- Python 3.12 (pinned by the repository `.python-version`)
- `uv` 0.11.28 for source development; packaged apps include the backend and
  require neither Python nor uv
- Git LFS 3.x for Git-managed large assets (`git lfs install` after cloning).
  Runtime model snapshots remain under Studio Home and use the pinned
  `hf-transfer`/`hf-xet` download clients instead of Git LFS.
- FFmpeg on PATH for dev checkouts, or the bundled Studio FFmpeg for packaged builds (used for MP4 assembly)
- ComfyUI only if you want ComfyUI-backed still or motion workflows (default `http://127.0.0.1:8188`)
- Planning/transcription run **in-process** by default through the selected provider; no separate AI server is required for the normal Studio path.
- OpenClaw is optional and external to the core Studio stack. Studio setup, packaging, planning, and rendering do not require it.
- EDMG Core is included by the default Studio backend bundle/install target

### Backend
```bash
uv lock --project python_backend --check
uv sync --project python_backend --frozen \
  --extra cpu --extra core --extra audio --group test --group lint
uv run --project python_backend --frozen \
  --extra cpu --extra core --extra audio \
  python -m edmg_studio_backend serve --host 127.0.0.1 --port 7863
```

Replace `cpu` with exactly one of `directml` (Windows) or `cuda` when that is
the environment you are validating. PyTorch indexes are fixed in
`pyproject.toml` and `uv.lock`; do not supply an index URL at runtime.

### Lightning backend helpers
From `studio/edmg-studio/`:

```bash
bash scripts/start_lightning_backend.sh
```

On managed Linux environments that require the active Python/conda environment,
let uv synchronize that environment from the same lock:

```bash
EDMG_BACKEND_ENV_MODE=active bash scripts/start_lightning_backend.sh
```

For NVIDIA machines, select the locked CUDA profile:

```bash
EDMG_BACKEND_ENV_MODE=active \
EDMG_BACKEND_ACCELERATOR_PROFILE=cuda \
bash scripts/start_lightning_backend.sh
```

Detached variant with PID and log files under `EDMG_STUDIO_HOME/logs/lightning-backend`:

```bash
bash scripts/start_lightning_backend_nohup.sh
```

Linux/Lightning Ollama sidecar helper:

```bash
EDMG_AI_OLLAMA_MODEL=qwen3:8b bash scripts/setup_linux_ollama.sh
```

That is the local Ollama fallback. To opt into the separate Ollama Cloud model
instead, authenticate and select it explicitly:

```bash
OLLAMA_SIGNIN=1 bash scripts/setup_linux_ollama.sh
EDMG_AI_OLLAMA_MODEL=nemotron-3-ultra:cloud bash scripts/setup_linux_ollama.sh
```

The helper now installs only checksum-pinned Ollama release archives. For an
intentional upgrade, override both `OLLAMA_VERSION` and
`OLLAMA_ARCHIVE_SHA256` (and `OLLAMA_ARCHIVE_URL` if you are not using the
standard GitHub release asset name).

Linux/Lightning S3 model-cache helper:

```bash
EDMG_AWS_MODEL_CACHE_BUCKET=your-edmg-model-bucket bash scripts/setup_linux_s3_model_cache.sh
```

### UI
```bash
corepack enable
pnpm install
pnpm run check:tooling
pnpm run dev
```

`corepack enable` is only needed once per machine if `pnpm` is not already on `PATH`. The package
manager version is pinned via `packageManager` in `package.json`.

To repoint the desktop/dev frontend at a different backend target without hand-editing the bootstrap
files, run one of these from `studio/edmg-studio/`:

```bash
pnpm backend:use managed 7863
pnpm backend:use external https://HOST
```

### Remote backend access security

Loopback development remains zero-configuration. A backend bound to a non-loopback host must use
bearer authentication unless `EDMG_BACKEND_ALLOW_INSECURE_REMOTE=1` is deliberately set for an
isolated development network. Configure the server with `EDMG_BACKEND_AUTH_TOKEN` and
`EDMG_BACKEND_AUTH_MODE=required`, then open **Settings → Desktop Backend → Backend Access
Security** in Studio and save the matching token. Electron encrypts the token with the operating
system credential service; browser-only Studio keeps it in memory for the current tab.

Use HTTPS for production remote targets. Project media GET routes retain a narrow, read-only
compatibility path so native `<audio>`, `<video>`, and image previews continue to work; project
metadata, settings, model operations, setup actions, and render control routes require the bearer
token.

## Versioning

- Canonical shipped desktop version: `studio/edmg-studio/package.json#version`
- Release staging copies that version into `studio/edmg-studio/release/staged-app/package.json`
- Canonical Windows release installers identify the version, `windows-x64` target, and immutable
  `cpu`, `directml`, or `cuda` backend profile; build them only through the repository `dist:win:*`
  scripts. A raw Electron Builder invocation deliberately emits an `unqualified` filename and is
  not a release package.
- Linux desktop artifacts identify the version, `linux-x64` target, and immutable profile and are
  built as `AppImage` packages through `pnpm run dist:linux` or `pnpm run dist:linux:cuda`.
- Use `pnpm run check:release-metadata` after staging if you want a direct version-propagation check

## Setup Wizard (no command line)

When you install the packaged app, EDMG Studio includes an in-app **Setup Wizard** (Sidebar → **Setup**) that:

- Uses an assisted Windows installer, so you can choose the **app install directory** instead of being forced into the default `C:\` path
- Lets you choose a **Studio Home** folder before large downloads, so project data, Electron data, ComfyUI Portable, and caches can live on a writable volume with verified free space
- Can check **Ollama** availability when the optional local AI fallback is selected
- Supports local **OpenAI-compatible** servers such as LM Studio or `llama.cpp` server through Studio Settings
- Lets you pull the default **local Ollama fallback model** (`qwen3:8b`) via a button when Ollama is the selected provider
- Installs the **backend runtime bundle** that powers the internal renderer
- Checks **ComfyUI** availability and can **download + extract ComfyUI Portable** on Windows when you want the optional ComfyUI path
- Verifies **FFmpeg** for MP4 assembly, preferring the Studio-bundled binary when present

This keeps the runtime UX like a DAW/game installer: click buttons, no terminal required.

On Linux, use the packaged `AppImage` or `pnpm run dist:linux` on a Linux host. The same Setup Wizard still manages Studio Home paths, but Ollama and ComfyUI are expected to be installed manually or pointed at existing services instead of using the Windows-only managed installers.

Linux/Lightning setup details, including active-env backend startup and ComfyUI
motion sidecar setup, live in [packaging/linux/README.md](./packaging/linux/README.md).

OpenClaw is not part of the required Studio install path. If you use it, treat it as an optional operator shell layered around Studio for automation or monitoring rather than as a dependency of the app itself.

Release/operator runbook:

- [Studio release runbook](../../docs/STUDIO_RELEASE_RUNBOOK.md)
- [Release checklist](../../RELEASE.md)
- [1.2.0 candidate known issues](../../docs/KNOWN_ISSUES.md)
- [Python toolchain and lock policy](../../docs/PYTHON_TOOLCHAIN.md)
- [Linux packaging notes](./packaging/linux/README.md)
- [GCP GPU VM deploy](../../docs/GCP_GPU_VM_DEPLOY.md)

Install/storage split:
- **Install directory**: where the packaged app itself is installed
- **Studio Home**: where projects, caches, Electron session data, portable tools, and large runtime payloads live

## Ports
- Studio backend: **7863**
- Managed EDMG Director MCP app: **3001**
- External AI service (optional): **7862**
- ComfyUI: **8188**

## EDMG Director with Studio

Studio now has a native **Labs → EDMG Director** page that combines planner and reactive workbenches for the selected project. In addition, the desktop app can launch the ChatGPT-oriented `edmg-director` MCP server as a managed sidecar on `127.0.0.1:3001`.

Useful env vars:

- `EDMG_DIRECTOR_HOST` (default: `127.0.0.1`)
- `EDMG_DIRECTOR_PORT` (default: `3001`)
- `EDMG_DIRECTOR_BASE_URL` (optional public/tunneled URL advertised to ChatGPT for widget asset loading)
- `EDMG_DIRECTOR_SPAWN` (`0` disables the managed sidecar)

For local Studio use, the native Director page does not require ChatGPT. If you want the ChatGPT app flow too, expose the managed sidecar over HTTPS and point ChatGPT at `/mcp`.

## Environment variables (Backend)
- `EDMG_STUDIO_HOME` (optional; preferred root for Studio storage)
- `EDMG_STUDIO_DATA_DIR` (default: `./data`)
- `EDMG_BACKEND_AUTH_MODE` (`auto`, `required`, or `disabled`; remote launch helpers use `required`)
- `EDMG_BACKEND_AUTH_TOKEN` (bearer token for protected backend routes; never place it in a frontend `VITE_*` variable)
- `EDMG_BACKEND_CORS_ORIGINS` (comma-separated trusted browser origins)
- `EDMG_BACKEND_CORS_ORIGIN_REGEX` (optional trusted-origin regex, primarily for managed cloud workspaces)
- `EDMG_BACKEND_PUBLIC_MEDIA_GETS` (`1` preserves read-only native media URL compatibility; set `0` when all clients use authenticated fetches)
- `EDMG_BACKEND_ALLOW_INSECURE_REMOTE` (explicit development-only override for a non-loopback backend without auth)
- `EDMG_AI_MODE` (default: `local`)
- `EDMG_AI_PROVIDER` (default: `nemotron_cloud`; alternatives: `openai_compat`, `ollama`, `rule_based`)
- `EDMG_AI_OLLAMA_URL` (default: `http://127.0.0.1:11434`)
- `EDMG_AI_OLLAMA_MODEL` (default local Ollama fallback: `qwen3:8b`; low-resource: `qwen3:4b`)
- `EDMG_AI_OPENAI_COMPAT_BASE_URL` (default: `https://integrate.api.nvidia.com/v1`)
- `EDMG_AI_OPENAI_COMPAT_MODEL` (default: `nvidia/llama-3.1-nemotron-ultra-253b-v1`)
- `EDMG_AI_OPENAI_COMPAT_API_KEY` (optional)
- `EDMG_COMFYUI_URL` (default: `http://127.0.0.1:8188`)
- `EDMG_COMFYUI_CHECKPOINT` (default: `sd_xl_base_1.0.safetensors`)
- `EDMG_FFMPEG_PATH` (optional override; packaged Studio prefers its bundled FFmpeg/FFprobe pair, while development falls back to PATH)
- `EDMG_FFPROBE_PATH` (optional advanced override; otherwise Studio resolves FFprobe beside the selected FFmpeg)
- `EDMG_AWS_MODEL_CACHE` (`1` enables S3-backed model cache/hosting)
- `EDMG_AWS_MODEL_CACHE_BUCKET` and `EDMG_AWS_MODEL_CACHE_PREFIX` (S3 bucket/prefix for model objects)
- `EDMG_MODEL_STORAGE_MODE` (`local_cache` keeps local files and mirrors them to S3; `cloud_only` stores supported models in S3 and restores them on demand)
- `EDMG_S3_ENDPOINT_URL` (optional S3-compatible endpoint)
- `EDMG_HF_BUCKET_MODEL_CACHE` (`1` enables Hugging Face bucket model cache/hosting)
- `EDMG_HF_BUCKET_ID` and `EDMG_HF_BUCKET_PREFIX` (HF dataset/bucket id and optional prefix)

If you need a lighter local planner for weaker CPUs or low-memory systems, set `EDMG_AI_PROVIDER=ollama` and `EDMG_AI_OLLAMA_MODEL=qwen3:4b`.

If you use an OpenAI-compatible gateway that exposes a different model alias than `nvidia/llama-3.1-nemotron-ultra-253b-v1`,
override `EDMG_AI_OPENAI_COMPAT_MODEL` to match that server.

S3-hosted model entries can use `source: "s3"` with either `s3_uri: "s3://bucket/key"` or `s3_key: "prefix/model.safetensors"` plus the configured model-cache bucket. Runtime resolution materializes supported single-file ComfyUI assets before rendering. Internal renderer entries use the same S3 path, but the object must be a `.zip`, `.tar`, `.tar.gz`, or `.tgz` archive containing a Diffusers snapshot with `model_index.json` at the archive root or inside one top-level directory.

## Recommended local model stack

- Planner default: NVIDIA Nemotron Ultra via `nemotron_cloud` (NIM)
- Local Ollama fallback planner: `qwen3:8b`, or `qwen3:4b` for lower-resource systems
- Broad still-image default: SDXL Base 1.0
- Fast still-image option: SD3.5 Large Turbo
- Reference still guidance: SD3.5 ControlNet Blur, Canny, and Depth
- Primary HF video backend: Wan2.2 TI2V 5B
- Short image-to-video fallback: SVD XT Img2Vid

Models → Imports also exposes a source-preserving migration for the recognized root-level legacy
TensorRT engines under the active Studio Home. Studio verifies and copies those files into the
canonical model-id bundle without deleting the originals. The engine copy remains visibly
incomplete—not installed or renderer-ready—until its ONNX assets, compiled profile, and matching
base-model metadata are verified.

## Hardware tiers

- Low-spec: `qwen3:4b` (Ollama) + SDXL Base 1.0
- Mid-range: Nemotron cloud or `qwen3:8b` + SDXL Base 1.0 + SD3.5 Large Turbo + SD3.5 Blur/Canny
- High-end: Nemotron cloud + SDXL Base 1.0 + SD3.5 Large Turbo + SD3.5 Blur/Canny/Depth + Wan2.2 TI2V 5B

If `EDMG_STUDIO_HOME` is set, Studio uses it as the root for:
- backend project data (`<studio-home>/data`)
- models (`<studio-home>/models`)
- Electron user/session data (`<studio-home>/electron`)
- caches and temporary files (`<studio-home>/cache`)
- logs (`<studio-home>/logs`)
- external tools (`<studio-home>/external`)

EDMG Core integration:
- If EDMG Core is installed in the same environment, Studio can:
  - Verify the core install
  - Export Deforum settings JSON per variant
  - Fetch the Deforum template

## Workflow
1. Create a project
2. Upload audio
3. Analyze + transcribe (in-process provider by default; optional external AI service)
4. Generate plan variants
5. Render with the internal renderer by default, or use ComfyUI optionally for supported still/motion workflows
6. Assemble MP4 (FFmpeg slideshow + audio)
7. Export Deforum settings (optional)

## Creator workflow features (2026 beta)

These source-candidate surfaces are documented here while the dedicated documentation relaunch
(P5-06) remains incomplete. Their presence in source is not packaged-release evidence.

### Understand — Music Graph v1

After **Analyze**, open **Workspace → Audio** to inspect Music Graph v1: tempo, beats, sections with energy/confidence, stems, semantic tags, and ASR lyric lines. The same graph powers Director, Render Conductor, timeline section markers, and live cue export.

- API: `GET /v1/projects/{id}/music_graph`
- UI: `UnderstandPanel` on Workspace (no separate Understand route yet; P2-05 partial)

### Review — variant compare and approval

**Sidebar → Review** compares rendered artifacts side-by-side, records approve/reject/cherry-pick decisions, and surfaces continuity warnings from the Conductor.

- API: `GET/POST /v1/projects/{id}/variant_review` (+ `/decision`)
- Continuity: `GET /v1/projects/{id}/render/conductor/continuity`

### Render Plan v1

**Render** includes a **Render Plan** panel for the stored Conductor plan: task DAG, cache keys, estimates, warnings, and genuine local or hosted engine routes. Legacy plans that reference the retired proxy route remain readable and guide users to configure a real renderer before refreshing.

- API: `GET /v1/projects/{id}/render/conductor/plan`
- Planning: new Studio requests explicitly allow only real Conductor engines

### Live cues and live assets

From **Workspace** handoff and **Review → Labs**, preview cue protocols compiled from Music Graph and bounded live-asset modulation packs.

- Cues: `GET /v1/projects/{id}/live_cues`
- Publish (OSC/MIDI/WebSocket): `POST .../live_cues/publish/start|stop`, `GET .../publish/status`
- Assets: `GET /v1/projects/{id}/live_assets`, `POST .../live_assets/modulation`

### Template handoff

**Workspace → Handoff** exports and imports versioned template packages (Visual DNA, director mode, stem modulation, and related project metadata).

- API: `GET /v1/projects/{id}/template_package/export`, `POST .../template_package/import`

### Release evidence

The release-evidence procedures write CycloneDX SBOM and SHA-256 checksum manifests under
`studio/edmg-studio/release/evidence/`. Generate them for the exact candidate being evaluated:

```powershell
cd studio/edmg-studio
pnpm run generate:release-evidence
pnpm run generate:release-evidence:dist
```

Those commands describe how to create evidence; documentation or stale files in that directory do
not establish that a current installer was built, signed, or qualified.

See [RELEASE.md](../../RELEASE.md) for signing (credential-gated), clean-machine smoke, and acceptance gates.

### API contract freeze (week of 2026-07-21)

The following routes are **frozen for beta integration** — response shapes are mirrored in `src/shared/api/contracts.ts` and covered by route/contract tests. Breaking changes require a schema version bump:

| Domain | Routes |
|--------|--------|
| System | `GET /v1/system/readiness`, `GET /v1/metrics/baseline` |
| Project durability | `GET /v1/projects/{id}/health`, recovery/autosave/timeline |
| Music Graph | `GET /v1/projects/{id}/music_graph` |
| Render Conductor | `GET .../render/conductor/plan`, `GET .../render/conductor/continuity` |
| Review | `GET/POST .../variant_review` |
| Live | `GET .../live_cues`, `GET/POST .../live_assets` |
| Templates | `GET/POST .../template_package/export|import` |
| Performer | `GET/POST .../render/performer/plan` |

### Baseline metrics (stub)

**Settings → System readiness** includes read-only baseline timing budgets. Full W7-04 evidence requires named-hardware benchmark runs.

- API: `GET /v1/metrics/baseline`
- Inject CI samples: `EDMG_BASELINE_METRICS_JSON='{"analysis":45000}'`


Studio's default render path is the **internal renderer** backed by the Studio backend runtime, local model installs, cache/history, and FFmpeg assembly.

Use ComfyUI only when you explicitly want one of the supported ComfyUI-backed still or motion workflows.

## Optional OpenClaw operator shell

If you want an external automation or operator surface, you can run OpenClaw alongside Studio.

Use it for things like queue triage, operator workflows, or sidecar automation against the Studio environment. Do not treat it as part of the required Studio runtime: Studio startup, setup, backend spawning, packaging, and rendering should all work without OpenClaw present.

## Optional ComfyUI motion rendering

Studio also supports **motion clips per scene** via two optional, local-friendly ComfyUI paths:

- **AnimateDiff (recommended for longer sequences)**  
  Requires `ComfyUI-AnimateDiff-Evolved` nodes. AnimateDiff supports *unlimited* animation length when you pass Context Options (sliding context windows). 

- **Stable Video Diffusion (SVD) img2vid (best for short clips / transitions)**  
  Requires `ComfyUI-Stable-Video-Diffusion` nodes (e.g. `SVDSimpleImg2Vid`). 

### Verify ComfyUI capabilities

From the Studio UI (Workspace), you’ll see availability checks (✓/×).  
Backend endpoint: `GET /v1/comfyui/capabilities` (uses ComfyUI’s `/object_info`). 

### Rendering motion

- Workspace → Render → Mode → **Motion (AnimateDiff)** or **Motion (SVD)**  
- Click **Enqueue motion scenes**, then use **Tick worker** repeatedly (or run a simple loop).

Outputs:
- Frames: `data/<project>/outputs/frames/...`
- Per-scene clips: `data/<project>/outputs/clips/...`
- Final concatenated video: `data/<project>/outputs/videos/variant_XX.mp4`
