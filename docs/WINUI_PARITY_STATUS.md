# WinUI-first parity implementation ledger

Baseline: `51c6b7ec86c6207de32a1e54a634a41a16c660fd` (`codex/Unified`).
Implementation branch: `codex/winui-parity-first`. Updated: 2026-09-07.

WinUI is the first delivery target. Electron is the functional baseline. Neither
screen existence nor a passing build establishes capability parity. Existing
capabilities remain supported; Labs do not block core completion.

## Source map and acceptance matrix

Electron sources below are under `studio/edmg-studio/src/pages/`. Native sources
are under `studio/edmg-studio-winui/Pages/` (paired `.xaml` and `.xaml.cs`). Tests
are under `studio/edmg-studio-winui/tests/EdmgStudio.Core.Tests/`.

| Capability | Electron reference | Native implementation | Existing test entrypoints | Remaining acceptance |
| --- | --- | --- | --- | --- |
| System summary and navigation | Dashboard.tsx | DashboardPage, MainPage | StudioNavigationDestinationTests, BackendStartupMonitorTests | Launched shell, keyboard navigation, offline states |
| Projects, creation, health | Projects.tsx | ProjectsPage | ProjectModelTests, LatestRequestGateTests | Live create/open/health; cancellation and repeated navigation |
| Import, analysis, planning | Workspace.tsx | WorkspacePage | WorkspaceModelTests, StudioApiClientTests | Real audio import/analyze, missing transcript/BPM, both-client round trip |
| Structured scenes, continuity, schedule draft approval | AiPlannerLab.tsx | AiPlannerLabPage | PlannerReactiveWorkflowTests | Edit/reorder, review, regenerate, apply, reopen; reject stale draft |
| Timeline clip/layer edits and media properties | Timeline.tsx | TimelinePage | TimelineProjectionTests, TimelineAutomationTests | Same fixture edited in both clients; unknown-field retention, undo/redo |
| Camera and musical automation | Timeline.tsx, ReactiveLab.tsx | TimelinePage, ReactiveLabPage | TimelineCameraProjectionTests, PlannerReactiveWorkflowTests | Native controls and rendered timing equivalence |
| Timeline supported scale | Timeline.tsx | TimelinePage | TimelineViewportTests | Full clip/keyframe virtualization, fit-to-project, frame-time/memory benchmarks |
| Render options, models, preflight | Render.tsx | RenderPage | InternalVideoRenderRequestBuilderTests, RenderRuntimeCapabilitiesTests, RenderQuickSetupTests | Control-by-control payload parity, full-motion route and real artifact evidence |
| Job progress, cancel/retry/pause/resume | RenderQueue.tsx | QueuePage | StudioApiClientTests, StudioJobConfirmationFactoryTests | Live cancellation/restart races; terminal output ownership |
| Outputs, preview, downloads | Outputs.tsx | OutputsPage, Direct3DPreviewControl | MediaPipelineTests, StudioOutputCatalogTests, StudioProjectMediaClientTests | **Known gap: video decoder uses `-an`; native preview has no audio playback.** Verify seek/playback and signed range access |
| Review, traits, decisions and locks | Review.tsx | ReviewPage | StudioReviewSelectionTests, StudioApiClientTests | Cross-client review state and artifact fidelity |
| Model catalog, licensing, installation | Models.tsx | ModelsPage | ModelRenderGuidanceTests, StudioApiClientTests | Manifest/revision/license controls and failure recovery |
| Settings, runtime readiness, storage | Settings.tsx, Setup.tsx | SettingsPage, SetupPage | BackendSettingsStoreTests, BackendConfigurationTests, WindowsBackendTokenProviderTests | Native field parity, backend isolation, all error states |
| Director workflows | EdmgDirector.tsx | Workspace Director mode, EdmgDirectorPage | StudioApiClientTests | Compare individual actions and saved intent |
| Cloud/provider controls | Cloud.tsx | CloudPage | StudioApiClientTests | Compare capabilities/locality and consent paths without provider spending |
| Forge and advanced workbenches | StudioForge.tsx | StudioForgePage | StudioApiClientTests | Preserve existing controls; experimental extensions remain Labs |
| Migration and recovery | Projects.tsx, Timeline.tsx, Setup.tsx | MigrationPage, TimelinePage, SetupPage | TimelineProjectionTests, BackendConfigurationTests | Interrupted writes, recovery and real previous-version migration |

This is an initial subsystem inventory, not a completed control-by-control parity
certification. All remaining acceptance cells stay open until direct evidence is recorded.

## Implemented changes in this branch

- Allow later stable .NET 10 SDK feature bands; retain the minimum SDK and reject previews.
- Replace the user-profile-specific mandatory analyzer import with optional
  `EdmgWinUIAnalyzerDirectory` configuration.
- Serialize video stop/replacement, share completion across concurrent disposal,
  and delete temporary media even when the decoder fails while stopping.
- Retain cancellation-source ownership until the owning preview operation exits.
- Release pooled frames on pacing cancellation and when the renderer is absent.
- Capture the unloading renderer before yielding so reattachment cannot be torn down by old cleanup.
- Ignore queued video frame updates from old sessions; suppress artificial seek events during transport initialization.
- Observe background teardown failures and handle pause errors locally.
- Render only visible ruler marks, with overscan, instead of constructing marks
  across the full project duration. This does not virtualize clips or change project timing.
- Cancel obsolete project/output reads and guard publication even if transport
  completes after navigation or replacement. Canceled refreshes are not shown as failures.
- Add Workspace mode tabs for Director, AI Planner, and Reactive Lab. The native
  Workspace now hosts the existing planner/reactive pages in embedded Frames and
  the shared Director document editor, so all three use the active project session
  without requiring a sidebar handoff.

## Verification and blocked evidence

- Initial native core baseline: 264 passed, 0 failed, 0 skipped.
- Native Release x64 build after initial playback fixes: passed, 0 warnings/errors.
- Core suite after playback/ruler changes: 272 passed, 0 failed, 0 skipped.
- Frozen backend lock check: passed with uv 0.11.28 and Python 3.12.13, using a
  dedicated task cache after the default uv cache failed with too many temporary files.
- Native launch: **blocked by automatic approval review** (`blocked by policy`),
  including an isolated launch with backend spawning disabled. No responsive
  window, interactive workflow, or crash-free run is claimed.
- Final build/test results for subsequent changes must be recorded before handoff.
- CUDA temporal proof, audio parity, complete control parity, full project scale,
  clean-machine install, signing, upgrade, rollback and release publication remain open.

## Next acceptance order

1. Complete native playback with synchronized audio and all Electron transport controls.
2. Prove native navigation and repeated open/close/play/seek/cancel cycles in a launched app.
3. Expand this matrix to individual controls/payload fields, fixing the native gaps first.
4. Run identical small/medium/large projects through both clients and compare persisted semantics.
5. Finish core planner/engine/job gaps, then genuine CUDA artifact proof.
6. Complete structural and release gates; retain explicit external-evidence blockers.
