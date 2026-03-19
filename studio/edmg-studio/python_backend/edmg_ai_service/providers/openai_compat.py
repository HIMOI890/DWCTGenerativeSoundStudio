from __future__ import annotations

import json
import re
from typing import Optional

import requests

from .base import PlanProvider
from .fallback import RuleBasedPlanner
from ..schemas import PlanRequest, PlanResponse, PlanVariant


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> Optional[dict]:
    try:
        return json.loads(text)
    except Exception:
        pass
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


class OpenAICompatPlanner(PlanProvider):
    """Calls any OpenAI-compatible `POST /v1/chat/completions` endpoint via `requests`."""

    def __init__(self, base_url: str, api_key: str | None, model: str, timeout_s: float = 180.0):
        b = base_url.rstrip("/")
        # Accept both forms:
        #  - http://host:port
        #  - http://host:port/v1
        if b.endswith("/v1"):
            b = b[:-3]
        self._base_url = b
        self._api_key = api_key
        self._model = model
        self._timeout_s = timeout_s
        self._fallback = RuleBasedPlanner()

    @property
    def name(self) -> str:
        return "openai_compat"

    @property
    def model(self) -> Optional[str]:
        return self._model

    def plan(self, req: PlanRequest) -> PlanResponse:
        system = (
            "You are EDMG Director. Return STRICT JSON only. "
            "Schema: {variants: [{name, logline, mood, visual_motifs:[...], color_palette:[...], "
            "scenes:[{start_s,end_s,prompt,negative_prompt,camera,motion,notes}]}]}"
        )

        ctx = {
            "title": req.title,
            "duration_s": req.duration_s,
            "bpm": req.bpm,
            "tags": req.tags,
            "user_notes": req.user_notes,
            "style_prefs": req.style_prefs,
            "lyrics": (req.lyrics[:2000] if req.lyrics else None),
            "num_variants": req.num_variants,
            "max_scenes": req.max_scenes,
        }

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": "Input JSON:\n" + json.dumps(ctx, ensure_ascii=False)},
            ],
            "temperature": 0.7,
        }

        try:
            r = requests.post(
                f"{self._base_url}/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=self._timeout_s,
            )
            r.raise_for_status()
            data = r.json()
            text = data["choices"][0]["message"]["content"]
        except Exception:
            return self._fallback.plan(req)

        obj = _extract_json(text)
        if not obj or "variants" not in obj:
            return self._fallback.plan(req)

        try:
            variants = [PlanVariant.model_validate(v) for v in obj["variants"]]
            return PlanResponse(provider=self.name, model=self._model, variants=variants)
        except Exception:
            return self._fallback.plan(req)
