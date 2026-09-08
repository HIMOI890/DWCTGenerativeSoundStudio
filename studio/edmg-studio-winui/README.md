# EDMG Studio — native Windows client

This directory contains the packaged WinUI 3 primary Windows frontend for EDMG Studio.
The established Electron/React client remains available for Linux and compatibility. Both clients
use the existing CUDA-capable FastAPI backend; WinUI does not duplicate inference or render logic.

## Native workflow coverage

The primary native workflow is implemented:

- Windows 11-style `NavigationView`, Mica title bar, light/dark theme integration, keyboard navigation, and accessible headings.
- Dashboard with live backend, project, runtime, storage, and AI-provider status.
- Projects library with empty, loading, error, refresh, create, and open states.
- Workspace with a native Windows audio picker and the authoritative create → upload → analyze/transcribe → plan-variants flow.
- Field-preserving lane/clip Timeline editing with JSON access, undo/redo, save, autosave, and recovery.
- Advanced Render settings for diffusion, hosted, CUDA, and TensorRT modes with backend preflight.
- Render Queue progress, pause/resume/cancel/retry actions, logs, and event diagnostics.
- Authenticated Outputs browsing/downloads and Review notes, traits, locks, and decisions.
- Model catalogue, license acceptance, install/restore/remove, promotion lanes, and packs.
- Settings for render routing, transcription, secret status, Foundry context, hardware, readiness, and metrics.
- Setup Wizard with runtime readiness, storage locations, safe cache fallback reporting, backend connection details, and Credential Locker token storage.
- Source, packaged, external, and healthy-source-attachment backend modes.
- Managed-process containment with a Windows Job Object, owned-process-tree shutdown, strict `2xx + {"ok":true}` health checks, listener ownership checks, retry isolation, cancelable startup, and crash status propagation.
- Runtime configuration compatibility with `runtime-defaults.json`, source `launcher_env.json`, `%APPDATA%\EDMG Studio\bootstrap.json`, and process-environment precedence.
- Native EDMG Studio branding: the canonical logo, atmospheric Studio and Workspace artwork, teal theme resources, branded package tiles, splash screen, and executable icon.

## Brand assets

The WinUI client treats the Electron product artwork as the canonical source. Regenerate the checked-in native package and in-app assets after changing that artwork:

```powershell
.\scripts\Generate-BrandAssets.ps1
```

The generator validates every output path before writing, copies the canonical logo/backgrounds into `Assets\Brand`, creates the scale-qualified MSIX graphics, and installs the existing multi-resolution EDMG Windows icon. The XAML theme provides distinct dark, light, and high-contrast resources; decorative artwork is disabled automatically in high-contrast mode.
- Canonical cache/storage environment mapping and persisted AI-provider environment mapping.

The obsolete proxy-render workflow is intentionally not exposed. Production renders use the
backend's supported render routes.

## Architecture

```text
EdmgStudio.WinUI (WinUI 3 / MSIX)
    ├── native pages and Windows integrations
    └── EdmgStudio.Core
          ├── backend configuration and lifecycle
          ├── typed Studio HTTP client
          └── project/workflow models
                    │
                    ▼
Existing edmg_studio_backend (FastAPI / Python 3.12)
```

The native client does not duplicate the AI, audio-analysis, render, model, or project-storage
engines. It communicates with the backend over bearer-authenticated localhost HTTP and uses the
same project format as the compatibility client.

### Native Direct3D preview pipeline

Outputs, Review, and the selected Timeline artifact use one reusable
`Direct3DPreviewControl`. The control owns a real WinUI 3 `SwapChainPanel`; its renderer creates a
DXGI composition swap chain with `CreateSwapChainForComposition` and uses:

- a high-performance hardware-adapter enumeration rather than assuming adapter zero
- software-adapter rejection and WARP only as a degraded fallback
- two `B8G8R8A8_UNorm` flip-model buffers, VSync presentation, no depth buffer, and a maximum
  frame latency of one where supported
- Direct2D drawing over the swap-chain back buffer with centered aspect-fit letterboxing
- physical pixel sizes calculated from panel DIPs and `XamlRoot.RasterizationScale`, with
  coalesced non-zero resizes and an inverse-scale swap-chain transform

The selected adapter description and exact 64-bit DXGI LUID are exposed in the preview's
diagnostic/help text. Keep the LUID when collecting GPU reports: a future CUDA interoperability
implementation must match the CUDA device to this exact DXGI adapter rather than relying on ordinal
device numbers.

The Python/CUDA backend remains the compute and render authority. Preview media is retrieved with
authenticated `ResponseHeadersRead` requests, and the request, response, network stream, and headers
are valid only during the streaming callback. The current image path decodes into one owned,
premultiplied CPU BGRA8 frame, then uploads through reusable D3D11 textures. It does not move
inference to DirectML or DirectX and does not retain a whole-media byte array.

The frame boundary is intentionally narrow:

- dimensions are positive and at most 16,384 per axis
- decoded memory is bounded to 512 MiB and all size arithmetic is checked
- format is explicit BGRA8 or RGBA8
- stride is explicit and may contain padded rows
- orientation is explicit top-down or bottom-up
- conversion copies rows into tightly packed native BGRA order

`IFrameUploader` is the replacement point for a future CUDA-D3D11 shared-texture uploader. That
future work can avoid the CPU copy without changing the preview control, renderer session, page, or
backend API contract. CUDA interop is not implemented in this release.

Image decoding uses Windows Imaging Component, applies metadata orientation and sRGB color
management, bounds compressed input to 256 MiB, and delete-on-close spools non-seekable streams.
Video preview uses FFprobe metadata and FFmpeg-decoded BGRA frames through the same
Direct3D surface, with play/pause and seeking. Temporary video input defaults to
512 MiB, configurable through `EDMG_STUDIO_VIDEO_SPOOL_MAX_BYTES`; known oversized
inputs are rejected before reading, and unknown lengths are bounded during streaming.
The current decoder is video-only (`-an`): synchronized audio playback is still a
WinUI/Electron parity gap. Generated video files can also be saved or revealed.

Decode continuations, row conversion, uploads, drawing, resizing, and `Present` run on a dedicated
renderer worker. The UI dispatcher is used only for panel attachment/detachment and status updates.
A capacity-one mailbox disposes stale frames, and only the latest valid CPU frame is retained for a
redraw after bounded device-loss recovery. Shutdown stops submissions, cancels acquisition,
completes the mailbox, detaches the panel on the UI dispatcher, waits for the worker, releases
D3D11/DXGI/D2D resources, and only then disposes the backend supervisor and API client.

## Prerequisites

- Windows 10 version 1809 or newer; Windows 11 is the primary experience.
- Visual Studio with the WinUI application development workload.
- .NET SDK selected by [`global.json`](global.json).
- The WinUI C# templates (`Microsoft.WindowsAppSDK.WinUI.CSharp.Templates`) when creating or regenerating projects.
- For source-managed backend startup, the repository's pinned `uv` toolchain and Python 3.12 environment.

Microsoft's current WinUI quick start describes the required Visual Studio workload, developer mode, and packaged project setup: <https://learn.microsoft.com/windows/apps/winui/winui3/create-your-first-winui3-app>.

## Build and test

Run these commands from this directory in PowerShell:

`global.json` requires stable .NET 10 SDK 10.0.301 or a later 10.0 feature band;
preview and .NET 11 SDKs are not selected. Record the resolved SDK in release evidence.
Optional local WinUI analyzers can be enabled with `EdmgWinUIAnalyzerDirectory`;
a normal checkout does not depend on a particular user's plugin installation.

```powershell
dotnet restore .\EdmgStudio.WinUI.csproj -r win-x64
dotnet build .\EdmgStudio.WinUI.csproj -p:Platform=x64 -p:Configuration=Release
dotnet test .\tests\EdmgStudio.Core.Tests\EdmgStudio.Core.Tests.csproj `
  -p:Platform=x64 -p:Configuration=Release
```

The whole solution can also be compiled with:

```powershell
dotnet build .\EdmgStudio.WinUI.slnx -p:Platform=x64
```

Only x64 is qualified for the native preview path. Do not build or publish this project as AnyCPU,
do not remove `Package.appxmanifest`, and do not add `WindowsPackageType=None`.

The current implementation/verification ledger is
[`docs/WINUI_PARITY_STATUS.md`](../../docs/WINUI_PARITY_STATUS.md). Core tests and a
successful build do not establish launched-app stability or full Electron parity.

The focused backend data-freshness tests live in the existing Python test suite and should be run with the repository's frozen backend environment.

## Run with package identity

The default development route retains package identity so Credential Locker, MSIX behavior, and Windows integrations are exercised:

```powershell
dotnet run --project .\EdmgStudio.WinUI.csproj `
  --launch-profile "EdmgStudio.WinUI (Package)" `
  -p:Platform=x64
```

Never start the packaged executable directly. Use the package launch profile or `winapp run` so
MSIX identity and Windows App SDK activation are present.

For a deterministic source-development launch against an existing local backend on port 7863:

```powershell
$env:EDMG_STUDIO_BACKEND_MODE = "managed"
$env:EDMG_STUDIO_BACKEND_HOST = "127.0.0.1"
$env:EDMG_STUDIO_BACKEND_PORT = "7863"
$env:EDMG_STUDIO_SPAWN_BACKEND = "1"
dotnet run --project .\EdmgStudio.WinUI.csproj `
  --launch-profile "EdmgStudio.WinUI (Package)" `
  -p:Platform=x64
```

Managed source mode attaches to an already healthy Studio backend. It never terminates that attached process. A backend spawned by the native client is placed in its own kill-on-close Job Object and is stopped with the client.

## Storage and configuration compatibility

Configuration precedence is:

```text
runtime defaults
  < source launcher_env.json
  < %APPDATA%\EDMG Studio\bootstrap.json
  < process environment
  < non-secret diagnostic command-line overrides
```

The explicitly selected Studio home, project data, models, cache, logs, and external-tools paths are preserved. If only a legacy data directory is configured, Studio home is derived from its parent. If the selected cache is unavailable, only cache-derived paths move to `%LOCALAPPDATA%\EDMG Studio\cache-fallback`; project data and models are not silently relocated.

If the shared bootstrap contains `pendingMigration`, managed backend startup stops with a migration-required status. Use the existing Studio client to complete that established migration workflow before retrying the native client. This avoids presenting empty target directories while data still resides at the source location.

Packaged activation does not inherit temporary environment changes from its launcher. For a diagnostic package launch without editing shared settings, use `winapp run` with non-secret arguments such as `--backend-mode managed --backend-host 127.0.0.1 --backend-port 7863 --spawn-backend true`. Backend tokens are deliberately excluded from command-line parsing; use Credential Locker or the backend-token environment contract instead.

## Project freshness rules

The shared backend now treats active workflow state as a dependency chain:

```text
new audio → invalidate active analysis and plan
new analysis → invalidate active plan
new plan → becomes the active variant source
```

Authored Timeline data, Visual DNA, imported lab state, outputs, jobs, and render history are preserved. Downstream conductor/performer caches still need explicit revision provenance rather than broad deletion.

## Packaging and release boundary

This is a packaged MSIX project, but it is not a Store-submission artifact yet.

- `Package.appxmanifest` currently uses a development identity and publisher placeholder. Replace both with the exact Partner Center identity and publisher values before Store packaging.
- The production backend is a complete validated PyInstaller `onedir` payload, not a standalone executable. It belongs under the installed app's `backend` directory and must pass the repository's existing release-manifest/hash gate before packaging. The top-level path avoids the MSIX-reserved `resources` tree while remaining one of the launcher's supported packaged locations.
- Do not commit or copy the current multi-gigabyte generated backend bundle into this source directory.
- Store signing, final product icons, installer upgrade tests, clean-machine proof, and customer-flow release validation remain separate release gates.

### Store upload staging

The canonical staging script can now create a Store upload artifact that contains the
self-contained Windows App SDK runtime **and** the validated production backend:

```powershell
# Copy the example, then replace every value from Partner Center > Product management > Identity details.
Copy-Item .\StoreIdentity.json.example .\StoreIdentity.json

cd ..\edmg-studio
pnpm run stage:winui:msix -- `
  -StoreIdentityFile ..\edmg-studio-winui\StoreIdentity.json `
  -IncludeProductionBackend
```

`StoreIdentity.json` is ignored by Git. Its values are deliberately not defaulted:
the package identity name and publisher are case-sensitive Partner Center values and
must not be guessed. With that file, staging requests `CI` output and emits an
`.msixupload` without a separate sideload test package. The Store re-signs submitted
MSIX/AppX packages; do not substitute a development certificate for the Partner Center
identity.

For a local, testable package, omit `-StoreIdentityFile`, retain
`-IncludeProductionBackend`, and either use a certificate generated from
`Package.appxmanifest` or supply the exact subject from an existing code-signing
certificate:

```powershell
$env:EDMG_CODE_SIGN_CERT = "<40-character certificate thumbprint>"
pnpm run stage:winui:msix -- `
  -SideloadPublisher "CN=Publisher matching the signing certificate" `
  -IncludeProductionBackend `
  -RequireSigning
```

`-SideloadPublisher` changes only the effective build manifest; it does not edit the
checked-in development manifest or claim a Partner Center identity. This signed sideload
artifact is not eligible for Partner Center upload until rebuilt with the exact Partner
Center identity.

The WinUI package intentionally omits the backend's unused `tcl86t.dll` and `tk86t.dll`
payloads. They are not referenced by EDMG Studio and their upstream PE metadata prevents
Windows SignTool from signing an otherwise valid MSIX. The canonical backend bundle remains
unchanged and is still validated before packaging.

The Electron client remains the Linux and compatibility surface while packaged WinUI customer-flow
release validation proceeds.
