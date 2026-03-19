import assert from "node:assert/strict";
import fs from "node:fs";
import fsp from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");

function log(msg) {
  console.log(`[packaged-desktop-smoke] ${msg}`);
}

function resolveElectronBinary() {
  const envPath = process.env.EDMG_STUDIO_ELECTRON_BINARY;
  if (envPath && fs.existsSync(envPath)) return envPath;
  const candidates = [
    path.join(root, "node_modules", "electron", "dist", process.platform === "win32" ? "electron.exe" : "electron"),
    path.join(root, "node_modules", "electron", "dist", "Electron.app", "Contents", "MacOS", "Electron"),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  return "";
}

function canLaunchElectron() {
  if (!resolveElectronBinary()) {
    return { ok: false, reason: "Electron binary unavailable (likely npm install --ignore-scripts without postinstall download)." };
  }
  if (process.platform === "linux" && !process.env.DISPLAY && !process.env.WAYLAND_DISPLAY) {
    const xvfb = "/usr/bin/xvfb-run";
    if (!fs.existsSync(xvfb)) {
      return { ok: false, reason: "No DISPLAY/WAYLAND session and xvfb-run not available." };
    }
  }
  return { ok: true, reason: "Electron launch supported" };
}

import { assertDesktopArtifacts, stageDesktopRelease } from './release-stage-lib.mjs';

function bundledResourcePaths(appDir) {
  return {
    backendExe: path.join(appDir, "electron-resources", "backend", process.platform === "win32" ? "edmg-studio-backend.exe" : "edmg-studio-backend"),
    ffmpegExe: path.join(appDir, "electron-resources", "bin", process.platform === "win32" ? "ffmpeg.exe" : "ffmpeg"),
  };
}

async function startMockBackend() {
  const server = http.createServer((req, res) => {
    if (req.url === "/health") {
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ ok: true, service: "packaged-desktop-smoke-mock" }));
      return;
    }
    res.writeHead(404, { "content-type": "application/json" });
    res.end(JSON.stringify({ ok: false, error: { message: "Not found" } }));
  });
  await new Promise((resolve, reject) => {
    server.listen(0, "127.0.0.1", () => resolve());
    server.on("error", reject);
  });
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Unable to bind mock backend");
  return { server, port: address.port };
}

function buildProbeHtml({ fixtureFile, fixtureDir, expectedBackendUrl, stageDir }) {
  const payload = JSON.stringify({ fixtureFile, fixtureDir, expectedBackendUrl, stageDir });
  return `<!doctype html>
<html>
  <body>
    <pre id="status">starting</pre>
    <script>
      const fixture = ${payload};
      async function run() {
        const out = {
          ok: false,
          bridgeAvailable: !!window.edmg,
          backendUrlSync: null,
          backendUrlAsync: null,
          reveal: null,
          open: null,
          stageDir: fixture.stageDir,
          errors: [],
        };
        try {
          out.backendUrlSync = typeof window.edmg?.backendUrl === 'function' ? window.edmg.backendUrl() : null;
          out.backendUrlAsync = typeof window.edmg?.getBackendUrl === 'function' ? await window.edmg.getBackendUrl() : null;
          out.reveal = typeof window.edmg?.revealPath === 'function' ? await window.edmg.revealPath(fixture.fixtureFile) : null;
          out.open = typeof window.edmg?.openPath === 'function' ? await window.edmg.openPath(fixture.fixtureDir) : null;
          out.ok = Boolean(
            out.bridgeAvailable &&
            out.backendUrlSync === fixture.expectedBackendUrl &&
            out.backendUrlAsync === fixture.expectedBackendUrl &&
            out.reveal?.ok &&
            out.open?.ok
          );
        } catch (error) {
          out.errors.push(String(error && error.message ? error.message : error));
        }
        document.getElementById('status').textContent = JSON.stringify(out, null, 2);
        if (window.__edmgTest?.writeReport) {
          await window.__edmgTest.writeReport(out);
        }
      }
      run();
    </script>
  </body>
</html>`;
}

async function waitForFile(filePath, timeoutMs = 20000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (fs.existsSync(filePath) && fs.statSync(filePath).size > 0) return;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(`Timed out waiting for ${filePath}`);
}

async function runStagedAppProbe() {
  const support = canLaunchElectron();
  const stageManifest = await stageDesktopRelease();
  const appDir = stageManifest.stageDir;
  const summary = {
    ok: true,
    skipped: !support.ok,
    reason: support.reason,
    appDir,
    stageManifestPath: path.join(appDir, '.edmg-stage', 'manifest.json'),
  };
  const resources = bundledResourcePaths(appDir);

  const distIndex = path.join(appDir, "dist", "index.html");
  assert.ok(fs.existsSync(distIndex), "staged app dist/index.html missing");
  assert.ok(fs.existsSync(path.join(appDir, 'electron-builder.yml')), 'staged app electron-builder.yml missing');
  assert.ok(fs.existsSync(resources.backendExe), `staged app missing bundled backend: ${resources.backendExe}`);
  assert.ok(fs.existsSync(resources.ffmpegExe), `staged app missing bundled ffmpeg: ${resources.ffmpegExe}`);
  summary.resources = resources;

  if (!support.ok) {
    return summary;
  }

  const fixtureRoot = await fsp.mkdtemp(path.join(os.tmpdir(), "edmg-packaged-probe-"));
  const fixtureDir = path.join(fixtureRoot, "frames");
  await fsp.mkdir(fixtureDir, { recursive: true });
  const fixtureFile = path.join(fixtureRoot, "demo.txt");
  await fsp.writeFile(fixtureFile, "packaged desktop smoke probe\n");
  const reportPath = path.join(fixtureRoot, "report.json");
  const htmlPath = path.join(appDir, "dist", "packaged-smoke-probe.html");

  const { server, port } = await startMockBackend();
  const expectedBackendUrl = `http://127.0.0.1:${port}`;
  await fsp.writeFile(htmlPath, buildProbeHtml({ fixtureFile, fixtureDir, expectedBackendUrl, stageDir: appDir }));

  const electronBinary = resolveElectronBinary();
  const args = ["."];
  let cmd = electronBinary;
  if (process.platform === "linux" && !process.env.DISPLAY && !process.env.WAYLAND_DISPLAY && fs.existsSync("/usr/bin/xvfb-run")) {
    cmd = "/usr/bin/xvfb-run";
    args.unshift("-a", electronBinary);
  }

  log(`launching staged app probe using ${cmd}`);
  const child = spawn(cmd, args, {
    cwd: appDir,
    env: {
      ...process.env,
      EDMG_STUDIO_TEST_MODE: "1",
      EDMG_STUDIO_TEST_PAGE: htmlPath,
      EDMG_STUDIO_TEST_REPORT_PATH: reportPath,
      EDMG_STUDIO_TEST_FAKE_PATH_ACTIONS: "1",
      EDMG_STUDIO_SPAWN_BACKEND: "0",
      EDMG_STUDIO_BACKEND_PORT: String(port),
      ELECTRON_DISABLE_SECURITY_WARNINGS: "1",
    },
    stdio: "inherit",
  });

  let exitCode = null;
  child.on("exit", (code) => {
    exitCode = code;
  });

  try {
    await waitForFile(reportPath, 25000);
    const report = JSON.parse(await fsp.readFile(reportPath, "utf8"));
    assert.equal(report.ok, true, `Staged app probe failed: ${JSON.stringify(report)}`);
    assert.equal(report.backendUrlSync, expectedBackendUrl);
    assert.equal(report.backendUrlAsync, expectedBackendUrl);
    assert.equal(report.reveal?.ok, true);
    assert.equal(report.open?.ok, true);
    return { ...summary, skipped: false, report };
  } finally {
    server.close();
    if (exitCode === null) child.kill("SIGTERM");
  }
}

async function main() {
  assertDesktopArtifacts();
  log("build artifact checks passed");

  const stagedProbe = await runStagedAppProbe();
  if (stagedProbe.skipped) {
    log(`staged app probe skipped: ${stagedProbe.reason}`);
  } else {
    log("staged app probe passed");
  }

  console.log(JSON.stringify({ ok: true, buildArtifacts: true, stagedProbe }, null, 2));
}

main().catch((error) => {
  console.error("[packaged-desktop-smoke] FAILED", error);
  process.exit(1);
});
