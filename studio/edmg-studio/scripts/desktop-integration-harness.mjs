import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const require = createRequire(import.meta.url);

function log(msg) {
  console.log(`[desktop-integration] ${msg}`);
}

function read(rel) {
  return fs.readFileSync(path.join(root, rel), "utf8");
}

function assertSourceCoverage() {
  const desktopArtifacts = read("src/components/desktopArtifacts.ts");
  assert.match(desktopArtifacts, /copied_path_fallback/, "desktopArtifacts.ts must preserve browser fallback");
  assert.match(desktopArtifacts, /hasDesktopPathBridge/, "desktopArtifacts.ts must detect Electron bridge");

  for (const rel of ["src/pages/Render.tsx", "src/pages/Outputs.tsx", "src/pages/RenderQueue.tsx"]) {
    const text = read(rel);
    assert.match(text, /desktopArtifacts/, `${rel} must import desktop artifact helper`);
    assert.match(text, /desktopActionLabel\(/, `${rel} must render desktop action labels`);
  }
}

async function assertPreloadContract() {
  const preloadPath = path.join(root, "preload.cjs");
  const Module = require("node:module");
  const originalLoad = Module._load;
  const exposed = {};
  const calls = [];
  const mockElectron = {
    contextBridge: {
      exposeInMainWorld(name, value) {
        exposed[name] = value;
      },
    },
    shell: {
      openExternal(url) {
        calls.push(["openExternal", url]);
        return Promise.resolve("");
      },
    },
    ipcRenderer: {
      invoke(channel, payload) {
        calls.push([channel, payload]);
        return Promise.resolve({ ok: true, action: channel, path: payload ?? null });
      },
    },
  };
  Module._load = function patched(request, parent, isMain) {
    if (request === "electron") return mockElectron;
    return originalLoad.call(this, request, parent, isMain);
  };
  const previous = process.env.EDMG_STUDIO_TEST_MODE;
  process.env.EDMG_STUDIO_TEST_MODE = "1";
  delete require.cache[preloadPath];
  try {
    require(preloadPath);
  } finally {
    Module._load = originalLoad;
    if (previous == null) delete process.env.EDMG_STUDIO_TEST_MODE;
    else process.env.EDMG_STUDIO_TEST_MODE = previous;
    delete require.cache[preloadPath];
  }

  assert.ok(exposed.edmg, "preload must expose window.edmg");
  assert.equal(typeof exposed.edmg.getBackendUrl, "function");
  assert.equal(typeof exposed.edmg.revealPath, "function");
  assert.equal(typeof exposed.edmg.openPath, "function");
  assert.equal(typeof exposed.__edmgTest?.writeReport, "function", "test bridge must be exposed in test mode");

  await exposed.edmg.revealPath("/tmp/demo.txt");
  await exposed.edmg.openPath("/tmp");
  await exposed.__edmgTest.writeReport({ ok: true });
  const channels = calls.map(([name]) => name);
  assert.ok(channels.includes("edmg:revealPath"), "preload revealPath must use IPC");
  assert.ok(channels.includes("edmg:openPath"), "preload openPath must use IPC");
  assert.ok(channels.includes("edmg:testWriteReport"), "test report must use IPC");
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
  if (!resolveElectronBinary()) return { ok: false, reason: "Electron binary unavailable (likely npm install --ignore-scripts without postinstall download)." };
  if (process.platform === "linux" && !process.env.DISPLAY && !process.env.WAYLAND_DISPLAY) {
    const xvfb = "/usr/bin/xvfb-run";
    if (!fs.existsSync(xvfb)) {
      return { ok: false, reason: "No DISPLAY/WAYLAND session and xvfb-run not available." };
    }
  }
  return { ok: true, reason: "Electron launch supported" };
}

async function startMockBackend() {
  const server = http.createServer((req, res) => {
    if (req.url === "/health") {
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ ok: true, service: "desktop-integration-mock" }));
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

function buildProbeHtml({ fixtureFile, fixtureDir, expectedBackendUrl }) {
  const payload = JSON.stringify({ fixtureFile, fixtureDir, expectedBackendUrl });
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
          errors: [],
        };
        try {
          out.backendUrlSync = typeof window.edmg?.backendUrl === 'function' ? window.edmg.backendUrl() : null;
          out.backendUrlAsync = typeof window.edmg?.getBackendUrl === 'function' ? await window.edmg.getBackendUrl() : null;
          out.reveal = typeof window.edmg?.revealPath === 'function' ? await window.edmg.revealPath(fixture.fixtureFile) : null;
          out.open = typeof window.edmg?.openPath === 'function' ? await window.edmg.openPath(fixture.fixtureDir) : null;
          out.ok = Boolean(
            out.bridgeAvailable &&
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

async function runElectronProbe() {
  const support = canLaunchElectron();
  if (!support.ok) {
    return { ok: true, skipped: true, reason: support.reason };
  }

  const fixtureRoot = fs.mkdtempSync(path.join(os.tmpdir(), "edmg-desktop-int-"));
  const fixtureDir = path.join(fixtureRoot, "frames");
  fs.mkdirSync(fixtureDir, { recursive: true });
  const fixtureFile = path.join(fixtureRoot, "demo.txt");
  fs.writeFileSync(fixtureFile, "desktop integration probe\n");
  const reportPath = path.join(fixtureRoot, "report.json");
  const htmlPath = path.join(fixtureRoot, "probe.html");

  const { server, port } = await startMockBackend();
  const expectedBackendUrl = `http://127.0.0.1:${port}`;
  fs.writeFileSync(htmlPath, buildProbeHtml({ fixtureFile, fixtureDir, expectedBackendUrl }));

  const electronBinary = resolveElectronBinary();
  const args = ["."];
  let cmd = electronBinary;
  if (process.platform === "linux" && !process.env.DISPLAY && !process.env.WAYLAND_DISPLAY && fs.existsSync("/usr/bin/xvfb-run")) {
    cmd = "/usr/bin/xvfb-run";
    args.unshift("-a", electronBinary);
  }

  log(`launching Electron probe using ${cmd}`);
  const child = spawn(cmd, args, {
    cwd: root,
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
    const report = JSON.parse(fs.readFileSync(reportPath, "utf8"));
    assert.equal(report.ok, true, `Electron probe failed: ${JSON.stringify(report)}`);
    assert.equal(report.backendUrlAsync, expectedBackendUrl);
    assert.equal(report.backendUrlSync, expectedBackendUrl);
    assert.equal(report.reveal?.ok, true);
    assert.equal(report.open?.ok, true);
    return { ok: true, skipped: false, report };
  } finally {
    server.close();
    if (exitCode === null) {
      child.kill("SIGTERM");
    }
  }
}

async function main() {
  assertSourceCoverage();
  log("source coverage checks passed");

  await assertPreloadContract();
  log("preload contract checks passed");

  const electronProbe = await runElectronProbe();
  if (electronProbe.skipped) {
    log(`live Electron probe skipped: ${electronProbe.reason}`);
  } else {
    log("live Electron probe passed");
  }

  const summary = {
    ok: true,
    sourceCoverage: true,
    preloadContract: true,
    electronProbe,
  };
  console.log(JSON.stringify(summary, null, 2));
}

main().catch((error) => {
  console.error("[desktop-integration] FAILED", error);
  process.exit(1);
});
