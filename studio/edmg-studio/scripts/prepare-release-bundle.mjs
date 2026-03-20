import crypto from "node:crypto";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const pythonBackendDir = path.join(root, "python_backend");
const electronBackendDir = path.join(root, "electron-resources", "backend");
const backendBinaryName = process.platform === "win32" ? "edmg-studio-backend.exe" : "edmg-studio-backend";
const bundledBackendPath = path.join(electronBackendDir, backendBinaryName);
const bundleManifestPath = path.join(electronBackendDir, "backend-bundle-manifest.json");

const ignoredDirNames = new Set([
  ".git",
  ".mypy_cache",
  ".pytest_cache",
  "__pycache__",
  "build",
  "dist",
  "venv",
]);

const trackedRootFiles = new Set([
  "backend_entry.py",
  "pyinstaller.spec",
  "pyproject.toml",
  "README.md",
]);

function runChecked(label, command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd ?? root,
    stdio: "inherit",
    shell: false,
  });
  if (result.status !== 0) {
    throw new Error(`${label} failed with exit code ${result.status ?? "unknown"}`);
  }
}

function canRun(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd ?? root,
    stdio: "ignore",
    shell: false,
  });
  return result.status === 0;
}

function resolvePythonBootstrapCommand() {
  const envPython = process.env.EDMG_STUDIO_PYTHON;
  const candidates = [
    envPython ? { command: envPython, prefix: [] } : null,
    { command: "python", prefix: [] },
    process.platform === "win32" ? { command: "py", prefix: ["-3"] } : null,
  ].filter(Boolean);

  for (const candidate of candidates) {
    if (canRun(candidate.command, [...candidate.prefix, "--version"], { cwd: root })) {
      return candidate;
    }
  }

  throw new Error("Could not find a usable Python command. Set EDMG_STUDIO_PYTHON or install Python.");
}

function venvPythonPath() {
  if (process.platform === "win32") {
    return path.join(pythonBackendDir, "venv", "Scripts", "python.exe");
  }
  return path.join(pythonBackendDir, "venv", "bin", "python");
}

function collectBackendFiles(dir, acc = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (ignoredDirNames.has(entry.name)) continue;
    if (entry.name.endsWith(".egg-info")) continue;

    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      collectBackendFiles(fullPath, acc);
      continue;
    }
    if (!entry.isFile()) continue;
    acc.push(fullPath);
  }
  return acc;
}

async function computeBackendSourceFingerprint() {
  const files = [];
  for (const name of trackedRootFiles) {
    const fullPath = path.join(pythonBackendDir, name);
    if (fs.existsSync(fullPath)) files.push(fullPath);
  }

  const packageDirs = collectBackendFiles(pythonBackendDir, []);
  for (const file of packageDirs) {
    if (!files.includes(file)) files.push(file);
  }

  files.sort((a, b) => a.localeCompare(b));
  const hash = crypto.createHash("sha256");
  let newestSourceMtimeMs = 0;

  for (const file of files) {
    const rel = path.relative(pythonBackendDir, file).split(path.sep).join("/");
    const stat = await fsp.stat(file);
    newestSourceMtimeMs = Math.max(newestSourceMtimeMs, stat.mtimeMs);
    hash.update(rel);
    hash.update("\n");
    hash.update(await fsp.readFile(file));
    hash.update("\n");
  }

  return {
    sourceHash: hash.digest("hex"),
    fileCount: files.length,
    newestSourceMtimeMs,
  };
}

async function sha256File(filePath) {
  const hash = crypto.createHash("sha256");
  hash.update(await fsp.readFile(filePath));
  return hash.digest("hex");
}

function readBundleManifest() {
  if (!fs.existsSync(bundleManifestPath)) return null;
  try {
    return JSON.parse(fs.readFileSync(bundleManifestPath, "utf8"));
  } catch {
    return null;
  }
}

function currentBundleMatches(sourceHash) {
  const manifest = readBundleManifest();
  return Boolean(manifest && manifest.sourceHash === sourceHash && fs.existsSync(bundledBackendPath));
}

function distBackendCandidates() {
  return [
    path.join(pythonBackendDir, "dist", "edmg-studio-backend", backendBinaryName),
    path.join(pythonBackendDir, "dist", backendBinaryName),
  ];
}

function findFreshDistBackend(newestSourceMtimeMs) {
  for (const candidate of distBackendCandidates()) {
    if (!fs.existsSync(candidate)) continue;
    const stat = fs.statSync(candidate);
    if (stat.mtimeMs >= newestSourceMtimeMs) {
      return candidate;
    }
  }
  return "";
}

function ensureBackendBuild() {
  const bootstrap = resolvePythonBootstrapCommand();
  const venvPython = venvPythonPath();

  if (fs.existsSync(venvPython) && !canRun(venvPython, ["--version"], { cwd: pythonBackendDir })) {
    fs.rmSync(path.join(pythonBackendDir, "venv"), { recursive: true, force: true });
  }

  if (!fs.existsSync(venvPython)) {
    runChecked("create backend virtualenv", bootstrap.command, [...bootstrap.prefix, "-m", "venv", "venv"], {
      cwd: pythonBackendDir,
    });
  }

  runChecked("upgrade backend packaging tools", venvPython, ["-m", "pip", "install", "-U", "pip", "wheel", "setuptools"], {
    cwd: pythonBackendDir,
  });
  runChecked("install backend studio bundle", venvPython, ["-m", "pip", "install", "-e", ".[studio_bundle]"], {
    cwd: pythonBackendDir,
  });
  runChecked("install pyinstaller", venvPython, ["-m", "pip", "install", "pyinstaller"], {
    cwd: pythonBackendDir,
  });
  runChecked("build backend bundle", venvPython, ["-m", "PyInstaller", ".\\pyinstaller.spec", "--clean", "--noconfirm"], {
    cwd: pythonBackendDir,
  });

  const built = distBackendCandidates().find((candidate) => fs.existsSync(candidate));
  if (!built) {
    throw new Error(`Backend build completed but ${backendBinaryName} was not found under python_backend/dist`);
  }
  return built;
}

async function stageBackendBundle(sourcePath, fingerprint, reusedExistingBuild) {
  await fsp.mkdir(electronBackendDir, { recursive: true });
  await fsp.copyFile(sourcePath, bundledBackendPath);

  const manifest = {
    ok: true,
    builder: "scripts/prepare-release-bundle.mjs",
    sourceHash: fingerprint.sourceHash,
    sourceFileCount: fingerprint.fileCount,
    newestSourceMtimeMs: fingerprint.newestSourceMtimeMs,
    bundledBackend: path.relative(root, bundledBackendPath).split(path.sep).join("/"),
    sourceArtifact: path.relative(root, sourcePath).split(path.sep).join("/"),
    binarySha256: await sha256File(bundledBackendPath),
    reusedExistingBuild,
    preparedAt: new Date().toISOString(),
  };
  await fsp.writeFile(bundleManifestPath, JSON.stringify(manifest, null, 2) + "\n", "utf8");
  return manifest;
}

async function main() {
  runChecked("prepare electron build assets", process.execPath, [path.join(__dirname, "prepare-electron-build.mjs")], {
    cwd: root,
  });

  const fingerprint = await computeBackendSourceFingerprint();
  if (currentBundleMatches(fingerprint.sourceHash)) {
    console.log(
      JSON.stringify(
        {
          ok: true,
          skippedRebuild: true,
          reason: "bundled backend already matches current backend sources",
          bundleManifestPath,
          sourceHash: fingerprint.sourceHash,
        },
        null,
        2,
      ),
    );
    return;
  }

  let sourceArtifact = findFreshDistBackend(fingerprint.newestSourceMtimeMs);
  let reusedExistingBuild = Boolean(sourceArtifact);
  if (!sourceArtifact) {
    sourceArtifact = ensureBackendBuild();
    reusedExistingBuild = false;
  }

  const manifest = await stageBackendBundle(sourceArtifact, fingerprint, reusedExistingBuild);
  console.log(JSON.stringify({ ok: true, bundleManifestPath, manifest }, null, 2));
}

main().catch((error) => {
  console.error("[prepare-release-bundle] FAILED", error);
  process.exit(1);
});
