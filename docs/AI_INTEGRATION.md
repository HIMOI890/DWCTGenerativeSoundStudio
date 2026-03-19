# AI Integration Design (API + Local Providers)

This document describes a pluggable AI integration layer for EDMG that supports:

- API-based models (e.g., OpenAI/Anthropic/etc.)
- Local inference via **Ollama**
- Local inference via **llama.cpp**
- Local inference via **HuggingFace Transformers**

The goal is to provide a **single, normalized semantic output** that can drive
Deforum prompt schedules, while preserving the existing lightweight heuristics
as a fallback.

---

## Objectives

1. **Unified interface** for all AI providers.
2. **Configurable backends** (API, Ollama, llama.cpp, HF Transformers).
3. **Time-aligned prompt scheduling** using transcript segments.
4. **Graceful fallback** to current heuristic NLP when AI is unavailable.
5. **Minimal disruption** to existing EDMG flows.

---

## Proposed Architecture

```
EDMG
├─ Audio Analyzer (existing)
├─ Transcription (Whisper / provider)
├─ AIProvider (new abstraction)
│  ├─ APIProvider (OpenAI/etc.)
│  ├─ OllamaProvider
│  ├─ LlamaCppProvider
│  └─ HFTransformersProvider
└─ Deforum Prompt Scheduler (existing + AI-enhanced)
```

### Provider Interface (concept)

```
class AIProvider:
    def analyze_lyrics(self, text: str, segments: list[dict]) -> SemanticAnalysis: ...
    def generate_prompt_schedule(self, semantic: SemanticAnalysis) -> list[PromptSegment]: ...
```

**Shared output model (normalized):**

- `themes: list[str]`
- `emotions: dict[str, float]`
- `visual_elements: list[str]`
- `timeline: list[PromptSegment]`
- `style_guidance: str`
- `camera_motion_hints: list[str]`

---

## Provider Details

### 1) API Provider (OpenAI/Anthropic/etc.)

**Use case:** High-quality semantic understanding and creative prompt planning.

**Inputs:**
- Full lyric text
- Transcription segments with timestamps
- Audio features (tempo, energy, spectral summary)

**Outputs:**
- Structured semantic summary
- Prompt schedule aligned to lyric segments

**Notes:**
- Configuration should allow provider/model selection and API key injection via env vars.

---

### 2) Ollama Provider (Local)

**Use case:** Offline local LLMs (e.g., `llama3`, `mistral`, `phi3`).

**Transport:** HTTP API (`http://localhost:11434`)

**Flow:**
1. Send lyric text + segments in a prompt template.
2. Parse JSON response into `SemanticAnalysis`.

**Notes:**
- Provide a model selector in config (e.g., `llama3.1:8b`).

---

### 3) llama.cpp Provider (Local)

**Use case:** Local CPU/GPU inference using GGUF models.

**Transport options:**
- Direct Python bindings (if compiled)
- Server mode (preferred, HTTP)

**Flow:**
1. Send structured prompt to local llama.cpp server.
2. Parse JSON output into normalized schema.

**Notes:**
- Server mode avoids Python binary/ABI issues.

---

### 4) HuggingFace Transformers Provider (Local)

**Use case:** Local inference with `transformers` and `pipeline`.

**Flow:**
1. Load text-generation or instruction-following model.
2. Prompt with lyric text + segment timings.
3. Parse JSON output.

**Notes:**
- Should be lazy-loaded and optional to avoid heavy startup.
- Provide a configuration for device mapping (`cpu`, `cuda`, `auto`).

---

## Configuration Proposal

```yaml
ai:
  provider: "ollama"  # "api" | "ollama" | "llama_cpp" | "hf"
  api:
    base_url: "https://api.openai.com/v1"
    model: "gpt-4o-mini"
    api_key_env: "OPENAI_API_KEY"
  ollama:
    base_url: "http://localhost:11434"
    model: "llama3.1:8b"
  llama_cpp:
    base_url: "http://localhost:8080"
    model: "your-gguf-model"
  hf:
    model: "meta-llama/Llama-3.2-3B-Instruct"
    device: "auto"
  behavior:
    schedule_mode: "timeline"
    temperature: 0.6
```

---

## Prompting Strategy

### Recommended Prompt Template (for all providers)

- System prompt:
  - You are an assistant that outputs strict JSON.
- User prompt:
  - Include lyrics text
  - Include segments as `[{"start":..., "end":..., "text":...}]`
  - Ask for:
    - themes
    - emotions + confidence
    - visual elements
    - camera hints
    - timeline prompt segments aligned to timestamps

---

## Fallback Behavior

If the AI provider fails or is disabled:

- Use current rule-based NLP (`NLPProcessor` + `lyrics_analyzer.py`).
- Generate prompts using existing heuristics.

---

## Minimal Implementation Plan

1. **Add provider interface and shared output model.**
2. **Implement Ollama provider first** (simple HTTP + JSON parsing).
3. **Add llama.cpp provider (server mode)**.
4. **Add HF Transformers provider** (lazy model load).
5. **Add API provider** (OpenAI/Anthropic configurable).
6. **Wire provider selection into config and UI.**
7. **Merge AI output into existing Deforum schedule generation.**

---

## Testing Strategy

- Unit tests for each provider (mock API responses).
- Integration tests for prompt schedule generation with fixed lyrics.
- Fallback tests with provider disabled.

