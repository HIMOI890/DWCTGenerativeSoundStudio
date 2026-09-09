"""Reactive Lab projection of the shared Workspace schedule.

There is one set of camera/motion points. Lab exports are projections of those
points, not a second timing engine. Manual value refinements are stored by ID.
"""
from __future__ import annotations

import math
from copy import deepcopy

from .editor_commands import digest
from .project_time import ProjectClock

MOTION_FIELDS = ("motion_score", "strength", "anchor_strength", "cfg", "steps", "subject_motion", "environment_motion")
CAMERA_FIELDS = ("zoom", "pan_x", "pan_y", "rotation_deg")


def apply_overrides(schedule: dict, overrides: dict) -> dict:
    result = deepcopy(schedule)
    for collection in ("motion_keys", "camera_keys"):
        for point in result[collection]:
            if point["id"] in overrides:
                point.update(deepcopy(overrides[point["id"]]))
                point["manually_edited"] = True
    result.pop("schedule_revision", None)
    result["schedule_revision"] = digest(result)
    return result


def reactive_projection(draft, clock: ProjectClock) -> dict:
    schedule = draft.schedule
    cameras = {(key["source_id"], key["t"]): key for key in schedule["camera_keys"]}
    keyframes = []
    locked = {scene.scene_id for scene in draft.document.scenes if scene.renderer_hints.get("locked")}
    for motion in schedule["motion_keys"]:
        camera = cameras.get((motion["source_id"], motion["t"]), {})
        keyframes.append({**deepcopy(draft.reactive_extensions.get("keyframes", {}).get(motion["id"], {})),
                          **deepcopy(motion), **{key: camera[key] for key in CAMERA_FIELDS if key in camera},
                          "camera_id": camera.get("id"), "time": motion["t"],
                          "locked": motion["source_id"] in locked,
                          "sample": str(clock.samples(motion["t"]))})
    schedules = {}
    for output, field in (("strength", "strength"), ("cfg_scale", "cfg"), ("steps", "steps"),
                          ("zoom", "zoom"), ("translation_x", "pan_x"),
                          ("translation_y", "pan_y"), ("rotation_z", "rotation_deg")):
        # At adjacent scene boundaries the incoming scene owns the frame.
        by_frame = {key["frame"]: key.get(field, 0) for key in keyframes}
        schedules[output] = ", ".join(f"{frame}:({value})" for frame, value in sorted(by_frame.items()))
    markers = [{**deepcopy(key), "time": key["t"], "sample": str(clock.samples(key["t"]))}
               for key in schedule["markers"]]
    return {
        **deepcopy(draft.reactive_extensions.get("payload", {})),
        "metadata": {**deepcopy(draft.reactive_extensions.get("metadata", {})),
                     "source": "workspace", "workflow_draft_id": draft.draft_id,
                     "schedule_revision": schedule["schedule_revision"],
                     "source_analysis_revision": schedule["source_analysis_revision"],
                     "analysis_revision": draft.document.analysis_revision,
                     "source_plan_revision": schedule["source_plan_revision"],
                     "selected_variant_index": draft.variant_index,
                     "fps": float(clock.fps), **clock.to_dict(),
                     "duration_s": schedule["transport"]["duration_s"]},
        "keyframes": keyframes,
        "beat_markers": [key for key in markers if ":beat:" in key["id"]],
        "cue_events": markers,
        "sections": [{"id": scene.scene_id, "name": scene.intent,
                      "startTime": float(clock.seconds(scene.start_sample)),
                      "endTime": float(clock.seconds(scene.end_sample)),
                      "start_sample": scene.start_sample, "end_sample": scene.end_sample}
                     for scene in draft.document.scenes],
        "repair_suggestions": [], "schedules": schedules,
        "handoff_manifest": {"workflow_draft_id": draft.draft_id, "output_policy": "draft",
                             "schedule_revision": schedule["schedule_revision"]},
        "overwrite_motion_track": False, "overwrite_camera": False,
    }


def review_reactive(draft, payload: dict, clock: ProjectClock) -> dict:
    """Accept value edits; source IDs and timing remain tied to reviewed scenes."""
    expected = reactive_projection(draft, clock)
    if (payload.get("metadata") or {}).get("workflow_draft_id") != draft.draft_id:
        raise ValueError("Reactive Lab belongs to a different Workspace draft; reload before saving")
    supplied = payload.get("keyframes")
    if not isinstance(supplied, list) or len(supplied) != len(expected["keyframes"]):
        raise ValueError("Keep the shared keyframe set; edit scene timing in the Workspace")
    points = {point.get("id"): point for point in supplied if isinstance(point, dict)}
    if len(points) != len(supplied):
        raise ValueError("Reactive keyframes need unique source IDs")
    overrides = deepcopy(draft.reactive_overrides)
    draft.reactive_extensions["payload"] = {key: deepcopy(value) for key, value in payload.items() if key not in expected}
    draft.reactive_extensions["metadata"] = {key: deepcopy(value) for key, value in (payload.get("metadata") or {}).items()
                                            if key not in expected["metadata"] or key in draft.reactive_extensions.get("metadata", {})}
    point_extensions = draft.reactive_extensions.setdefault("keyframes", {})
    for before in expected["keyframes"]:
        after = points.get(before["id"])
        if after is None or any(after.get(key) != before.get(key) for key in
                                ("time", "t", "frame", "sample", "source_id", "camera_id", "locked")):
            raise ValueError("Keep keyframe source IDs and timing linked to the shared schedule")
        point_extensions[before["id"]] = {key: deepcopy(value) for key, value in after.items()
                                          if key not in before or key in point_extensions.get(before["id"], {})}
        for fields, point_id in ((MOTION_FIELDS, before["id"]), (CAMERA_FIELDS, before["camera_id"])):
            for field in fields:
                value = after.get(field)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError(f"Reactive {field} must be a finite number")
                lower, upper = ((1, 200) if field == "steps" else (0, 30) if field == "cfg" else
                                (0.01, 100) if field == "zoom" else (-360, 360) if field == "rotation_deg" else
                                (-10000, 10000) if field in ("pan_x", "pan_y") else (0, 1))
                if not lower <= value <= upper or (field == "steps" and value != int(value)):
                    raise ValueError(f"Reactive {field} must be within {lower}–{upper}")
                if value != before.get(field):
                    if before["locked"]:
                        raise ValueError("Unlock this scene before refining its reactive keyframes")
                    overrides.setdefault(point_id, {})[field] = value
    return overrides
