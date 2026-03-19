# Release Checklist (Windows-first)

Goal: install **DWCT EDMG Studio** like a DAW/game:

- Run a standard **NSIS installer** (Next/Back)
- Launch the app
- Use the in-app **Setup Wizard** (GUI buttons) to install/verify dependencies

## Build

1) (Optional) Stage FFmpeg into the app resources:

```powershell
./packaging/windows/get_ffmpeg.ps1
```

2) Build installer:

```powershell
./packaging/windows/build_all.ps1
```

Artifacts:

- `studio/edmg-studio/release/`

## End-user runtime (no command line)

In the packaged app, go to **Sidebar → Setup**.

The Setup Wizard:

- checks **Ollama** (local AI)
- can **download & launch the Ollama Windows installer**
- can **pull the default model** via button
- checks **ComfyUI** and can **download/extract ComfyUI Portable (Windows)**
- verifies **FFmpeg** for MP4 assembly

The rest of the app uses the local **FastAPI backend** automatically (no separate “AI server” process required).
