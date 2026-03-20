import assert from "node:assert/strict";
import fs from "node:fs";
import fsp from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");

function log(message) {
  console.log(`[packaged-customer-flow] ${message}`);
}

function resolvePackagedApp() {
  const envPath = process.env.EDMG_STUDIO_PACKAGED_APP;
  if (envPath && fs.existsSync(envPath)) return envPath;
  const candidate = path.join(root, "dist", "win-unpacked", process.platform === "win32" ? "EDMG Studio.exe" : "EDMG Studio");
  return fs.existsSync(candidate) ? candidate : "";
}

function resolveAudioFixture() {
  const envPath = process.env.EDMG_STUDIO_AUDIO_FIXTURE;
  if (envPath && fs.existsSync(envPath)) return envPath;
  const candidate = path.resolve(root, "..", "..", "juce_example", "out", "build", "x64-Debug", "_deps", "juce-src", "examples", "Assets", "cassette_recorder.wav");
  return fs.existsSync(candidate) ? candidate : "";
}

function chooseHomeRoot() {
  const preferred = process.env.EDMG_STUDIO_PROOF_ROOT;
  if (preferred) return preferred;
  if (process.platform === "win32" && fs.existsSync("D:\\")) return "D:\\";
  return os.tmpdir();
}

async function allocatePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => {
    server.listen(0, "127.0.0.1", () => resolve());
    server.on("error", reject);
  });
  const address = server.address();
  server.close();
  if (!address || typeof address === "string") throw new Error("Unable to allocate backend port");
  return address.port;
}

async function stopExistingPackagedProcesses() {
  if (process.platform !== "win32") return;
  const appDir = path.join(root, "dist", "win-unpacked").replace(/'/g, "''");
  const command = `$ErrorActionPreference='SilentlyContinue'; $appDir='${appDir}'; Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($appDir, [System.StringComparison]::OrdinalIgnoreCase) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }`;
  await new Promise((resolve, reject) => {
    const child = spawn("powershell", ["-NoProfile", "-Command", command], { stdio: "ignore" });
    child.on("exit", (code) => (code === 0 ? resolve() : reject(new Error(`Failed to stop stale packaged processes: ${code}`))));
    child.on("error", reject);
  });
  await new Promise((resolve) => setTimeout(resolve, 1500));
}

async function waitForHealth(baseUrl, timeoutMs = 120000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      return await requestJson(`${baseUrl}/health`);
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 750));
    }
  }
  throw new Error(`Backend never became healthy at ${baseUrl}`);
}

async function requestJson(url, init = {}) {
  const response = await fetch(url, {
    ...init,
    headers: {
      accept: "application/json",
      ...(init.headers || {}),
    },
  });
  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { raw: text };
    }
  }
  if (!response.ok) {
    throw new Error(`${init.method || "GET"} ${url} failed: ${response.status} ${text}`);
  }
  return payload;
}

async function postJson(url, body) {
  return requestJson(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function uploadAudio(url, audioPath) {
  const form = new FormData();
  const bytes = await fsp.readFile(audioPath);
  form.set("file", new Blob([bytes]), path.basename(audioPath));
  return requestJson(url, { method: "POST", body: form });
}

async function killProcessTree(child) {
  if (!child || child.exitCode !== null) return;
  if (process.platform === "win32") {
    await new Promise((resolve) => {
      const killer = spawn("taskkill", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore" });
      killer.on("exit", () => resolve());
      killer.on("error", () => resolve());
    });
    return;
  }
  child.kill("SIGTERM");
}

async function main() {
  const appExe = resolvePackagedApp();
  assert.ok(appExe, "Packaged app not found. Run npm run dist:win first or set EDMG_STUDIO_PACKAGED_APP.");
  const audioFixture = resolveAudioFixture();
  assert.ok(audioFixture, "Audio fixture not found. Set EDMG_STUDIO_AUDIO_FIXTURE.");

  await stopExistingPackagedProcesses();

  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\..+/, "").replace("T", "_");
  const homeRoot = chooseHomeRoot();
  const studioHome = path.join(homeRoot, `EDMG-Packaged-Proof-${stamp}`);
  await fsp.mkdir(studioHome, { recursive: true });
  const testPage = path.join(studioHome, "blank.html");
  await fsp.writeFile(testPage, "<!doctype html><html><body>packaged customer flow</body></html>\n");
  const port = Number(process.env.EDMG_STUDIO_PROOF_PORT || (await allocatePort()));
  const baseUrl = `http://127.0.0.1:${port}`;

  log(`launching ${appExe}`);
  const child = spawn(appExe, [], {
    cwd: path.dirname(appExe),
    env: {
      ...process.env,
      EDMG_STUDIO_HOME: studioHome,
      EDMG_STUDIO_BACKEND_HOST: "127.0.0.1",
      EDMG_STUDIO_BACKEND_PORT: String(port),
      EDMG_STUDIO_TEST_MODE: "1",
      EDMG_STUDIO_TEST_PAGE: testPage,
      EDMG_STUDIO_TEST_FAKE_PATH_ACTIONS: "1",
      ELECTRON_DISABLE_SECURITY_WARNINGS: "1",
    },
    stdio: "ignore",
  });

  try {
    const health = await waitForHealth(baseUrl);
    const status = await requestJson(`${baseUrl}/v1/setup/status`);
    const config = await requestJson(`${baseUrl}/v1/config`);
    const created = await postJson(`${baseUrl}/v1/projects`, { name: "Packaged Customer Proof" });
    const projectId = created?.project?.id;
    assert.ok(projectId, "Project creation did not return an id");

    const upload = await uploadAudio(`${baseUrl}/v1/projects/${projectId}/assets/audio`, audioFixture);
    const analyze = await requestJson(`${baseUrl}/v1/projects/${projectId}/analyze_audio`, { method: "POST" });
    const plan = await postJson(`${baseUrl}/v1/projects/${projectId}/plan?mode=local`, {
      title: "Packaged Customer Proof",
      style_prefs: "audio reactive neon performance visuals",
      num_variants: 1,
      max_scenes: 4,
    });
    const apply = await postJson(`${baseUrl}/v1/projects/${projectId}/timeline/apply_plan`, { variant_index: 0, overwrite: true });
    const validate = await requestJson(`${baseUrl}/v1/projects/${projectId}/pipeline/validate?variant_index=0&preset=fast&mode=auto&engine=auto`);
    const run = await requestJson(`${baseUrl}/v1/projects/${projectId}/pipeline/run?variant_index=0&preset=fast&mode=auto&engine=auto`, { method: "POST" });
    const targetJobId = run?.job?.id || run?.assemble_job?.id;
    assert.ok(targetJobId, `Pipeline run did not return a target job payload: ${JSON.stringify(run)}`);

    let job = null;
    const tickHistory = [];
    const tickDeadline = Date.now() + 6 * 60 * 1000;
    while (Date.now() < tickDeadline) {
      const tick = await requestJson(`${baseUrl}/v1/jobs/tick`, { method: "POST" });
      if (tick?.job) {
        tickHistory.push({ id: tick.job.id, status: tick.job.status, type: tick.job.type });
        if (tick.job.id === targetJobId) {
          job = tick.job;
          if (["succeeded", "failed", "canceled"].includes(String(job.status))) break;
        }
      }
      await new Promise((resolve) => setTimeout(resolve, 750));
    }
    const jobs = await requestJson(`${baseUrl}/v1/projects/${projectId}/jobs`);
    if (!job && Array.isArray(jobs?.jobs)) {
      job = jobs.jobs.find((entry) => entry.id === targetJobId) || null;
    }
    assert.ok(job, `Did not observe the packaged target job ${targetJobId} in tick responses or job list`);
    const outputs = await requestJson(`${baseUrl}/v1/projects/${projectId}/outputs`);
    const comfyOk = Boolean(status?.comfyui?.ok);
    const usesInternalPipeline = Boolean(run?.job?.id);
    const expectedDataDir = path.join(studioHome, "data");
    const expectedModelsDir = path.join(studioHome, "models");
    const expectedExternalDir = path.join(studioHome, "external");
    const expectedLogsDir = path.join(studioHome, "logs");
    const expectedOllamaModelsDir = path.join(expectedModelsDir, "ollama");
    const summary = {
      ok: job.status === "succeeded",
      studioHome,
      baseUrl,
      health,
      paths: {
        studioHome: config?.studio_home ?? null,
        dataDir: config?.data_dir ?? null,
        modelsDir: config?.models_dir ?? null,
        ollamaModelsDir: config?.ollama_models_dir ?? null,
        logsDir: config?.logs_dir ?? null,
        externalDir: config?.external_dir ?? null,
        expectedDataDir,
        expectedModelsDir,
        expectedOllamaModelsDir,
        expectedLogsDir,
        expectedExternalDir,
        dataDirExists: fs.existsSync(expectedDataDir),
        modelsDirExists: fs.existsSync(expectedModelsDir),
        ollamaModelsDirExists: fs.existsSync(expectedOllamaModelsDir),
        logsDirExists: fs.existsSync(expectedLogsDir),
        externalDirExists: fs.existsSync(expectedExternalDir),
      },
      setupStatus: {
        backendBundleOk: status?.backend_bundle?.ok,
        ffmpegOk: status?.ffmpeg?.ok,
        edmgAvailable: status?.edmg?.available,
        edmgInstallable: status?.edmg?.installable,
        edmgRepoRoot: status?.edmg?.repo_root ?? null,
        sevenZipOk: status?.sevenzip?.ok,
        sevenZipPath: status?.sevenzip?.path ?? null,
        ollamaOk: status?.ollama?.ok,
        ollamaManagedModelsDir: status?.ollama?.managed_models_dir ?? null,
        ollamaManagedLaunchScript: status?.ollama?.managed_launch_script ?? null,
        ollamaLaunchAvailable: status?.ollama?.launch_available ?? null,
        comfyOk: status?.comfyui?.ok,
      },
      projectId,
      uploadOk: Boolean(upload?.ok),
      analyzeKeys: Object.keys((analyze && typeof analyze === "object" ? analyze.analysis : {}) || {}),
      variantCount: Array.isArray(plan?.variants) ? plan.variants.length : 0,
      trackCount: Array.isArray(apply?.timeline?.tracks) ? apply.timeline.tracks.length : 0,
      validate: {
        mode: validate?.recommended?.mode,
        engine: validate?.recommended?.engine,
        modelId: validate?.recommended?.model_id,
        reason: validate?.recommended?.reason,
        diagnostics: Array.isArray(validate?.recommended?.diagnostics) ? validate.recommended.diagnostics : [],
      },
      run: {
        pathType: usesInternalPipeline ? "internal_or_proxy" : "queued_comfy_pipeline",
        renderMode: run?.render_mode,
        selectedMode: run?.selected?.mode,
        selectedEngine: run?.selected?.engine,
        selectedModel: run?.selected?.model_id,
        preflightMode: run?.preflight?.mode,
        renderEnqueued: run?.render_enqueued ?? null,
        assembleJobId: run?.assemble_job?.id ?? null,
      },
      job: {
        id: job.id,
        status: job.status,
        type: job.type,
        error: job.error || null,
      },
      outputs: {
        videoCount: Array.isArray(outputs?.videos) ? outputs.videos.length : 0,
        latestMode: outputs?.latest_internal_render?.mode || null,
        latestVideo: outputs?.latest_internal_render?.video || null,
        activeInternalJobs: Array.isArray(outputs?.active_internal_jobs)
          ? outputs.active_internal_jobs.map((entry) => `${entry.id}:${entry.status}`)
          : [],
      },
      tickHistory,
      projectJobs: Array.isArray(jobs?.jobs)
        ? jobs.jobs.map((entry) => ({ id: entry.id, status: entry.status, type: entry.type, error: entry.error || null }))
        : [],
    };

    console.log(JSON.stringify(summary, null, 2));

    assert.equal(summary.setupStatus.backendBundleOk, true, "Packaged backend bundle should be available");
    assert.equal(summary.setupStatus.ffmpegOk, true, "Bundled FFmpeg should be available");
    assert.equal(summary.setupStatus.edmgAvailable, true, "Bundled EDMG Core should be available");
    assert.equal(summary.paths.studioHome, studioHome, "Packaged config should report the requested Studio home");
    assert.equal(summary.paths.dataDir, expectedDataDir, "Packaged config data_dir should live under Studio home");
    assert.equal(summary.paths.modelsDir, expectedModelsDir, "Packaged config models_dir should live under Studio home");
    assert.equal(summary.paths.ollamaModelsDir, expectedOllamaModelsDir, "Packaged config ollama_models_dir should live under Studio models");
    assert.equal(summary.paths.logsDir, expectedLogsDir, "Packaged config logs_dir should live under Studio home");
    assert.equal(summary.paths.externalDir, expectedExternalDir, "Packaged config external_dir should live under Studio home");
    assert.equal(summary.setupStatus.ollamaManagedModelsDir, expectedOllamaModelsDir, "Setup status should expose the managed Ollama models root");
    assert.equal(summary.paths.dataDirExists, true, "Packaged run should create the Studio data root");
    assert.equal(summary.paths.modelsDirExists, true, "Packaged run should create the Studio models root");
    assert.equal(summary.paths.ollamaModelsDirExists, true, "Packaged run should create the Studio Ollama models root");
    assert.equal(summary.paths.logsDirExists, true, "Packaged run should create the Studio logs root");
    assert.equal(summary.paths.externalDirExists, true, "Packaged run should create the Studio external root");
    assert.equal(summary.variantCount > 0, true, "Expected at least one planned variant");
    assert.equal(summary.trackCount > 0, true, "Expected timeline tracks after apply");
    assert.equal(summary.job.status, "succeeded", "Packaged render job should succeed");
    assert.equal(summary.outputs.videoCount > 0, true, "Expected rendered videos in outputs");
    if (!comfyOk) {
      assert.equal(["proxy", "internal"].includes(String(summary.validate.mode)), true, `Expected internal fallback recommendation when ComfyUI is unavailable, got ${summary.validate.mode}`);
      if (summary.run.renderMode) {
        assert.equal(["proxy", "internal"].includes(String(summary.run.renderMode)), true, `Expected internal/proxy packaged render mode, got ${summary.run.renderMode}`);
      }
      if (summary.outputs.latestMode) {
        assert.equal(["proxy", "internal"].includes(String(summary.outputs.latestMode)), true, `Expected latest packaged fallback mode, got ${summary.outputs.latestMode}`);
      }
    } else {
      assert.equal(["stills", "motion"].includes(String(summary.validate.mode)), true, `Expected stills/motion recommendation when ComfyUI is available, got ${summary.validate.mode}`);
      assert.equal(Boolean(summary.run.assembleJobId), true, "ComfyUI path should return an assemble job id");
    }
  } finally {
    await killProcessTree(child);
  }
}

main().catch((error) => {
  console.error("[packaged-customer-flow] FAILED", error);
  process.exit(1);
});
