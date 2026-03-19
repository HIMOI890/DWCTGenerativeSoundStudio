from __future__ import annotations

import math
from typing import Optional

from .base import PlanProvider
from ..schemas import PlanRequest, PlanResponse, PlanVariant, Scene


class RuleBasedPlanner(PlanProvider):
    """Deterministic, dependency-free fallback planner.

    Keeps EDMG usable even when no LLM is available.
    """

    @property
    def name(self) -> str:
        return "rule_based"

    @property
    def model(self) -> Optional[str]:
        return None

    def plan(self, req: PlanRequest) -> PlanResponse:
        dur = float(req.duration_s) if req.duration_s and req.duration_s > 0 else 60.0
        max_scenes = int(req.max_scenes)

        # Aim ~8s per scene, bounded
        target = 8.0
        n = max(3, min(max_scenes, int(math.ceil(dur / target))))
        scene_len = dur / n

        base_mood = "energetic" if (req.bpm or 0) >= 120 else "moody"
        if req.tags:
            base_mood = f"{base_mood}, {', '.join(req.tags[:3])}"

        style_clause = f" Style: {req.style_prefs}." if (req.style_prefs or "").strip() else ""
        prompt_seed = (
            "music video inspired by the lyrics, symbolic storytelling, cinematic lighting, high detail"
            if req.lyrics else
            "abstract music video, cinematic lighting, high detail, coherent subject"
        )

        variants: list[PlanVariant] = []
        for i in range(req.num_variants):
            name = ["Classic", "Surreal", "Minimal"][i] if i < 3 else f"Variant {i+1}"
            scenes: list[Scene] = []
            for s in range(n):
                start = round(s * scene_len, 3)
                end = round(min(dur, (s + 1) * scene_len), 3)
                prompt = f"{prompt_seed}. Scene {s+1}/{n}. {base_mood}.{style_clause}"
                scenes.append(Scene(
                    start_s=start,
                    end_s=end,
                    prompt=prompt,
                    negative_prompt="blurry, low quality, watermark, text, logo",
                    camera="slow push-in" if s % 2 == 0 else "slow pan",
                    motion="beat-synced subtle motion",
                    notes="Auto-generated fallback plan"
                ))

            variants.append(PlanVariant(
                name=name,
                logline="Auto-generated music video concept (fallback planner).",
                mood=base_mood,
                visual_motifs=req.tags[:6],
                color_palette=["deep blacks", "neon accents"] if "electronic" in " ".join(req.tags).lower() else ["warm highlights", "soft shadows"],
                scenes=scenes,
            ))

        return PlanResponse(provider=self.name, model=None, variants=variants)
