import { app, BrowserWindow, dialog, ipcMain, shell } from "electron";
import { spawn } from "node:child_process";
import fs from "node:fs";
import { promises as fsp } from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const APP_NAME = "EDMG Studio";
const IS_DEV = !app.isPackaged;
const IS_WINDOWS = process.platform === "win32";
const BOOTSTRAP_CONFIG_BASENAME = "bootstrap.json";

const BACKEND_HOST = process.env.EDMG_STUDIO_BACKEND_HOST ?? "127.0.0.1";
let BACKEND_PORT = Number(process.env.EDMG_STUDIO_BACKEND_PORT ?? "7863");
const SPAWN_BACKEND = (process.env.EDMG_STUDIO_SPAWN_BACKEND ?? "1") !== "0";

const TEST_MODE = (process.env.EDMG_STUDIO_TEST_MODE ?? "0") === "1";
const TEST_PAGE = process.env.EDMG_STUDIO_TEST_PAGE
  ? path.resolve(process.env.EDMG_STUDIO_TEST_PAGE)
  : "";
const TEST_REPORT_PATH = process.env.EDMG_STUDIO_TEST_REPORT_PATH
  ? path.resolve(process.env.EDMG_STUDIO_TEST_REPORT_PATH)
  : "";
const FAKE_PATH_ACTIONS = (process.env.EDMG_STUDIO_TEST_FAKE_PATH_ACTIONS ?? "0") === "1";

const UI_PORT = process.env.EDMG_STUDIO_UI_PORT ?? "5173";
const DEV_SERVER_URL =
  process.env.VITE_DEV_SERVER_URL ??
  process.env.EDMG_STUDIO_DEV_SERVER_URL ??
  `http://127.0.0.1:${UI_PORT}`;

const AI_SETTINGS_DEFAULTS = Object.freeze({
  mode: "local",
  provider: "ollama",
  aiBaseUrl: "http://127.0.0.1:7862",
  ollamaUrl: "http://127.0.0.1:11434",
  ollamaModel: "qwen2.5:3b-instruct",
  openaiCompatBaseUrl: "http://127.0.0.1:8000",
  openaiCompatModel: "qwen2.5-7b-instruct",
});

const AI_SETTINGS_ENV_KEYS = Object.freeze({
  mode: "EDMG_AI_MODE",
  provider: "EDMG_AI_PROVIDER",
  aiBaseUrl: "EDMG_AI_BASE_URL",
  ollamaUrl: "EDMG_AI_OLLAMA_URL",
  ollamaModel: "EDMG_AI_OLLAMA_MODEL",
  openaiCompatBaseUrl: "EDMG_AI_OPENAI_COMPAT_BASE_URL",
  openaiCompatModel: "EDMG_AI_OPENAI_COMPAT_MODEL",
});

const AI_LOCAL_PROVIDER_ALIASES = Object.freeze({
  ollama: "ollama",
  openai: "openai_compat",
  "openai-compatible": "openai_compat",
  openai_compat: "openai_compat",
  rule_based: "rule_based",
  none: "rule_based",
});

const STORAGE_SETTINGS_DEFAULT_DIRS = Object.freeze({
  dataDir: "data",
  modelsDir: "models",
  cacheRoot: "cache",
  logsDir: "logs",
  externalDir: "external",
});

const STORAGE_SETTINGS_ENV_KEYS = Object.freeze({
  dataDir: "EDMG_STUDIO_DATA_DIR",
  modelsDir: "EDMG_STUDIO_MODELS_DIR",
  cacheRoot: "EDMG_STUDIO_CACHE_DIR",
  logsDir: "EDMG_STUDIO_LOGS_DIR",
  externalDir: "EDMG_STUDIO_EXTERNAL_DIR",
});

let currentBackendUrl = `http://${BACKEND_HOST}:${BACKEND_PORT}`;
console.log(`EDMG_currentBackendUrl=${currentBackendUrl}`);

let mainWindow = null;
let backendProc = null;
let backendSpawnFailed = false;

app.setName(APP_NAME);

function ensureDirSync(targetPath) {
  fs.mkdirSync(targetPath, { recursive: true });
}

function pathExistsSync(targetPath) {
  try {
    return fs.existsSync(targetPath);
  } catch {
    return false;
  }
}

function resolveConfiguredPath(rawValue) {
  const value = String(rawValue ?? "").trim();
  if (!value) return "";
  const resolved = path.resolve(value);
  return resolved.toLowerCase().includes("app.asar") ? "" : resolved;
}

function getBootstrapConfigPath() {
  return path.join(app.getPath("appData"), APP_NAME, BOOTSTRAP_CONFIG_BASENAME);
}

function readBootstrapConfig() {
  const filePath = getBootstrapConfigPath();
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return {};
  }
}

function writeBootstrapConfig(nextConfig) {
  const filePath = getBootstrapConfigPath();
  ensureDirSync(path.dirname(filePath));
  fs.writeFileSync(filePath, JSON.stringify(nextConfig, null, 2), "utf8");
}

function getLauncherEnvPath() {
  if (!IS_DEV) return "";
  const filePath = path.join(__dirname, "launcher_env.json");
  return filePath.toLowerCase().includes("app.asar") ? "" : filePath;
}

function readLauncherEnv() {
  const filePath = getLauncherEnvPath();
  if (!filePath) return {};
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return {};
  }
}

function writeLauncherEnv(nextConfig) {
  const filePath = getLauncherEnvPath();
  if (!filePath) return false;
  ensureDirSync(path.dirname(filePath));
  fs.writeFileSync(filePath, JSON.stringify(nextConfig, null, 2), "utf8");
  return true;
}

function pickConfiguredString(...values) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return "";
}

function getDefaultStoragePaths(studioHome) {
  const resolvedHome = resolveConfiguredPath(studioHome);
  if (!resolvedHome) {
    const fallbackDataDir = path.join(app.getPath("userData"), "data");
    const fallbackHome = path.dirname(fallbackDataDir);
    return getDefaultStoragePaths(fallbackHome);
  }

  const electronUserData = path.join(resolvedHome, "electron");
  return {
    studioHome: resolvedHome,
    dataDir: path.join(resolvedHome, STORAGE_SETTINGS_DEFAULT_DIRS.dataDir),
    modelsDir: path.join(resolvedHome, STORAGE_SETTINGS_DEFAULT_DIRS.modelsDir),
    cacheRoot: path.join(resolvedHome, STORAGE_SETTINGS_DEFAULT_DIRS.cacheRoot),
    logsDir: path.join(resolvedHome, STORAGE_SETTINGS_DEFAULT_DIRS.logsDir),
    externalDir: path.join(resolvedHome, STORAGE_SETTINGS_DEFAULT_DIRS.externalDir),
    electronUserData,
    sessionData: path.join(electronUserData, "session"),
  };
}

function getRawStorageSettingsFromEnv(envLike) {
  const env = envLike && typeof envLike === "object" ? envLike : {};
  return {
    dataDir: env[STORAGE_SETTINGS_ENV_KEYS.dataDir],
    modelsDir: env[STORAGE_SETTINGS_ENV_KEYS.modelsDir],
    cacheRoot: env[STORAGE_SETTINGS_ENV_KEYS.cacheRoot],
    logsDir: env[STORAGE_SETTINGS_ENV_KEYS.logsDir],
    externalDir: env[STORAGE_SETTINGS_ENV_KEYS.externalDir],
  };
}

function readBootstrapStorageSettingsRaw() {
  const bootstrapConfig = readBootstrapConfig();
  if (bootstrapConfig?.storageSettings && typeof bootstrapConfig.storageSettings === "object") {
    return bootstrapConfig.storageSettings;
  }
  return {};
}

function readLauncherStorageSettingsRaw() {
  const launcherEnv = readLauncherEnv();
  const launcherHome = resolveConfiguredPath(launcherEnv?.EDMG_STUDIO_HOME);
  const raw = getRawStorageSettingsFromEnv(launcherEnv);
  return launcherHome ? trimStorageOverrides(raw, launcherHome) : normalizeStorageOverrides(raw);
}

function hasAnyStorageSetting(rawSettings) {
  return Object.values(rawSettings ?? {}).some((value) => typeof value === "string" && value.trim());
}

function normalizeStorageOverrides(rawSettings = {}) {
  const current = rawSettings && typeof rawSettings === "object" ? rawSettings : {};
  return {
    dataDir: resolveConfiguredPath(current.dataDir),
    modelsDir: resolveConfiguredPath(current.modelsDir),
    cacheRoot: resolveConfiguredPath(current.cacheRoot),
    logsDir: resolveConfiguredPath(current.logsDir),
    externalDir: resolveConfiguredPath(current.externalDir),
  };
}

function buildResolvedStudioPaths(studioHome, rawSettings = {}) {
  const defaults = getDefaultStoragePaths(studioHome);
  const overrides = normalizeStorageOverrides(rawSettings);
  return {
    studioHome: defaults.studioHome,
    dataDir: overrides.dataDir || defaults.dataDir,
    modelsDir: overrides.modelsDir || defaults.modelsDir,
    cacheRoot: overrides.cacheRoot || defaults.cacheRoot,
    logsDir: overrides.logsDir || defaults.logsDir,
    externalDir: overrides.externalDir || defaults.externalDir,
    electronUserData: defaults.electronUserData,
    sessionData: defaults.sessionData,
  };
}

function trimStorageOverrides(rawSettings = {}, studioHome = "") {
  const defaults = getDefaultStoragePaths(studioHome || getConfiguredStudioHome() || path.dirname(getDefaultDataDir()));
  const normalized = normalizeStorageOverrides(rawSettings);
  const trimmed = {};
  for (const [key, value] of Object.entries(normalized)) {
    if (value && !samePath(value, defaults[key])) {
      trimmed[key] = value;
    }
  }
  return trimmed;
}

function getRawAiSettingsFromEnv(envLike) {
  const env = envLike && typeof envLike === "object" ? envLike : {};
  return {
    mode: env[AI_SETTINGS_ENV_KEYS.mode],
    provider: env[AI_SETTINGS_ENV_KEYS.provider],
    aiBaseUrl: env[AI_SETTINGS_ENV_KEYS.aiBaseUrl],
    ollamaUrl: env[AI_SETTINGS_ENV_KEYS.ollamaUrl],
    ollamaModel: env[AI_SETTINGS_ENV_KEYS.ollamaModel],
    openaiCompatBaseUrl: env[AI_SETTINGS_ENV_KEYS.openaiCompatBaseUrl],
    openaiCompatModel: env[AI_SETTINGS_ENV_KEYS.openaiCompatModel],
  };
}

function readBootstrapAiSettingsRaw() {
  const bootstrapConfig = readBootstrapConfig();
  if (bootstrapConfig?.aiSettings && typeof bootstrapConfig.aiSettings === "object") {
    return bootstrapConfig.aiSettings;
  }
  return {};
}

function hasAnyAiSetting(rawSettings) {
  return Object.values(rawSettings ?? {}).some((value) => typeof value === "string" && value.trim());
}

function normalizeAiMode(rawValue) {
  const mode = String(rawValue ?? "").trim().toLowerCase();
  return mode === "http" || mode === "remote" ? "http" : "local";
}

function normalizeAiProvider(rawValue) {
  const provider = String(rawValue ?? "").trim().toLowerCase();
  return AI_LOCAL_PROVIDER_ALIASES[provider] ?? AI_SETTINGS_DEFAULTS.provider;
}

function normalizeAiSettings(rawSettings = {}) {
  const current = rawSettings && typeof rawSettings === "object" ? rawSettings : {};
  return {
    mode: normalizeAiMode(current.mode),
    provider: normalizeAiProvider(current.provider),
    aiBaseUrl: pickConfiguredString(current.aiBaseUrl, AI_SETTINGS_DEFAULTS.aiBaseUrl),
    ollamaUrl: pickConfiguredString(current.ollamaUrl, AI_SETTINGS_DEFAULTS.ollamaUrl),
    ollamaModel: pickConfiguredString(current.ollamaModel, AI_SETTINGS_DEFAULTS.ollamaModel),
    openaiCompatBaseUrl: pickConfiguredString(
      current.openaiCompatBaseUrl,
      AI_SETTINGS_DEFAULTS.openaiCompatBaseUrl
    ),
    openaiCompatModel: pickConfiguredString(
      current.openaiCompatModel,
      AI_SETTINGS_DEFAULTS.openaiCompatModel
    ),
  };
}

function getConfiguredAiSettings() {
  const launcherRaw = getRawAiSettingsFromEnv(readLauncherEnv());
  const bootstrapRaw = readBootstrapAiSettingsRaw();
  const envRaw = getRawAiSettingsFromEnv(process.env);
  const configured = normalizeAiSettings({
    ...launcherRaw,
    ...bootstrapRaw,
    ...envRaw,
  });

  let source = "default";
  if (hasAnyAiSetting(launcherRaw)) source = "launcher";
  if (hasAnyAiSetting(bootstrapRaw)) source = "bootstrap";
  if (hasAnyAiSetting(envRaw)) source = "env";

  return { ...configured, source };
}

function syncAiSettingsToProcessEnv(rawSettings) {
  const aiSettings = normalizeAiSettings(rawSettings);
  process.env.EDMG_AI_MODE = aiSettings.mode;
  process.env.EDMG_AI_PROVIDER = aiSettings.provider;
  process.env.EDMG_AI_BASE_URL = aiSettings.aiBaseUrl;
  process.env.EDMG_AI_OLLAMA_URL = aiSettings.ollamaUrl;
  process.env.EDMG_AI_OLLAMA_MODEL = aiSettings.ollamaModel;
  process.env.EDMG_AI_OPENAI_COMPAT_BASE_URL = aiSettings.openaiCompatBaseUrl;
  process.env.EDMG_AI_OPENAI_COMPAT_MODEL = aiSettings.openaiCompatModel;
  return aiSettings;
}

syncAiSettingsToProcessEnv(getConfiguredAiSettings());

function getConfiguredDataDir(includeLauncher = true) {
  const explicitDataDir = resolveConfiguredPath(process.env.EDMG_STUDIO_DATA_DIR);
  if (explicitDataDir) return explicitDataDir;

  const bootstrapDataDir = resolveConfiguredPath(readBootstrapStorageSettingsRaw()?.dataDir);
  if (bootstrapDataDir) return bootstrapDataDir;

  const bootstrapConfig = readBootstrapConfig();
  const bootstrapHome = resolveConfiguredPath(bootstrapConfig?.studioHome);
  if (bootstrapHome) return path.join(bootstrapHome, "data");

  if (includeLauncher) {
    const launcherStorage = readLauncherStorageSettingsRaw();
    const launcherStorageDataDir = resolveConfiguredPath(launcherStorage?.dataDir);
    if (launcherStorageDataDir) return launcherStorageDataDir;
    const launcherEnv = readLauncherEnv();
    const launcherHome = resolveConfiguredPath(launcherEnv?.EDMG_STUDIO_HOME);
    if (launcherHome) return path.join(launcherHome, "data");
    const launcherDataDir = resolveConfiguredPath(launcherEnv?.EDMG_STUDIO_DATA_DIR);
    if (launcherDataDir) return launcherDataDir;
  }

  return "";
}

function getConfiguredStudioHome() {
  const explicitHome = resolveConfiguredPath(process.env.EDMG_STUDIO_HOME);
  if (explicitHome) return explicitHome;

  const explicitDataDir = getConfiguredDataDir(false);
  if (explicitDataDir) return path.dirname(explicitDataDir);

  const bootstrapConfig = readBootstrapConfig();
  const savedHome = resolveConfiguredPath(bootstrapConfig?.studioHome);
  if (savedHome) return savedHome;

  const launcherEnv = readLauncherEnv();
  const launcherHome = resolveConfiguredPath(launcherEnv?.EDMG_STUDIO_HOME);
  if (launcherHome) return launcherHome;

  return "";
}

const INITIAL_STUDIO_HOME = getConfiguredStudioHome();

function applyStudioStoragePaths(paths) {
  if (!paths?.studioHome) return;

  ensureDirSync(paths.electronUserData);
  app.setPath("userData", paths.electronUserData);

  try {
    ensureDirSync(paths.sessionData);
    app.setPath("sessionData", paths.sessionData);
  } catch {}

  try {
    ensureDirSync(paths.logsDir);
    app.setPath("logs", paths.logsDir);
  } catch {}
}

if (INITIAL_STUDIO_HOME) {
  applyStudioStoragePaths(getStudioPaths(INITIAL_STUDIO_HOME));
}

function getStudioPaths(studioHomeOverride = "", storageOverrideValues = null) {
  const overrideHome = resolveConfiguredPath(studioHomeOverride);
  const configuredDataDir = getConfiguredDataDir();
  const configuredStudioHome = getConfiguredStudioHome();
  const bootstrapConfig = readBootstrapConfig();
  const resolvedHome =
    overrideHome ||
    configuredStudioHome ||
    path.dirname(configuredDataDir || getDefaultDataDir());
  const launcherRaw = readLauncherStorageSettingsRaw();
  const bootstrapRaw = readBootstrapStorageSettingsRaw();
  const envRaw = trimStorageOverrides(getRawStorageSettingsFromEnv(process.env), resolvedHome);
  const overrideRaw =
    storageOverrideValues && typeof storageOverrideValues === "object" ? storageOverrideValues : {};
  const mergedRaw = {
    ...launcherRaw,
    ...bootstrapRaw,
    ...envRaw,
    ...overrideRaw,
  };
  const paths = buildResolvedStudioPaths(resolvedHome, mergedRaw);

  let storageSource = "default";
  if (hasAnyStorageSetting(launcherRaw)) storageSource = "launcher";
  if (hasAnyStorageSetting(bootstrapRaw)) storageSource = "bootstrap";
  if (hasAnyStorageSetting(envRaw)) storageSource = "env";
  if (hasAnyStorageSetting(overrideRaw)) storageSource = "override";

  return {
    ...paths,
    storageOverrides: trimStorageOverrides(mergedRaw, resolvedHome),
    bootstrapConfigPath: getBootstrapConfigPath(),
    pendingMigration: bootstrapConfig?.pendingMigration ?? null,
    lastMigration: bootstrapConfig?.lastMigration ?? null,
    source: (overrideHome || configuredStudioHome || configuredDataDir || storageSource !== "default") ? "configured" : "default",
    storageSource,
  };
}

function buildManagedStudioEnv(studioHomeOverride = "", storageOverrideValues = null) {
  const paths = getStudioPaths(studioHomeOverride, storageOverrideValues);
  const managed = {
    EDMG_STUDIO_HOME: paths.studioHome,
    EDMG_STUDIO_DATA_DIR: paths.dataDir,
    EDMG_STUDIO_MODELS_DIR: paths.modelsDir,
    EDMG_STUDIO_CACHE_DIR: paths.cacheRoot,
    EDMG_STUDIO_LOGS_DIR: paths.logsDir,
    EDMG_STUDIO_EXTERNAL_DIR: paths.externalDir,
    PIP_CACHE_DIR: path.join(paths.cacheRoot, "pip"),
    XDG_CACHE_HOME: path.join(paths.cacheRoot, "xdg"),
    HF_HOME: path.join(paths.cacheRoot, "huggingface"),
    HUGGINGFACE_HUB_CACHE: path.join(paths.cacheRoot, "huggingface", "hub"),
    TRANSFORMERS_CACHE: path.join(paths.cacheRoot, "transformers"),
    TORCH_HOME: path.join(paths.cacheRoot, "torch"),
    NLTK_DATA: path.join(paths.cacheRoot, "nltk_data"),
    WHISPER_CACHE_DIR: path.join(paths.cacheRoot, "whisper"),
    MPLCONFIGDIR: path.join(paths.cacheRoot, "matplotlib"),
    TMP: path.join(paths.cacheRoot, "tmp"),
    TEMP: path.join(paths.cacheRoot, "tmp"),
  };

  for (const targetPath of Object.values(managed)) {
    if (typeof targetPath === "string" && targetPath.trim()) {
      ensureDirSync(targetPath);
    }
  }

  return managed;
}

function syncStorageSettingsToProcessEnv(studioHome = "", storageOverrides = null) {
  const paths = buildResolvedStudioPaths(
    studioHome || getConfiguredStudioHome() || path.dirname(getDefaultDataDir()),
    storageOverrides || {}
  );
  const managed = {
    EDMG_STUDIO_HOME: paths.studioHome,
    EDMG_STUDIO_DATA_DIR: paths.dataDir,
    EDMG_STUDIO_MODELS_DIR: paths.modelsDir,
    EDMG_STUDIO_CACHE_DIR: paths.cacheRoot,
    EDMG_STUDIO_LOGS_DIR: paths.logsDir,
    EDMG_STUDIO_EXTERNAL_DIR: paths.externalDir,
    PIP_CACHE_DIR: path.join(paths.cacheRoot, "pip"),
    XDG_CACHE_HOME: path.join(paths.cacheRoot, "xdg"),
    HF_HOME: path.join(paths.cacheRoot, "huggingface"),
    HUGGINGFACE_HUB_CACHE: path.join(paths.cacheRoot, "huggingface", "hub"),
    TRANSFORMERS_CACHE: path.join(paths.cacheRoot, "transformers"),
    TORCH_HOME: path.join(paths.cacheRoot, "torch"),
    NLTK_DATA: path.join(paths.cacheRoot, "nltk_data"),
    WHISPER_CACHE_DIR: path.join(paths.cacheRoot, "whisper"),
    MPLCONFIGDIR: path.join(paths.cacheRoot, "matplotlib"),
    TMP: path.join(paths.cacheRoot, "tmp"),
    TEMP: path.join(paths.cacheRoot, "tmp"),
  };
  for (const [key, value] of Object.entries(managed)) {
    ensureDirSync(value);
    process.env[key] = value;
  }
  return {
    ...paths,
    storageOverrides: trimStorageOverrides(storageOverrides || {}, paths.studioHome),
    bootstrapConfigPath: getBootstrapConfigPath(),
    pendingMigration: readBootstrapConfig()?.pendingMigration ?? null,
    lastMigration: readBootstrapConfig()?.lastMigration ?? null,
    source: "configured",
    storageSource: "override",
  };
}

function buildManagedAiEnv() {
  const aiSettings = getConfiguredAiSettings();
  return {
    EDMG_AI_MODE: aiSettings.mode,
    EDMG_AI_PROVIDER: aiSettings.provider,
    EDMG_AI_BASE_URL: aiSettings.aiBaseUrl,
    EDMG_AI_OLLAMA_URL: aiSettings.ollamaUrl,
    EDMG_AI_OLLAMA_MODEL: aiSettings.ollamaModel,
    EDMG_AI_OPENAI_COMPAT_BASE_URL: aiSettings.openaiCompatBaseUrl,
    EDMG_AI_OPENAI_COMPAT_MODEL: aiSettings.openaiCompatModel,
  };
}

function normalizePath(rawValue) {
  const value = String(rawValue ?? "").trim();
  if (!value) return "";
  return path.resolve(value);
}

function samePath(left, right) {
  const a = normalizePath(left);
  const b = normalizePath(right);
  if (!a || !b) return false;
  return IS_WINDOWS ? a.toLowerCase() === b.toLowerCase() : a === b;
}

async function pathExists(targetPath) {
  try {
    await fsp.lstat(targetPath);
    return true;
  } catch {
    return false;
  }
}

function selectStudioPathSet(paths) {
  return {
    studioHome: paths?.studioHome ?? "",
    dataDir: paths?.dataDir ?? "",
    modelsDir: paths?.modelsDir ?? "",
    cacheRoot: paths?.cacheRoot ?? "",
    electronUserData: paths?.electronUserData ?? "",
    sessionData: paths?.sessionData ?? "",
    logsDir: paths?.logsDir ?? "",
    externalDir: paths?.externalDir ?? "",
  };
}

function buildPendingMigration(sourcePaths, targetPaths) {
  const keys = ["dataDir", "modelsDir", "cacheRoot", "logsDir", "externalDir", "electronUserData"];
  const changed = keys.some((key) => !samePath(sourcePaths?.[key], targetPaths?.[key]));
  if (!changed) return null;

  return {
    requestedAt: new Date().toISOString(),
    source: selectStudioPathSet(sourcePaths),
    target: selectStudioPathSet(targetPaths),
  };
}

function summarizePendingMigration(plan) {
  if (!plan?.source || !plan?.target) return "";
  const labels = [
    ["dataDir", "project data"],
    ["modelsDir", "models"],
    ["cacheRoot", "cache"],
    ["logsDir", "logs"],
    ["externalDir", "external tools"],
    ["electronUserData", "Electron data"],
  ].filter(([key]) => !samePath(plan.source?.[key], plan.target?.[key]));
  if (!labels.length) return "";
  return `Existing ${labels.map(([, label]) => label).join(", ")} will migrate into the new storage layout on restart.`;
}

async function safeMergeCopy(src, dst) {
  const info = await fsp.lstat(src);
  if (info.isDirectory()) {
    await fsp.mkdir(dst, { recursive: true });
    let filesCopied = 0;
    let filesRenamed = 0;
    for (const entry of await fsp.readdir(src)) {
      const child = await safeMergeCopy(path.join(src, entry), path.join(dst, entry));
      filesCopied += child.filesCopied;
      filesRenamed += child.filesRenamed;
    }
    return { filesCopied, filesRenamed };
  }

  await fsp.mkdir(path.dirname(dst), { recursive: true });
  let target = dst;
  let filesRenamed = 0;

  if (await pathExists(target)) {
    const parsed = path.parse(target);
    let counter = 1;
    do {
      target = path.join(parsed.dir, `${parsed.name}_dup${counter}${parsed.ext}`);
      counter += 1;
    } while (await pathExists(target));
    filesRenamed = 1;
  }

  await fsp.copyFile(src, target);
  return { filesCopied: 1, filesRenamed };
}

async function createMovedMarker(sourcePath, targetPath) {
  await fsp.mkdir(sourcePath, { recursive: true });
  await fsp.writeFile(
    path.join(sourcePath, "MOVED_TO.txt"),
    `This folder was migrated to:\n${targetPath}\n`,
    "utf8"
  );
}

async function createJunction(sourcePath, targetPath) {
  if (!IS_WINDOWS) return false;
  try {
    await fsp.symlink(targetPath, sourcePath, "junction");
    return true;
  } catch {
    return false;
  }
}

async function migrateDirectory({ sourcePath, targetPath, label, allowJunction = true }) {
  const source = normalizePath(sourcePath);
  const target = normalizePath(targetPath);

  if (!source || !target || samePath(source, target)) {
    return { label, status: "skipped", sourcePath: source, targetPath: target, reason: "already_aligned" };
  }

  if (!(await pathExists(source))) {
    return { label, status: "skipped", sourcePath: source, targetPath: target, reason: "missing_source" };
  }

  try {
    const { filesCopied, filesRenamed } = await safeMergeCopy(source, target);
    let cleanup = "kept_source";
    let compatibilityPath = "none";

    try {
      await fsp.rm(source, { recursive: true, force: true });
      cleanup = "removed_source";
      if (allowJunction) {
        if (await createJunction(source, target)) {
          compatibilityPath = "junction";
        } else {
          await createMovedMarker(source, target);
          compatibilityPath = "marker";
        }
      }
    } catch (cleanupError) {
      cleanup = `kept_source:${String(cleanupError?.message ?? cleanupError)}`;
    }

    return {
      label,
      status: "migrated",
      sourcePath: source,
      targetPath: target,
      filesCopied,
      filesRenamed,
      cleanup,
      compatibilityPath,
    };
  } catch (error) {
    return {
      label,
      status: "failed",
      sourcePath: source,
      targetPath: target,
      error: String(error?.message ?? error),
    };
  }
}

async function runPendingStudioMigrationIfNeeded() {
  const bootstrapConfig = readBootstrapConfig();
  const plan = bootstrapConfig?.pendingMigration;
  if (!plan?.source || !plan?.target) return null;

  const bootstrapRoot = path.dirname(getBootstrapConfigPath());
  const results = [];

  results.push(await migrateDirectory({
    sourcePath: plan.source.dataDir,
    targetPath: plan.target.dataDir,
    label: "project_data",
  }));

  results.push(await migrateDirectory({
    sourcePath: plan.source.modelsDir,
    targetPath: plan.target.modelsDir,
    label: "models",
  }));

  results.push(await migrateDirectory({
    sourcePath: plan.source.cacheRoot,
    targetPath: plan.target.cacheRoot,
    label: "cache",
  }));

  results.push(await migrateDirectory({
    sourcePath: plan.source.logsDir,
    targetPath: plan.target.logsDir,
    label: "logs",
  }));

  results.push(await migrateDirectory({
    sourcePath: plan.source.externalDir,
    targetPath: plan.target.externalDir,
    label: "external_tools",
  }));

  if (!samePath(plan.source.electronUserData, bootstrapRoot)) {
    results.push(await migrateDirectory({
      sourcePath: plan.source.electronUserData,
      targetPath: plan.target.electronUserData,
      label: "electron_data",
    }));
  } else {
    results.push({
      label: "electron_data",
      status: "skipped",
      sourcePath: normalizePath(plan.source.electronUserData),
      targetPath: normalizePath(plan.target.electronUserData),
      reason: "shares_bootstrap_root",
      message: "Left the old Electron root in place because it contains the bootstrap config.",
    });
  }

  const failed = results.filter((item) => item.status === "failed");
  const summary = {
    requestedAt: plan.requestedAt,
    completedAt: new Date().toISOString(),
    source: plan.source,
    target: plan.target,
    ok: failed.length === 0,
    results,
  };

  const nextConfig = {
    ...bootstrapConfig,
    lastMigration: summary,
  };
  delete nextConfig.pendingMigration;
  writeBootstrapConfig(nextConfig);

  console.log("[studio-migration]", JSON.stringify(summary));
  return summary;
}

function getDefaultDataDir() {
  const configuredDataDir = getConfiguredDataDir();
  if (configuredDataDir) return configuredDataDir;

  const configuredStudioHome = getConfiguredStudioHome();
  if (configuredStudioHome) {
    return getDefaultStoragePaths(configuredStudioHome).dataDir;
  }

  return path.join(app.getPath("userData"), "data");
}

function getPreloadPath() {
  return path.join(__dirname, "preload.cjs");
}

function getProdIndexPath() {
  return path.join(app.getAppPath(), "dist", "index.html");
}

function getDevPythonPath() {
  const explicit = process.env.EDMG_STUDIO_PYTHON;
  if (explicit && explicit.trim()) return explicit.trim();

  if (IS_WINDOWS) {
    return path.join(__dirname, "python_backend", "venv", "Scripts", "python.exe");
  }

  return path.join(__dirname, "python_backend", "venv", "bin", "python");
}

function getPackagedBackendPath() {
  const exeName = IS_WINDOWS ? "edmg-studio-backend.exe" : "edmg-studio-backend";
  return path.join(process.resourcesPath, "backend", exeName);
}

function resolveManagedFfmpegPath() {
  const explicit = String(process.env.EDMG_FFMPEG_PATH ?? "").trim();
  if (explicit) {
    if (!path.isAbsolute(explicit) || pathExistsSync(explicit)) {
      return explicit;
    }
    console.warn("[ffmpeg] explicit EDMG_FFMPEG_PATH missing, falling back:", explicit);
  }

  const exeName = IS_WINDOWS ? "ffmpeg.exe" : "ffmpeg";
  const candidates = app.isPackaged
    ? [
        path.join(process.resourcesPath, "bin", exeName),
        path.join(process.resourcesPath, "electron-resources", "bin", exeName),
      ]
    : [
        path.join(__dirname, "electron-resources", "bin", exeName),
      ];

  for (const candidate of candidates) {
    if (pathExistsSync(candidate)) {
      return candidate;
    }
  }

  return explicit || "ffmpeg";
}

function getBackendLaunchSpec() {
  if (app.isPackaged) {
    const command = getPackagedBackendPath();
    return {
      command,
      args: ["serve", "--host", BACKEND_HOST, "--port", String(BACKEND_PORT)],
      cwd: path.dirname(command),
      label: "packaged-backend",
    };
  }

  return {
    command: getDevPythonPath(),
    args: ["-m", "edmg_studio_backend", "serve", "--host", BACKEND_HOST, "--port", String(BACKEND_PORT)],
    cwd: path.join(__dirname, "python_backend"),
    label: "python-backend",
  };
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isExistingDirectory(targetPath) {
  try {
    return fs.existsSync(targetPath) && fs.statSync(targetPath).isDirectory();
  } catch {
    return false;
  }
}

async function probeBackend(url = currentBackendUrl) {
  return new Promise((resolve) => {
    const req = http.get(`${url}/health`, (res) => {
      res.resume();
      resolve(res.statusCode != null && res.statusCode >= 200 && res.statusCode < 500);
    });

    req.on("error", () => resolve(false));
    req.setTimeout(1500, () => {
      req.destroy();
      resolve(false);
    });
  });
}

async function waitForBackendReady(timeoutMs = 15000) {
  const started = Date.now();

  while (Date.now() - started < timeoutMs) {
    if (await probeBackend()) {
      return true;
    }
    await delay(300);
  }

  return false;
}

async function startBackendIfNeeded() {
  if (!SPAWN_BACKEND) {
    console.log("[edmg] spawn backend=false");
    return false;
  }

  if (await probeBackend()) {
    console.log("[backend] already reachable:", currentBackendUrl);
    return true;
  }

  const spec = getBackendLaunchSpec();
  const managedStudioEnv = buildManagedStudioEnv();
  const backendDataDir = managedStudioEnv.EDMG_STUDIO_DATA_DIR;
  const ffmpegPath = resolveManagedFfmpegPath();

  if (app.isPackaged && !fs.existsSync(spec.command)) {
    backendSpawnFailed = true;
    console.error("[backend] packaged backend missing:", spec.command);

    if (!TEST_MODE) {
      dialog.showErrorBox(
        "EDMG Studio backend missing",
        `Could not find packaged backend:\n${spec.command}`
      );
    }

    return false;
  }

  console.log("[edmg] spawn backend=true");
  console.log("[backend] launching", {
    label: spec.label,
    command: spec.command,
    args: spec.args,
    cwd: spec.cwd,
  });
  console.log("[backend] EDMG_STUDIO_DATA_DIR=", backendDataDir);
  console.log("[backend] EDMG_STUDIO_MODELS_DIR=", managedStudioEnv.EDMG_STUDIO_MODELS_DIR);
  console.log("[backend] EDMG_STUDIO_EXTERNAL_DIR=", managedStudioEnv.EDMG_STUDIO_EXTERNAL_DIR);
  console.log("[backend] EDMG_FFMPEG_PATH=", ffmpegPath);

  try {
    backendProc = spawn(spec.command, spec.args, {
      cwd: spec.cwd,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
      env: {
        ...process.env,
        ...managedStudioEnv,
        ...buildManagedAiEnv(),
        EDMG_STUDIO_BACKEND_HOST: BACKEND_HOST,
        EDMG_STUDIO_BACKEND_PORT: String(BACKEND_PORT),
        EDMG_FFMPEG_PATH: ffmpegPath,
      },
    });
  } catch (error) {
    backendSpawnFailed = true;
    console.error("[backend] spawn threw:", error);

    if (!TEST_MODE) {
      dialog.showErrorBox(
        "EDMG Studio backend failed to start",
        String(error?.message ?? error)
      );
    }

    return false;
  }

  backendProc.stdout?.on("data", (chunk) => {
    process.stdout.write(`[backend] ${chunk}`);
  });

  backendProc.stderr?.on("data", (chunk) => {
    process.stderr.write(`[backend] ${chunk}`);
  });

  backendProc.on("error", (error) => {
    backendSpawnFailed = true;
    console.error("[backend] child process error:", error);

    if (!TEST_MODE) {
      dialog.showErrorBox(
        "EDMG Studio backend failed to start",
        `${error?.message ?? error}`
      );
    }
  });

  backendProc.on("exit", (code, signal) => {
    console.log("[backend] exited", { code, signal });
    backendProc = null;
  });

  const ready = await waitForBackendReady();
  if (!ready) {
    console.warn("[backend] not reachable:", currentBackendUrl);
  }

  return ready;
}

function stopBackend() {
  if (!backendProc) return;

  try {
    backendProc.kill();
  } catch (error) {
    console.warn("[backend] failed to stop cleanly:", error);
  }

  backendProc = null;
}

async function loadRenderer(win) {
  if (TEST_MODE && TEST_PAGE) {
    await win.loadFile(TEST_PAGE);
    return;
  }

  if (IS_DEV) {
    await win.loadURL(DEV_SERVER_URL);
    return;
  }

  await win.loadFile(getProdIndexPath());
}

function attachWindowDiagnostics(win) {
  win.webContents.on("did-fail-load", (_event, code, desc, url) => {
    console.error("[renderer] did-fail-load", { code, desc, url });
  });

  win.webContents.on("render-process-gone", (_event, details) => {
    console.error("[renderer] render-process-gone", details);
  });

  win.webContents.on("console-message", (_event, level, message, line, sourceId) => {
    console.log("[renderer console]", { level, message, line, sourceId });
  });
}

async function createMainWindow() {
  const win = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1100,
    minHeight: 720,
    title: APP_NAME,
    backgroundColor: "#05070b",
    show: false,
    autoHideMenuBar: false,
    webPreferences: {
      preload: getPreloadPath(),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      devTools: true,
      additionalArguments: [
        `--edmg-backend-host=${BACKEND_HOST}`,
        `--edmg-backend-port=${String(BACKEND_PORT)}`,
      ],
    },
  });

  attachWindowDiagnostics(win);

  win.once("ready-to-show", () => {
    win.show();
  });

  win.on("closed", () => {
    if (mainWindow === win) {
      mainWindow = null;
    }
  });

  await loadRenderer(win);

  mainWindow = win;
  return win;
}

function registerIpcHandlers() {
  ipcMain.handle("edmg:getBackendUrl", async () => currentBackendUrl);
  ipcMain.handle("edmg:getStudioPaths", async () => ({ ok: true, ...getStudioPaths() }));
  ipcMain.handle("edmg:getAiSettings", async () => ({ ok: true, ...getConfiguredAiSettings() }));

  const saveStorageSettings = async (nextSettings = {}) => {
    const requested = nextSettings && typeof nextSettings === "object" ? nextSettings : {};
    const studioHome = resolveConfiguredPath(requested.studioHome || requested.home || requested.path);
    if (!studioHome) {
      return { ok: false, error: "Pick a valid folder first." };
    }

    const currentPaths = selectStudioPathSet(getStudioPaths());
    const requestedOverrides = {
      dataDir: requested.dataDir,
      modelsDir: requested.modelsDir,
      cacheRoot: requested.cacheRoot,
      logsDir: requested.logsDir,
      externalDir: requested.externalDir,
    };
    const trimmedOverrides = trimStorageOverrides(requestedOverrides, studioHome);
    const targetPaths = syncStorageSettingsToProcessEnv(studioHome, trimmedOverrides);
    const pendingMigration = buildPendingMigration(currentPaths, targetPaths);

    const nextConfig = {
      ...readBootstrapConfig(),
      studioHome,
      storageSettings: trimmedOverrides,
      updatedAt: new Date().toISOString(),
    };
    if (pendingMigration) {
      nextConfig.pendingMigration = pendingMigration;
    } else {
      delete nextConfig.pendingMigration;
    }
    writeBootstrapConfig(nextConfig);
    writeLauncherEnv({
      ...readLauncherEnv(),
      EDMG_STUDIO_HOME: studioHome,
      EDMG_STUDIO_DATA_DIR: targetPaths.dataDir,
      EDMG_STUDIO_MODELS_DIR: targetPaths.modelsDir,
      EDMG_STUDIO_CACHE_DIR: targetPaths.cacheRoot,
      EDMG_STUDIO_LOGS_DIR: targetPaths.logsDir,
      EDMG_STUDIO_EXTERNAL_DIR: targetPaths.externalDir,
    });

    return {
      ok: true,
      restartRequired: true,
      migrationPlanned: !!pendingMigration,
      migrationSummary: summarizePendingMigration(pendingMigration),
      ...targetPaths,
    };
  };

  ipcMain.handle("edmg:setStorageSettings", async (_event, nextSettings = {}) =>
    saveStorageSettings(nextSettings)
  );

  ipcMain.handle("edmg:setStudioHome", async (_event, targetPath) =>
    saveStorageSettings({ studioHome: targetPath })
  );

  ipcMain.handle("edmg:setAiSettings", async (_event, nextSettings = {}) => {
    const aiSettings = syncAiSettingsToProcessEnv(nextSettings);
    const nextConfig = {
      ...readBootstrapConfig(),
      aiSettings,
      updatedAt: new Date().toISOString(),
    };
    writeBootstrapConfig(nextConfig);
    writeLauncherEnv({
      ...readLauncherEnv(),
      EDMG_AI_MODE: aiSettings.mode,
      EDMG_AI_PROVIDER: aiSettings.provider,
      EDMG_AI_BASE_URL: aiSettings.aiBaseUrl,
      EDMG_AI_OLLAMA_URL: aiSettings.ollamaUrl,
      EDMG_AI_OLLAMA_MODEL: aiSettings.ollamaModel,
      EDMG_AI_OPENAI_COMPAT_BASE_URL: aiSettings.openaiCompatBaseUrl,
      EDMG_AI_OPENAI_COMPAT_MODEL: aiSettings.openaiCompatModel,
    });

    return {
      ok: true,
      restartRequired: true,
      ...aiSettings,
    };
  });

  ipcMain.handle("edmg:openPath", async (_event, targetPath) => {
    const resolved = path.resolve(String(targetPath ?? ""));

    if (FAKE_PATH_ACTIONS) {
      return {
        ok: true,
        action: isExistingDirectory(resolved) ? "open_directory" : "open_file",
        path: resolved,
        fake: true,
      };
    }

    const error = await shell.openPath(resolved);
    if (error) {
      return { ok: false, error };
    }

    return {
      ok: true,
      action: isExistingDirectory(resolved) ? "open_directory" : "open_file",
      path: resolved,
      fake: false,
    };
  });

  ipcMain.handle("edmg:revealPath", async (_event, targetPath) => {
    const resolved = path.resolve(String(targetPath ?? ""));

    if (FAKE_PATH_ACTIONS) {
      return {
        ok: true,
        action: "reveal_file",
        path: resolved,
        fake: true,
      };
    }

    shell.showItemInFolder(resolved);
    return {
      ok: true,
      action: "reveal_file",
      path: resolved,
      fake: false,
    };
  });

  ipcMain.handle("edmg:pickFile", async (_event, options = {}) => {
    const result = await dialog.showOpenDialog(mainWindow ?? undefined, {
      title: options?.title ?? "Select file",
      defaultPath: options?.defaultPath,
      filters: Array.isArray(options?.filters) ? options.filters : undefined,
      properties: Array.isArray(options?.properties) && options.properties.length
        ? options.properties
        : ["openFile"],
    });

    if (result.canceled) {
      return { ok: false, canceled: true, paths: [] };
    }

    return { ok: true, canceled: false, paths: result.filePaths };
  });

  ipcMain.handle("edmg:pickDirectory", async (_event, options = {}) => {
    const result = await dialog.showOpenDialog(mainWindow ?? undefined, {
      title: options?.title ?? "Select folder",
      defaultPath: options?.defaultPath,
      properties: ["openDirectory", "createDirectory"],
    });

    if (result.canceled || !result.filePaths.length) {
      return { ok: false, canceled: true, path: "" };
    }

    return { ok: true, canceled: false, path: result.filePaths[0] };
  });

  ipcMain.handle("edmg:relaunch", async () => {
    app.relaunch();
    app.exit(0);
    return { ok: true };
  });

  ipcMain.handle("edmg:testWriteReport", async (_event, payload) => {
    if (!TEST_MODE || !TEST_REPORT_PATH) {
      return { ok: false, skipped: true };
    }

    await fsp.mkdir(path.dirname(TEST_REPORT_PATH), { recursive: true });
    await fsp.writeFile(TEST_REPORT_PATH, JSON.stringify(payload, null, 2), "utf8");

    return {
      ok: true,
      path: TEST_REPORT_PATH,
    };
  });
}

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("before-quit", () => {
  stopBackend();
});

app.on("activate", async () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    await createMainWindow();
  }
});

app.whenReady().then(async () => {
  await runPendingStudioMigrationIfNeeded();
  registerIpcHandlers();
  await startBackendIfNeeded();
  await createMainWindow();
}).catch((error) => {
  console.error("[main] fatal startup error:", error);
  dialog.showErrorBox("EDMG Studio failed to start", String(error?.message ?? error));
  app.quit();
});
