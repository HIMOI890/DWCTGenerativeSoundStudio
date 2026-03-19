/* global JSONEditor, EDMG */

const logEl = document.getElementById('log');
const statusEl = document.getElementById('status');
const audioFileEl = document.getElementById('audioFile');
const enableLyricsEl = document.getElementById('enableLyrics');
const basePromptEl = document.getElementById('basePrompt');
const stylePromptEl = document.getElementById('stylePrompt');
const modelSelectEl = document.getElementById('modelSelect');
const hfTokenEl = document.getElementById('hfToken');

const apiBase = (typeof EDMG !== 'undefined' && EDMG.apiBase) ? EDMG.apiBase() : 'http://127.0.0.1:7861';
const supportsFileOps = typeof EDMG !== 'undefined' && EDMG.saveJson && EDMG.openJson;
const supportsPathOps = typeof EDMG !== 'undefined' && EDMG.openPath && EDMG.restartApi;

function log(...args) {
  const msg = args.map(a => (typeof a === 'string' ? a : JSON.stringify(a, null, 2))).join(' ');
  logEl.textContent += `${msg}\n`;
  logEl.scrollTop = logEl.scrollHeight;
}

async function apiGet(path) {
  const res = await fetch(`${apiBase}${path}`);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

async function apiPostJson(path, body) {
  const res = await fetch(`${apiBase}${path}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body || {})
  });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

async function apiPostForm(path, form) {
  const res = await fetch(`${apiBase}${path}`, {
    method: 'POST',
    body: form
  });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

// JSON editor
const editor = new JSONEditor(document.getElementById('editor'), {
  mode: 'tree',
  mainMenuBar: true,
  navigationBar: true,
  statusBar: true,
  search: true
});

let latestAnalysis = null;

async function refreshStatus() {
  try {
    const h = await apiGet('/health');
    statusEl.textContent = `API: ok (${h.status || 'healthy'})`;
  } catch (e) {
    statusEl.textContent = 'API: not reachable';
  }
}

async function loadTemplate() {
  const t = await apiGet('/deforum/template');
  editor.set(t);
  log('Loaded Deforum template.');
}

async function refreshModels() {
  const data = await apiGet('/models/catalog');
  modelSelectEl.innerHTML = '';
  for (const m of data.models) {
    const opt = document.createElement('option');
    opt.value = m.name;
    opt.textContent = `${m.display_name} ${m.installed ? '(installed)' : ''}`;
    modelSelectEl.appendChild(opt);
  }
  log(`Loaded model catalog (${data.models.length}). Root: ${data.models_root}`);
}

async function analyzeAudio() {
  const file = audioFileEl.files && audioFileEl.files[0];
  if (!file) {
    log('Pick an audio file first.');
    return;
  }
  const form = new FormData();
  form.append('file', file);
  const url = `/analysis/analyze-audio?enable_lyrics=${enableLyricsEl.checked ? 'true' : 'false'}`;
  log('Analyzing…');
  const res = await apiPostForm(url, form);
  latestAnalysis = res;
  log({ analysis: { tempo_bpm: res.tempo_bpm, duration: res.duration, beats: (res.beats||[]).length, energy: (res.energy||[]).length }, lyrics: res.lyrics_inferred });
  return res;
}

async function calibrateSync() {
  if (!latestAnalysis || !(latestAnalysis.beats || []).length) {
    log('Run Analyze first (needs beats).');
    return;
  }
  const fps = (editor.get().fps || 30);
  const res = await apiPostJson('/analysis/calibrate-sync', { beats: latestAnalysis.beats, fps });
  log(`Sync offset: ${res.offset_seconds.toFixed(4)}s (score ${res.score.toFixed(2)})`);
  // Put it into the JSON as a hint; Deforum itself doesn't have a standard key.
  const cur = editor.get();
  cur._edmg_sync_offset_seconds = res.offset_seconds;
  editor.set(cur);
}

async function generateDeforum() {
  const base = basePromptEl.value || '';
  const style = stylePromptEl.value || '';

  const userSettings = editor.get();
  userSettings.base_prompt = base;
  userSettings.style_prompt = style;

  const payload = latestAnalysis
    ? { analysis: latestAnalysis, settings: userSettings }
    : { settings: userSettings };

  const out = await apiPostJson('/deforum/generate-deforum', payload);
  editor.set(out);
  log('Generated Deforum settings.');
}

async function downloadModel() {
  const name = modelSelectEl.value;
  if (!name) return;
  const token = hfTokenEl.value || null;
  log(`Downloading ${name}…`);
  const res = await apiPostJson('/models/download', { model_name: name, token });
  log(res);
  await refreshModels();
}

async function saveJsonToFile() {
  if (!supportsFileOps) {
    log('Export not available in this environment.');
    return;
  }
  const content = JSON.stringify(editor.get(), null, 2);
  const res = await EDMG.saveJson('deforum_settings.json', content);
  if (res.ok) {
    log(`Exported JSON to ${res.path}`);
  } else if (!res.canceled) {
    log('Export failed.');
  }
}

async function loadJsonFromFile() {
  if (!supportsFileOps) {
    log('Import not available in this environment.');
    return;
  }
  const res = await EDMG.openJson();
  if (!res.ok) return;
  try {
    const parsed = JSON.parse(res.content);
    editor.set(parsed);
    log(`Imported JSON from ${res.path}`);
  } catch (e) {
    log('Failed to parse JSON file.', e.message);
  }
}

async function copyJsonToClipboard() {
  const content = JSON.stringify(editor.get(), null, 2);
  try {
    await navigator.clipboard.writeText(content);
    log('Copied JSON to clipboard.');
  } catch (e) {
    log('Clipboard copy failed.', e.message);
  }
}

function clearLogs() {
  logEl.textContent = '';
}

async function openPath(relativePath, label) {
  if (!supportsPathOps) {
    log('Path helpers not available in this environment.');
    return;
  }
  const res = await EDMG.openPath(relativePath, false);
  if (!res.ok) {
    log(`Failed to open ${label}.`, res.error || '');
    return;
  }
  log(`Opened ${label}: ${res.path}`);
}

async function restartApi() {
  if (!supportsPathOps) {
    log('API restart not available in this environment.');
    return;
  }
  await EDMG.restartApi();
  log('Requested API restart.');
}

// Wire events
document.getElementById('btnLoadTemplate').addEventListener('click', () => loadTemplate().catch(e => log('ERR', e.message)));
document.getElementById('btnRefreshModels').addEventListener('click', () => refreshModels().catch(e => log('ERR', e.message)));
document.getElementById('btnAnalyze').addEventListener('click', () => analyzeAudio().catch(e => log('ERR', e.message)));
document.getElementById('btnCalibrate').addEventListener('click', () => calibrateSync().catch(e => log('ERR', e.message)));
document.getElementById('btnGenerate').addEventListener('click', () => generateDeforum().catch(e => log('ERR', e.message)));
document.getElementById('btnDownloadModel').addEventListener('click', () => downloadModel().catch(e => log('ERR', e.message)));
document.getElementById('btnSaveJson').addEventListener('click', () => saveJsonToFile().catch(e => log('ERR', e.message)));
document.getElementById('btnLoadJson').addEventListener('click', () => loadJsonFromFile().catch(e => log('ERR', e.message)));
document.getElementById('btnCopyJson').addEventListener('click', () => copyJsonToClipboard().catch(e => log('ERR', e.message)));
document.getElementById('btnClearLog').addEventListener('click', () => clearLogs());
document.getElementById('btnOpenOutputs').addEventListener('click', () => openPath('outputs', 'outputs folder').catch(e => log('ERR', e.message)));
document.getElementById('btnOpenModels').addEventListener('click', () => openPath('models_store', 'models_store folder').catch(e => log('ERR', e.message)));
document.getElementById('btnOpenRepo').addEventListener('click', () => openPath('.', 'repo root').catch(e => log('ERR', e.message)));
document.getElementById('btnRestartApi').addEventListener('click', () => restartApi().catch(e => log('ERR', e.message)));

// Initial load
refreshStatus().then(() => loadTemplate()).then(() => refreshModels()).catch(() => {});
setInterval(refreshStatus, 2000);
