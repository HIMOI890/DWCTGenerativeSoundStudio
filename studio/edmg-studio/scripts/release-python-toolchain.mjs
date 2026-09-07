import crypto from "node:crypto";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";

export const PINNED_UV_VERSION = "0.11.28";
export const RELEASE_MANIFEST_SCHEMA_VERSION = 5;
export const ACCELERATOR_PROFILES = Object.freeze(["cpu", "directml", "cuda"]);
export const RELEASE_PLATFORMS = Object.freeze(["linux", "win32"]);
export const RELEASE_CAPABILITY_EXTRAS = Object.freeze([
  "core",
  "audio",
  "asr",
  "internal-video",
  "aws",
]);
export const REQUIRED_LINUX_SETUP_SCRIPTS = Object.freeze([
  "scripts/setup_linux_ollama.sh",
  "scripts/setup_linux_comfyui.sh",
]);
export const REQUIRED_LINUX_SETUP_METADATA = Object.freeze([
  "scripts/linux-sidecar-pins/ollama-release.env",
  "scripts/linux-sidecar-pins/comfyui-sources.env",
  "scripts/linux-sidecar-pins/comfyui-model-assets.env",
  "scripts/linux-sidecar-pins/comfyui-core-requirements.txt",
  "scripts/linux-sidecar-pins/comfyui-manager-requirements.txt",
  "scripts/linux-sidecar-pins/comfyui-stable-video-diffusion-requirements.txt",
  "scripts/linux-sidecar-pins/huggingface-download-runtime-requirements.txt",
]);
export const REQUIRED_LINUX_SETUP_FILES = Object.freeze([
  ...REQUIRED_LINUX_SETUP_SCRIPTS,
  ...REQUIRED_LINUX_SETUP_METADATA,
]);
export const REQUIRED_FASTER_WHISPER_VAD_ASSET =
  "_internal/faster_whisper/assets/silero_vad_v6.onnx";

const DYNAMIC_DEPENDENCY_ENV_VARS = Object.freeze([
  "EDMG_BACKEND_BUNDLE_EXTRA",
  "EDMG_BACKEND_CUDA_BUNDLE",
  "EDMG_STUDIO_CUDA_BUNDLE",
  "EDMG_BACKEND_TORCH_INDEX_URL",
  "EDMG_CUDA_WHEEL_INDEX",
  "EDMG_CUDA_WHEEL_TAG",
  "PIP_TORCH_INDEX_URL",
  "PIP_CONFIG_FILE",
  "PIP_FIND_LINKS",
  "PIP_INDEX_URL",
  "PIP_EXTRA_INDEX_URL",
  "UV_CONFIG_FILE",
  "UV_DEFAULT_INDEX",
  "UV_EXTRA_INDEX_URL",
  "UV_FIND_LINKS",
  "UV_INDEX",
  "UV_INDEX_URL",
  "UV_NO_SOURCES",
  "UV_PROJECT_ENVIRONMENT",
]);

function nonEmptyEnvValue(env, key) {
  return String(env?.[key] ?? "").trim();
}

export function assertNoDynamicDependencyOverrides(env = process.env) {
  const configured = DYNAMIC_DEPENDENCY_ENV_VARS.filter((key) => nonEmptyEnvValue(env, key));
  if (configured.length) {
    throw new Error(
      `Release dependency/index overrides are forbidden: ${configured.join(", ")}. ` +
        "Update pyproject.toml and uv.lock instead.",
    );
  }
}

function parseProfileArgs(argv) {
  const values = [];
  for (let index = 0; index < argv.length; index += 1) {
    const arg = String(argv[index] ?? "");
    if (arg === "--profile") {
      if (index + 1 >= argv.length) throw new Error("--profile requires a value");
      values.push(String(argv[index + 1]));
      index += 1;
      continue;
    }
    if (arg.startsWith("--profile=")) {
      values.push(arg.slice("--profile=".length));
      continue;
    }
    throw new Error(`Unknown prepare-release-bundle argument: ${arg}`);
  }
  if (values.length > 1) throw new Error("Specify exactly one accelerator profile");
  return values[0] ?? "";
}

export function resolveAcceleratorProfile({ argv = [], env = process.env, platform = process.platform } = {}) {
  const fromArgs = parseProfileArgs(argv).trim();
  const fromEnv = String(env.EDMG_BACKEND_ACCELERATOR_PROFILE ?? "").trim();
  if (fromArgs && fromEnv && fromArgs !== fromEnv) {
    throw new Error(`Conflicting accelerator profiles: --profile=${fromArgs} and EDMG_BACKEND_ACCELERATOR_PROFILE=${fromEnv}`);
  }

  const profile = fromArgs || fromEnv || (platform === "win32" ? "directml" : "cpu");
  if (!ACCELERATOR_PROFILES.includes(profile)) {
    throw new Error(
      `Invalid accelerator profile ${JSON.stringify(profile)}. Expected exactly one of: ${ACCELERATOR_PROFILES.join(", ")}`,
    );
  }
  if (profile === "directml" && platform !== "win32") {
    throw new Error("The directml release profile is supported only on Windows");
  }
  return profile;
}

export function backendEntryPointForPlatform(platform = process.platform) {
  const normalized = String(platform ?? "").trim();
  if (normalized === "win32") return "edmg-studio-backend.exe";
  if (normalized === "linux") return "edmg-studio-backend";
  throw new Error(
    `Unsupported release platform ${JSON.stringify(normalized)}. Expected one of: ${RELEASE_PLATFORMS.join(", ")}`,
  );
}

export function selectedExtras(profile) {
  if (!ACCELERATOR_PROFILES.includes(profile)) throw new Error(`Unsupported accelerator profile: ${profile}`);
  return [profile, ...RELEASE_CAPABILITY_EXTRAS];
}

export function releaseUvEnvironment(studioRoot, profile, env = process.env) {
  if (!ACCELERATOR_PROFILES.includes(profile)) throw new Error(`Unsupported accelerator profile: ${profile}`);
  return {
    ...env,
    // Release builds must not share the source-runtime .venv. A running Studio
    // instance may legitimately sync a different accelerator profile there.
    UV_PROJECT_ENVIRONMENT: path.join(studioRoot, "release", "uv-environments", profile),
    // The global uv cache and this repository can live on different Windows
    // volumes. Copy mode avoids a noisy hardlink attempt and fallback.
    UV_LINK_MODE: "copy",
  };
}

function extraArgs(profile) {
  return selectedExtras(profile).flatMap((extra) => ["--extra", extra]);
}

export function uvLockCheckArgs() {
  return ["lock", "--check"];
}

export function uvSyncArgs(profile) {
  return ["sync", "--frozen", "--no-default-groups", ...extraArgs(profile), "--group", "build"];
}

export function uvRunArgs(profile, commandArgs) {
  return [
    "run",
    "--frozen",
    "--no-sync",
    "--no-default-groups",
    ...extraArgs(profile),
    "--group",
    "build",
    ...commandArgs,
  ];
}

export function uvExportCycloneDxArgs(profile) {
  return ["export", "--format", "cyclonedx1.5", "--frozen", "--no-default-groups", ...extraArgs(profile), "--group", "build"];
}

export function parseUvVersion(output) {
  const match = String(output ?? "").trim().match(/^uv\s+(\d+\.\d+\.\d+)(?:\s|$)/);
  if (!match) throw new Error(`Could not parse uv version output: ${JSON.stringify(String(output ?? "").trim())}`);
  return match[1];
}

export function assertPinnedUvVersion(output, expected = PINNED_UV_VERSION) {
  const actual = parseUvVersion(output);
  if (actual !== expected) {
    throw new Error(`uv ${actual} is unsupported for release builds; install the pinned uv ${expected}`);
  }
  return actual;
}

export function assertPython312(version) {
  const value = String(version ?? "").trim();
  if (!/^3\.12(?:\.|$)/.test(value)) {
    throw new Error(`Release Python ${value || "unknown"} is unsupported; Python 3.12 is required`);
  }
  return value;
}

export function assertTrackedCleanDependencyStatus({ trackedStatus, dirtyStatus, paths }) {
  if (trackedStatus !== 0) {
    throw new Error(`Release dependency inputs must be tracked by git: ${paths.join(", ")}`);
  }
  if (String(dirtyStatus ?? "").trim()) {
    throw new Error(
      "Release dependency inputs must be committed and clean before packaging:\n" + String(dirtyStatus).trim(),
    );
  }
}

export function normalizeTorchIndex(value) {
  return String(value ?? "").trim().replace(/\/+$/, "");
}

export function assertTorchIndexForProfile(profile, index) {
  const normalized = normalizeTorchIndex(index);
  if (profile === "cpu" || profile === "directml") {
    if (normalized !== "https://download.pytorch.org/whl/cpu") {
      throw new Error(`${profile} releases must use the locked PyTorch CPU index; got ${normalized || "none"}`);
    }
    return normalized;
  }
  if (profile === "cuda") {
    if (!/^https:\/\/download\.pytorch\.org\/whl\/cu\d+$/.test(normalized)) {
      throw new Error(`CUDA releases must use a fixed locked PyTorch CUDA index; got ${normalized || "none"}`);
    }
    return normalized;
  }
  throw new Error(`Unsupported accelerator profile: ${profile}`);
}

function isSha256(value) {
  return /^[a-f0-9]{64}$/.test(String(value ?? ""));
}

function normalizedBundlePath(value) {
  const candidate = String(value ?? "").replaceAll("\\", "/");
  if (!candidate || candidate.startsWith("/") || /^[a-z]:\//i.test(candidate)) return "";
  const normalized = path.posix.normalize(candidate);
  if (normalized !== candidate || normalized === "." || normalized === ".." || normalized.startsWith("../")) {
    return "";
  }
  return normalized;
}

function safeSymlinkTarget(entryPath, value) {
  const target = String(value ?? "").replaceAll("\\", "/");
  if (!target || target.startsWith("/") || /^[a-z]:\//i.test(target)) return "";
  const resolved = path.posix.normalize(path.posix.join(path.posix.dirname(entryPath), target));
  if (resolved === ".." || resolved.startsWith("../")) return "";
  return target;
}

export function isHfRuntimeEvidencePath(key, value) {
  const entryPath = normalizedBundlePath(value);
  if (!entryPath) return false;
  const exactPaths = {
    huggingfaceHubMetadata: "_internal/huggingface_hub-0.36.2.dist-info/METADATA",
    hfTransferMetadata: "_internal/hf_transfer-0.1.9.dist-info/METADATA",
    hfXetMetadata: "_internal/hf_xet-1.5.1.dist-info/METADATA",
  };
  if (Object.hasOwn(exactPaths, key)) return entryPath === exactPaths[key];

  const packageName = key === "hfTransferModule"
    ? "hf_transfer"
    : key === "hfXetModule"
      ? "hf_xet"
      : "";
  if (!packageName) return false;
  const prefix = `_internal/${packageName}/${packageName}`;
  if (!entryPath.startsWith(prefix)) return false;
  const suffix = entryPath.slice(prefix.length);
  return suffix.startsWith(".") &&
    !suffix.includes("/") &&
    (suffix.endsWith(".pyd") || suffix.endsWith(".so"));
}

function sameStringArray(left, right) {
  return Array.isArray(left) && Array.isArray(right) &&
    left.length === right.length && left.every((value, index) => value === right[index]);
}

function normalizedTorchPackages(packages) {
  if (!Array.isArray(packages)) return [];
  return packages
    .map((entry) => ({
      name: String(entry?.name ?? ""),
      version: String(entry?.version ?? ""),
      index: normalizeTorchIndex(entry?.index),
    }))
    .sort((left, right) => left.name.localeCompare(right.name));
}

function normalizedHfRuntimePackages(packages) {
  if (!Array.isArray(packages)) return [];
  return packages
    .map((entry) => ({
      name: String(entry?.name ?? ""),
      version: String(entry?.version ?? ""),
    }))
    .sort((left, right) => left.name.localeCompare(right.name));
}

export function validateReleaseManifest(
  manifest,
  {
    expectedProfile = "",
    expectedPlatform = "",
    expectedBackendEntryPoint = "",
    expectedUvVersion = PINNED_UV_VERSION,
  } = {},
) {
  const errors = [];
  if (!manifest || typeof manifest !== "object") return ["manifest is not an object"];
  if (manifest.schemaVersion !== RELEASE_MANIFEST_SCHEMA_VERSION) {
    errors.push(`schemaVersion must be ${RELEASE_MANIFEST_SCHEMA_VERSION}`);
  }
  if (manifest.ok !== true) errors.push("ok must be true");
  if (!isSha256(manifest.sourceHash)) errors.push("sourceHash must be a SHA-256 digest");
  if (!isSha256(manifest.lockSha256)) errors.push("lockSha256 must be a SHA-256 digest");
  if (!isSha256(manifest.binarySha256)) errors.push("binarySha256 must be a SHA-256 digest");
  if (!ACCELERATOR_PROFILES.includes(manifest.acceleratorProfile)) errors.push("acceleratorProfile is invalid");
  if (expectedProfile && manifest.acceleratorProfile !== expectedProfile) {
    errors.push(`acceleratorProfile must be ${expectedProfile}`);
  }
  if (!sameStringArray(manifest.capabilityExtras, RELEASE_CAPABILITY_EXTRAS)) {
    errors.push("capabilityExtras do not match the release capability set");
  }
  if (manifest.uvVersion !== expectedUvVersion) errors.push(`uvVersion must be ${expectedUvVersion}`);
  if (!/^3\.12(?:\.|$)/.test(String(manifest.pythonVersion ?? ""))) errors.push("pythonVersion must be Python 3.12");
  if (!String(manifest.pyinstallerVersion ?? "").trim()) errors.push("pyinstallerVersion is required");
  if (!Number.isInteger(manifest.sourceFileCount) || manifest.sourceFileCount <= 0) errors.push("sourceFileCount is invalid");
  if (!Number.isInteger(manifest.binarySize) || manifest.binarySize <= 0) errors.push("binarySize is invalid");
  if (manifest.bundleLayout !== "onedir") errors.push("bundleLayout must be onedir");
  const releasePlatform = String(manifest.platform ?? "");
  if (!RELEASE_PLATFORMS.includes(releasePlatform)) {
    errors.push("platform is invalid");
  }
  if (expectedPlatform && releasePlatform !== expectedPlatform) {
    errors.push(`platform must be ${expectedPlatform}`);
  }
  const backendEntryPoint = normalizedBundlePath(manifest.backendEntryPoint);
  if (!backendEntryPoint) errors.push("backendEntryPoint must be a safe bundle-relative path");
  if (RELEASE_PLATFORMS.includes(releasePlatform)) {
    const platformEntryPoint = backendEntryPointForPlatform(releasePlatform);
    if (backendEntryPoint !== platformEntryPoint) {
      errors.push(`backendEntryPoint must be ${platformEntryPoint} on ${releasePlatform}`);
    }
  }
  if (expectedBackendEntryPoint && backendEntryPoint !== expectedBackendEntryPoint) {
    errors.push(`backendEntryPoint must be ${expectedBackendEntryPoint}`);
  }

  const bundleEntries = Array.isArray(manifest.bundleEntries) ? manifest.bundleEntries : [];
  if (!bundleEntries.length) {
    errors.push("bundleEntries must inventory the complete onedir runtime");
  } else {
    const seen = new Set();
    let regularFileCount = 0;
    let regularFileBytes = 0;
    let previousPath = "";
    for (const entry of bundleEntries) {
      const entryPath = normalizedBundlePath(entry?.path);
      if (!entryPath) {
        errors.push("bundleEntries contain an unsafe path");
        break;
      }
      if (seen.has(entryPath)) {
        errors.push(`bundleEntries contain duplicate path ${entryPath}`);
        break;
      }
      if (previousPath && previousPath.localeCompare(entryPath) > 0) {
        errors.push("bundleEntries must be sorted by path");
        break;
      }
      seen.add(entryPath);
      previousPath = entryPath;
      if (entry?.type === "file") {
        regularFileCount += 1;
        if (!Number.isInteger(entry.size) || entry.size < 0 || !isSha256(entry.sha256)) {
          errors.push(`bundleEntries file metadata is invalid for ${entryPath}`);
          break;
        }
        regularFileBytes += entry.size;
      } else if (entry?.type === "symlink") {
        if (!safeSymlinkTarget(entryPath, entry.target)) {
          errors.push(`bundleEntries symlink target is invalid for ${entryPath}`);
          break;
        }
      } else {
        errors.push(`bundleEntries type is invalid for ${entryPath}`);
        break;
      }
    }
    if (manifest.bundleEntryCount !== bundleEntries.length) errors.push("bundleEntryCount is invalid");
    if (manifest.bundleFileCount !== regularFileCount || regularFileCount <= 0) errors.push("bundleFileCount is invalid");
    if (manifest.bundleSize !== regularFileBytes || regularFileBytes <= 0) errors.push("bundleSize is invalid");
    const launcher = bundleEntries.find((entry) => entry?.path === backendEntryPoint && entry?.type === "file");
    if (!launcher) {
      errors.push("backendEntryPoint is missing from bundleEntries");
    } else if (launcher.size !== manifest.binarySize || launcher.sha256 !== manifest.binarySha256) {
      errors.push("backendEntryPoint metadata does not match binary provenance");
    }

    const helperEntryPoint = normalizedBundlePath(manifest.hfBucketHelper?.entryPoint);
    const helper = bundleEntries.find(
      (entry) => entry?.path === helperEntryPoint && entry?.type === "file",
    );
    if (!helperEntryPoint || !helper) {
      errors.push("hfBucketHelper.entryPoint is missing from bundleEntries");
    } else {
      if (manifest.hfBucketHelper?.helperVersion !== "1.0.0") {
        errors.push("hfBucketHelper.helperVersion must be 1.0.0");
      }
      if (manifest.hfBucketHelper?.huggingfaceHubVersion !== "1.20.1") {
        errors.push("hfBucketHelper.huggingfaceHubVersion must be 1.20.1");
      }
      if (manifest.hfBucketHelper?.hfXetVersion !== "1.5.1") {
        errors.push("hfBucketHelper.hfXetVersion must be 1.5.1");
      }
      if (!isSha256(manifest.hfBucketHelper?.lockSha256)) {
        errors.push("hfBucketHelper.lockSha256 must be a SHA-256 digest");
      }
      if (
        helper.size !== manifest.hfBucketHelper?.binarySize ||
        helper.sha256 !== manifest.hfBucketHelper?.binarySha256
      ) {
        errors.push("hfBucketHelper metadata does not match its bundled executable");
      }
    }

    const defaultsEntryPoint = normalizedBundlePath(manifest.launcherEnvDefaults?.entryPoint);
    const defaults = bundleEntries.find(
      (entry) => entry?.path === defaultsEntryPoint && entry?.type === "file",
    );
    if (!defaultsEntryPoint || !defaults) {
      errors.push("launcherEnvDefaults.entryPoint is missing from bundleEntries");
    } else if (
      defaults.size !== manifest.launcherEnvDefaults?.size ||
      defaults.sha256 !== manifest.launcherEnvDefaults?.sha256
    ) {
      errors.push("launcherEnvDefaults metadata does not match the bundled defaults");
    }
    const fasterWhisperVadAsset = bundleEntries.find(
      (entry) =>
        entry?.path === REQUIRED_FASTER_WHISPER_VAD_ASSET &&
        entry?.type === "file" &&
        Number.isInteger(entry?.size) &&
        entry.size > 0,
    );
    if (!fasterWhisperVadAsset) {
      errors.push(`${REQUIRED_FASTER_WHISPER_VAD_ASSET} is missing or empty in the backend bundle`);
    }
    if (releasePlatform === "linux") {
      for (const entryPoint of REQUIRED_LINUX_SETUP_FILES) {
        const entry = bundleEntries.find(
          (candidate) => candidate?.path === entryPoint && candidate?.type === "file",
        );
        if (!entry) errors.push(`${entryPoint} is missing from the Linux backend bundle`);
      }
    }
  }

  const torchPackages = normalizedTorchPackages(manifest.torchPackages);
  const expectedNames = ["torch", "torchaudio", "torchvision"];
  if (!sameStringArray(torchPackages.map((entry) => entry.name), expectedNames)) {
    errors.push("torchPackages must contain torch, torchaudio, and torchvision");
  }
  for (const entry of torchPackages) {
    if (!entry.version) errors.push(`torchPackages.${entry.name}.version is required`);
    if (entry.index !== normalizeTorchIndex(manifest.torchIndex)) {
      errors.push(`torchPackages.${entry.name}.index does not match torchIndex`);
    }
  }
  if (ACCELERATOR_PROFILES.includes(manifest.acceleratorProfile)) {
    try {
      assertTorchIndexForProfile(manifest.acceleratorProfile, manifest.torchIndex);
    } catch (error) {
      errors.push(error.message);
    }
  }

  const hfRuntimePackages = normalizedHfRuntimePackages(manifest.hfRuntimePackages);
  const expectedHfRuntimePackages = [
    { name: "hf-transfer", version: "0.1.9" },
    { name: "hf-xet", version: "1.5.1" },
    { name: "huggingface-hub", version: "0.36.2" },
  ];
  if (JSON.stringify(hfRuntimePackages) !== JSON.stringify(expectedHfRuntimePackages)) {
    errors.push(
      "hfRuntimePackages must contain huggingface-hub==0.36.2, hf-transfer==0.1.9, and hf-xet==1.5.1",
    );
  }
  for (const key of [
    "huggingfaceHubMetadata",
    "hfTransferMetadata",
    "hfTransferModule",
    "hfXetMetadata",
    "hfXetModule",
  ]) {
    const entryPath = normalizedBundlePath(manifest.hfRuntimeBundleEvidence?.[key]);
    const entry = bundleEntries.find(
      (candidate) => candidate?.path === entryPath && candidate?.type === "file",
    );
    if (!isHfRuntimeEvidencePath(key, entryPath) || !entry) {
      errors.push(`hfRuntimeBundleEvidence.${key} is missing from bundleEntries`);
    }
  }

  if (!Array.isArray(manifest.fingerprintInputs) || manifest.fingerprintInputs.length < 3) {
    errors.push("fingerprintInputs must include the Python and lock metadata");
  } else {
    const requiredSuffixes = [
      ".python-version",
      "python_backend/pyproject.toml",
      "python_backend/uv.lock",
      "python_backend/hf_bucket_helper/pyproject.toml",
      "python_backend/hf_bucket_helper/uv.lock",
      "launcher_env.defaults.json",
      ...(releasePlatform === "linux" ? REQUIRED_LINUX_SETUP_FILES : []),
    ];
    for (const suffix of requiredSuffixes) {
      const entry = manifest.fingerprintInputs.find((candidate) => String(candidate?.path ?? "").replaceAll("\\", "/").endsWith(suffix));
      if (!entry || !isSha256(entry.sha256)) errors.push(`fingerprintInputs is missing ${suffix}`);
    }
    const helperLock = manifest.fingerprintInputs.find((candidate) =>
      String(candidate?.path ?? "").replaceAll("\\", "/").endsWith("python_backend/hf_bucket_helper/uv.lock")
    );
    if (helperLock?.sha256 !== manifest.hfBucketHelper?.lockSha256) {
      errors.push("hfBucketHelper.lockSha256 does not match its fingerprint input");
    }
    const launcherDefaults = manifest.fingerprintInputs.find((candidate) =>
      String(candidate?.path ?? "").replaceAll("\\", "/").endsWith("launcher_env.defaults.json")
    );
    if (launcherDefaults?.sha256 !== manifest.launcherEnvDefaults?.sha256) {
      errors.push("launcherEnvDefaults.sha256 does not match its fingerprint input");
    }
  }
  if (!Array.isArray(manifest.nltkResources) || !manifest.nltkResources.length) {
    errors.push("nltkResources provenance is required");
  } else {
    for (const entry of manifest.nltkResources) {
      if (!String(entry?.name ?? "") || !String(entry?.url ?? "") || !isSha256(entry?.sha256)) {
        errors.push("nltkResources entries require name, immutable URL, and SHA-256");
        break;
      }
    }
  }
  return errors;
}

export function assertValidReleaseManifest(manifest, options = {}) {
  const errors = validateReleaseManifest(manifest, options);
  if (errors.length) throw new Error(`Invalid backend release manifest: ${errors.join("; ")}`);
  return manifest;
}

export function releaseProvenanceMatches(manifest, expected) {
  const expectedPlatform = String(expected?.platform ?? "");
  const expectedBackendEntryPoint = normalizedBundlePath(expected?.backendEntryPoint);
  if (!RELEASE_PLATFORMS.includes(expectedPlatform) || !expectedBackendEntryPoint) return false;
  if (validateReleaseManifest(manifest, {
    expectedProfile: expected.acceleratorProfile,
    expectedPlatform,
    expectedBackendEntryPoint,
  }).length) return false;
  return manifest.platform === expectedPlatform &&
    manifest.backendEntryPoint === expectedBackendEntryPoint &&
    manifest.sourceHash === expected.sourceHash &&
    manifest.lockSha256 === expected.lockSha256 &&
    manifest.uvVersion === expected.uvVersion &&
    manifest.pythonVersion === expected.pythonVersion &&
    manifest.pythonImplementation === expected.pythonImplementation &&
    manifest.pyinstallerVersion === expected.pyinstallerVersion &&
    manifest.torchIndex === expected.torchIndex &&
    JSON.stringify(normalizedTorchPackages(manifest.torchPackages)) === JSON.stringify(normalizedTorchPackages(expected.torchPackages)) &&
    JSON.stringify(normalizedHfRuntimePackages(manifest.hfRuntimePackages)) === JSON.stringify(normalizedHfRuntimePackages(expected.hfRuntimePackages)) &&
    JSON.stringify(manifest.nltkResources) === JSON.stringify(expected.nltkResources) &&
    JSON.stringify(manifest.fingerprintInputs) === JSON.stringify(expected.fingerprintInputs) &&
    sameStringArray(manifest.capabilityExtras, expected.capabilityExtras);
}

export async function sha256File(filePath) {
  const hash = crypto.createHash("sha256");
  await new Promise((resolve, reject) => {
    const stream = fs.createReadStream(filePath);
    stream.on("data", (chunk) => hash.update(chunk));
    stream.on("error", reject);
    stream.on("end", resolve);
  });
  return hash.digest("hex");
}

export async function fileFingerprintEntries(files, baseDir) {
  const entries = [];
  for (const filePath of files) {
    entries.push({
      path: filePath.replace(baseDir, "").replace(/^[/\\]+/, "").split("\\").join("/"),
      sha256: await sha256File(filePath),
    });
  }
  return entries;
}

export async function collectBundleEntries(bundleDirectory, {
  exclude = ["backend-bundle-manifest.json"],
} = {}) {
  const root = path.resolve(bundleDirectory);
  const excluded = new Set(exclude.map((entry) => normalizedBundlePath(entry)).filter(Boolean));
  const entries = [];

  async function walk(directory) {
    const children = await fsp.readdir(directory, { withFileTypes: true });
    children.sort((left, right) => left.name.localeCompare(right.name));
    for (const child of children) {
      const absolutePath = path.join(directory, child.name);
      const relativePath = path.relative(root, absolutePath).split(path.sep).join("/");
      if (excluded.has(relativePath)) continue;
      if (child.isSymbolicLink()) {
        entries.push({
          path: relativePath,
          type: "symlink",
          target: (await fsp.readlink(absolutePath)).replaceAll("\\", "/"),
        });
      } else if (child.isDirectory()) {
        await walk(absolutePath);
      } else if (child.isFile()) {
        const stat = await fsp.stat(absolutePath);
        entries.push({
          path: relativePath,
          type: "file",
          size: stat.size,
          sha256: await sha256File(absolutePath),
        });
      } else {
        throw new Error(`Unsupported backend bundle entry: ${absolutePath}`);
      }
    }
  }

  await walk(root);
  return entries.sort((left, right) => left.path.localeCompare(right.path));
}

export async function materializeExternalBundleSymlinks(bundleDirectory) {
  const root = path.resolve(bundleDirectory);
  let materialized = 0;

  async function walk(directory) {
    for (const child of await fsp.readdir(directory, { withFileTypes: true })) {
      const absolutePath = path.join(directory, child.name);
      if (child.isSymbolicLink()) {
        const target = await fsp.readlink(absolutePath);
        const resolvedTarget = path.resolve(path.dirname(absolutePath), target);
        const relativeTarget = path.relative(root, resolvedTarget);
        const staysInsideBundle = relativeTarget === "" ||
          (!path.isAbsolute(relativeTarget) &&
            relativeTarget !== ".." &&
            !relativeTarget.startsWith(`..${path.sep}`));
        if (staysInsideBundle && !path.isAbsolute(target)) continue;

        const realTarget = await fsp.realpath(absolutePath);
        const targetStat = await fsp.stat(realTarget);
        if (!targetStat.isFile()) {
          throw new Error(`External backend symlink must resolve to a file: ${absolutePath} -> ${target}`);
        }
        const temporaryPath = `${absolutePath}.edmg-materializing-${process.pid}`;
        await fsp.copyFile(realTarget, temporaryPath);
        await fsp.chmod(temporaryPath, targetStat.mode);
        await fsp.rename(temporaryPath, absolutePath);
        materialized += 1;
      } else if (child.isDirectory()) {
        await walk(absolutePath);
      }
    }
  }

  await walk(root);
  return materialized;
}

export async function bundleMatchesManifest(bundleDirectory, manifest) {
  if (!fs.existsSync(bundleDirectory) || !Array.isArray(manifest?.bundleEntries)) return false;
  try {
    const stat = await fsp.stat(bundleDirectory);
    if (!stat.isDirectory()) return false;
    const actual = await collectBundleEntries(bundleDirectory);
    return JSON.stringify(actual) === JSON.stringify(manifest.bundleEntries);
  } catch {
    return false;
  }
}

export async function binaryMatchesManifest(binaryPath, manifest) {
  if (!fs.existsSync(binaryPath) || !isSha256(manifest?.binarySha256)) return false;
  const stat = await fsp.stat(binaryPath);
  if (manifest.binarySize !== stat.size) return false;
  return (await sha256File(binaryPath)) === manifest.binarySha256;
}
