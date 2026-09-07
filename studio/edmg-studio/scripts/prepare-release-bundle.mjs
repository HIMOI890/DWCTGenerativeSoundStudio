import crypto from "node:crypto";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";

import {
  PINNED_UV_VERSION,
  RELEASE_CAPABILITY_EXTRAS,
  REQUIRED_LINUX_SETUP_FILES,
  RELEASE_MANIFEST_SCHEMA_VERSION,
  assertNoDynamicDependencyOverrides,
  assertPinnedUvVersion,
  assertPython312,
  assertTorchIndexForProfile,
  assertTrackedCleanDependencyStatus,
  assertValidReleaseManifest,
  backendEntryPointForPlatform,
  bundleMatchesManifest,
  collectBundleEntries,
  isHfRuntimeEvidencePath,
  materializeExternalBundleSymlinks,
  releaseProvenanceMatches,
  releaseUvEnvironment,
  resolveAcceleratorProfile,
  sha256File,
  uvLockCheckArgs,
  uvRunArgs,
  uvSyncArgs,
} from "./release-python-toolchain.mjs";
import { writeReleaseEvidence } from "./release-evidence-lib.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const repoRoot = path.resolve(root, "..", "..");
const pythonBackendDir = path.join(root, "python_backend");
const pythonVersionPath = path.join(repoRoot, ".python-version");
const pyprojectPath = path.join(pythonBackendDir, "pyproject.toml");
const uvLockPath = path.join(pythonBackendDir, "uv.lock");
const hfBucketHelperDir = path.join(pythonBackendDir, "hf_bucket_helper");
const hfBucketHelperPyprojectPath = path.join(hfBucketHelperDir, "pyproject.toml");
const hfBucketHelperLockPath = path.join(hfBucketHelperDir, "uv.lock");
const hfBucketHelperSpecPath = path.join(hfBucketHelperDir, "pyinstaller.spec");
const launcherDefaultsPath = path.join(root, "launcher_env.defaults.json");
const provenanceScriptPath = path.join(__dirname, "release_provenance.py");
const toolchainScriptPath = path.join(__dirname, "release-python-toolchain.mjs");
const electronBackendDir = path.join(root, "electron-resources", "backend");
const electronResourcesDir = path.dirname(electronBackendDir);
const backendStagingDir = path.join(electronResourcesDir, "backend.staging");
const backendPreviousDir = path.join(electronResourcesDir, "backend.previous");
const directorAppDir = path.resolve(root, "..", "..", "chatgpt-apps", "edmg-director");
const electronDirectorDir = path.join(root, "electron-resources", "director");
const directorBundleManifestPath = path.join(electronDirectorDir, "director-bundle-manifest.json");
const releasePlatform = process.platform;
const backendBinaryName = backendEntryPointForPlatform(releasePlatform);
const hfBucketHelperBinaryName = process.platform === "win32" ? "edmg-hf-bucket-helper.exe" : "edmg-hf-bucket-helper";
const builtHfBucketHelperPath = path.join(hfBucketHelperDir, "dist", hfBucketHelperBinaryName);
const bundledBackendPath = path.join(electronBackendDir, backendBinaryName);
const bundleManifestPath = path.join(electronBackendDir, "backend-bundle-manifest.json");
const pnpmCommand = process.platform === "win32" ? "pnpm.cmd" : "pnpm";
const linuxSetupAssetPaths = REQUIRED_LINUX_SETUP_FILES.map((relativePath) => path.join(root, relativePath));

const dependencyInputPaths = [
  pythonVersionPath,
  pyprojectPath,
  uvLockPath,
  hfBucketHelperPyprojectPath,
  hfBucketHelperLockPath,
  launcherDefaultsPath,
  ...(releasePlatform === "linux" ? linuxSetupAssetPaths : []),
];
const requiredBackendSourceFiles = [
  "edmg_studio_backend/__init__.py",
  "edmg_studio_backend/app.py",
  "edmg_studio_backend/integrations/hf_bucket.py",
  "edmg_studio_backend/services/internal_video.py",
  "edmg_studio_backend/services/internal_video_models.py",
  "edmg_studio_backend/services/model_catalog.py",
  "edmg_studio_backend/services/model_manager.py",
  "edmg_studio_backend/services/model_cache_settings.py",
  "edmg_studio_backend/services/tensorrt_standalone.py",
  "edmg_studio_backend/services/tensorrt_video.py",
];

function runChecked(label, command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd ?? root,
    env: options.env ?? process.env,
    stdio: "inherit",
    shell: false,
  });
  if (result.error) throw new Error(`${label} failed: ${result.error.message}`);
  if (result.status !== 0) {
    throw new Error(`${label} failed with exit code ${result.status ?? "unknown"}`);
  }
}

function runCaptured(label, command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd ?? root,
    env: options.env ?? process.env,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
    shell: false,
  });
  if (result.error) throw new Error(`${label} failed: ${result.error.message}`);
  if (result.status !== 0) {
    const detail = String(result.stderr || result.stdout || "").trim();
    throw new Error(`${label} failed with exit code ${result.status ?? "unknown"}${detail ? `: ${detail}` : ""}`);
  }
  return String(result.stdout || "").trim();
}

function runPnpmChecked(label, args, options = {}) {
  const execPath = String(process.env.npm_execpath || "").trim();
  if (execPath && fs.existsSync(execPath)) {
    if (/\.(?:c?js|mjs)$/i.test(execPath)) {
      runChecked(label, process.execPath, [execPath, ...args], options);
      return;
    }
    runChecked(label, execPath, args, options);
    return;
  }
  runChecked(label, pnpmCommand, args, options);
}

function repoRelative(filePath) {
  return path.relative(repoRoot, filePath).split(path.sep).join("/");
}

function assertRequiredFiles() {
  const missing = [
    ...dependencyInputPaths,
    provenanceScriptPath,
    toolchainScriptPath,
    hfBucketHelperSpecPath,
    ...requiredBackendSourceFiles.map((relativePath) => path.join(pythonBackendDir, relativePath)),
  ].filter((filePath) => !fs.existsSync(filePath));
  if (missing.length) {
    throw new Error(`Release bundle is missing required inputs: ${missing.map(repoRelative).join(", ")}`);
  }
  const pythonPin = fs.readFileSync(pythonVersionPath, "utf8").trim();
  if (pythonPin !== "3.12") {
    throw new Error(`.python-version must contain exactly 3.12 for release builds; got ${JSON.stringify(pythonPin)}`);
  }
}

function assertTrackedCleanDependencyInputs() {
  const relativePaths = dependencyInputPaths.map(repoRelative);
  const tracked = spawnSync("git", ["ls-files", "--error-unmatch", "--", ...relativePaths], {
    cwd: repoRoot,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
    shell: false,
  });
  if (tracked.error) throw new Error(`Could not verify tracked dependency inputs: ${tracked.error.message}`);
  const dirty = runCaptured(
    "check release dependency input state",
    "git",
    ["status", "--porcelain=v1", "--", ...relativePaths],
    { cwd: repoRoot },
  );
  assertTrackedCleanDependencyStatus({
    trackedStatus: tracked.status,
    dirtyStatus: dirty,
    paths: relativePaths,
  });
}

function resolveUv() {
  const uvCommand = String(process.env.EDMG_UV || "uv").trim();
  if (!uvCommand) throw new Error("EDMG_UV must not be empty");
  const versionOutput = runCaptured("query uv version", uvCommand, ["--version"], { cwd: pythonBackendDir });
  const uvVersion = assertPinnedUvVersion(versionOutput, PINNED_UV_VERSION);
  return { uvCommand, uvVersion };
}

function synchronizeReleaseEnvironment(uvCommand, profile, env) {
  runChecked("validate committed uv lock", uvCommand, uvLockCheckArgs(), { cwd: pythonBackendDir, env });
  runChecked("synchronize frozen release environment", uvCommand, uvSyncArgs(profile), { cwd: pythonBackendDir, env });
}

function hfBucketHelperEnvironment(env) {
  return {
    ...env,
    UV_PROJECT_ENVIRONMENT: path.join(root, "release", "uv-environments", "hf-bucket-helper"),
    UV_LINK_MODE: "copy",
  };
}

function buildHfBucketHelper(uvCommand, env) {
  const helperEnv = hfBucketHelperEnvironment(env);
  runChecked("validate Hugging Face Bucket helper lock", uvCommand, ["lock", "--check"], {
    cwd: hfBucketHelperDir,
    env: helperEnv,
  });
  runChecked(
    "synchronize frozen Hugging Face Bucket helper environment",
    uvCommand,
    ["sync", "--frozen", "--no-default-groups", "--group", "build"],
    { cwd: hfBucketHelperDir, env: helperEnv },
  );
  runChecked(
    "build isolated Hugging Face Bucket helper",
    uvCommand,
    [
      "run",
      "--frozen",
      "--no-sync",
      "--no-default-groups",
      "--group",
      "build",
      "pyinstaller",
      "pyinstaller.spec",
      "--clean",
      "--noconfirm",
    ],
    { cwd: hfBucketHelperDir, env: helperEnv },
  );
  if (!fs.existsSync(builtHfBucketHelperPath) || !fs.statSync(builtHfBucketHelperPath).isFile()) {
    throw new Error(`Hugging Face Bucket helper build is missing ${builtHfBucketHelperPath}`);
  }
  return builtHfBucketHelperPath;
}

function collectReleaseProvenance(uvCommand, profile, env) {
  const stdout = runCaptured(
    "collect release provenance",
    uvCommand,
    uvRunArgs(profile, [
      "python",
      provenanceScriptPath,
      "--lock",
      uvLockPath,
      "--profile",
      profile,
    ]),
    { cwd: pythonBackendDir, env },
  );
  let payload;
  try {
    payload = JSON.parse(stdout);
  } catch {
    throw new Error(`Release provenance helper returned invalid JSON: ${stdout}`);
  }
  assertPython312(payload.pythonVersion);
  assertTorchIndexForProfile(profile, payload.torchIndex);
  if (!String(payload.pyinstallerVersion || "").trim()) throw new Error("Release provenance omitted PyInstaller version");
  if (!Array.isArray(payload.torchPackages) || payload.torchPackages.length !== 3) {
    throw new Error("Release provenance must include torch, torchvision, and torchaudio");
  }
  const hfRuntime = Object.fromEntries(
    (Array.isArray(payload.hfRuntimePackages) ? payload.hfRuntimePackages : [])
      .map((entry) => [String(entry?.name || ""), String(entry?.version || "")]),
  );
  for (const [name, version] of [
    ["huggingface-hub", "0.36.2"],
    ["hf-transfer", "0.1.9"],
    ["hf-xet", "1.5.1"],
  ]) {
    if (hfRuntime[name] !== version) {
      throw new Error(`Release provenance must include ${name}==${version}`);
    }
  }
  if (!Array.isArray(payload.nltkResources) || payload.nltkResources.length === 0) {
    throw new Error("Release provenance must include pinned NLTK resources");
  }
  return payload;
}

function trackedBackendFiles() {
  const stdout = runCaptured(
    "inventory tracked backend sources",
    "git",
    ["ls-files", "-z", "--", "studio/edmg-studio/python_backend"],
    { cwd: repoRoot },
  );
  const paths = stdout.split("\0").filter(Boolean).map((relativePath) => path.join(repoRoot, relativePath));
  if (!paths.length) throw new Error("No tracked backend source files were found");
  return paths;
}

async function computeBackendSourceFingerprint() {
  const filesByRelativePath = new Map();
  for (const filePath of [
    ...trackedBackendFiles(),
    pythonVersionPath,
    fileURLToPath(import.meta.url),
    toolchainScriptPath,
    provenanceScriptPath,
  ]) {
    if (!fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) continue;
    filesByRelativePath.set(repoRelative(filePath), filePath);
  }

  const files = [...filesByRelativePath.entries()].sort(([left], [right]) => left.localeCompare(right));
  const hash = crypto.createHash("sha256");
  for (const [relativePath, filePath] of files) {
    hash.update(relativePath);
    hash.update("\n");
    hash.update(await fsp.readFile(filePath));
    hash.update("\n");
  }

  const fingerprintInputs = [];
  for (const filePath of dependencyInputPaths) {
    fingerprintInputs.push({ path: repoRelative(filePath), sha256: await sha256File(filePath) });
  }
  return {
    sourceHash: hash.digest("hex"),
    fileCount: files.length,
    fingerprintInputs,
    requiredSources: [...requiredBackendSourceFiles],
  };
}

function readBundleManifest() {
  if (!fs.existsSync(bundleManifestPath)) return null;
  try {
    return JSON.parse(fs.readFileSync(bundleManifestPath, "utf8"));
  } catch {
    return null;
  }
}

function summarizeBundleManifest(manifest) {
  const { bundleEntries: _bundleEntries, ...summary } = manifest;
  return {
    ...summary,
    bundleEntriesRecorded: Array.isArray(manifest.bundleEntries) ? manifest.bundleEntries.length : 0,
  };
}

function distBackendDirectory() {
  return path.join(pythonBackendDir, "dist", "edmg-studio-backend");
}

async function reusableBundle(expected) {
  const manifest = readBundleManifest();
  if (!manifest || !releaseProvenanceMatches(manifest, expected)) return null;
  if (!(await bundleMatchesManifest(electronBackendDir, manifest))) return null;
  return manifest;
}

function buildBackendBundle(uvCommand, profile, env) {
  const helper = buildHfBucketHelper(uvCommand, env);
  runChecked(
    "build backend bundle with frozen uv environment",
    uvCommand,
    uvRunArgs(profile, ["pyinstaller", "pyinstaller.spec", "--clean", "--noconfirm"]),
    { cwd: pythonBackendDir, env },
  );
  const built = distBackendDirectory();
  const builtLauncher = path.join(built, backendBinaryName);
  if (!fs.existsSync(built) || !fs.statSync(built).isDirectory() || !fs.existsSync(builtLauncher)) {
    throw new Error(
      `Backend build completed but the onedir bundle ${path.relative(root, built)} was not complete`,
    );
  }
  fs.copyFileSync(helper, path.join(built, hfBucketHelperBinaryName));
  fs.copyFileSync(launcherDefaultsPath, path.join(built, "launcher_env.defaults.json"));
  if (releasePlatform === "linux") {
    for (const [index, sourcePath] of linuxSetupAssetPaths.entries()) {
      const bundleRelativePath = REQUIRED_LINUX_SETUP_FILES[index];
      const destinationPath = path.join(built, ...bundleRelativePath.split("/"));
      fs.mkdirSync(path.dirname(destinationPath), { recursive: true });
      fs.copyFileSync(sourcePath, destinationPath);
      if (bundleRelativePath.endsWith(".sh")) {
        fs.chmodSync(destinationPath, 0o755);
      }
    }
  }
  return built;
}

function assertOwnedBackendStagePath(target) {
  const resolved = path.resolve(target);
  const allowed = new Set([
    path.resolve(electronBackendDir),
    path.resolve(backendStagingDir),
    path.resolve(backendPreviousDir),
  ]);
  if (!allowed.has(resolved) || path.dirname(resolved) !== path.resolve(electronResourcesDir)) {
    throw new Error(`Refusing to replace unexpected backend bundle path: ${target}`);
  }
  return resolved;
}

async function removeBackendStagePath(target) {
  await fsp.rm(assertOwnedBackendStagePath(target), { recursive: true, force: true });
}

async function prepareBackendStagePaths() {
  await fsp.mkdir(electronResourcesDir, { recursive: true });
  if (!fs.existsSync(electronBackendDir) && fs.existsSync(backendPreviousDir)) {
    await fsp.rename(backendPreviousDir, electronBackendDir);
  }
  await removeBackendStagePath(backendStagingDir);
  if (fs.existsSync(electronBackendDir)) await removeBackendStagePath(backendPreviousDir);
}

async function activateBackendStage() {
  let movedPrevious = false;
  try {
    if (fs.existsSync(electronBackendDir)) {
      await fsp.rename(electronBackendDir, backendPreviousDir);
      movedPrevious = true;
    }
    await fsp.rename(backendStagingDir, electronBackendDir);
  } catch (error) {
    if (movedPrevious && !fs.existsSync(electronBackendDir) && fs.existsSync(backendPreviousDir)) {
      await fsp.rename(backendPreviousDir, electronBackendDir);
    }
    throw error;
  }
  if (movedPrevious) await removeBackendStagePath(backendPreviousDir);
}

async function stageBackendBundle(sourceDirectory, expected) {
  await prepareBackendStagePaths();
  await fsp.cp(sourceDirectory, backendStagingDir, {
    recursive: true,
    force: true,
    dereference: false,
  });
  await materializeExternalBundleSymlinks(backendStagingDir);
  // Preserve the tracked placeholder when replacing the generated directory.
  // It is intentionally part of the full-tree inventory below.
  await fsp.writeFile(path.join(backendStagingDir, ".gitkeep"), "", "utf8");
  const stagedLauncherPath = path.join(backendStagingDir, backendBinaryName);
  if (!fs.existsSync(stagedLauncherPath) || !fs.statSync(stagedLauncherPath).isFile()) {
    throw new Error(`Staged onedir backend is missing ${backendBinaryName}`);
  }
  const bundleEntries = await collectBundleEntries(backendStagingDir);
  const launcher = bundleEntries.find((entry) => entry.path === backendBinaryName && entry.type === "file");
  if (!launcher) throw new Error(`Staged onedir backend inventory is missing ${backendBinaryName}`);
  const bundleFiles = bundleEntries.filter((entry) => entry.type === "file");
  const helper = bundleEntries.find(
    (entry) => entry.path === hfBucketHelperBinaryName && entry.type === "file",
  );
  if (!helper) throw new Error(`Staged onedir backend inventory is missing ${hfBucketHelperBinaryName}`);
  const launcherDefaults = bundleEntries.find(
    (entry) => entry.path === "launcher_env.defaults.json" && entry.type === "file",
  );
  if (!launcherDefaults) {
    throw new Error("Staged onedir backend inventory is missing launcher_env.defaults.json");
  }
  const requireRuntimeEntry = (label, evidenceKey) => {
    const entry = bundleEntries.find(
      (candidate) =>
        candidate.type === "file" &&
        isHfRuntimeEvidencePath(evidenceKey, candidate.path),
    );
    if (!entry) throw new Error(`Staged onedir backend is missing ${label}`);
    return entry.path;
  };
  const hfRuntimeBundleEvidence = {
    huggingfaceHubMetadata: requireRuntimeEntry(
      "huggingface-hub 0.36.2 metadata",
      "huggingfaceHubMetadata",
    ),
    hfTransferMetadata: requireRuntimeEntry(
      "hf-transfer 0.1.9 metadata",
      "hfTransferMetadata",
    ),
    hfTransferModule: requireRuntimeEntry(
      "hf-transfer native module",
      "hfTransferModule",
    ),
    hfXetMetadata: requireRuntimeEntry(
      "hf-xet 1.5.1 metadata",
      "hfXetMetadata",
    ),
    hfXetModule: requireRuntimeEntry(
      "hf-xet native module",
      "hfXetModule",
    ),
  };
  const manifest = {
    schemaVersion: RELEASE_MANIFEST_SCHEMA_VERSION,
    ok: true,
    builder: "scripts/prepare-release-bundle.mjs",
    platform: expected.platform,
    sourceHash: expected.sourceHash,
    sourceFileCount: expected.sourceFileCount,
    requiredBackendSources: expected.requiredBackendSources,
    fingerprintInputs: expected.fingerprintInputs,
    lockSha256: expected.lockSha256,
    acceleratorProfile: expected.acceleratorProfile,
    capabilityExtras: expected.capabilityExtras,
    pythonVersion: expected.pythonVersion,
    pythonImplementation: expected.pythonImplementation,
    uvVersion: expected.uvVersion,
    pyinstallerVersion: expected.pyinstallerVersion,
    torchIndex: expected.torchIndex,
    torchPackages: expected.torchPackages,
    hfRuntimePackages: expected.hfRuntimePackages,
    hfRuntimeBundleEvidence,
    nltkResources: expected.nltkResources,
    bundleLayout: "onedir",
    backendEntryPoint: expected.backendEntryPoint,
    bundleEntries,
    bundleEntryCount: bundleEntries.length,
    bundleFileCount: bundleFiles.length,
    bundleSize: bundleFiles.reduce((total, entry) => total + entry.size, 0),
    bundledBackend: path.relative(root, bundledBackendPath).split(path.sep).join("/"),
    sourceArtifact: path.relative(root, sourceDirectory).split(path.sep).join("/"),
    binarySha256: launcher.sha256,
    binarySize: launcher.size,
    hfBucketHelper: {
      entryPoint: hfBucketHelperBinaryName,
      helperVersion: "1.0.0",
      huggingfaceHubVersion: "1.20.1",
      hfXetVersion: "1.5.1",
      lockSha256: await sha256File(hfBucketHelperLockPath),
      binarySha256: helper.sha256,
      binarySize: helper.size,
    },
    launcherEnvDefaults: {
      entryPoint: "launcher_env.defaults.json",
      sha256: launcherDefaults.sha256,
      size: launcherDefaults.size,
    },
    reusedExistingBuild: false,
    preparedAt: new Date().toISOString(),
  };
  assertValidReleaseManifest(manifest, { expectedProfile: expected.acceleratorProfile });
  await fsp.writeFile(
    path.join(backendStagingDir, path.basename(bundleManifestPath)),
    JSON.stringify(manifest, null, 2) + "\n",
    "utf8",
  );
  await activateBackendStage();
  return manifest;
}

function isPathInside(parentDirectory, candidate) {
  const relative = path.relative(path.resolve(parentDirectory), path.resolve(candidate));
  return relative === "" || (!path.isAbsolute(relative) && relative !== ".." && !relative.startsWith(`..${path.sep}`));
}

async function inspectDirectorDependencyTree(packageJson) {
  const nodeModulesDir = path.join(electronDirectorDir, "node_modules");
  if (!fs.existsSync(nodeModulesDir) || !fs.statSync(nodeModulesDir).isDirectory()) {
    throw new Error("Director production install did not create node_modules");
  }

  const dependencyNames = Object.keys(packageJson.dependencies || {}).sort();
  const missingDependencies = dependencyNames.filter((name) => {
    const packagePath = path.join(nodeModulesDir, ...name.split("/"), "package.json");
    return !fs.existsSync(packagePath) || !fs.statSync(packagePath).isFile();
  });
  if (missingDependencies.length) {
    throw new Error(`Director production install is missing dependencies: ${missingDependencies.join(", ")}`);
  }

  let directoryCount = 0;
  let fileCount = 0;
  let symlinkCount = 0;
  let totalSize = 0;
  async function walk(directory) {
    for (const child of await fsp.readdir(directory, { withFileTypes: true })) {
      const childPath = path.join(directory, child.name);
      if (child.isSymbolicLink()) {
        symlinkCount += 1;
        const target = await fsp.readlink(childPath);
        const resolvedTarget = path.resolve(path.dirname(childPath), target);
        if (!isPathInside(electronDirectorDir, resolvedTarget)) {
          throw new Error(
            `Director production dependency link escapes the bundle: ${path.relative(electronDirectorDir, childPath)} -> ${target}`,
          );
        }
      } else if (child.isDirectory()) {
        directoryCount += 1;
        await walk(childPath);
      } else if (child.isFile()) {
        fileCount += 1;
        totalSize += (await fsp.stat(childPath)).size;
      } else {
        throw new Error(`Unsupported Director dependency entry: ${childPath}`);
      }
    }
  }
  await walk(nodeModulesDir);
  if (fileCount === 0) throw new Error("Director production dependency tree is empty");
  return { dependencyNames, directoryCount, fileCount, symlinkCount, totalSize };
}

async function stageDirectorBundle() {
  if (!fs.existsSync(directorAppDir)) throw new Error(`Director app directory is missing: ${directorAppDir}`);
  for (const name of ["package.json", "pnpm-lock.yaml"]) {
    if (!fs.existsSync(path.join(directorAppDir, name))) {
      throw new Error(`Director frozen dependency input is missing: ${path.join(directorAppDir, name)}`);
    }
  }

  runPnpmChecked("install frozen director dependencies", ["install", "--frozen-lockfile"], { cwd: directorAppDir });
  runPnpmChecked("build director bundle", ["run", "build"], { cwd: directorAppDir });

  const requiredEntries = [
    path.join(directorAppDir, "dist-server", "server.js"),
    path.join(directorAppDir, "assets"),
    path.join(directorAppDir, "package.json"),
  ];
  for (const entry of requiredEntries) {
    if (!fs.existsSync(entry)) throw new Error(`Director bundle build is missing required artifact: ${entry}`);
  }

  await fsp.rm(electronDirectorDir, { recursive: true, force: true });
  await fsp.mkdir(electronDirectorDir, { recursive: true });
  const copyEntries = ["assets", "dist-server", "package.json", "pnpm-lock.yaml", "README.md"];
  for (const name of copyEntries) {
    const source = path.join(directorAppDir, name);
    if (!fs.existsSync(source)) continue;
    await fsp.cp(source, path.join(electronDirectorDir, name), {
      recursive: true,
      force: true,
      dereference: false,
    });
  }

  runPnpmChecked(
    "install frozen production director dependencies",
    [
      "install",
      "--prod",
      "--frozen-lockfile",
      "--config.node-linker=hoisted",
      "--config.package-import-method=copy",
    ],
    { cwd: electronDirectorDir },
  );
  const bundledPackageJson = JSON.parse(
    await fsp.readFile(path.join(electronDirectorDir, "package.json"), "utf8"),
  );
  const productionDependencies = await inspectDirectorDependencyTree(bundledPackageJson);
  const directorEntrypoint = path.join(electronDirectorDir, "dist-server", "server.js");
  runCaptured(
    "load staged director entrypoint",
    process.execPath,
    [
      "--input-type=module",
      "--eval",
      `await import(${JSON.stringify(pathToFileURL(directorEntrypoint).href)});`,
    ],
    { cwd: electronDirectorDir },
  );

  const manifest = {
    ok: true,
    builder: "scripts/prepare-release-bundle.mjs",
    directorAppDir: path.relative(root, directorAppDir).split(path.sep).join("/"),
    bundledDirectorDir: path.relative(root, electronDirectorDir).split(path.sep).join("/"),
    included: [...copyEntries, "node_modules"].filter((name) => fs.existsSync(path.join(electronDirectorDir, name))),
    dependencyInstall: {
      productionOnly: true,
      nodeLinker: "hoisted",
      packageImportMethod: "copy",
      selfContained: true,
      entrypointImportVerified: true,
      ...productionDependencies,
    },
    lockSha256: await sha256File(path.join(directorAppDir, "pnpm-lock.yaml")),
    preparedAt: new Date().toISOString(),
  };
  await fsp.writeFile(directorBundleManifestPath, JSON.stringify(manifest, null, 2) + "\n", "utf8");
  return manifest;
}

async function main() {
  assertNoDynamicDependencyOverrides(process.env);
  const acceleratorProfile = resolveAcceleratorProfile({ argv: process.argv.slice(2), env: process.env });
  assertRequiredFiles();
  assertTrackedCleanDependencyInputs();
  const { uvCommand, uvVersion } = resolveUv();
  const releaseEnv = releaseUvEnvironment(root, acceleratorProfile, process.env);

  runChecked("prepare electron build assets", process.execPath, [path.join(__dirname, "prepare-electron-build.mjs")], {
    cwd: root,
  });
  synchronizeReleaseEnvironment(uvCommand, acceleratorProfile, releaseEnv);
  const provenance = collectReleaseProvenance(uvCommand, acceleratorProfile, releaseEnv);
  const fingerprint = await computeBackendSourceFingerprint();
  const lockSha256 = await sha256File(uvLockPath);
  const expected = {
    sourceHash: fingerprint.sourceHash,
    sourceFileCount: fingerprint.fileCount,
    requiredBackendSources: fingerprint.requiredSources,
    fingerprintInputs: fingerprint.fingerprintInputs,
    lockSha256,
    acceleratorProfile,
    platform: releasePlatform,
    backendEntryPoint: backendBinaryName,
    capabilityExtras: [...RELEASE_CAPABILITY_EXTRAS],
    uvVersion,
    ...provenance,
  };

  const existing = await reusableBundle(expected);
  if (existing) {
    const directorManifest = await stageDirectorBundle();
    const releaseEvidence = await writeReleaseEvidence({
      root,
      phase: "bundle",
      profile: acceleratorProfile,
      uvCommand,
      version: JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8")).version,
      env: releaseEnv,
    });
    console.log(JSON.stringify({
      ok: true,
      skippedRebuild: true,
      reason: "bundled backend matches the committed lock, profile, provenance, sources, and binary hash",
      bundleManifestPath,
      manifest: summarizeBundleManifest(existing),
      directorBundleManifestPath,
      directorManifest,
      releaseEvidence: {
        indexPath: path.relative(root, releaseEvidence.indexPath).split(path.sep).join("/"),
        checksumPath: path.relative(root, releaseEvidence.checksumPath).split(path.sep).join("/"),
        sbomPath: path.relative(root, releaseEvidence.sbomPath).split(path.sep).join("/"),
      },
    }, null, 2));
    return;
  }

  const sourceArtifact = buildBackendBundle(uvCommand, acceleratorProfile, releaseEnv);
  const manifest = await stageBackendBundle(sourceArtifact, expected);
  const directorManifest = await stageDirectorBundle();
  const releaseEvidence = await writeReleaseEvidence({
    root,
    phase: "bundle",
    profile: acceleratorProfile,
    uvCommand,
    version: JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8")).version,
    env: releaseEnv,
  });
  console.log(JSON.stringify({
    ok: true,
    bundleManifestPath,
    manifest: summarizeBundleManifest(manifest),
    directorBundleManifestPath,
    directorManifest,
    releaseEvidence: {
      indexPath: path.relative(root, releaseEvidence.indexPath).split(path.sep).join("/"),
      checksumPath: path.relative(root, releaseEvidence.checksumPath).split(path.sep).join("/"),
      sbomPath: path.relative(root, releaseEvidence.sbomPath).split(path.sep).join("/"),
    },
  }, null, 2));
}

main().catch((error) => {
  console.error("[prepare-release-bundle] FAILED", error);
  process.exit(1);
});
