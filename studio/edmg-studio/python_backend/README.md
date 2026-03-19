# EDMG Studio Backend (v1.1.0)

## Run
```bash
pip install -e ".[studio_bundle]"
edmg-studio-backend serve --host 127.0.0.1 --port 7863
```

## AI (Ollama by default)

The backend defaults to **EDMG_AI_MODE=local** and will call **Ollama** directly (no separate AI server to run).

Recommended env vars:

```bash
EDMG_AI_MODE=local
EDMG_AI_PROVIDER=ollama
EDMG_AI_OLLAMA_URL=http://127.0.0.1:11434
EDMG_AI_OLLAMA_MODEL=qwen2.5:3b-instruct
```

If you want an external AI service instead:

```bash
EDMG_AI_MODE=http
EDMG_AI_BASE_URL=http://127.0.0.1:7862
```

OpenAI-compatible option (LM Studio / vLLM / Groq / Together, etc.):

```bash
EDMG_AI_MODE=local
EDMG_AI_PROVIDER=openai_compat
EDMG_AI_OPENAI_COMPAT_BASE_URL=http://127.0.0.1:1234/v1
EDMG_AI_OPENAI_COMPAT_MODEL=...
EDMG_AI_OPENAI_COMPAT_API_KEY=...  # if required
```

## Integrations
- ComfyUI renders are queued locally.
- AI Director is optional; used for transcription/features/plan.
- EDMG Core is bundled into the Studio backend install/build target; Studio Setup can repair or reinstall it if needed.
- FFmpeg defaults to the Studio-bundled binary when available; `EDMG_FFMPEG_PATH` remains an override.
