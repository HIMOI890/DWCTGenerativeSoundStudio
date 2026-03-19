import fs from "node:fs";
import path from "node:path";

const root = process.cwd();
const rootMain = path.join(root, "main.mjs");
const rootPreload = path.join(root, "preload.cjs");
const electronDir = path.join(root, "electron");
const electronMain = path.join(electronDir, "main.mjs");
const electronPreload = path.join(electronDir, "preload.cjs");

if (!fs.existsSync(rootMain)) throw new Error(`Missing: ${rootMain}`);
if (!fs.existsSync(rootPreload)) throw new Error(`Missing: ${rootPreload}`);

fs.mkdirSync(electronDir, { recursive: true });
fs.copyFileSync(rootMain, electronMain);
fs.copyFileSync(rootPreload, electronPreload);

console.log("Validated root Electron entry files and synced mirror copies under electron/.");
