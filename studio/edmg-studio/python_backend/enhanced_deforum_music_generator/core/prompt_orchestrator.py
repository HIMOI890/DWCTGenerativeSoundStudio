from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ..public_api import AudioAnalysis


@dataclass(frozen=True)
class OrchestrationConfig:
    fps: int = 24
    min_scenes: int = 4
    max_scenes: int = 10


def _frame(t: float, fps: int) -> int:
    return max(0, int(round(float(t) * float(fps))))


def _heuristic_scene_frames(analysis: AudioAnalysis, cfg: OrchestrationConfig) -> List[int]:
    dur = float(getattr(analysis, "duration", 0.0) or 0.0)
    beats = list(getattr(analysis, "beats", []) or [])
    fps = max(1, int(cfg.fps))
    if beats:
        frames = sorted({_frame(b, fps) for b in beats})
        if len(frames) > cfg.max_scenes:
            step = max(1, len(frames) // cfg.max_scenes)
            frames = frames[::step]
        return frames[: cfg.max_scenes] or [0]
    if dur > 0:
        segs = max(cfg.min_scenes, min(cfg.max_scenes, 6))
        return [int((i * dur / segs) * fps) for i in range(segs)]
    return [0]


def _keywords_from_lyrics(analysis: AudioAnalysis, *, max_k: int = 8) -> List[str]:
    segs = getattr(analysis, "lyric_segments", []) or []
    words: List[str] = []
    for s in segs:
        t = str(s.get("text", "")).lower()
        for w in re_split_words(t):
            if len(w) >= 4:
                words.append(w)
    # unique
    out: List[str] = []
    seen = set()
    for w in words:
        if w not in seen:
            out.append(w)
            seen.add(w)
        if len(out) >= max_k:
            break
    return out


def re_split_words(t: str) -> List[str]:
    import re
    return [w for w in re.split(r"[^a-z0-9]+", t) if w]


class PromptOrchestrator:
    """Prompt orchestration: deterministic baseline + optional AI augmentation."""

    def __init__(self, provider: Any | None = None, cfg: OrchestrationConfig | None = None):
        self.provider = provider
        self.cfg = cfg or OrchestrationConfig()

    def orchestrate(
        self,
        analysis: AudioAnalysis,
        *,
        base_prompt: str,
        style_prompt: str = "",
        negative_prompt: str = "",
        use_ai: bool = False,
    ) -> Dict[str, Any]:
        fps = max(1, int(self.cfg.fps))
        frames = _heuristic_scene_frames(analysis, self.cfg)

        base = " ".join([p for p in [base_prompt.strip(), style_prompt.strip()] if p]).strip() or "cinematic"
        keywords = _keywords_from_lyrics(analysis)
        emos = list(getattr(analysis, "lyric_emotions", []) or [])
        mood = ", ".join(emos[:3]) if emos else "cinematic"

        scene_plan: List[Dict[str, Any]] = []
        prompts: Dict[str, str] = {}
        neg_prompts: Dict[str, str] = {}

        # AI plan (optional). If it fails, fall back to deterministic plan.
        if use_ai and self.provider is not None:
            try:
                plan = self._ai_scene_plan(base=base, mood=mood, keywords=keywords, n=len(frames))
                if plan:
                    frames = frames[: len(plan)]
                    for f, s in zip(frames, plan):
                        scene_plan.append({"frame": int(f), **s})
                        prompts[str(int(f))] = s.get("prompt") or base
                        if negative_prompt:
                            neg_prompts[str(int(f))] = negative_prompt
                    return {
                        "fps": fps,
                        "scene_plan": scene_plan,
                        "prompts": prompts,
                        "negative_prompts": neg_prompts,
                    }
            except Exception:
                pass

        # Deterministic plan
        for i, f in enumerate(frames):
            flavor = f"{mood}"
            if keywords:
                flavor += " | " + ", ".join(keywords[:3])
            prompt = f"{base}, {flavor}, shot {i+1}/{len(frames)}"
            scene_plan.append({"frame": int(f), "mood": mood, "keywords": keywords, "prompt": prompt})
            prompts[str(int(f))] = prompt
            if negative_prompt:
                neg_prompts[str(int(f))] = negative_prompt

        return {"fps": fps, "scene_plan": scene_plan, "prompts": prompts, "negative_prompts": neg_prompts}

    def _ai_scene_plan(self, *, base: str, mood: str, keywords: List[str], n: int) -> List[Dict[str, Any]]:
        # Provider API contract: provider.complete(prompt, max_tokens=...)
        req = {
            "base_prompt": base,
            "mood": mood,
            "keywords": keywords,
            "n_scenes": n,
        }
        prompt = (
            "Create a JSON array of scene objects for a music video prompt schedule. "
            "Each object must have keys: title, prompt. "
            "No markdown. JSON only. Input: " + json.dumps(req)
        )
        txt = self.provider.complete(prompt, max_tokens=512)
        # extract JSON array
        import re
        m = re.search(r"\[[\s\S]*\]", txt)
        if not m:
            return []
        arr = json.loads(m.group(0))
        if not isinstance(arr, list):
            return []
        out=[]
        for x in arr:
            if isinstance(x, dict) and "prompt" in x:
                out.append({"title": str(x.get("title","scene")), "prompt": str(x["prompt"])})
        return out[:n]
