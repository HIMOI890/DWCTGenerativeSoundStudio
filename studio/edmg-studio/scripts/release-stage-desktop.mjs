import { promises as fs } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT = path.resolve(__dirname, "..");
const STAGE_DIR = path.join(ROOT, "release", "staged-app");
const STAGE_META_DIR = path.join(STAGE_DIR, ".edmg-stage");
const MANIFEST_PATH = path.join(STAGE_META_DIR, "manifest.json");

const COPY_ITEMS = [
  "dist",
  "main.mjs",
  "preload.cjs",
  "package.json",
  "electron-resources",
  "electron-builder.yml",
];

async function rmrf(target) {
  await fs.rm(target, { recursive: true, force: true });
}

async function ensureDir(target) {
  await fs.mkdir(target, { recursive: true });
}

async function copyEntry(relPath) {
  const src = path.join(ROOT, relPath);
  const dest = path.join(STAGE_DIR, relPath);
  const stat = await fs.stat(src);
  if (stat.isDirectory()) {
    await fs.cp(src, dest, { recursive: true, force: true });
  } else {
    await ensureDir(path.dirname(dest));
    await fs.copyFile(src, dest);
  }
}

function sanitizePackageJson(pkg) {
  const {
    build,
    scripts,
    devDependencies,
    private: _private,
    packageManager,
    ...rest
  } = pkg;

  return {
    name: rest.name,
    version: rest.version,
    description: rest.description || "EDMG Studio desktop app",
    author: rest.author || "Tyler",
    type: rest.type,
    main: rest.main || "main.mjs",
    dependencies: rest.dependencies || {},
  };
}

async function main() {
  await rmrf(STAGE_DIR);
  await ensureDir(STAGE_DIR);

  for (const relPath of COPY_ITEMS) {
    await copyEntry(relPath);
  }

  const rootPkgPath = path.join(ROOT, "package.json");
  const stagePkgPath = path.join(STAGE_DIR, "package.json");
  const rootPkg = JSON.parse(await fs.readFile(rootPkgPath, "utf8"));
  const appPkg = sanitizePackageJson(rootPkg);
  await fs.writeFile(stagePkgPath, JSON.stringify(appPkg, null, 2) + "\n", "utf8");

  await ensureDir(STAGE_META_DIR);
  const manifest = {
    ok: true,
    stageDir: STAGE_DIR,
    createdAt: new Date().toISOString(),
    main: "main.mjs",
    copied: [
      "dist/**",
      "main.mjs",
      "preload.cjs",
      "package.json",
      "electron-resources/**",
      "electron-builder.yml",
    ],
    extraResources: ["electron-resources"],
    sanitizedPackageJson: true,
  };
  await fs.writeFile(MANIFEST_PATH, JSON.stringify(manifest, null, 2) + "\n", "utf8");
  process.stdout.write(JSON.stringify(manifest, null, 2) + "\n");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
