"""
AI Provider abstraction layer for EDMG.

This module provides a lightweight, optional integration layer for
API-based and local LLM providers (OpenAI-compatible, Ollama, llama.cpp,
HuggingFace Transformers). All providers are best-effort and designed to
gracefully fall back when dependencies or services are unavailable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import json
import os
import re

try:
    import requests  # type: ignore
    _REQ_OK = True
except Exception:
    requests = None
    _REQ_OK = False

try:
    from transformers import pipeline  # type: ignore
    _TF_OK = True
except Exception:
    pipeline = None
    _TF_OK = False


@dataclass
class PromptSegment:
    start: float
    end: float
    prompt: str


@dataclass
class SemanticAnalysis:
    themes: List[str] = field(default_factory=list)
    emotions: Dict[str, float] = field(default_factory=dict)
    visual_elements: List[str] = field(default_factory=list)
    timeline: List[PromptSegment] = field(default_factory=list)
    style_guidance: str = ""
    camera_motion_hints: List[str] = field(default_factory=list)


@dataclass
class AIProviderConfig:
    provider: str = "ollama"
    model: str = "llama3.1:8b"
    base_url: str = "http://localhost:11434"
    api_key_env: str = "OPENAI_API_KEY"
    timeout: int = 60
    temperature: float = 0.4
    max_tokens: int = 800


class AIProvider:
    """Abstract provider interface."""

    def analyze_lyrics(self, text: str, segments: Optional[List[Dict[str, Any]]] = None) -> SemanticAnalysis:
        raise NotImplementedError

    def generate_prompts(self, prompt: str, num_prompts: int) -> List[str]:
        raise NotImplementedError


class BaseJSONProvider(AIProvider):
    """Helper base class for providers that return JSON text."""

    def _extract_json_list(self, text: str) -> List[str]:
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return [str(item) for item in data]
        except Exception:
            pass
        match = re.search(r"\[(?:.|\n)*\]", text)
        if match:
            try:
                data = json.loads(match.group(0))
                if isinstance(data, list):
                    return [str(item) for item in data]
            except Exception:
                pass
        return []

    def analyze_lyrics(self, text: str, segments: Optional[List[Dict[str, Any]]] = None) -> SemanticAnalysis:
        return SemanticAnalysis()


class OpenAIProvider(BaseJSONProvider):
    """OpenAI-compatible chat completion provider."""

    def __init__(self, config: AIProviderConfig):
        self.config = config
        self.api_key = os.getenv(config.api_key_env)

    def generate_prompts(self, prompt: str, num_prompts: int) -> List[str]:
        if not (_REQ_OK and requests and self.api_key):
            return []
        payload = {
            "model": self.config.model,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "messages": [
                {"role": "system", "content": "Respond ONLY with a JSON array of prompt strings."},
                {"role": "user", "content": prompt},
            ],
        }
        headers = {"authorization": f"Bearer {self.api_key}"}
        url = self.config.base_url.rstrip("/") + "/v1/chat/completions"
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=self.config.timeout)
            resp.raise_for_status()
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return self._extract_json_list(content)[:num_prompts]
        except Exception:
            return []


class OllamaProvider(BaseJSONProvider):
    """Ollama local provider (HTTP API)."""

    def __init__(self, config: AIProviderConfig):
        self.config = config

    def generate_prompts(self, prompt: str, num_prompts: int) -> List[str]:
        if not (_REQ_OK and requests):
            return []
        url = self.config.base_url.rstrip("/") + "/api/generate"
        payload = {
            "model": self.config.model,
            "prompt": prompt + "\nReturn ONLY a JSON array of prompt strings.",
            "stream": False,
            "options": {"temperature": self.config.temperature},
        }
        try:
            resp = requests.post(url, json=payload, timeout=self.config.timeout)
            resp.raise_for_status()
            data = resp.json()
            content = data.get("response", "")
            return self._extract_json_list(content)[:num_prompts]
        except Exception:
            return []


class LlamaCppProvider(BaseJSONProvider):
    """llama.cpp server provider."""

    def __init__(self, config: AIProviderConfig):
        self.config = config

    def generate_prompts(self, prompt: str, num_prompts: int) -> List[str]:
        if not (_REQ_OK and requests):
            return []
        url = self.config.base_url.rstrip("/") + "/completion"
        payload = {
            "prompt": prompt + "\nReturn ONLY a JSON array of prompt strings.",
            "temperature": self.config.temperature,
            "n_predict": self.config.max_tokens,
        }
        try:
            resp = requests.post(url, json=payload, timeout=self.config.timeout)
            resp.raise_for_status()
            data = resp.json()
            content = data.get("content", "")
            return self._extract_json_list(content)[:num_prompts]
        except Exception:
            return []


class HFTransformersProvider(BaseJSONProvider):
    """Local HuggingFace transformers provider."""

    def __init__(self, config: AIProviderConfig):
        self.config = config
        self._pipeline = None
        if _TF_OK and pipeline:
            try:
                self._pipeline = pipeline("text-generation", model=self.config.model)
            except Exception:
                self._pipeline = None

    def generate_prompts(self, prompt: str, num_prompts: int) -> List[str]:
        if not self._pipeline:
            return []
        try:
            outputs = self._pipeline(
                prompt + "\nReturn ONLY a JSON array of prompt strings.",
                max_new_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
            )
            text = outputs[0].get("generated_text", "") if outputs else ""
            return self._extract_json_list(text)[:num_prompts]
        except Exception:
            return []


def build_ai_provider(config: AIProviderConfig) -> AIProvider:
    provider = config.provider.lower()
    if provider in {"openai", "api"}:
        return OpenAIProvider(config)
    if provider in {"ollama"}:
        return OllamaProvider(config)
    if provider in {"llamacpp", "llama.cpp", "llama_cpp"}:
        return LlamaCppProvider(config)
    if provider in {"hf", "huggingface", "transformers"}:
        return HFTransformersProvider(config)
    raise ValueError(f"Unknown AI provider: {config.provider}")

