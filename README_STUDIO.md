# EDMG Studio (included)

This repo includes the **primary desktop product** under:

- `studio/edmg-studio/`

It is an Electron + React UI with a local FastAPI backend, intended to be the
"DAW-like" Studio experience (projects → audio ingest → AI plan → render queue → outputs).

The original DWCTEDMG codebase remains the engine + integrations, but Studio is the
canonical product surface and can install the EDMG Core engine into the same workflow.

## Quick start (recommended)

From the repo root:

- `RUN_ME.bat`
- `./run_me.sh`

That launcher keeps the Studio product aligned with the same `Studio Home`, backend port,
and runtime data that the in-app Setup page uses.
The Studio backend install/build path now targets EDMG Core as part of the same backend bundle, the packaged Studio app bundles FFmpeg for the internal renderer, and Ollama plus ComfyUI remain external tools.
The packaged Windows installer is now configured as an assisted installer so the app install location can be chosen explicitly, while `Studio Home` remains the separate root for heavy runtime data on `D:\` or another drive.

## Quick start (dev)

1. Start Studio backend
- `cd studio/edmg-studio/python_backend`
- create venv, `pip install -e ".[studio_bundle]"`
- run `edmg-studio-backend serve --host 127.0.0.1 --port 7863`

2. Start Studio UI
- `cd studio/edmg-studio`
- `npm install`
- `npm run dev`

The backend talks to local Ollama directly by default, so a separate AI service is not required for the normal Studio flow.
