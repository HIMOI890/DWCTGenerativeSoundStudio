import React from "react";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Models from "../pages/Models";
import { installEdmgBridge, installFetchMock, renderWithStudio } from "./testUtils";

vi.mock("@huggingface/hub", () => ({
  listModels: async function* () {
    // Model discovery is deliberately empty in these polling tests.
  },
}));

const catalog = {
  catalog: [],
  user: [],
  packs: [],
  accepted: {},
  installed: {},
  cloud: {},
  storage_mode: "local_cache",
};

function installStrictModeAbortFetchMock() {
  const callCounts = new Map<string, number>();
  const payloads: Record<string, unknown> = {
    "/v1/models/catalog": catalog,
    "/v1/models/tasks": { tasks: [] },
    "/v1/settings/render_providers": {},
  };
  const abortOnce = new Set(["/v1/models/catalog", "/v1/settings/render_providers"]);
  const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
    const path = new URL(String(input instanceof Request ? input.url : input)).pathname;
    const callCount = (callCounts.get(path) ?? 0) + 1;
    callCounts.set(path, callCount);
    if (abortOnce.has(path) && callCount === 1) {
      return new Promise<Response>((_resolve, reject) => {
        const rejectAbort = () => reject(new DOMException("signal is aborted without reason", "AbortError"));
        if (init?.signal?.aborted) rejectAbort();
        else init?.signal?.addEventListener("abort", rejectAbort, { once: true });
      });
    }
    return Promise.resolve({
      ok: true,
      status: 200,
      json: async () => payloads[path],
    } as Response);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { callCounts, fetchMock };
}

const readyLegacyTensorRt = {
  legacy: {
    detected: true,
    status: "ready_to_import",
    expected_file_count: 4,
    usable_file_count: 4,
    total_bytes: 4_636_659_776,
    files: [
      { role: "text_encoder", name: "text_encoder.engine", present: true, non_empty: true, safe_regular_file: true, size_bytes: 492_983_636, hash_state: "pending_import_verification" },
      { role: "unet", name: "unet_b1_workspace4096.engine", present: true, non_empty: true, safe_regular_file: true, size_bytes: 3_561_191_108, hash_state: "pending_import_verification" },
      { role: "vae_decoder", name: "vae_decoder.engine", present: true, non_empty: true, safe_regular_file: true, size_bytes: 350_683_876, hash_state: "pending_import_verification" },
      { role: "vae_encoder", name: "vae_encoder.engine", present: true, non_empty: true, safe_regular_file: true, size_bytes: 231_801_156, hash_state: "pending_import_verification" },
    ],
    missing_roles: [],
    unusable_roles: [],
    source_preserved: true,
  },
  canonical: {
    exists: false,
    status: "absent",
    manifest: { valid: false, schema_version: null },
    engine_files_verified: false,
    unet_engine_ready: false,
    onnx_ready: false,
    profile_metadata_ready: false,
    base_model_metadata_ready: false,
    renderer_ready: false,
    gaps: [],
  },
  migration: {
    available: true,
    blocked_reason: null,
    copy_only: true,
    source_will_be_preserved: true,
    disk: {
      required_free_bytes: 4_868_492_765,
      available_free_bytes: 20_000_000_000,
      enough_space: true,
    },
  },
};

describe("Models page polling", () => {
  it("shows managed packages in basic mode with separate runtime status and uninstall", async () => {
    vi.useRealTimers();
    const modelId = "hf_ltx_25_distilled_internal";
    const fetchMock = installFetchMock({
      "/v1/models/catalog": {
        ...catalog,
        catalog: [{ id: modelId, name: "LTX-2.5 Distilled", kind: "internal_package", source: "hf",
          recommended: "advanced", installable: true, package_managed: true,
          required_files: ["vae/video.safetensors"], download_size_bytes: 70122982342,
          package_status: { installed: true, runtime_ready: false, files_present: true,
            hardware_known: true, hardware_compatible: false,
            blockers: ["LTX execution adapter is pending."], validation_issues: [] } }],
        installed: { [modelId]: true }, accepted: { [modelId]: true },
      },
      "/v1/models/tasks": { tasks: [] },
      "/v1/settings/render_providers": {},
      "/v1/models/uninstall": { task: { id: "remove", status: "queued" } },
      "/v1/models/validate": { task: { id: "check", status: "queued" } },
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderWithStudio(<Models backendUrl="http://127.0.0.1:7863" config={{}} />);
    expect(await screen.findByText("LTX-2.5 Distilled")).toBeTruthy();
    expect(screen.getByText("Installed locally")).toBeTruthy();
    expect(screen.getByText("Runtime blocked")).toBeTruthy();
    expect(screen.getByText(/Below provisional targets/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Revalidate files" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/models/validate"))).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "Uninstall package" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith("/models/uninstall") && JSON.parse(String(init?.body)).model_id === modelId)).toBe(true));
    confirm.mockRestore();
  });

  beforeEach(() => {
    vi.useFakeTimers();
    window.localStorage.clear();
    installEdmgBridge();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("loads catalog and providers once, then stops task polling while idle", async () => {
    const fetchMock = installFetchMock({
      "/v1/models/catalog": catalog,
      "/v1/models/tasks": { tasks: [] },
      "/v1/settings/render_providers": {},
    });

    renderWithStudio(<Models backendUrl="http://127.0.0.1:7863" config={{}} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText("Model Manager")).toBeTruthy();
    expect(screen.getByText("No model install tasks yet.")).toBeTruthy();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });

    const paths = fetchMock.mock.calls.map(([url]) => new URL(String(url)).pathname);
    expect(paths.filter((path) => path === "/v1/models/catalog")).toHaveLength(1);
    expect(paths.filter((path) => path === "/v1/settings/render_providers")).toHaveLength(1);
    expect(paths.filter((path) => path === "/v1/models/tasks")).toHaveLength(1);
  });

  it("restarts canceled StrictMode loads without showing an abort as an error", async () => {
    vi.useRealTimers();
    const { callCounts } = installStrictModeAbortFetchMock();

    renderWithStudio(
      <React.StrictMode>
        <Models backendUrl="http://127.0.0.1:7863" config={{}} />
      </React.StrictMode>,
    );

    expect(await screen.findByText("local + cache", { selector: "b" })).toBeTruthy();
    await waitFor(() => {
      expect(callCounts.get("/v1/models/catalog")).toBe(2);
      expect(callCounts.get("/v1/settings/render_providers")).toBe(2);
    });
    expect(screen.queryByText(/signal is aborted without reason/i)).toBeNull();
  });

  it("shows stage and progress in the default basic UI mode", async () => {
    installFetchMock({
      "/v1/models/catalog": catalog,
      "/v1/models/tasks": {
        tasks: [{
          id: "model-1",
          name: "Install SDXL",
          status: "running",
          progress: 0.42,
          stage: "Downloading inference weights",
          bytes_completed: 1_610_612_736,
          bytes_total: 4_294_967_296,
          files_completed: 3,
          files_total: 8,
        }],
      },
      "/v1/settings/render_providers": {},
    });

    renderWithStudio(<Models backendUrl="http://127.0.0.1:7863" config={{}} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(screen.getByText("Model install progress")).toBeTruthy();
    expect(screen.getByText("Stage: Downloading inference weights")).toBeTruthy();
    expect(screen.getByText("running • 42%")).toBeTruthy();
    expect(screen.getByText(/Downloaded/).textContent).toContain("1.50 GB");
    expect(screen.getByText(/Downloaded/).textContent).toContain("4.00 GB");
    expect(screen.getByText(/Downloaded/).textContent).toContain("Files 3 of 8");
    expect(screen.getByRole("progressbar", { name: "Install SDXL progress" })).toBeTruthy();
  });

  it("requests cooperative cancellation for an active model install", async () => {
    let cancellationRequested = false;
    const runningTask = {
      id: "flux-install-1",
      name: "Install: FLUX.1 Schnell (Internal / Diffusers)",
      status: "running",
      model_id: "hf_flux1_schnell_internal",
      stage: "downloading",
      progress: 0.1,
      cancel_requested: false,
    };
    const fetchMock = installFetchMock({
      "/v1/models/catalog": catalog,
      "/v1/models/tasks": () => ({
        tasks: [{
          ...runningTask,
          stage: cancellationRequested ? "cancelling" : "downloading",
          cancel_requested: cancellationRequested,
        }],
      }),
      "/v1/settings/render_providers": {},
      "POST /v1/models/tasks/cancel": () => {
        cancellationRequested = true;
        return {
          task: { ...runningTask, stage: "cancelling", cancel_requested: true },
        };
      },
    });

    renderWithStudio(<Models backendUrl="http://127.0.0.1:7863" config={{}} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    fireEvent.click(screen.getByRole("button", { name: `Cancel ${runningTask.name}` }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    const cancelPost = fetchMock.mock.calls.find(([url, init]) => (
      new URL(String(url)).pathname === "/v1/models/tasks/cancel"
      && String(init?.method).toUpperCase() === "POST"
      && String(init?.body || "").includes('"task_id":"flux-install-1"')
    ));
    expect(cancelPost).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancellation requested…" })).toBeTruthy();
  });

  it("shows actionable model-task failure details", async () => {
    installFetchMock({
      "/v1/models/catalog": catalog,
      "/v1/models/tasks": {
        tasks: [{
          id: "flux-failed-1",
          name: "Install: FLUX.1 Schnell (Internal / Diffusers)",
          status: "failed",
          model_id: "hf_flux1_schnell_internal",
          stage: "failed",
          error: "Hugging Face denied access",
          error_hint: "Accept the gated model conditions in your Hugging Face account, then retry.",
          error_code: "HF_AUTH_REQUIRED",
        }],
      },
      "/v1/settings/render_providers": {},
    });

    renderWithStudio(<Models backendUrl="http://127.0.0.1:7863" config={{}} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(screen.getByRole("alert").textContent).toContain("Hugging Face denied access");
    expect(screen.getByRole("alert").textContent).toContain("Accept the gated model conditions");
    expect(screen.getByRole("alert").textContent).toContain("HF_AUTH_REQUIRED");
  });

  it("starts the explicit source-preserving TensorRT verify-and-copy task", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    let importStarted = false;
    const runningTask = {
      id: "trt-copy-1",
      name: "Verify and copy legacy TensorRT engines",
      status: "running",
      model_id: "local_sd15_tensorrt_bundle",
      stage: "Copying unet_b1_workspace4096.engine",
      progress: 0.25,
    };
    const fetchMock = installFetchMock({
      "/v1/models/catalog": { ...catalog, tensorrt_migration: readyLegacyTensorRt },
      "/v1/models/tasks": () => ({ tasks: importStarted ? [runningTask] : [] }),
      "/v1/settings/render_providers": {},
      "POST /v1/models/tensorrt/import-legacy": () => {
        importStarted = true;
        return { task: runningTask };
      },
    });

    renderWithStudio(<Models backendUrl="http://127.0.0.1:7863" config={{}} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(screen.getByText("Legacy TensorRT engine migration")).toBeTruthy();
    expect(screen.getByText(/Found 4 safe, non-empty engine candidates/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Verify and copy engines" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(confirm).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/Verification and safe copy started/)).toBeTruthy();
    const post = fetchMock.mock.calls.find(([url, init]) => (
      new URL(String(url)).pathname === "/v1/models/tensorrt/import-legacy"
      && String(init?.method).toUpperCase() === "POST"
    ));
    expect(post).toBeTruthy();
  });

  it("does not describe an engine-only canonical copy as renderer-ready", async () => {
    installFetchMock({
      "/v1/models/catalog": {
        ...catalog,
        tensorrt_migration: {
          ...readyLegacyTensorRt,
          canonical: {
            ...readyLegacyTensorRt.canonical,
            exists: true,
            status: "engine_copy_incomplete_setup",
            manifest: { valid: true, schema_version: 1 },
            engine_files_verified: true,
            unet_engine_ready: true,
            renderer_ready: false,
            gaps: [
              "The canonical bundle still needs non-empty ONNX assets and the UNet config.",
              "The compiled width, height, and batch profile has not been verified.",
              "The matching SD 1.5 base-model metadata has not been verified.",
            ],
          },
          migration: {
            ...readyLegacyTensorRt.migration,
            available: false,
            blocked_reason: "canonical_exists",
          },
        },
      },
      "/v1/models/tasks": { tasks: [] },
      "/v1/settings/render_providers": {},
    });

    renderWithStudio(<Models backendUrl="http://127.0.0.1:7863" config={{}} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(screen.getByText("Engine copy present — setup incomplete")).toBeTruthy();
    expect(screen.getByText("Canonical bundle is intentionally not marked ready")).toBeTruthy();
    expect(screen.getByText(/still needs non-empty ONNX assets/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Verify and copy engines" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
