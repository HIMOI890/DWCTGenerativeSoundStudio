const { app, BrowserWindow, dialog, ipcMain, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');

let apiProc = null;

function repoRoot() {
  // desktop/electron/electron/main.js -> repo root
  return path.resolve(__dirname, '..', '..', '..');
}

function pythonExe() {
  // Allow overriding to a venv python
  return process.env.EDMG_PYTHON || 'python';
}

function startApi() {
  if (apiProc) return;
  const root = repoRoot();
  const env = { ...process.env };
  env.PYTHONPATH = env.PYTHONPATH ? `${root}:${env.PYTHONPATH}` : root;
  env.EDMG_API_HOST = env.EDMG_API_HOST || '127.0.0.1';
  env.EDMG_API_PORT = env.EDMG_API_PORT || '7862';

  apiProc = spawn(pythonExe(), ['-m', 'scripts.run_api'], {
    cwd: root,
    env,
    stdio: 'pipe'
  });

  apiProc.stdout.on('data', (d) => {
    process.stdout.write(`[API] ${d}`);
  });
  apiProc.stderr.on('data', (d) => {
    process.stderr.write(`[API] ${d}`);
  });
  apiProc.on('exit', (code) => {
    apiProc = null;
    console.log(`API exited with code ${code}`);
  });
}

function stopApi() {
  if (!apiProc) return;
  try {
    apiProc.kill();
  } catch (_) {}
  apiProc = null;
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1200,
    height: 800,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  win.loadFile(path.join(__dirname, '..', 'renderer', 'index.html'));
}

ipcMain.handle('edmg-save-json', async (_event, defaultName, content) => {
  const win = BrowserWindow.getFocusedWindow();
  const { canceled, filePath } = await dialog.showSaveDialog(win, {
    defaultPath: defaultName || 'deforum_settings.json',
    filters: [{ name: 'JSON', extensions: ['json'] }]
  });
  if (canceled || !filePath) return { ok: false, canceled: true };
  fs.writeFileSync(filePath, content, 'utf8');
  return { ok: true, path: filePath };
});

ipcMain.handle('edmg-open-json', async () => {
  const win = BrowserWindow.getFocusedWindow();
  const { canceled, filePaths } = await dialog.showOpenDialog(win, {
    properties: ['openFile'],
    filters: [{ name: 'JSON', extensions: ['json'] }]
  });
  if (canceled || !filePaths || !filePaths[0]) return { ok: false, canceled: true };
  const content = fs.readFileSync(filePaths[0], 'utf8');
  return { ok: true, path: filePaths[0], content };
});

ipcMain.handle('edmg-open-path', async (_event, relativePath, reveal) => {
  const root = repoRoot();
  const targetPath = path.resolve(root, relativePath || '.');
  if (!fs.existsSync(targetPath)) {
    return { ok: false, error: 'Path not found', path: targetPath };
  }
  if (reveal) {
    shell.showItemInFolder(targetPath);
  } else {
    await shell.openPath(targetPath);
  }
  return { ok: true, path: targetPath };
});

ipcMain.handle('edmg-restart-api', async () => {
  stopApi();
  startApi();
  return { ok: true };
});

app.whenReady().then(() => {
  startApi();
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  stopApi();
  if (process.platform !== 'darwin') app.quit();
});
