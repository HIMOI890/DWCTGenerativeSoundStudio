"""Canonical, non-destructive planner schedule compilation and approval.

Compilation is pure: a schedule describes proposed timeline content and never
mutates the active timeline. Ownership, not track type, controls replacement.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from copy import deepcopy
from typing import Any

from ..services.deforum_normalize import (
    negative_prompt_from_scene,
    operational_render_prompt_from_scene,
)

OWNER = "studio_planner"
SCHEMA_VERSION = 1


def _number(value: Any, fallback: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else fallback
    except (TypeError, ValueError, OverflowError):
        return fallback


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()[:24]


def project_transport(meta: dict, duration: float) -> tuple[float, float]:
    timeline = meta.get("timeline") or {}
    transport = timeline.get("transport") or {}
    fps = _number(transport.get("fps") or timeline.get("fps_output") or timeline.get("fps"), 24)
    if not 1 <= fps <= 120:
        raise ValueError("Schedule output FPS must be within 1–120")
    if not 0 < duration <= 24 * 60 * 60:
        raise ValueError("Schedule duration must be positive and at most 24 hours")
    return fps, duration


def compile_schedule(*, project_id: str, project_revision: int, variant_index: int,
                     variant: dict, analysis: dict, fps: float, duration_s: float) -> dict:
    if not math.isfinite(fps) or not 1 <= fps <= 120 or not math.isfinite(duration_s) or duration_s <= 0:
        raise ValueError("Invalid schedule transport")
    scenes = [deepcopy(scene) for scene in variant.get("scenes", []) if isinstance(scene, dict)]
    if not scenes or len(scenes) > 1000:
        raise ValueError("A schedule requires 1–1000 scenes")
    scenes.sort(key=lambda scene: _number(scene.get("start_s")))
    warnings: list[str] = []
    features = analysis.get("features") if isinstance(analysis.get("features"), dict) else {}
    bpm = _number(features.get("bpm") or features.get("tempo_bpm"))
    if not 20 <= bpm <= 300:
        bpm = 0
        warnings.append("No stable BPM: timing uses scene boundaries and available analysis events.")
    transcript = analysis.get("transcript")
    if not transcript or (isinstance(transcript, dict) and not transcript.get("text")):
        warnings.append("No transcript: no lyric-derived cues were invented.")
    energy = features.get("rms_energy") or []
    if not isinstance(energy, list):
        energy = []
    energy = [max(0, _number(value)) for value in energy[:100000]]
    peak = max(energy, default=0)
    if peak <= 0:
        warnings.append("No usable energy curve: motion uses restrained scene defaults.")
    prompts, anchors, camera, motion, markers = [], [], [], [], []
    previous_end: Any = None
    previous_time = 0.0
    smoothed = 0.0
    previous_locks: dict = {}
    seen_scene_ids: set[str] = set()

    def point(scene_id: str, kind: str, stamp: float, reason: str, **data) -> dict:
        stamp = round(max(0.0, min(duration_s, stamp)), 6)
        return {"id": f"planner:{variant_index}:{scene_id}:{kind}:{stamp:g}", "owner": OWNER,
                "source_id": scene_id, "t": stamp, "frame": int(math.floor(stamp * fps + 0.5)),
                "reason": reason, **data}

    for index, scene in enumerate(scenes):
        scene_id = str(scene.get("id") or f"scene-{index}")
        if scene_id in seen_scene_ids:
            scene_id = f"{scene_id}-{index}"
            warnings.append(f"Scene {index + 1} reused an identifier; its draft points have a unique source ID.")
        seen_scene_ids.add(scene_id)
        start = max(0.0, min(duration_s, _number(scene.get("start_s"))))
        end = max(0.0, min(duration_s, _number(scene.get("end_s"), duration_s)))
        if start < previous_time:
            warnings.append(f"Scene {index + 1} overlapped its predecessor; its draft starts at the previous end.")
            start = previous_time
        if end <= start:
            warnings.append(f"Scene {index + 1} has no valid duration and was omitted from the draft.")
            continue
        if previous_end is not None:
            scene["start_state"] = deepcopy(previous_end)
        previous_end = deepcopy(scene.get("end_state", scene.get("start_state", "")))
        previous_time = end
        prompt = operational_render_prompt_from_scene(scene)
        locks = {key: deepcopy(scene.get(key) or previous_locks.get(key, "")) for key in
                 ("setting", "character_lock", "style_lock", "shot_type", "screen_direction", "palette")}
        previous_locks = locks
        for edge, stamp, state in (("start", start, scene.get("start_state", "")), ("end", end, previous_end)):
            prompts.append(point(scene_id, f"prompt-{edge}", stamp, f"Scene {index + 1} {edge}",
                                 prompt=prompt, negative_prompt=negative_prompt_from_scene(scene, fallback=""),
                                 end_s=end, boundary=edge))
            anchors.append(point(scene_id, f"anchor-{edge}", stamp, "Exact scene continuity boundary",
                                 state=deepcopy(state), locks=locks, boundary=edge))
        event_times = {start, end}
        section_source = analysis.get("sections") or features.get("sections") or []
        for section in section_source if isinstance(section_source, list) else []:
            if isinstance(section, dict):
                stamp = _number(section.get("start_s", section.get("start")), -1)
                if start < stamp < end:
                    event_times.add(stamp)
                    markers.append(point(scene_id, "section", stamp, "Analysis section boundary", label=str(section.get("label") or section.get("name") or "Section")))
        # A bounded phrase grid avoids per-frame/per-beat explosion on long audio.
        interval = max(2.0, 60.0 / bpm * 8 if bpm else 4.0, duration_s / 2000)
        for kind, field in (("beat", "beat_times"), ("onset", "onset_times")):
            raw_events = features.get(field) or []
            if not isinstance(raw_events, list):
                continue
            last_event = -interval
            for value in raw_events[:100000]:
                stamp = _number(value, -1)
                if start < stamp < end and stamp - last_event >= interval:
                    last_event = stamp
                    event_times.add(stamp)
                    markers.append(point(scene_id, kind, stamp, f"Analysis {kind}; spacing filtered to avoid excessive keys", label=kind.title()))
        stamp = start + interval
        while stamp < end:
            event_times.add(stamp)
            stamp += interval
        for stamp in sorted(event_times):
            sample = energy[min(len(energy) - 1, int(stamp / duration_s * len(energy)))] / peak if peak > 0 else 0.25
            alpha = 0.55 if sample > smoothed else 0.2
            smoothed += alpha * (sample - smoothed)
            intensity = round(max(0.0, min(1.0, smoothed)), 4)
            reason = "Scene boundary" if stamp in (start, end) else ("Smoothed analysis energy at phrase interval" if peak > 0 else "Restrained motion between scene boundaries")
            motion.append(point(scene_id, "motion", stamp, reason, motion_score=intensity,
                                strength=round(0.25 + 0.25 * intensity, 4), anchor_strength=0.15,
                                cfg=6.0, steps=15, subject_motion=intensity, environment_motion=round(intensity * 0.25, 4)))
            camera.append(point(scene_id, "camera", stamp, reason, zoom=round(1 + 0.025 * intensity, 5),
                                pan_x=0.0, pan_y=0.0, rotation_deg=0.0, intent=str(scene.get("camera") or "")))
        markers.append(point(scene_id, "scene", start, "Scene boundary", label=f"Scene {index + 1}"))
    if not prompts:
        raise ValueError("No valid scene intervals remain in the schedule")
    for points in (prompts, anchors, camera, motion, markers):
        points.sort(key=lambda item: (item["t"], item["id"]))
    result = {"schema_version": SCHEMA_VERSION, "status": "draft", "project_id": project_id,
              "source_project_revision": project_revision, "source_analysis_revision": _digest(analysis),
              "source_plan_revision": _digest([{k: v for k, v in scene.items() if k != "schedule_draft"} for scene in scenes]),
              "variant_index": variant_index, "transport": {"fps": fps, "duration_s": duration_s, "bpm": bpm or None},
              "prompt_anchors": prompts, "image_anchors": anchors, "camera_keys": camera,
              "motion_keys": motion, "markers": markers, "warnings": warnings,
              "provenance": {"compiler": "studio_planner_schedule", "version": SCHEMA_VERSION},
              "summary": {"scenes": len({item["source_id"] for item in prompts}), "prompt_anchors": len(prompts),
                          "image_anchors": len(anchors), "camera_keys": len(camera), "motion_keys": len(motion),
                          "markers": len(markers), "warnings": len(warnings)}}
    result["schedule_revision"] = _digest(result)
    return result


def attach_schedule_drafts(project, *, resulting_revision: int, variant_indices: set[int] | None = None) -> None:
    plan = project.meta.get("last_plan") or {}
    analysis = project.meta.get("analysis") or {}
    for index, variant in enumerate(plan.get("variants") or []):
        if not isinstance(variant, dict) or (variant_indices is not None and index not in variant_indices):
            continue
        duration = _number(analysis.get("duration_s") or (analysis.get("features") or {}).get("duration_s") or variant.get("duration_s") or plan.get("duration_s"))
        if not duration:
            duration = max((_number(scene.get("end_s")) for scene in variant.get("scenes", []) if isinstance(scene, dict)), default=0)
        fps, duration = project_transport(project.meta, duration)
        variant["schedule_draft"] = compile_schedule(project_id=project.id, project_revision=resulting_revision,
            variant_index=index, variant=variant, analysis=analysis, fps=fps, duration_s=duration)
        variant["schedule_draft"]["generated_at"] = time.time()
        for scene in variant.get("scenes") or []:
            if isinstance(scene, dict):
                scene["operational_prompt"] = operational_render_prompt_from_scene(scene)
    if plan.get("variants"):
        # All planner entry points publish to the same Workspace draft. Import
        # locally to keep the pure compiler usable without a domain cycle.
        from .director_workflow import prepare_workflow
        index = min(variant_indices) if variant_indices else 0
        prepare_workflow(project, lambda _: plan, resulting_revision=resulting_revision,
                         source="planner", variant_index=index)


def apply_schedule(timeline: dict, draft: dict) -> dict:
    result = deepcopy(timeline)
    tracks = result.setdefault("tracks", [])
    if not isinstance(tracks, list):
        raise ValueError("Timeline tracks must be an array")
    revision = draft["schedule_revision"]
    duration = draft["transport"]["duration_s"]
    fps = draft["transport"]["fps"]

    def owned_track(kind: str, clips: list[dict]):
        existing = next((track for track in tracks if isinstance(track, dict) and track.get("owner") == OWNER and track.get("type") == kind), None)
        if existing is not None and existing.get("locked"):
            return
        retained = [clip for clip in (existing or {}).get("clips", []) if clip.get("owner") != OWNER or clip.get("locked")]
        retained_ids = {clip.get("id") for clip in retained}
        updated = {**(existing or {}), "id": (existing or {}).get("id", f"planner:{kind}"), "name": f"Planner {kind}",
                   "owner": OWNER, "type": kind, "schedule_revision": revision,
                   "clips": retained + [clip for clip in clips if clip["id"] not in retained_ids]}
        if existing is None:
            tracks.append(updated)
        else:
            tracks[tracks.index(existing)] = updated

    owned_track("prompt", [{"id": item["id"], "owner": OWNER, "source_id": item["source_id"],
                             "start_s": item["t"], "end_s": item["end_s"], "data": {"prompt": item["prompt"], "negative_prompt": item["negative_prompt"]}}
                            for item in draft["prompt_anchors"] if item["boundary"] == "start"])
    owned_track("keyimage", [{"id": item["id"], "owner": OWNER, "source_id": item["source_id"],
                               "start_s": item["t"], "end_s": min(duration, item["t"] + 1 / fps), "data": deepcopy(item)} for item in draft["image_anchors"]])
    schedules = {name: ", ".join(f"{item['frame']}:({item[field]})" for item in draft["motion_keys"])
                 for name, field in (("strength_schedule", "strength"), ("cfg_scale_schedule", "cfg"), ("steps_schedule", "steps"))}
    owned_track("motion", [{"id": "planner:motion:envelope", "owner": OWNER, "start_s": 0, "end_s": duration,
                             "data": {**schedules, "keyframes": deepcopy(draft["motion_keys"])}}])
    camera = result.setdefault("camera", {})
    if not camera.get("locked"):
        preserved = [item for item in camera.get("keyframes", []) if item.get("owner") != OWNER or item.get("locked")]
        ids = {item.get("id") for item in preserved}
        camera["keyframes"] = sorted(preserved + [deepcopy(item) for item in draft["camera_keys"] if item["id"] not in ids], key=lambda item: _number(item.get("t")))
    preserved_markers = [item for item in result.get("markers", []) if item.get("owner") != OWNER or item.get("locked")]
    ids = {item.get("id") for item in preserved_markers}
    result["markers"] = sorted(preserved_markers + [deepcopy(item) for item in draft["markers"] if item["id"] not in ids], key=lambda item: _number(item.get("t")))
    result["duration_s"] = max(duration, _number(result.get("duration_s")))
    result["approved_schedule"] = {"schedule_revision": revision, "variant_index": draft["variant_index"],
                                   "source_project_revision": draft["source_project_revision"], "transport": deepcopy(draft["transport"])}
    return result
