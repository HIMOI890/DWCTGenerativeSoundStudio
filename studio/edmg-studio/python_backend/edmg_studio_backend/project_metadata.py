from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

SERVER_OWNED_METADATA_FIELDS = frozenset(
    {
        "active_parseq_manifest",
        "id", "project_id", "revision", "created_at", "updated_at", "schema_version",
        "filename", "path", "artifacts",
        "analysis",
        "audio",
        "conductor_promotions",
        "exports",
        "internal_render_history",
        "jobs",
        "last_conductor_intent",
        "last_conductor_plan",
        "last_creative_direction",
        "last_internal_render",
        "last_performer_plan",
        "last_plan",
        "last_planner_lab",
        "last_reactive_lab",
        "last_timeline_render",
        "last_timeline_render_request",
        "outputs",
        "render_recipe_graph",
    }
)


class MetadataValidationError(ValueError):
    pass


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default)).strip()))
    except Exception:
        return default


def is_server_owned_metadata_field(key: str) -> bool:
    normalized = key.casefold()
    return normalized in SERVER_OWNED_METADATA_FIELDS or normalized.endswith(("_path", "_dir", "_filename"))


@dataclass(frozen=True)
class MetadataPatchLimits:
    max_depth: int
    max_items: int
    max_bytes: int

    @classmethod
    def from_env(cls) -> "MetadataPatchLimits":
        return cls(
            max_depth=_env_int("EDMG_PROJECT_METADATA_PATCH_MAX_DEPTH", 12),
            max_items=_env_int("EDMG_PROJECT_METADATA_PATCH_MAX_ITEMS", 10_000),
            max_bytes=_env_int("EDMG_PROJECT_METADATA_PATCH_MAX_BYTES", 1_048_576),
        )


def _walk_metadata(
    value: Any,
    *,
    depth: int,
    limits: MetadataPatchLimits,
    state: dict[str, int],
    label: str,
) -> None:
    if depth > limits.max_depth:
        raise MetadataValidationError(
            f"{label} exceeds maximum nesting depth of {limits.max_depth}."
        )
    if isinstance(value, dict):
        for key, item in value.items():
            state["items"] += 1
            if state["items"] > limits.max_items:
                raise MetadataValidationError(
                    f"{label} exceeds maximum item count of {limits.max_items}."
                )
            if not isinstance(key, str):
                raise MetadataValidationError(f"{label} keys must be strings.")
            _walk_metadata(
                item,
                depth=depth + 1,
                limits=limits,
                state=state,
                label=label,
            )
        return
    if isinstance(value, list):
        for item in value:
            state["items"] += 1
            if state["items"] > limits.max_items:
                raise MetadataValidationError(
                    f"{label} exceeds maximum item count of {limits.max_items}."
                )
            _walk_metadata(
                item,
                depth=depth + 1,
                limits=limits,
                state=state,
                label=label,
            )
        return
    if value is None or isinstance(value, (bool, int, float, str)):
        return
    raise MetadataValidationError(f"{label} must contain only JSON-compatible values.")


def validate_metadata_patch(
    patch: dict[str, Any],
    *,
    limits: MetadataPatchLimits | None = None,
    label: str = "Metadata patch",
) -> dict[str, Any]:
    if not isinstance(patch, dict):
        raise MetadataValidationError(f"{label} must be an object.")
    resolved_limits = limits or MetadataPatchLimits.from_env()
    try:
        encoded = json.dumps(patch, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise MetadataValidationError(f"{label} must be JSON-serializable.") from exc
    if len(encoded.encode("utf-8")) > resolved_limits.max_bytes:
        raise MetadataValidationError(
            f"{label} exceeds maximum encoded size of {resolved_limits.max_bytes} bytes."
        )
    _walk_metadata(
        patch,
        depth=1,
        limits=resolved_limits,
        state={"items": 0},
        label=label,
    )
    return patch


def merge_metadata_values(current: Any, patch: Any) -> Any:
    """Merge JSON objects recursively; arrays and scalar values replace explicitly."""
    if not isinstance(current, dict) or not isinstance(patch, dict):
        return deepcopy(patch)
    merged = deepcopy(current)
    for key, value in patch.items():
        merged[key] = merge_metadata_values(merged.get(key), value)
    return merged


def extract_recoverable_metadata(meta: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(meta, dict):
        return {}
    return {
        str(key): deepcopy(value)
        for key, value in meta.items()
        if not is_server_owned_metadata_field(str(key))
    }


def recoverable_metadata_from_patch(
    current_meta: dict[str, Any] | None,
    patch: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    base = extract_recoverable_metadata(current_meta)
    if not isinstance(patch, dict):
        return base, []
    ignored = sorted(
        {str(key) for key in patch.keys() if is_server_owned_metadata_field(str(key))}
    )
    for key, value in patch.items():
        key_str = str(key)
        if is_server_owned_metadata_field(key_str):
            continue
        base[key_str] = merge_metadata_values(base.get(key_str), value)
    return base, ignored


def merge_recovery_metadata(
    current_meta: dict[str, Any] | None,
    recovered_meta: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    merged: dict[str, Any] = deepcopy(current_meta or {})
    current = current_meta if isinstance(current_meta, dict) else {}
    recovered = recovered_meta if isinstance(recovered_meta, dict) else {}
    for key, value in current.items():
        if is_server_owned_metadata_field(str(key)):
            merged[str(key)] = deepcopy(value)
    ignored = sorted(
        {str(key) for key in recovered.keys() if is_server_owned_metadata_field(str(key))}
    )
    for key, value in recovered.items():
        key_str = str(key)
        if is_server_owned_metadata_field(key_str):
            continue
        merged[key_str] = merge_metadata_values(merged.get(key_str), value)
    return merged, ignored
