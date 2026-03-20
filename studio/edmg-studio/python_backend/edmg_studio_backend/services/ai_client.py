from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

import requests


class AiDirector(Protocol):
    """Small interface used by Studio backend."""

    def plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    def transcribe(self, audio_path: str, model_size: str = "small") -> dict[str, Any]:
        ...

    def audio_features(self, audio_path: str) -> dict[str, Any]:
        ...

    def status(self) -> dict[str, Any]:
        ...


@dataclass
class HttpAiDirectorClient:
    base_url: str
    timeout_s: float = 180.0

    def __post_init__(self):
        self.base_url = self.base_url.rstrip("/")

    def plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        r = requests.post(f"{self.base_url}/v1/plan", json=payload, timeout=self.timeout_s)
        r.raise_for_status()
        return r.json()

    def transcribe(self, audio_path: str, model_size: str = "small") -> dict[str, Any]:
        with open(audio_path, "rb") as f:
            files = {"file": f}
            r = requests.post(
                f"{self.base_url}/v1/transcribe",
                files=files,
                data={"model_size": model_size},
                timeout=self.timeout_s,
            )
        if r.status_code == 501:
            return {"text": None, "note": "transcription not enabled on AI service"}
        r.raise_for_status()
        return r.json()

    def audio_features(self, audio_path: str) -> dict[str, Any]:
        with open(audio_path, "rb") as f:
            files = {"file": f}
            r = requests.post(f"{self.base_url}/v1/audio_features", files=files, timeout=self.timeout_s)
        if r.status_code == 501:
            return {"note": "audio_features not enabled on AI service"}
        r.raise_for_status()
        return r.json()

    def status(self) -> dict[str, Any]:
        try:
            r = requests.get(f"{self.base_url}/health", timeout=5.0)
            r.raise_for_status()
            return {"mode": "http", "ok": True, **r.json()}
        except Exception as e:
            return {"mode": "http", "ok": False, "error": str(e), "base_url": self.base_url}


class LocalAiDirectorClient:
    """In-process AI: uses vendored `edmg_ai_service` directly.

    Default provider is Ollama (EDMG_AI_PROVIDER=ollama). This avoids users having to run a separate
    AI FastAPI process; the Studio backend calls Ollama directly.
    """

    def __init__(self, timeout_s: float = 180.0):
        self.timeout_s = timeout_s
        self._ensure_import_path()
        self._provider = None
        self._provider_settings = None

    @staticmethod
    def _ensure_import_path() -> None:
        # `edmg_ai_service` is vendored into the Studio backend environment.
        return

    def _load(self):
        if self._provider is not None:
            return
        from edmg_ai_service.config import Settings as AiSettings
        from edmg_ai_service.provider_factory import build_provider
        from .config import settings as backend_settings
        from .secrets import SecretStore

        self._provider_settings = AiSettings()
        provider_name = (self._provider_settings.provider or "").strip().lower()
        if provider_name in ("openai_compat", "openai-compatible", "openai") and not self._provider_settings.openai_compat_api_key:
            secret_api_key = SecretStore(backend_settings.data_dir).get("openai_compat_api_key")
            if secret_api_key:
                self._provider_settings = replace(self._provider_settings, openai_compat_api_key=secret_api_key)
        self._provider = build_provider(self._provider_settings)

    def plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._load()
        from edmg_ai_service.schemas import PlanRequest

        req = PlanRequest(**payload)
        resp = self._provider.plan(req)
        # Pydantic v2
        return resp.model_dump()

    def transcribe(self, audio_path: str, model_size: str = "small") -> dict[str, Any]:
        try:
            self._ensure_import_path()
            from edmg_ai_service.asr import transcribe
        except Exception as e:
            return {"text": None, "note": f"transcription not available: {e}"}

        try:
            text = transcribe(audio_path, model_size=model_size)
            return {"text": text}
        except Exception as e:
            return {"text": None, "error": str(e)}

    def audio_features(self, audio_path: str) -> dict[str, Any]:
        try:
            self._ensure_import_path()
            from edmg_ai_service.audio import lightweight_audio_features
        except Exception as e:
            return {"note": f"audio_features not available: {e}"}

        try:
            return lightweight_audio_features(audio_path)
        except Exception as e:
            return {"error": str(e)}

    def status(self) -> dict[str, Any]:
        try:
            self._load()
            provider_name = getattr(self._provider, "name", None)
            provider_status = {
                "provider": provider_name,
                "model": getattr(self._provider, "model", None),
            }
            if provider_name == "ollama":
                provider_status["base_url"] = getattr(self._provider_settings, "ollama_url", None)
            elif provider_name == "openai_compat":
                provider_status["base_url"] = getattr(self._provider_settings, "openai_compat_base_url", None)
                provider_status["api_key_configured"] = bool(
                    getattr(self._provider_settings, "openai_compat_api_key", None)
                )
            return {
                "mode": "local",
                "ok": True,
                **provider_status,
            }
        except Exception as e:
            return {"mode": "local", "ok": False, "error": str(e)}


def build_ai_client(ai_mode: str, ai_base_url: str, timeout_s: float) -> AiDirector:
    mode = (ai_mode or "").strip().lower()
    if mode in ("http", "remote"):
        return HttpAiDirectorClient(base_url=ai_base_url, timeout_s=timeout_s)
    return LocalAiDirectorClient(timeout_s=timeout_s)
