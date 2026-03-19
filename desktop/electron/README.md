# EDMG Studio (Legacy Electron UI)

This shell is retained for compatibility, but the canonical desktop product is now:

- `studio/edmg-studio`

Use the root launcher (`RUN_ME.bat` / `./run_me.sh`) or see [`README_STUDIO.md`](../../README_STUDIO.md) for the current product entrypoint.

This is a **JSON-first** Electron GUI for the Enhanced Deforum Music Generator.
It starts the FastAPI backend automatically (CPU-first) and gives you a full
Deforum settings JSON editor with:

- Audio analysis upload (tempo / beats / energy)
- Optional Whisper-based lyrics transcription (if installed)
- Sync calibration (estimates a small audio->video offset)
- Bundled Hugging Face video model catalog + downloader
- One-click generation of a complete Deforum settings JSON
- Quick utilities to open common folders (outputs, models_store, repo root)
- Restart button for the backend API

## Run

From the repo root:

```bash
cd desktop/electron
npm install
npm run start
```

### Python selection

By default the app runs `python -m scripts.run_api` from the repo root.
If you want it to use a specific venv, set:

```bash
set EDMG_PYTHON=C:\path\to\venv\Scripts\python.exe
```

### Backend port

```bash
set EDMG_API_PORT=7862
```

## Notes

- The UI loads JSONEditor from `node_modules`, so it works offline.
- The backend defaults to 720p @ 30fps in the template.
