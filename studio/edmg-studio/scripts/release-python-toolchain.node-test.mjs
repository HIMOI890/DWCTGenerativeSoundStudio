import assert from "node:assert/strict";
import fs from "node:fs";
import fsp from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  PINNED_UV_VERSION,
  RELEASE_CAPABILITY_EXTRAS,
  REQUIRED_FASTER_WHISPER_VAD_ASSET,
  REQUIRED_LINUX_SETUP_FILES,
  assertNoDynamicDependencyOverrides,
  assertPinnedUvVersion,
  assertPython312,
  assertTorchIndexForProfile,
  backendEntryPointForPlatform,
  assertTrackedCleanDependencyStatus,
  binaryMatchesManifest,
  bundleMatchesManifest,
  collectBundleEntries,
  isHfRuntimeEvidencePath,
  materializeExternalBundleSymlinks,
  releaseUvEnvironment,
  releaseProvenanceMatches,
  resolveAcceleratorProfile,
  selectedExtras,
  sha256File,
  uvLockCheckArgs,
  uvRunArgs,
  uvSyncArgs,
  uvExportCycloneDxArgs,
  validateReleaseManifest,
} from "./release-python-toolchain.mjs";
import {
  assertExpectedGplv3LicenseText,
  ensureCachedArchive,
  findUniqueArchiveFile,
  loadPinnedMediaManifest,
  renderMediaSourceNotice,
  resolveMediaBuildCacheRoot,
  resolvePinnedMediaAsset,
  verifyPinnedArchive,
} from "./stage-media-tools.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const studioRoot = path.resolve(__dirname, "..");

function validManifest(overrides = {}) {
  const cpuIndex = "https://download.pytorch.org/whl/cpu";
  const platform = overrides.platform ?? process.platform;
  const backendEntryPoint = overrides.backendEntryPoint ?? backendEntryPointForPlatform(platform);
  const helperEntryPoint = platform === "win32" ? "edmg-hf-bucket-helper.exe" : "edmg-hf-bucket-helper";
  const binarySha256 = "3".repeat(64);
  const binarySize = 123;
  const helperSha256 = "7".repeat(64);
  const helperSize = 45;
  const defaultsSha256 = "8".repeat(64);
  const defaultsSize = 67;
  const hfRuntimeBundleEvidence = {
    huggingfaceHubMetadata: "_internal/huggingface_hub-0.36.2.dist-info/METADATA",
    hfTransferMetadata: "_internal/hf_transfer-0.1.9.dist-info/METADATA",
    hfTransferModule: platform === "win32"
      ? "_internal/hf_transfer/hf_transfer.pyd"
      : "_internal/hf_transfer/hf_transfer.abi3.so",
    hfXetMetadata: "_internal/hf_xet-1.5.1.dist-info/METADATA",
    hfXetModule: platform === "win32"
      ? "_internal/hf_xet/hf_xet.pyd"
      : "_internal/hf_xet/hf_xet.abi3.so",
  };
  const hfRuntimeEntries = Object.values(hfRuntimeBundleEvidence).map((entryPath) => ({
    path: entryPath,
    type: "file",
    size: 11,
    sha256: "a".repeat(64),
  }));
  const linuxSetupEntries = platform === "linux"
    ? REQUIRED_LINUX_SETUP_FILES.map((entryPath) => ({
      path: entryPath,
      type: "file",
      size: 17,
      sha256: "b".repeat(64),
    }))
    : [];
  const asrRuntimeEntries = [{
    path: REQUIRED_FASTER_WHISPER_VAD_ASSET,
    type: "file",
    size: 17,
    sha256: "c".repeat(64),
  }];
  const bundleEntries = [
    { path: helperEntryPoint, type: "file", size: helperSize, sha256: helperSha256 },
    { path: backendEntryPoint, type: "file", size: binarySize, sha256: binarySha256 },
    { path: "launcher_env.defaults.json", type: "file", size: defaultsSize, sha256: defaultsSha256 },
    ...hfRuntimeEntries,
    ...asrRuntimeEntries,
    ...linuxSetupEntries,
  ].sort((left, right) => left.path.localeCompare(right.path));
  return {
    schemaVersion: 5,
    ok: true,
    platform,
    sourceHash: "1".repeat(64),
    sourceFileCount: 10,
    lockSha256: "2".repeat(64),
    binarySha256,
    binarySize,
    bundleLayout: "onedir",
    backendEntryPoint,
    bundleEntries,
    bundleEntryCount: bundleEntries.length,
    bundleFileCount: bundleEntries.length,
    bundleSize: bundleEntries.reduce((total, entry) => total + entry.size, 0),
    hfBucketHelper: {
      entryPoint: helperEntryPoint,
      helperVersion: "1.0.0",
      huggingfaceHubVersion: "1.20.1",
      hfXetVersion: "1.5.1",
      lockSha256: "8".repeat(64),
      binarySha256: helperSha256,
      binarySize: helperSize,
    },
    launcherEnvDefaults: {
      entryPoint: "launcher_env.defaults.json",
      sha256: defaultsSha256,
      size: defaultsSize,
    },
    acceleratorProfile: "cpu",
    capabilityExtras: [...RELEASE_CAPABILITY_EXTRAS],
    uvVersion: PINNED_UV_VERSION,
    pythonVersion: "3.12.10",
    pythonImplementation: "CPython",
    pyinstallerVersion: "6.16.0",
    torchIndex: cpuIndex,
    torchPackages: [
      { name: "torch", version: "2.8.0+cpu", index: cpuIndex },
      { name: "torchaudio", version: "2.8.0+cpu", index: cpuIndex },
      { name: "torchvision", version: "0.23.0+cpu", index: cpuIndex },
    ],
    hfRuntimePackages: [
      { name: "huggingface-hub", version: "0.36.2" },
      { name: "hf-transfer", version: "0.1.9" },
      { name: "hf-xet", version: "1.5.1" },
    ],
    hfRuntimeBundleEvidence,
    fingerprintInputs: [
      { path: ".python-version", sha256: "4".repeat(64) },
      { path: "studio/edmg-studio/python_backend/pyproject.toml", sha256: "5".repeat(64) },
      { path: "studio/edmg-studio/python_backend/uv.lock", sha256: "2".repeat(64) },
      { path: "studio/edmg-studio/python_backend/hf_bucket_helper/pyproject.toml", sha256: "7".repeat(64) },
      { path: "studio/edmg-studio/python_backend/hf_bucket_helper/uv.lock", sha256: "8".repeat(64) },
      { path: "studio/edmg-studio/launcher_env.defaults.json", sha256: "8".repeat(64) },
      ...(platform === "linux"
        ? REQUIRED_LINUX_SETUP_FILES.map((entryPath) => ({
          path: `studio/edmg-studio/${entryPath}`,
          sha256: "b".repeat(64),
        }))
        : []),
    ],
    nltkResources: [
      {
        name: "punkt",
        url: "https://raw.githubusercontent.com/nltk/nltk_data/immutable/packages/tokenizers/punkt.zip",
        sha256: "6".repeat(64),
        size: 1,
      },
    ],
    ...overrides,
  };
}

test("accelerator profile selection is strict and platform aware", () => {
  assert.equal(resolveAcceleratorProfile({ argv: ["--profile", "cpu"], env: {}, platform: "win32" }), "cpu");
  assert.equal(resolveAcceleratorProfile({ argv: [], env: {}, platform: "win32" }), "directml");
  assert.equal(resolveAcceleratorProfile({ argv: [], env: {}, platform: "linux" }), "cpu");
  assert.equal(
    resolveAcceleratorProfile({ argv: [], env: { EDMG_BACKEND_ACCELERATOR_PROFILE: "cuda" }, platform: "linux" }),
    "cuda",
  );
  assert.throws(
    () => resolveAcceleratorProfile({ argv: ["--profile", "cpu"], env: { EDMG_BACKEND_ACCELERATOR_PROFILE: "cuda" } }),
    /Conflicting accelerator profiles/,
  );
  assert.throws(() => resolveAcceleratorProfile({ argv: ["--profile", "CPU"], env: {} }), /Invalid accelerator profile/);
  assert.throws(() => resolveAcceleratorProfile({ argv: ["--profile", "directml"], env: {}, platform: "linux" }), /only on Windows/);
  assert.throws(() => resolveAcceleratorProfile({ argv: ["--extra", "cpu"], env: {} }), /Unknown/);
});

test("dynamic dependency and index overrides are rejected", () => {
  assert.doesNotThrow(() => assertNoDynamicDependencyOverrides({}));
  for (const name of [
    "EDMG_BACKEND_BUNDLE_EXTRA",
    "EDMG_BACKEND_TORCH_INDEX_URL",
    "PIP_INDEX_URL",
    "UV_INDEX",
    "UV_CONFIG_FILE",
    "UV_PROJECT_ENVIRONMENT",
  ]) {
    assert.throws(() => assertNoDynamicDependencyOverrides({ [name]: "unexpected" }), new RegExp(name));
  }
});

test("frozen uv commands compose one accelerator with deterministic capabilities", () => {
  assert.deepEqual(selectedExtras("cpu"), ["cpu", ...RELEASE_CAPABILITY_EXTRAS]);
  assert.deepEqual(uvLockCheckArgs(), ["lock", "--check"]);
  assert.deepEqual(uvExportCycloneDxArgs("cpu").slice(0, 4), ["export", "--format", "cyclonedx1.5", "--frozen"]);
  assert.deepEqual(uvSyncArgs("cuda"), [
    "sync",
    "--frozen",
    "--no-default-groups",
    "--extra",
    "cuda",
    "--extra",
    "core",
    "--extra",
    "audio",
    "--extra",
    "asr",
    "--extra",
    "internal-video",
    "--extra",
    "aws",
    "--group",
    "build",
  ]);
  const run = uvRunArgs("cpu", ["pyinstaller", "pyinstaller.spec", "--clean", "--noconfirm"]);
  assert.deepEqual(run.slice(0, 4), ["run", "--frozen", "--no-sync", "--no-default-groups"]);
  assert.deepEqual(run.slice(-6), ["--group", "build", "pyinstaller", "pyinstaller.spec", "--clean", "--noconfirm"]);
  assert.equal(run.filter((value) => value === "--extra").length, 6);
});

test("release builds isolate accelerator environments from the source runtime", () => {
  const sourceEnv = { KEEP_ME: "yes", UV_LINK_MODE: "hardlink" };
  const releaseEnv = releaseUvEnvironment(studioRoot, "cuda", sourceEnv);
  assert.equal(releaseEnv.KEEP_ME, "yes");
  assert.equal(releaseEnv.UV_LINK_MODE, "copy");
  assert.equal(
    releaseEnv.UV_PROJECT_ENVIRONMENT,
    path.join(studioRoot, "release", "uv-environments", "cuda"),
  );
  assert.equal(sourceEnv.UV_PROJECT_ENVIRONMENT, undefined);
  assert.throws(() => releaseUvEnvironment(studioRoot, "unknown", {}), /Unsupported accelerator profile/);
});
test("PyInstaller release spec bundles Faster-Whisper VAD data and metadata", () => {
  const spec = fs.readFileSync(path.join(studioRoot, "python_backend", "pyinstaller.spec"), "utf8");
  assert.match(spec, /collect_data_files,\s*["']faster_whisper["']/);
  assert.match(spec, /copy_metadata,\s*["']faster-whisper["']/);
});

test("media-tool assets pin immutable checksum-verified FFmpeg and FFprobe archives", () => {
  const manifest = loadPinnedMediaManifest();
  assert.equal(manifest.releaseTag, "autobuild-2026-07-31-14-10");

  for (const [platform, archiveFormat] of [["win32", "zip"], ["linux", "tar.xz"]]) {
    const asset = resolvePinnedMediaAsset({ platform, arch: "x64", manifest });
    assert.equal(asset.archiveFormat, archiveFormat);
    assert.match(asset.url, new RegExp(`/releases/download/${manifest.releaseTag}/`));
    assert.match(asset.sha256, /^[a-f0-9]{64}$/);
    assert.ok(asset.size > 100_000_000);
    assert.equal(asset.binaryNames.ffmpeg, platform === "win32" ? "ffmpeg.exe" : "ffmpeg");
    assert.equal(asset.binaryNames.ffprobe, platform === "win32" ? "ffprobe.exe" : "ffprobe");
    assert.equal(asset.distributionNotice.licenseArchiveName, "LICENSE.txt");
    assert.equal(asset.distributionNotice.licenseOutputName, "FFmpeg-LICENSE.txt");
    assert.equal(asset.distributionNotice.sourceNoticeOutputName, "FFmpeg-SOURCE.txt");
    assert.match(asset.distributionNotice.licenseName, /GNU General Public License version 3/i);
    assert.match(asset.distributionNotice.ffmpegSource.commit, /^[a-f0-9]{40}$/);
    assert.match(asset.distributionNotice.buildSource.commit, /^[a-f0-9]{40}$/);

    const sourceNotice = renderMediaSourceNotice(asset);
    for (const expectedValue of [
      asset.releaseTag,
      asset.archiveName,
      String(asset.size),
      asset.sha256,
      asset.distributionNotice.licenseOutputName,
      asset.distributionNotice.ffmpegSource.commit,
      asset.distributionNotice.buildSource.commit,
    ]) {
      assert.match(sourceNotice, new RegExp(expectedValue.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
    }
  }
  assert.throws(
    () => resolvePinnedMediaAsset({ platform: "linux", arch: "arm64", manifest }),
    /No pinned FFmpeg\/FFprobe asset/,
  );

  const abbreviatedCommitManifest = structuredClone(manifest);
  abbreviatedCommitManifest.distributionNotice.ffmpegSourceCommit = "9b6c8969e0";
  assert.throws(
    () => resolvePinnedMediaAsset({ platform: "win32", arch: "x64", manifest: abbreviatedCommitManifest }),
    /full Git commit digest/,
  );

  const nonGplManifest = structuredClone(manifest);
  nonGplManifest.assets["win32-x64"].archiveName = nonGplManifest.assets["win32-x64"].archiveName.replace(
    "-gpl-",
    "-lgpl-",
  );
  nonGplManifest.assets["win32-x64"].url = nonGplManifest.assets["win32-x64"].url.replace(
    "-gpl-",
    "-lgpl-",
  );
  assert.throws(
    () => resolvePinnedMediaAsset({ platform: "win32", arch: "x64", manifest: nonGplManifest }),
    /must identify the pinned .* GPL build/,
  );
});

test("media-tool redistribution evidence accepts GPLv3 and rejects unexpected license text", () => {
  const gplv3 = "GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007\n";
  assert.equal(assertExpectedGplv3LicenseText(gplv3), gplv3);
  assert.throws(
    () => assertExpectedGplv3LicenseText("GNU GENERAL PUBLIC LICENSE\nVersion 2, June 1991\n"),
    /expected the GPLv3 license text/,
  );
  assert.throws(
    () => assertExpectedGplv3LicenseText("GNU LESSER GENERAL PUBLIC LICENSE\nVersion 3\n"),
    /expected the GPLv3 license text/,
  );
});

test("media-tool archive license discovery fails closed when evidence is missing or ambiguous", async () => {
  const tempDir = await fsp.mkdtemp(path.join(os.tmpdir(), "edmg-media-license-"));
  const firstDir = path.join(tempDir, "first");
  const secondDir = path.join(tempDir, "second");
  await fsp.mkdir(firstDir, { recursive: true });
  try {
    await assert.rejects(
      findUniqueArchiveFile(tempDir, "LICENSE.txt", { caseInsensitive: true }),
      /Expected exactly one LICENSE\.txt.*found 0/,
    );

    const licensePath = path.join(firstDir, "license.TXT");
    await fsp.writeFile(licensePath, "GNU GENERAL PUBLIC LICENSE\nVersion 3\n", "utf8");
    assert.equal(
      await findUniqueArchiveFile(tempDir, "LICENSE.txt", { caseInsensitive: true }),
      licensePath,
    );

    await fsp.mkdir(secondDir, { recursive: true });
    await fsp.writeFile(path.join(secondDir, "LICENSE.txt"), "duplicate\n", "utf8");
    await assert.rejects(
      findUniqueArchiveFile(tempDir, "LICENSE.txt", { caseInsensitive: true }),
      /Expected exactly one LICENSE\.txt.*found 2/,
    );
  } finally {
    await fsp.rm(tempDir, { recursive: true, force: true });
  }
});

test("media-tool archive cache rejects drift and reuses only verified content", async () => {
  const tempDir = await fsp.mkdtemp(path.join(os.tmpdir(), "edmg-media-cache-"));
  const fixturePath = path.join(tempDir, "fixture.bin");
  const payload = Buffer.from("pinned media archive\n", "utf8");
  await fsp.writeFile(fixturePath, payload);
  const asset = {
    archiveName: "fixture.zip",
    releaseTag: "autobuild-2026-07-31-14-10",
    size: payload.length,
    sha256: await sha256File(fixturePath),
    url: "https://example.invalid/fixture.zip",
  };
  const cacheRoot = path.join(tempDir, "cache-root");
  let fetchCount = 0;
  const fetchImpl = async () => {
    fetchCount += 1;
    return new Response(payload, { status: 200 });
  };
  try {
    const archivePath = await ensureCachedArchive(asset, { cacheRoot, fetchImpl, retries: 1, log() {} });
    assert.equal(fetchCount, 1);
    assert.equal(await verifyPinnedArchive(archivePath, asset), true);

    await ensureCachedArchive(asset, {
      cacheRoot,
      fetchImpl: async () => { throw new Error("verified cache should not download"); },
      retries: 1,
      log() {},
    });
    assert.equal(fetchCount, 1);

    await fsp.writeFile(archivePath, "tampered\n", "utf8");
    await ensureCachedArchive(asset, { cacheRoot, fetchImpl, retries: 1, log() {} });
    assert.equal(fetchCount, 2);
    assert.equal(await verifyPinnedArchive(archivePath, asset), true);
  } finally {
    await fsp.rm(tempDir, { recursive: true, force: true });
  }
});

test("media-tool build cache honors EDMG_STUDIO_BUILD_CACHE_ROOT", () => {
  const configured = resolveMediaBuildCacheRoot({
    root: studioRoot,
    env: { EDMG_STUDIO_BUILD_CACHE_ROOT: "release-cache" },
  });
  assert.equal(configured, path.join(studioRoot, "release-cache"));
  assert.equal(
    resolveMediaBuildCacheRoot({ root: studioRoot, env: {} }),
    path.join(studioRoot, ".cache"),
  );
});

test("uv, Python, and Torch provenance checks enforce the release pins", () => {
  assert.equal(assertPinnedUvVersion("uv 0.11.28 (build metadata)"), "0.11.28");
  assert.throws(() => assertPinnedUvVersion("uv 0.11.27"), /pinned uv 0\.11\.28/);
  assert.equal(assertPython312("3.12.10"), "3.12.10");
  assert.throws(() => assertPython312("3.13.1"), /Python 3\.12/);
  assert.equal(assertTorchIndexForProfile("directml", "https://download.pytorch.org/whl/cpu/"), "https://download.pytorch.org/whl/cpu");
  assert.equal(assertTorchIndexForProfile("cuda", "https://download.pytorch.org/whl/cu130"), "https://download.pytorch.org/whl/cu130");
  assert.throws(() => assertTorchIndexForProfile("cuda", "https://example.invalid/cu130"), /fixed locked/);
});

test("release dependency metadata must be tracked and clean", () => {
  assert.doesNotThrow(() => assertTrackedCleanDependencyStatus({ trackedStatus: 0, dirtyStatus: "", paths: ["uv.lock"] }));
  assert.throws(
    () => assertTrackedCleanDependencyStatus({ trackedStatus: 1, dirtyStatus: "", paths: ["uv.lock"] }),
    /tracked by git/,
  );
  assert.throws(
    () => assertTrackedCleanDependencyStatus({ trackedStatus: 0, dirtyStatus: " M uv.lock", paths: ["uv.lock"] }),
    /committed and clean/,
  );
});

test("schema-5 onedir manifest validation and reuse reject provenance drift", () => {
  const manifest = validManifest();
  assert.deepEqual(validateReleaseManifest(manifest), []);
  assert.equal(releaseProvenanceMatches(manifest, manifest), true);
  assert.equal(releaseProvenanceMatches(manifest, { ...manifest, lockSha256: "9".repeat(64) }), false);
  assert.equal(releaseProvenanceMatches(manifest, { ...manifest, acceleratorProfile: "directml" }), false);
  assert.match(validateReleaseManifest({ ...manifest, pythonVersion: "3.13.0" }).join("; "), /Python 3\.12/);
  assert.match(validateReleaseManifest({ ...manifest, capabilityExtras: ["core"] }).join("; "), /capabilityExtras/);
  assert.match(
    validateReleaseManifest({ ...manifest, hfBucketHelper: undefined }).join("; "),
    /hfBucketHelper/,
  );
  assert.match(
    validateReleaseManifest({ ...manifest, launcherEnvDefaults: undefined }).join("; "),
    /launcherEnvDefaults/,
  );
  assert.match(
    validateReleaseManifest({ ...manifest, hfRuntimePackages: [] }).join("; "),
    /hfRuntimePackages/,
  );
  assert.match(
    validateReleaseManifest({ ...manifest, hfRuntimeBundleEvidence: undefined }).join("; "),
    /hfRuntimeBundleEvidence/,
  );
  const withoutFasterWhisperVad = {
    ...manifest,
    bundleEntries: manifest.bundleEntries.filter(
      (entry) => entry.path !== REQUIRED_FASTER_WHISPER_VAD_ASSET,
    ),
  };
  withoutFasterWhisperVad.bundleEntryCount = withoutFasterWhisperVad.bundleEntries.length;
  withoutFasterWhisperVad.bundleFileCount = withoutFasterWhisperVad.bundleEntries.length;
  withoutFasterWhisperVad.bundleSize = withoutFasterWhisperVad.bundleEntries.reduce(
    (total, entry) => total + entry.size,
    0,
  );
  assert.match(
    validateReleaseManifest(withoutFasterWhisperVad).join("; "),
    /silero_vad_v6\.onnx is missing or empty/,
  );
});

test("Linux release manifests require bundled and fingerprinted sidecar setup assets", () => {
  const manifest = validManifest({ platform: "linux" });
  assert.deepEqual(validateReleaseManifest(manifest), []);

  for (const entryPoint of REQUIRED_LINUX_SETUP_FILES) {
    const withoutBundleEntry = {
      ...manifest,
      bundleEntries: manifest.bundleEntries.filter((entry) => entry.path !== entryPoint),
    };
    withoutBundleEntry.bundleEntryCount = withoutBundleEntry.bundleEntries.length;
    withoutBundleEntry.bundleFileCount = withoutBundleEntry.bundleEntries.length;
    withoutBundleEntry.bundleSize = withoutBundleEntry.bundleEntries.reduce((total, entry) => total + entry.size, 0);
    assert.match(validateReleaseManifest(withoutBundleEntry).join("; "), new RegExp(`${entryPoint} is missing`));

    const withoutFingerprint = {
      ...manifest,
      fingerprintInputs: manifest.fingerprintInputs.filter((entry) => !entry.path.endsWith(entryPoint)),
    };
    assert.match(validateReleaseManifest(withoutFingerprint).join("; "), new RegExp(`fingerprintInputs is missing ${entryPoint}`));
    assert.equal(releaseProvenanceMatches(manifest, withoutFingerprint), false);
  }
});

test("release bundle reuse is scoped to the current platform and backend entry point", () => {
  const windowsManifest = validManifest({ platform: "win32" });
  const linuxManifest = validManifest({ platform: "linux" });

  assert.deepEqual(validateReleaseManifest(windowsManifest), []);
  assert.deepEqual(validateReleaseManifest(linuxManifest), []);
  assert.equal(releaseProvenanceMatches(windowsManifest, windowsManifest), true);
  assert.equal(releaseProvenanceMatches(linuxManifest, linuxManifest), true);

  assert.equal(
    releaseProvenanceMatches(windowsManifest, {
      ...windowsManifest,
      platform: "linux",
      backendEntryPoint: "edmg-studio-backend",
    }),
    false,
  );
  assert.equal(
    releaseProvenanceMatches(linuxManifest, {
      ...linuxManifest,
      platform: "win32",
      backendEntryPoint: "edmg-studio-backend.exe",
    }),
    false,
  );
  assert.equal(
    releaseProvenanceMatches(windowsManifest, {
      ...windowsManifest,
      backendEntryPoint: "edmg-studio-backend",
    }),
    false,
  );
  assert.match(
    validateReleaseManifest({
      ...linuxManifest,
      backendEntryPoint: "edmg-studio-backend.exe",
    }).join("; "),
    /backendEntryPoint must be edmg-studio-backend on linux/,
  );
});

test("Hugging Face runtime evidence matching is linear and path constrained", () => {
  assert.equal(
    isHfRuntimeEvidencePath(
      "hfTransferModule",
      "_internal/hf_transfer/hf_transfer.abi3.so",
    ),
    true,
  );
  assert.equal(
    isHfRuntimeEvidencePath(
      "hfXetModule",
      "_internal/hf_xet/hf_xet.pyd",
    ),
    true,
  );
  assert.equal(
    isHfRuntimeEvidencePath(
      "hfTransferModule",
      `_internal/hf_transfer/hf_transfer.${".".repeat(100_000)}txt`,
    ),
    false,
  );
  assert.equal(
    isHfRuntimeEvidencePath(
      "hfXetModule",
      "_internal/hf_xet/hf_xet.abi3.so/escaped",
    ),
    false,
  );
});

test("binary reuse verifies both size and SHA-256", async () => {
  const tempDir = await fsp.mkdtemp(path.join(os.tmpdir(), "edmg-release-manifest-"));
  const binaryPath = path.join(tempDir, "backend.bin");
  try {
    await fsp.writeFile(binaryPath, "locked backend\n", "utf8");
    const stat = await fsp.stat(binaryPath);
    const manifest = validManifest({ binarySize: stat.size, binarySha256: await sha256File(binaryPath) });
    assert.equal(await binaryMatchesManifest(binaryPath, manifest), true);
    await fsp.appendFile(binaryPath, "tampered\n", "utf8");
    assert.equal(await binaryMatchesManifest(binaryPath, manifest), false);
  } finally {
    await fsp.rm(tempDir, { recursive: true, force: true });
  }
});

test("onedir reuse verifies every staged backend entry", async () => {
  const tempDir = await fsp.mkdtemp(path.join(os.tmpdir(), "edmg-release-onedir-"));
  const backendEntryPoint = process.platform === "win32" ? "edmg-studio-backend.exe" : "edmg-studio-backend";
  const launcherPath = path.join(tempDir, backendEntryPoint);
  const helperEntryPoint = process.platform === "win32" ? "edmg-hf-bucket-helper.exe" : "edmg-hf-bucket-helper";
  const helperPath = path.join(tempDir, helperEntryPoint);
  const defaultsPath = path.join(tempDir, "launcher_env.defaults.json");
  const runtimePath = path.join(tempDir, "_internal", "torch-runtime.bin");
  const fasterWhisperVadPath = path.join(
    tempDir,
    ...REQUIRED_FASTER_WHISPER_VAD_ASSET.split("/"),
  );
  try {
    await fsp.mkdir(path.dirname(runtimePath), { recursive: true });
    await fsp.mkdir(path.dirname(fasterWhisperVadPath), { recursive: true });
    await fsp.writeFile(launcherPath, "launcher\n", "utf8");
    await fsp.writeFile(helperPath, "helper\n", "utf8");
    await fsp.writeFile(defaultsPath, "{}\n", "utf8");
    await fsp.writeFile(runtimePath, "runtime\n", "utf8");
    await fsp.writeFile(fasterWhisperVadPath, "silero vad runtime\n", "utf8");
    for (const evidencePath of Object.values(validManifest().hfRuntimeBundleEvidence)) {
      const absoluteEvidencePath = path.join(tempDir, ...evidencePath.split("/"));
      await fsp.mkdir(path.dirname(absoluteEvidencePath), { recursive: true });
      await fsp.writeFile(absoluteEvidencePath, "runtime hf\n", "utf8");
    }
    if (process.platform === "linux") {
      for (const entryPoint of REQUIRED_LINUX_SETUP_FILES) {
        const absoluteScriptPath = path.join(tempDir, ...entryPoint.split("/"));
        await fsp.mkdir(path.dirname(absoluteScriptPath), { recursive: true });
        await fsp.writeFile(absoluteScriptPath, entryPoint.endsWith(".sh") ? "#!/usr/bin/env bash\n" : "pin\n", "utf8");
      }
    }
    const bundleEntries = await collectBundleEntries(tempDir);
    const launcher = bundleEntries.find((entry) => entry.path === backendEntryPoint);
    const helper = bundleEntries.find((entry) => entry.path === helperEntryPoint);
    const defaults = bundleEntries.find((entry) => entry.path === "launcher_env.defaults.json");
    const fingerprintInputs = validManifest().fingerprintInputs.map((entry) =>
      entry.path.endsWith("launcher_env.defaults.json")
        ? { ...entry, sha256: defaults.sha256 }
        : entry
    );
    const manifest = validManifest({
      backendEntryPoint,
      bundleEntries,
      bundleEntryCount: bundleEntries.length,
      bundleFileCount: bundleEntries.filter((entry) => entry.type === "file").length,
      bundleSize: bundleEntries
        .filter((entry) => entry.type === "file")
        .reduce((total, entry) => total + entry.size, 0),
      binarySize: launcher.size,
      binarySha256: launcher.sha256,
      hfBucketHelper: {
        entryPoint: helperEntryPoint,
        helperVersion: "1.0.0",
        huggingfaceHubVersion: "1.20.1",
        hfXetVersion: "1.5.1",
        lockSha256: "8".repeat(64),
        binarySha256: helper.sha256,
        binarySize: helper.size,
      },
      launcherEnvDefaults: {
        entryPoint: "launcher_env.defaults.json",
        sha256: defaults.sha256,
        size: defaults.size,
      },
      fingerprintInputs,
    });
    assert.deepEqual(validateReleaseManifest(manifest), []);
    assert.equal(await bundleMatchesManifest(tempDir, manifest), true);
    await fsp.appendFile(runtimePath, "tampered\n", "utf8");
    assert.equal(await bundleMatchesManifest(tempDir, manifest), false);
    await fsp.writeFile(path.join(tempDir, "unexpected.txt"), "extra\n", "utf8");
    assert.equal(await bundleMatchesManifest(tempDir, manifest), false);
  } finally {
    await fsp.rm(tempDir, { recursive: true, force: true });
  }
});

test(
  "Linux bundle staging materializes external symlinks and preserves internal links",
  { skip: process.platform === "win32" },
  async () => {
    const tempDir = await fsp.mkdtemp(path.join(os.tmpdir(), "edmg-release-symlinks-"));
    const bundleDir = path.join(tempDir, "bundle");
    const externalFile = path.join(tempDir, "system-library.so");
    const bundledFile = path.join(bundleDir, "bundled-library.so");
    const externalLink = path.join(bundleDir, "external-library.so");
    const internalLink = path.join(bundleDir, "internal-library.so");
    try {
      await fsp.mkdir(bundleDir, { recursive: true });
      await fsp.writeFile(externalFile, "external library\n", "utf8");
      await fsp.writeFile(bundledFile, "bundled library\n", "utf8");
      await fsp.symlink(externalFile, externalLink);
      await fsp.symlink(path.basename(bundledFile), internalLink);

      assert.equal(await materializeExternalBundleSymlinks(bundleDir), 1);
      assert.equal((await fsp.lstat(externalLink)).isFile(), true);
      assert.equal(await fsp.readFile(externalLink, "utf8"), "external library\n");
      assert.equal((await fsp.lstat(internalLink)).isSymbolicLink(), true);

      const entries = await collectBundleEntries(bundleDir);
      assert.equal(entries.find((entry) => entry.path === "external-library.so")?.type, "file");
      assert.deepEqual(
        entries.find((entry) => entry.path === "internal-library.so"),
        {
          path: "internal-library.so",
          type: "symlink",
          target: "bundled-library.so",
        },
      );
    } finally {
      await fsp.rm(tempDir, { recursive: true, force: true });
    }
  },
);

test("supported release paths contain no pip or venv build fallback", () => {
  const prepare = fs.readFileSync(path.join(__dirname, "prepare-release-bundle.mjs"), "utf8");
  const windowsBuild = fs.readFileSync(path.join(studioRoot, "packaging", "windows", "build_all.ps1"), "utf8");
  const gitignore = fs.readFileSync(path.resolve(studioRoot, "..", "..", ".gitignore"), "utf8");
  const pyinstallerSupport = fs.readFileSync(path.join(studioRoot, "python_backend", "pyinstaller_support.py"), "utf8");
  const pyinstallerSpec = fs.readFileSync(path.join(studioRoot, "python_backend", "pyinstaller.spec"), "utf8");
  assert.doesNotMatch(prepare, /(?:-m\s+pip|pip\s+install|-m\s+venv)/i);
  assert.doesNotMatch(windowsBuild, /(?:-m\s+pip|pip\s+install|-m\s+venv)/i);
  assert.doesNotMatch(pyinstallerSupport, /nltk\.download\s*\(/);
  assert.match(pyinstallerSpec, /upx=False/);
  assert.match(pyinstallerSpec, /exclude_binaries=True/);
  assert.match(pyinstallerSpec, /COLLECT\s*\(/);
  assert.match(gitignore, /electron-resources\/backend\/\*/);
  assert.match(gitignore, /!studio\/edmg-studio\/electron-resources\/backend\/\.gitkeep/);
});

test("release bundle builds and requires the isolated HF Bucket helper and launcher defaults", () => {
  const prepare = fs.readFileSync(path.join(__dirname, "prepare-release-bundle.mjs"), "utf8");
  const defaults = JSON.parse(
    fs.readFileSync(path.join(studioRoot, "launcher_env.defaults.json"), "utf8"),
  );
  assert.match(prepare, /hf_bucket_helper/);
  assert.match(prepare, /buildHfBucketHelper/);
  assert.match(prepare, /validate Hugging Face Bucket helper lock/);
  assert.match(prepare, /synchronize frozen Hugging Face Bucket helper environment/);
  assert.match(prepare, /hfBucketHelperBinaryName/);
  assert.match(prepare, /launcher_env\.defaults\.json/);
  assert.equal(defaults.HF_HUB_ENABLE_HF_TRANSFER, "1");
  assert.equal(defaults.HF_HUB_DISABLE_XET, "0");
  assert.equal(defaults.HF_XET_HIGH_PERFORMANCE, "1");
  const helper = validManifest().hfBucketHelper;
  assert.equal(helper.huggingfaceHubVersion, "1.20.1");
  assert.equal(helper.hfXetVersion, "1.5.1");
});

test("Director release stages a self-contained production hoisted install", () => {
  const prepare = fs.readFileSync(path.join(__dirname, "prepare-release-bundle.mjs"), "utf8");
  const directorRoot = path.resolve(studioRoot, "..", "..", "chatgpt-apps", "edmg-director");
  const directorPackage = JSON.parse(fs.readFileSync(path.join(directorRoot, "package.json"), "utf8"));
  const directorLock = fs.readFileSync(path.join(directorRoot, "pnpm-lock.yaml"), "utf8");
  assert.match(prepare, /"--prod"/);
  assert.match(prepare, /"--frozen-lockfile"/);
  assert.match(prepare, /"--config\.node-linker=hoisted"/);
  assert.match(prepare, /"--config\.package-import-method=copy"/);
  assert.match(prepare, /inspectDirectorDependencyTree/);
  assert.match(prepare, /load staged director entrypoint/);
  assert.match(prepare, /await import/);
  assert.doesNotMatch(prepare, /const copyEntries = \[[^\]]*"node_modules"/s);
  assert.equal(directorPackage.pnpm?.overrides?.["fast-uri"], "3.1.5");
  assert.equal(directorPackage.pnpm?.overrides?.["ip-address"], "10.3.1");
  assert.equal(directorPackage.pnpm?.overrides?.hono, "4.12.34");
  for (const resolution of ["fast-uri@3.1.5:", "ip-address@10.3.1:", "hono@4.12.34:"]) {
    assert.match(directorLock, new RegExp(`^  ${resolution.replaceAll(".", "\\.")}`, "m"));
  }
});

test("package release commands select explicit profiles without changing pnpm", () => {
  const packageJson = JSON.parse(fs.readFileSync(path.join(studioRoot, "package.json"), "utf8"));
  assert.equal(packageJson.packageManager, "pnpm@10.33.0");
  for (const profile of ["cpu", "directml", "cuda"]) {
    assert.equal(
      packageJson.scripts[`prepare:release-bundle:${profile}`],
      `node scripts/prepare-release-bundle.mjs --profile ${profile}`,
    );
  }
  assert.match(packageJson.scripts["dist:win:cpu"], /prepare:release-bundle:cpu/);
  assert.match(packageJson.scripts["dist:win:directml"], /prepare:release-bundle:directml/);
  assert.match(packageJson.scripts["dist:win:cuda"], /dist:win:cuda:dir/);
  assert.match(packageJson.scripts["dist:win:cuda"], /stage:winui:msix/);
  assert.match(packageJson.scripts["dist:win:cuda"], /build_inno_external\.ps1/);
  assert.match(packageJson.scripts["dist:win:cuda"], /--profile cuda/);
  assert.match(packageJson.scripts["dist:win:cuda"], /--artifact-set win-inno-cuda/);
  assert.match(packageJson.scripts["dist:win:cuda:dir"], /prepare:release-bundle:cuda/);
  assert.match(packageJson.scripts["dist:win:cuda:nsis"], /prepare:release-bundle:cuda/);
  assert.match(packageJson.scripts["dist:linux"], /prepare:release-bundle:cpu/);
  assert.match(packageJson.scripts["dist:linux:cuda"], /prepare:release-bundle:cuda/);
  assert.match(packageJson.scripts["validate:desktop"], /pnpm run typecheck && pnpm run lint && pnpm run test:ui/);
});

test("Windows packaging stages and installs a self-contained packaged WinUI primary frontend", () => {
  const stageWinUi = fs.readFileSync(
    path.join(studioRoot, "packaging", "windows", "stage_winui_msix.ps1"),
    "utf8",
  );
  const manageWinUi = fs.readFileSync(
    path.join(studioRoot, "packaging", "windows", "manage_winui_package.ps1"),
    "utf8",
  );
  const innoBuild = fs.readFileSync(
    path.join(studioRoot, "packaging", "windows", "build_inno_external.ps1"),
    "utf8",
  );

  assert.match(stageWinUi, /-p:Platform=x64/);
  assert.match(stageWinUi, /-p:RuntimeIdentifier=win-x64/);
  assert.match(stageWinUi, /-p:WindowsAppSDKSelfContained=true/);
  assert.match(stageWinUi, /-p:PublishTrimmed=false/);
  assert.match(stageWinUi, /-p:AppxBundle=Never/);
  assert.match(stageWinUi, /local-name\(\)='PackageDependency'/);
  assert.match(stageWinUi, /Generated WinUI MSIX is not self-contained/);
  assert.match(stageWinUi, /windowsAppSdkDeployment = "self-contained"/);

  assert.match(innoBuild, /windowsAppSdkDeployment.*-cne "self-contained"/);
  assert.match(innoBuild, /release\\winui-msix/);
  assert.match(innoBuild, /-Action Install -InstallRoot/);
  assert.match(innoBuild, /-Action Uninstall -InstallRoot/);
  assert.match(innoBuild, /EDMG Studio \(Electron compatibility\)/);
  assert.match(innoBuild, /-Action Launch/);
  assert.match(
    innoBuild,
    /\[Run\][\s\S]*-Action Install -InstallRoot[\s\S]*-Action Launch"; Description: "Launch EDMG Studio"/,
    "the package must be registered before the post-install WinUI launch",
  );

  assert.match(manageWinUi, /Get-AppxPackage -Name \$packageName/);
  assert.match(manageWinUi, /shell:AppsFolder\\\$\(\$package\.PackageFamilyName\)!\$applicationId/);
  assert.match(manageWinUi, /Get-AuthenticodeSignature/);
  assert.match(manageWinUi, /Add-AppxPackage -Path \$normalizedMsixPath/);
  assert.match(manageWinUi, /Remove-AppxPackage -Package \$package\.PackageFullName/);
  assert.ok(
    manageWinUi.indexOf("Write-Utf8FileAtomically $locatorPath") <
      manageWinUi.indexOf("Add-AppxPackage -Path $normalizedMsixPath"),
    "backend discovery must be configured before package registration",
  );
  assert.ok(
    manageWinUi.indexOf("Remove-AppxPackage -Package $package.PackageFullName") <
      manageWinUi.lastIndexOf("Remove-MatchingLocator $normalizedRoot"),
    "uninstall must remove the package before its backend locator",
  );
  assert.match(manageWinUi, /Write-Utf8FileAtomically \$locatorPath \$previousLocator/);
});

test("build-tool transitive security overrides stay on audited patched releases", () => {
  const packageJson = JSON.parse(fs.readFileSync(path.join(studioRoot, "package.json"), "utf8"));
  const lockfile = fs.readFileSync(path.join(studioRoot, "pnpm-lock.yaml"), "utf8");
  assert.deepEqual(
    {
      brace1: packageJson.pnpm?.overrides?.["brace-expansion@1"],
      brace2: packageJson.pnpm?.overrides?.["brace-expansion@2"],
      brace5: packageJson.pnpm?.overrides?.["brace-expansion@5"],
      fastUri3: packageJson.pnpm?.overrides?.["fast-uri@3"],
      jsYaml4: packageJson.pnpm?.overrides?.["js-yaml@4"],
    },
    {
      brace1: "1.1.18",
      brace2: "2.1.4",
      brace5: "5.0.9",
      fastUri3: "3.1.5",
      jsYaml4: "4.3.1",
    },
  );
  for (const resolution of [
    "brace-expansion@1.1.18:",
    "brace-expansion@2.1.4:",
    "brace-expansion@5.0.9:",
    "fast-uri@3.1.5:",
    "js-yaml@4.3.1:",
  ]) {
    assert.match(lockfile, new RegExp(`^  ${resolution.replaceAll(".", "\\.")}`, "m"));
  }
});

test("Studio CI uses Vitest 4 compatible worker arguments", () => {
  const workflow = fs.readFileSync(
    path.resolve(studioRoot, "..", "..", ".github", "workflows", "studio.yml"),
    "utf8",
  );
  const packageJson = JSON.parse(fs.readFileSync(path.join(studioRoot, "package.json"), "utf8"));

  assert.equal(packageJson.scripts["test:ui:release"], "vitest run --maxWorkers=1");
  assert.match(packageJson.scripts["validate:desktop"], /pnpm run test:ui:release/);
  assert.match(workflow, /pnpm run test:ui:release/);
  assert.doesNotMatch(workflow, /--minWorkers/);
});

test("desktop integration supports the canonical root WSL Electron workflow", () => {
  const harness = fs.readFileSync(path.join(studioRoot, "scripts", "desktop-integration-harness.mjs"), "utf8");
  const packagedSmoke = fs.readFileSync(path.join(studioRoot, "scripts", "packaged-desktop-smoke.mjs"), "utf8");

  assert.match(harness, /EDMG_DIRECTOR_SPAWN:\s*"0"/);
  assert.match(packagedSmoke, /EDMG_DIRECTOR_SPAWN:\s*"0"/);
  assert.match(harness, /process\.platform === "linux"/);
  assert.match(harness, /process\.getuid\(\) === 0/);
  assert.match(harness, /args\.push\("--no-sandbox"\)/);
  assert.match(packagedSmoke, /process\.platform === "linux"/);
  assert.match(packagedSmoke, /process\.getuid\(\) === 0/);
  assert.match(packagedSmoke, /args\.push\("--no-sandbox"\)/);
});

test("packaged customer proof compares canonical filesystem identities", () => {
  const customerFlow = fs.readFileSync(path.join(studioRoot, "scripts", "packaged-customer-flow.mjs"), "utf8");

  assert.match(customerFlow, /async function assertSameExistingPath/);
  assert.match(customerFlow, /fsp\.realpath\(actual\)/);
  assert.match(customerFlow, /fsp\.realpath\(expected\)/);
  assert.match(customerFlow, /await assertSameExistingPath\(summary\.paths\.studioHome/);
});

test("zero-state setup proof compares canonical filesystem identities", () => {
  const zeroStateProof = fs.readFileSync(path.join(studioRoot, "scripts", "packaged-zero-state-setup-proof.mjs"), "utf8");

  assert.match(zeroStateProof, /async function assertSameExistingPath/);
  assert.match(zeroStateProof, /fsp\.realpath\(actual\)/);
  assert.match(zeroStateProof, /await assertSameExistingPath\(summary\.config\.studioHome/);
  assert.match(zeroStateProof, /await assertSameExistingPath\(summary\.finalStatus\.ollamaExe/);
});

test("desktop packaging stages and requires pinned FFmpeg plus FFprobe on Windows and Linux", () => {
  const prepareElectron = fs.readFileSync(path.join(studioRoot, "scripts", "prepare-electron-build.mjs"), "utf8");
  const mediaStager = fs.readFileSync(path.join(studioRoot, "scripts", "stage-media-tools.mjs"), "utf8");
  const packagedSmoke = fs.readFileSync(path.join(studioRoot, "scripts", "packaged-desktop-smoke.mjs"), "utf8");
  const windowsHelper = fs.readFileSync(path.join(studioRoot, "packaging", "windows", "get_ffmpeg.ps1"), "utf8");
  const windowsBuild = fs.readFileSync(path.join(studioRoot, "packaging", "windows", "build_all.ps1"), "utf8");
  const linuxHelper = fs.readFileSync(path.join(studioRoot, "packaging", "linux", "get_ffmpeg.sh"), "utf8");

  assert.match(prepareElectron, /stagePinnedMediaTools/);
  assert.match(mediaStager, /EDMG_STUDIO_BUILD_CACHE_ROOT/);
  assert.match(mediaStager, /verifyPinnedArchive/);
  assert.match(mediaStager, /ffprobe/);
  assert.doesNotMatch(windowsHelper, /Get-Command\s+["']ffmpeg\.exe/);
  assert.match(windowsHelper, /stage-media-tools\.mjs/);
  assert.match(windowsBuild, /ffprobe\.exe/);
  assert.match(linuxHelper, /stage-media-tools\.mjs/);
  assert.match(packagedSmoke, /ffprobeExe/);
  assert.match(packagedSmoke, /ffmpegLicense/);
  assert.match(packagedSmoke, /ffmpegSourceNotice/);
  assert.match(packagedSmoke, /mediaDistributionEvidence/);
  assert.match(mediaStager, /distributionNotice\.licenseOutputName/);
  assert.match(mediaStager, /distributionNotice\.sourceNoticeOutputName/);
  assert.match(mediaStager, /copyFile\(licenseSourcePath, licensePendingPath\)/);
  assert.match(packagedSmoke, /process\.platform === "win32" \|\| process\.platform === "linux"/);
});

test("Linux backend bundle build copies the frozen setup sidecars", () => {
  const prepareBundle = fs.readFileSync(path.join(studioRoot, "scripts", "prepare-release-bundle.mjs"), "utf8");
  assert.match(prepareBundle, /REQUIRED_LINUX_SETUP_FILES/);
  for (const entryPoint of REQUIRED_LINUX_SETUP_FILES) {
    assert.equal(fs.existsSync(path.join(studioRoot, ...entryPoint.split("/"))), true, entryPoint);
  }
  assert.match(prepareBundle, /fs\.chmodSync\(destinationPath, 0o755\)/);
});

test("legacy TensorRT Deforum simulation is not reachable from the Studio UI", () => {
  const renderPage = fs.readFileSync(path.join(studioRoot, "src", "pages", "Render.tsx"), "utf8");
  const removedService = path.join(
    studioRoot,
    "python_backend",
    "edmg_studio_backend",
    "services",
    "tensorrt_deforum.py",
  );
  assert.doesNotMatch(renderPage, /tensorrt-deforum/);
  assert.match(renderPage, /\/render\/internal\/video/);
  assert.match(renderPage, /render_mode: internalRenderMode/);
  assert.equal(fs.existsSync(removedService), false);
});

test("Inno external installer replaces exact app-owned payload entries on upgrade", () => {
  const innoBuild = fs.readFileSync(
    path.join(studioRoot, "packaging", "windows", "build_inno_external.ps1"),
    "utf8",
  );
  assert.match(innoBuild, /VER < EncodeVer\(7,0,0\)/);
  assert.match(innoBuild, /ArchiveExtraction=enhanced\/nopassword/);
  assert.match(innoBuild, /PayloadExpandedSize/);
  assert.match(innoBuild, /Security\.Cryptography\.SHA256\]::Create/);
  assert.doesNotMatch(innoBuild, /Get-FileHash/);
  assert.match(innoBuild, /ExternalSize: \{0\}; Hash: "\{1\}"/);
  assert.match(innoBuild, /AppPublisherURL=https:\/\/github\.com\/DWCTEDMG\/DWCTGenerativeSoundStudio/);
  assert.match(innoBuild, /AppSupportURL=https:\/\/github\.com\/DWCTEDMG\/DWCTGenerativeSoundStudio\/issues/);
  assert.doesNotMatch(innoBuild, /github\.com\/HIMOI890\/DWCTGenerativeSoundStudio/);
  assert.match(innoBuild, /\[InstallDelete\]/);
  assert.match(innoBuild, /Get-ChildItem -LiteralPath \$WinUnpackedDir -Force/);
  assert.match(innoBuild, /Where-Object \{ \$_.Name -notlike "unins\*" \}/);
  assert.match(innoBuild, /Escape-InnoValue \$ownedEntry\.Name/);
  assert.doesNotMatch(innoBuild, /Type: filesandordirs; Name: "\{app\}"/);
  assert.match(innoBuild, /external extractarchive recursesubdirs createallsubdirs ignoreversion/);
  assert.doesNotMatch(innoBuild, /\[UninstallDelete\]/);
  assert.doesNotMatch(innoBuild, /\{app\}\\\*/);
  assert.doesNotMatch(innoBuild, /payload\\tools\\7zip/);
  assert.match(innoBuild, /Invoke-WindowsSigning \$StudioDir \$PayloadSignables "pre-archive payload"/);
  assert.match(innoBuild, /Invoke-WindowsSigning \$StudioDir @\(\$SetupPath\) "post-compile setup"/);
  assert.ok(
    innoBuild.indexOf("pre-archive payload") < innoBuild.indexOf("7-Zip payload archive"),
    "payload executable signatures must be verified before their archive hash is computed",
  );
  assert.ok(
    innoBuild.indexOf("Inno Setup compile") < innoBuild.indexOf("post-compile setup"),
    "the compiled setup must be signed after ISCC finishes",
  );
});

test("release and deployment entry points use the canonical repository identity", () => {
  const canonicalRepository = "github.com/DWCTEDMG/DWCTGenerativeSoundStudio";
  const legacyRepository = /github\.com\/HIMOI890\/DWCTGenerativeSoundStudio/;
  const entryPoints = [
    "edmg_gcp_gpu_bootstrap.sh",
    "edmg_remote_reinstall_ports.sh",
    "run_gcp_edmg_bootstrap.ps1",
    "run_vast_edmg_direct_36066304.ps1",
    "packaging/windows/build_inno_external.ps1",
    "tools/edmgctl/go.mod",
    "tools/edmgctl/cmd/edmgctl/main.go",
  ];
  for (const relativePath of entryPoints) {
    const source = fs.readFileSync(path.join(studioRoot, ...relativePath.split("/")), "utf8");
    assert.match(source, new RegExp(canonicalRepository.replaceAll(".", "\\.")), relativePath);
    assert.doesNotMatch(source, legacyRepository, relativePath);
  }
});
