import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const cacheRoot = process.env.EDMG_STUDIO_BUILD_CACHE_ROOT || path.join(root, ".cache");
const electronCache = path.join(cacheRoot, "electron");
const electronBuilderCache = path.join(cacheRoot, "electron-builder");

fs.mkdirSync(electronCache, { recursive: true });
fs.mkdirSync(electronBuilderCache, { recursive: true });

const builderBin = path.join(
  root,
  "node_modules",
  ".bin",
  process.platform === "win32" ? "electron-builder.cmd" : "electron-builder",
);

if (!fs.existsSync(builderBin)) {
  throw new Error(`electron-builder binary not found: ${builderBin}`);
}

const childEnv = {
  ...process.env,
  ELECTRON_CACHE: electronCache,
  ELECTRON_BUILDER_CACHE: electronBuilderCache,
};

const result =
  process.platform === "win32"
    ? spawnSync("cmd.exe", ["/d", "/s", "/c", builderBin, ...process.argv.slice(2)], {
        cwd: root,
        stdio: "inherit",
        shell: false,
        env: childEnv,
      })
    : spawnSync(builderBin, process.argv.slice(2), {
        cwd: root,
        stdio: "inherit",
        shell: false,
        env: childEnv,
      });

if (result.error) {
  throw result.error;
}

if (result.status !== 0) {
  process.exit(result.status ?? 1);
}
