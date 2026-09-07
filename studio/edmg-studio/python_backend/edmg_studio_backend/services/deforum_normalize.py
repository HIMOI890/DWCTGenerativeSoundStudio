from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .deforum_motion import (
    DeforumMotionScheduleBundle,
    merge_motion_schedule_bundles,
    motion_bundle_from_mapping,
)
from .deforum_prompt_timeline import normalize_prompt_map


@dataclass(frozen=True)
class UnifiedDeforumRenderContext:
    prompts: tuple[tuple[int, str], ...] = ()
    negative_prompts: tuple[tuple[int, str], ...] = ()
    motion: DeforumMotionScheduleBundle = field(default_factory=DeforumMotionScheduleBundle)


DEFAULT_RENDER_PROMPT = "Cinematic image sequence with a coherent subject and controlled atmosphere."
DEFAULT_NEGATIVE_PROMPT = "blurry, low quality, watermark, text, logo"

# Stable Diffusion 1.x and the CLIP branch used by FLUX accept 77 tokens, including
# special tokens.  Fifty-six concise English words leave practical headroom for BPE
# splits while still carrying subject, action, motion, framing, world, and style.
# The complete authored storyboard remains in ``prompt_pack`` and structured fields.
CLIP_SAFE_RENDER_PROMPT_MAX_WORDS = 56


_SCENE_PROMPT_FIELDS: tuple[str, ...] = (
    "render_prompt",
    "prompt_pack",
    "prompt",
    "text",
    "description",
    "visual_description",
    "image_prompt",
    "scene_prompt",
    "positive_prompt",
)

_SCENE_CONTEXT_FIELDS: tuple[str, ...] = (
    "name",
    "title",
    "transcript_cue",
    "camera_hint",
    "motion_hint",
    "continuity_note",
    "continuityNote",
)


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(_clean_text(item) for item in value if _clean_text(item)).strip()
    if isinstance(value, dict):
        return ""
    return " ".join(str(value).replace("\r", " ").replace("\n", " ").split()).strip()


def _scene_contract_text(scene: dict[str, Any], *fields: str) -> str:
    storyboard = scene.get("storyboard") if isinstance(scene.get("storyboard"), dict) else {}
    for source in (scene, storyboard):
        for field in fields:
            text = _clean_text(source.get(field))
            if text:
                return text
    return ""


def prompt_excerpt(value: Any, *, max_words: int) -> str:
    """Return a deterministic, sentence-safe prompt excerpt.

    Studio keeps the complete authored storyboard contract in project metadata. Model-facing
    prompts use bounded excerpts so essential identity and continuity clauses are not displaced
    by later prose when CLIP/T5 tokenizers apply their own hard limits.
    """

    text = _clean_text(value).strip(" ,;:.-")
    if not text:
        return ""
    words = text.split()
    if len(words) <= max(1, int(max_words)):
        return text
    excerpt = words[: max(1, int(max_words))]
    trailing_connectors = {
        "a",
        "an",
        "and",
        "for",
        "from",
        "in",
        "into",
        "of",
        "on",
        "or",
        "the",
        "this",
        "to",
        "with",
    }
    while len(excerpt) > 1 and excerpt[-1].strip(" ,;:.-").lower() in trailing_connectors:
        excerpt.pop()
    return " ".join(excerpt).rstrip(" ,;:.-")


def limit_prompt_words(value: Any, *, max_words: int) -> str:
    text = _clean_text(value)
    words = text.split()
    limit = max(1, int(max_words))
    if len(words) <= limit:
        return text
    return " ".join(words[:limit]).rstrip(" ,;:.-") + "."


def _model_visual_phrase(
    value: Any,
    *,
    max_words: int,
    concrete_core: bool = False,
) -> str:
    """Return natural image-language instead of procedural storyboard prose."""

    text = _clean_text(value).strip(" ,;:.-")
    if not text:
        return ""
    if concrete_core:
        lowered = text.lower()
        cut_markers = (
            ";",
            " as one geographically continuous",
            " with stable spatial relationships",
            " consistent medium",
            " identical face",
        )
        cut_at = len(text)
        for marker in cut_markers:
            marker_at = lowered.find(marker)
            if marker_at > 0:
                cut_at = min(cut_at, marker_at)
        concrete = text[:cut_at].strip(" ,;:.-")
        if len(concrete.split()) >= 2:
            text = concrete
    # CLIP tokenizers split punctuation-heavy compounds into several tokens.
    text = text.replace("-", " ")
    return prompt_excerpt(text, max_words=max_words)


def operational_render_prompt_from_scene(
    scene: dict[str, Any],
    *,
    fallback: str = DEFAULT_RENDER_PROMPT,
    max_words: int = CLIP_SAFE_RENDER_PROMPT_MAX_WORDS,
    include_states: bool = False,
) -> str:
    """Build the concise prompt actually sent to local diffusion models.

    The order is deliberate: a concrete subject and persistent props, visible action, authored
    motion, framing, world, and visual style all fit inside the practical CLIP window. Procedural
    labels such as ``Character lock:`` are excluded because they consume tokens without describing
    pixels. The full prompt pack and exact boundary states remain untouched for storyboard review,
    export, continuity enforcement, and provenance.
    """

    if not isinstance(scene, dict):
        return limit_prompt_words(fallback, max_words=max_words)

    character_lock = _scene_contract_text(
        scene,
        "character_lock",
        "characterLock",
        "subject",
        "subject_anchor",
    )
    action = _scene_contract_text(scene, "action", "shot_action")
    style_lock = _scene_contract_text(
        scene,
        "style_lock",
        "styleLock",
        "visual_lock",
        "visualLock",
    )
    setting = _scene_contract_text(
        scene,
        "setting",
        "location",
        "location_hint",
        "locationHint",
    )
    shot_type = _scene_contract_text(scene, "shot_type", "shotType", "composition")
    camera = _scene_contract_text(scene, "camera", "camera_move", "camera_hint")
    subject_motion = _scene_contract_text(scene, "motion", "subject_motion", "motion_hint")
    environment_motion = _scene_contract_text(
        scene,
        "environment_motion",
        "environmentMotion",
    )
    start_state = _scene_contract_text(scene, "start_state", "startState", "opening_state")
    end_state = _scene_contract_text(scene, "end_state", "endState", "closing_state")
    continuity = _scene_contract_text(
        scene,
        "continuity_note",
        "continuityNote",
        "continuity",
    )

    clauses: list[str] = []

    def append_clause(value: Any, *, word_limit: int, concrete_core: bool = False) -> None:
        phrase = _model_visual_phrase(
            value,
            max_words=word_limit,
            concrete_core=concrete_core,
        )
        if not phrase:
            return
        normalized = phrase.casefold()
        if any(existing.rstrip(".").casefold() == normalized for existing in clauses):
            return
        clauses.append(f"{phrase.rstrip('. ')}.")

    append_clause(character_lock, word_limit=12, concrete_core=True)
    if character_lock:
        clauses.append("Single prominent subject, same identity and props.")
    append_clause(action, word_limit=9)
    append_clause(subject_motion, word_limit=5)
    append_clause(environment_motion, word_limit=5)
    append_clause(shot_type, word_limit=4)
    append_clause(camera, word_limit=4)
    append_clause(setting, word_limit=6, concrete_core=True)
    append_clause(style_lock, word_limit=8, concrete_core=True)

    if include_states:
        start_excerpt = _model_visual_phrase(start_state, max_words=7, concrete_core=True)
        end_excerpt = _model_visual_phrase(end_state, max_words=7, concrete_core=True)
        if start_excerpt:
            clauses.append(f"Begin with {start_excerpt}.")
        if end_excerpt:
            clauses.append(f"Resolve with {end_excerpt}.")

    if include_states:
        continuity_excerpt = _model_visual_phrase(continuity, max_words=6, concrete_core=True)
        if continuity_excerpt:
            clauses.append(f"{continuity_excerpt}.")

    if not clauses:
        source = render_prompt_from_scene(scene, fallback=fallback)
        return limit_prompt_words(source or fallback, max_words=max_words)
    return limit_prompt_words(" ".join(clauses), max_words=max_words)


def _is_generic_render_prompt(text: str) -> bool:
    normalized = " ".join(str(text or "").lower().split())
    if not normalized:
        return True
    generic = {
        DEFAULT_RENDER_PROMPT.lower(),
        "cinematic",
        "cinematic subject motion",
        "cinematic music video keyframe, detailed, high quality",
        "edmg studio draft proxy",
    }
    if normalized in generic:
        return True
    return normalized.startswith("cinematic image sequence with a coherent subject")


def render_prompt_from_scene(scene: dict[str, Any], *, fallback: str = DEFAULT_RENDER_PROMPT) -> str:
    """Return the strongest render prompt carried by a Studio scene payload."""
    if not isinstance(scene, dict):
        return fallback

    operational = _clean_text(scene.get("render_prompt"))
    if operational:
        return operational

    primary: list[str] = []
    for field in _SCENE_PROMPT_FIELDS:
        text = _clean_text(scene.get(field))
        if text and text not in primary:
            primary.append(text)

    visual = scene.get("visual") if isinstance(scene.get("visual"), dict) else {}
    for field in ("prompt", "description", "subject", "setting", "style"):
        text = _clean_text(visual.get(field))
        if text and text not in primary:
            primary.append(text)

    strong_primary = [text for text in primary if not _is_generic_render_prompt(text)]
    base = strong_primary[0] if strong_primary else (primary[0] if primary else "")

    context: list[str] = []
    for field in _SCENE_CONTEXT_FIELDS:
        text = _clean_text(scene.get(field))
        if text and not _is_generic_render_prompt(text) and text not in context and text != base:
            context.append(text)

    if base and context:
        base = " ".join([base, *context[:3]]).strip()
    return base or fallback


def negative_prompt_from_scene(scene: dict[str, Any], *, fallback: str = DEFAULT_NEGATIVE_PROMPT) -> str:
    if not isinstance(scene, dict):
        return fallback
    for field in ("negative_prompt", "negativePrompt", "negative", "negative_prompt_pack"):
        text = _clean_text(scene.get(field))
        if text:
            return text
    return fallback


def _frame_at_time(seconds: Any, fps: int) -> int:
    try:
        return max(0, int(round(float(seconds) * float(max(1, fps)))))
    except Exception:
        return 0


def _pairs_from_start_end(start_frame: int, end_frame: int, start_value: Any, end_value: Any) -> tuple[tuple[int, float], ...]:
    try:
        left = float(start_value)
        right = float(end_value)
    except Exception:
        return ()
    if start_frame == end_frame:
        return ((int(start_frame), float(right)),)
    return ((int(start_frame), float(left)), (int(end_frame), float(right)))


def _pairs_from_constant(start_frame: int, end_frame: int, value: Any) -> tuple[tuple[int, float], ...]:
    return _pairs_from_start_end(start_frame, end_frame, value, value)


def _variant_prompt_pairs(variant: dict[str, Any] | None) -> list[tuple[int, str]]:
    if not isinstance(variant, dict):
        return []
    prompts = variant.get("prompts")
    if isinstance(prompts, dict):
        return normalize_prompt_map(prompts)
    return []


def _variant_negative_pairs(variant: dict[str, Any] | None) -> list[tuple[int, str]]:
    if not isinstance(variant, dict):
        return []
    prompts = variant.get("negative_prompts")
    if isinstance(prompts, dict):
        return normalize_prompt_map(prompts)
    return []


def _scene_prompt_pairs(scenes: list[dict[str, Any]], fps: int) -> list[tuple[int, str]]:
    pairs: list[tuple[int, str]] = []
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        # Scene-derived prompts ultimately feed local CLIP-conditioned image models.
        # Rebuild them from the structured storyboard contract here as a final model
        # boundary so legacy projects cannot leak a stale or overlong render_prompt
        # into keyframe generation. Explicit Timeline, variant, and request-level
        # Deforum prompt tracks still retain their documented override precedence.
        fallback = render_prompt_from_scene(scene, fallback="")
        prompt = operational_render_prompt_from_scene(
            scene,
            fallback=fallback,
            max_words=CLIP_SAFE_RENDER_PROMPT_MAX_WORDS,
            include_states=False,
        )
        pairs.append((_frame_at_time(scene.get("start_s", 0.0), fps), prompt))
    return normalize_prompt_map(pairs)


def _scene_negative_pairs(scenes: list[dict[str, Any]], fps: int) -> list[tuple[int, str]]:
    pairs: list[tuple[int, str]] = []
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        negative = negative_prompt_from_scene(scene, fallback="")
        if not negative:
            continue
        pairs.append((_frame_at_time(scene.get("start_s", 0.0), fps), negative))
    return normalize_prompt_map(pairs)


def _prompt_track_pairs(timeline: dict[str, Any] | None, fps: int) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    if not isinstance(timeline, dict):
        return [], []

    prompt_pairs: list[tuple[int, str]] = []
    negative_pairs: list[tuple[int, str]] = []
    tracks = timeline.get("tracks")
    if not isinstance(tracks, list):
        return [], []

    for track in tracks:
        if not isinstance(track, dict):
            continue
        if str(track.get("type") or "").lower() != "prompt":
            continue
        clips = track.get("clips")
        if not isinstance(clips, list):
            continue
        for clip in clips:
            if not isinstance(clip, dict):
                continue
            data = clip.get("data") if isinstance(clip.get("data"), dict) else {}
            frame = _frame_at_time(clip.get("start_s", 0.0), fps)
            if "prompt" in data:
                prompt_pairs.append((frame, str(data.get("prompt") or "")))
            if "negative_prompt" in data:
                negative_pairs.append((frame, str(data.get("negative_prompt") or "")))

    return normalize_prompt_map(prompt_pairs), normalize_prompt_map(negative_pairs)


def _motion_track_bundle(timeline: dict[str, Any] | None, fps: int) -> DeforumMotionScheduleBundle:
    if not isinstance(timeline, dict):
        return DeforumMotionScheduleBundle()

    tracks = timeline.get("tracks")
    if not isinstance(tracks, list):
        return DeforumMotionScheduleBundle()

    bundles: list[DeforumMotionScheduleBundle] = []
    for track in tracks:
        if not isinstance(track, dict):
            continue
        if str(track.get("type") or "").lower() != "motion":
            continue
        clips = track.get("clips")
        if not isinstance(clips, list):
            continue
        for clip in clips:
            if not isinstance(clip, dict):
                continue
            start_frame = _frame_at_time(clip.get("start_s", 0.0), fps)
            end_frame = _frame_at_time(clip.get("end_s", clip.get("start_s", 0.0)), fps)
            if end_frame < start_frame:
                end_frame = start_frame

            data = clip.get("data") if isinstance(clip.get("data"), dict) else {}
            schedule_map = data.get("motion_schedules") if isinstance(data.get("motion_schedules"), dict) else {}

            direct = {
                "zoom": data.get("zoom", data.get("zoom_schedule", schedule_map.get("zoom", schedule_map.get("zoom_schedule")))),
                "angle": data.get(
                    "angle",
                    data.get(
                        "rotation_schedule",
                        schedule_map.get(
                            "angle",
                            schedule_map.get(
                                "rotation_schedule",
                                schedule_map.get("rotation_z_schedule", data.get("rotation_deg")),
                            ),
                        ),
                    ),
                ),
                "translation_x": data.get(
                    "translation_x",
                    data.get("pan_x_schedule", schedule_map.get("translation_x", schedule_map.get("pan_x_schedule", data.get("pan_x")))),
                ),
                "translation_y": data.get(
                    "translation_y",
                    data.get("pan_y_schedule", schedule_map.get("translation_y", schedule_map.get("pan_y_schedule", data.get("pan_y")))),
                ),
                "translation_z": data.get(
                    "translation_z",
                    data.get("pan_z_schedule", schedule_map.get("translation_z", schedule_map.get("pan_z_schedule", data.get("pan_z")))),
                ),
                "rotation_3d_x": data.get(
                    "rotation_3d_x",
                    data.get("rotation_x_schedule", schedule_map.get("rotation_3d_x", schedule_map.get("rotation_x_schedule", data.get("pitch")))),
                ),
                "rotation_3d_y": data.get(
                    "rotation_3d_y",
                    data.get("rotation_y_schedule", schedule_map.get("rotation_3d_y", schedule_map.get("rotation_y_schedule", data.get("yaw")))),
                ),
                "rotation_3d_z": data.get(
                    "rotation_3d_z",
                    data.get("rotation_3d_z_schedule", schedule_map.get("rotation_3d_z", schedule_map.get("rotation_3d_z_schedule", data.get("roll")))),
                ),
                "fov": data.get("fov", schedule_map.get("fov", schedule_map.get("fov_schedule", data.get("field_of_view")))),
                "strength_schedule": data.get("strength_schedule", schedule_map.get("strength_schedule", data.get("strength"))),
                "cfg_scale_schedule": data.get("cfg_scale_schedule", schedule_map.get("cfg_scale_schedule", data.get("cfg"))),
                "steps_schedule": data.get("steps_schedule", schedule_map.get("steps_schedule", data.get("steps"))),
                "denoise_schedule": data.get("denoise_schedule", schedule_map.get("denoise_schedule", data.get("denoise"))),
            }

            clip_bundle = DeforumMotionScheduleBundle(
                zoom=_pairs_from_start_end(start_frame, end_frame, data.get("zoom_start", 1.0), data.get("zoom_end", data.get("zoom_start", 1.0))),
                angle=_pairs_from_start_end(start_frame, end_frame, data.get("rotation_start", 0.0), data.get("rotation_end", data.get("rotation_start", 0.0))),
                translation_x=_pairs_from_start_end(start_frame, end_frame, data.get("pan_x_start", 0.0), data.get("pan_x_end", data.get("pan_x_start", 0.0))),
                translation_y=_pairs_from_start_end(start_frame, end_frame, data.get("pan_y_start", 0.0), data.get("pan_y_end", data.get("pan_y_start", 0.0))),
                translation_z=_pairs_from_start_end(start_frame, end_frame, data.get("pan_z_start", 0.0), data.get("pan_z_end", data.get("pan_z_start", 0.0))) if (data.get("pan_z_start") is not None or data.get("pan_z_end") is not None) else (),
                rotation_3d_x=_pairs_from_start_end(start_frame, end_frame, data.get("pitch_start", 0.0), data.get("pitch_end", data.get("pitch_start", 0.0))) if (data.get("pitch_start") is not None or data.get("pitch_end") is not None) else (),
                rotation_3d_y=_pairs_from_start_end(start_frame, end_frame, data.get("yaw_start", 0.0), data.get("yaw_end", data.get("yaw_start", 0.0))) if (data.get("yaw_start") is not None or data.get("yaw_end") is not None) else (),
                rotation_3d_z=_pairs_from_start_end(start_frame, end_frame, data.get("roll_start", 0.0), data.get("roll_end", data.get("roll_start", 0.0))) if (data.get("roll_start") is not None or data.get("roll_end") is not None) else (),
                strength_schedule=_pairs_from_constant(start_frame, end_frame, data.get("strength")) if data.get("strength") is not None else (),
                cfg_scale_schedule=_pairs_from_constant(start_frame, end_frame, data.get("cfg")) if data.get("cfg") is not None else (),
                steps_schedule=_pairs_from_constant(start_frame, end_frame, data.get("steps")) if data.get("steps") is not None else (),
                denoise_schedule=_pairs_from_constant(start_frame, end_frame, data.get("denoise")) if data.get("denoise") is not None else (),
            )
            bundles.append(merge_motion_schedule_bundles(clip_bundle, motion_bundle_from_mapping(direct)))

    return merge_motion_schedule_bundles(*bundles)


def _request_override_prompts(overrides: dict[str, Any] | None, key: str) -> list[tuple[int, str]]:
    if not isinstance(overrides, dict):
        return []
    raw = overrides.get(key)
    if not isinstance(raw, dict):
        return []
    return normalize_prompt_map(raw)


def _request_override_motion(overrides: dict[str, Any] | None) -> DeforumMotionScheduleBundle:
    if not isinstance(overrides, dict):
        return DeforumMotionScheduleBundle()
    mapped = {
        "zoom": overrides.get("deforum_zoom"),
        "angle": overrides.get("deforum_angle"),
        "translation_x": overrides.get("deforum_translation_x"),
        "translation_y": overrides.get("deforum_translation_y"),
        "translation_z": overrides.get("deforum_translation_z"),
        "rotation_3d_x": overrides.get("deforum_rotation_3d_x"),
        "rotation_3d_y": overrides.get("deforum_rotation_3d_y"),
        "rotation_3d_z": overrides.get("deforum_rotation_3d_z"),
        "fov": overrides.get("deforum_fov"),
        "strength_schedule": overrides.get("deforum_strength_schedule"),
        "cfg_scale_schedule": overrides.get("deforum_cfg_scale_schedule"),
        "steps_schedule": overrides.get("deforum_steps_schedule"),
        "denoise_schedule": overrides.get("deforum_denoise_schedule"),
    }
    return motion_bundle_from_mapping({key: value for key, value in mapped.items() if value is not None})


def build_deforum_render_context(
    *,
    scenes: list[dict[str, Any]],
    timeline: dict[str, Any] | None,
    variant: dict[str, Any] | None,
    fps: int,
    default_negative_prompt: str,
    overrides: dict[str, Any] | None = None,
) -> UnifiedDeforumRenderContext:
    timeline_prompts, timeline_negative = _prompt_track_pairs(timeline, fps)
    variant_prompts = _variant_prompt_pairs(variant)
    variant_negative = _variant_negative_pairs(variant)
    scene_prompts = _scene_prompt_pairs(scenes, fps)
    scene_negative = _scene_negative_pairs(scenes, fps)

    prompt_override = _request_override_prompts(overrides, "deforum_prompts")
    negative_override = _request_override_prompts(overrides, "deforum_negative_prompts")

    prompt_pairs = prompt_override or timeline_prompts or variant_prompts or scene_prompts
    negative_pairs = negative_override or timeline_negative or variant_negative or scene_negative
    if not negative_pairs and default_negative_prompt:
        negative_pairs = [(0, default_negative_prompt)]

    variant_motion_raw = variant.get("motion_schedules") if isinstance(variant, dict) and isinstance(variant.get("motion_schedules"), dict) else {}
    motion = merge_motion_schedule_bundles(
        motion_bundle_from_mapping(variant_motion_raw),
        _motion_track_bundle(timeline, fps),
        _request_override_motion(overrides),
    )

    return UnifiedDeforumRenderContext(
        prompts=tuple(prompt_pairs),
        negative_prompts=tuple(negative_pairs),
        motion=motion,
    )
