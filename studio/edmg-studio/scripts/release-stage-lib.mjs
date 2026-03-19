import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
export const root = path.resolve(__dirname, '..');

function normalizeToPosix(p) {
  return p.split(path.sep).join('/');
}

export function defaultStageDir() {
  return path.join(root, 'release', 'staged-app');
}

export function loadPackageJson() {
  const pkgPath = path.join(root, 'package.json');
  return JSON.parse(fs.readFileSync(pkgPath, 'utf8'));
}

export function assertDesktopArtifacts() {
  const distIndex = path.join(root, 'dist', 'index.html');
  const distAssets = path.join(root, 'dist', 'assets');
  const mainPath = path.join(root, 'main.mjs');
  const preloadPath = path.join(root, 'preload.cjs');
  const pkgPath = path.join(root, 'package.json');
  if (!fs.existsSync(distIndex)) throw new Error('dist/index.html must exist. Run npm run build first.');
  if (!fs.existsSync(distAssets)) throw new Error('dist/assets must exist. Run npm run build first.');
  if (!fs.existsSync(mainPath)) throw new Error('main.mjs must exist');
  if (!fs.existsSync(preloadPath)) throw new Error('preload.cjs must exist');
  if (!fs.existsSync(pkgPath)) throw new Error('package.json must exist');
  const assets = fs.readdirSync(distAssets);
  if (!assets.length) throw new Error('dist/assets must contain built assets');
  const pkg = loadPackageJson();
  if (pkg.main !== 'main.mjs') throw new Error('package.json main must point to main.mjs');
}

async function rmrf(target) {
  await fsp.rm(target, { recursive: true, force: true });
}

async function copyDir(src, dst) {
  await fsp.mkdir(dst, { recursive: true });
  for (const entry of await fsp.readdir(src, { withFileTypes: true })) {
    const from = path.join(src, entry.name);
    const to = path.join(dst, entry.name);
    if (entry.isDirectory()) {
      await copyDir(from, to);
    } else if (entry.isSymbolicLink()) {
      const target = await fsp.readlink(from);
      await fsp.symlink(target, to);
    } else {
      await fsp.copyFile(from, to);
    }
  }
}

async function copyPattern(relativePattern, stageDir, copied) {
  const normalized = normalizeToPosix(relativePattern);
  if (normalized.endsWith('/**')) {
    const base = normalized.slice(0, -3);
    const src = path.join(root, base);
    const dst = path.join(stageDir, base);
    if (!fs.existsSync(src)) throw new Error(`Missing required directory for staging: ${src}`);
    await copyDir(src, dst);
    copied.push(base + '/**');
    return;
  }
  const src = path.join(root, normalized);
  const dst = path.join(stageDir, normalized);
  if (!fs.existsSync(src)) throw new Error(`Missing required file for staging: ${src}`);
  await fsp.mkdir(path.dirname(dst), { recursive: true });
  await fsp.copyFile(src, dst);
  copied.push(normalized);
}

export async function stageDesktopRelease({ outDir = defaultStageDir(), clean = true } = {}) {
  assertDesktopArtifacts();
  const pkg = loadPackageJson();
  const copied = [];
  if (clean) await rmrf(outDir);
  await fsp.mkdir(outDir, { recursive: true });

  const buildFiles = Array.isArray(pkg.build?.files) ? pkg.build.files : [];
  for (const pattern of buildFiles) {
    await copyPattern(pattern, outDir, copied);
  }

  const extraResources = Array.isArray(pkg.build?.extraResources) ? pkg.build.extraResources : [];
  for (const entry of extraResources) {
    if (!entry || typeof entry !== 'object' || !entry.from) continue;
    const src = path.join(root, entry.from);
    const dst = path.join(outDir, entry.from);
    if (!fs.existsSync(src)) continue;
    await copyDir(src, dst);
    copied.push(normalizeToPosix(entry.from) + '/**');
  }

  const builderConfig = path.join(root, 'electron-builder.yml');
  if (fs.existsSync(builderConfig)) {
    await fsp.copyFile(builderConfig, path.join(outDir, 'electron-builder.yml'));
    copied.push('electron-builder.yml');
  }

  const manifest = {
    ok: true,
    stageDir: outDir,
    createdAt: new Date().toISOString(),
    main: pkg.main,
    copied,
    extraResources: extraResources.map((entry) => entry?.from).filter(Boolean),
  };
  await fsp.mkdir(path.join(outDir, '.edmg-stage'), { recursive: true });
  await fsp.writeFile(path.join(outDir, '.edmg-stage', 'manifest.json'), JSON.stringify(manifest, null, 2));
  return manifest;
}
