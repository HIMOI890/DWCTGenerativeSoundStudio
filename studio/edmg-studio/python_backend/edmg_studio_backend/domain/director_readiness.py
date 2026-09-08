"""Hardware-aware Director and renderer resolution.

This module deliberately stops at admission.  It resolves the pipeline that a
Workspace request would use and reports every blocker before a model worker is
allowed to load weights.  A model being present in the cache is not treated as
proof that its renderer adapter or hardware profile is qualified.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DirectorMode = Literal["automatic", "fast", "quality", "maximum"]
RendererEngine = Literal["hunyuan_video15", "ltx_25", "external"]

STANDARD_DIRECTOR_MODEL_ID = "hf_qwen3_vl_8b_director"
HIGH_TIER_DIRECTOR_MODEL_ID = "hf_qwen3_vl_30b_director"
HUNYUAN_MODEL_ID = "hf_hunyuan_video15_internal"
LTX_MODEL_ID = "hf_ltx_25_distilled_internal"

DIRECTOR_MODES: tuple[str, ...] = ("automatic", "fast", "quality", "maximum")
RENDERER_ENGINES: tuple[str, ...] = ("hunyuan_video15", "ltx_25", "external")


class ReadinessSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["director", "renderer"]
    engine: str
    model_id: str
    label: str
    profile: str
    installed: bool
    adapter_ready: bool
    ready: bool
    reason: str


class DirectorReadiness(BaseModel):
    model_config = ConfigDict(extra="allow")

    version: Literal[1] = 1
    requested_mode: DirectorMode
    requested_engine: str = "automatic"
    resolved_mode: DirectorMode
    profile: str
    hardware_tier: str
    hardware: dict[str, Any] = Field(default_factory=dict)
    director: ReadinessSelection
    renderer: ReadinessSelection
    ready: bool
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)


def normalize_director_mode(value: str | None) -> DirectorMode:
    normalized = str(value or "automatic").strip().lower().replace("_", "-")
    aliases = {
        "auto": "automatic",
        "balanced": "automatic",
        "quality-first": "quality",
        "max": "maximum",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in DIRECTOR_MODES:
        raise ValueError(f"Unsupported Director mode: {value}")
    return normalized  # type: ignore[return-value]


def normalize_renderer_engine(value: str | None) -> str:
    normalized = str(value or "automatic").strip().lower().replace("-", "_")
    aliases = {
        "auto": "automatic",
        "automatic": "automatic",
        "hunyuan": "hunyuan_video15",
        "hunyuanvideo15": "hunyuan_video15",
        "hunyuan_video_15": "hunyuan_video15",
        "ltx": "ltx_25",
        "ltx25": "ltx_25",
        "ltx_2_5": "ltx_25",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"automatic", *RENDERER_ENGINES}:
        raise ValueError(f"Unsupported renderer engine: {value}")
    return normalized


def _number(hardware: dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        value = float(hardware.get(key) or default)
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def hardware_tier(hardware: dict[str, Any]) -> str:
    """Classify hardware using capability ranges, never a GPU name."""

    vram = _number(hardware, "vram_gb")
    ram = _number(hardware, "ram_gb")
    backend = str(hardware.get("backend") or "cpu").lower()
    if backend == "cuda" and vram >= 40:
        return "ultra"
    if vram >= 24 or ram >= 48:
        return "high"
    if vram >= 10 or ram >= 24:
        return "mid"
    return "low"


def _model_installed(installed_models: dict[str, Any], model_id: str) -> bool:
    value = installed_models.get(model_id, False)
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "missing", "none"}
    return bool(value)


def _hardware_summary(hardware: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "backend",
        "device",
        "device_name",
        "gpu_vendor",
        "vram_gb",
        "ram_gb",
        "cpu_threads",
        "cuda_runtime_ready",
        "directml_runtime_ready",
    )
    return {key: hardware[key] for key in keep if key in hardware}


def _profile_for(mode: DirectorMode, tier: str, engine: str) -> str:
    if engine == "hunyuan_video15":
        if mode == "fast" or tier == "low":
            return "low_vram_chunked"
        if mode == "maximum" and tier in {"high", "ultra"}:
            return "maximum_quality"
        return "standard"
    if engine == "ltx_25":
        return "maximum_quality" if mode == "maximum" or tier in {"high", "ultra"} else "quality"
    return "provider_default"


def _renderer_for_mode(
    mode: DirectorMode,
    tier: str,
    *,
    installed_models: dict[str, Any],
    requested_engine: str,
) -> tuple[str, list[str], list[str]]:
    warnings: list[str] = []
    blockers: list[str] = []
    if requested_engine != "automatic":
        return requested_engine, warnings, blockers

    if mode in {"fast", "automatic"}:
        return "hunyuan_video15", warnings, blockers

    if tier in {"high", "ultra"} and _model_installed(installed_models, LTX_MODEL_ID):
        return "ltx_25", warnings, blockers
    if mode in {"quality", "maximum"}:
        warnings.append(
            "LTX-2.5 is not installed or not available for this hardware; using HunyuanVideo-1.5 as the internal fallback."
        )
    return "hunyuan_video15", warnings, blockers


def _director_for_mode(
    mode: DirectorMode,
    tier: str,
    *,
    installed_models: dict[str, Any],
) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if mode in {"quality", "maximum"} and tier in {"high", "ultra"}:
        if _model_installed(installed_models, HIGH_TIER_DIRECTOR_MODEL_ID):
            return HIGH_TIER_DIRECTOR_MODEL_ID, warnings
        warnings.append(
            "Qwen3-VL-30B-A3B is not installed; falling back to the standard Qwen3-VL-8B Director."
        )
    return STANDARD_DIRECTOR_MODEL_ID, warnings


def _selection(
    *,
    role: Literal["director", "renderer"],
    engine: str,
    model_id: str,
    label: str,
    profile: str,
    installed: bool,
    adapter_ready: bool,
    reason: str,
) -> ReadinessSelection:
    return ReadinessSelection(
        role=role,
        engine=engine,
        model_id=model_id,
        label=label,
        profile=profile,
        installed=installed,
        adapter_ready=adapter_ready,
        ready=installed and adapter_ready,
        reason=reason,
    )


def resolve_director_readiness(
    hardware: dict[str, Any] | None = None,
    *,
    mode: str | None = "automatic",
    engine: str | None = "automatic",
    installed_models: dict[str, Any] | None = None,
    allow_external: bool = False,
) -> DirectorReadiness:
    """Resolve a pipeline and report blockers without loading any model.

    ``installed_models`` is intentionally a plain mapping so the resolver can
    be used by the API, WinUI/Electron contract tests, and offline diagnostics.
    Renderer adapter readiness is separate from installation: the current
    catalog may contain a future model before its execution adapter is qualified.
    """

    normalized_mode = normalize_director_mode(mode)
    requested_engine = normalize_renderer_engine(engine)
    hw = dict(hardware or {})
    installed = dict(installed_models or {})
    tier = hardware_tier(hw)
    renderer_engine, renderer_warnings, blockers = _renderer_for_mode(
        normalized_mode,
        tier,
        installed_models=installed,
        requested_engine=requested_engine,
    )
    director_model, director_warnings = _director_for_mode(
        normalized_mode,
        tier,
        installed_models=installed,
    )
    warnings = renderer_warnings + director_warnings

    director_profile = (
        "standard_offload"
        if tier == "low"
        else ("high_quality" if tier in {"high", "ultra"} else "standard")
    )
    director_installed = _model_installed(installed, director_model)
    # Qwen3-VL-8B is the first qualified Director lane.  The 30B-A3B entry is
    # intentionally discovery-only until its pinned runtime and memory profile
    # pass the same qualification gate as the standard model.
    director_runtime_ready = director_model == STANDARD_DIRECTOR_MODEL_ID
    if not director_installed:
        director_reason = f"Install {director_model} in Models before loading the Director."
    elif director_runtime_ready:
        director_reason = "Installed and the Director adapter is available."
    else:
        director_reason = (
            "Qwen3-VL-30B-A3B is installed, but its local Director adapter is not "
            "release-qualified yet."
        )
    if tier == "low" and director_model == STANDARD_DIRECTOR_MODEL_ID:
        if _number(hw, "ram_gb") < 16:
            director_runtime_ready = False
            blockers.append(
                "Qwen3-VL-8B requires at least 16 GB system RAM for the low-memory Director profile."
            )
        elif _number(hw, "vram_gb") < 6 and str(hw.get("backend") or "cpu").lower() not in {
            "cpu",
            "directml",
        }:
            director_runtime_ready = False
            blockers.append(
                "The detected GPU does not meet the minimum VRAM target for the standard Director profile."
            )

    if not director_installed:
        blockers.append(director_reason)
    elif not director_runtime_ready:
        blockers.append(director_reason)

    renderer_model = {
        "hunyuan_video15": HUNYUAN_MODEL_ID,
        "ltx_25": LTX_MODEL_ID,
        "external": "external_provider",
    }[renderer_engine]
    renderer_label = {
        "hunyuan_video15": "HunyuanVideo-1.5",
        "ltx_25": "LTX-2.5 Distilled",
        "external": "External provider",
    }[renderer_engine]
    renderer_profile = _profile_for(normalized_mode, tier, renderer_engine)
    renderer_installed = renderer_engine == "external" or _model_installed(
        installed, renderer_model
    )
    # No Hunyuan/LTX execution adapter has passed the project qualification
    # gate yet.  Keeping this explicit prevents a downloaded snapshot from
    # being reported as a working temporal renderer.
    renderer_adapter_ready = renderer_engine == "external" and allow_external
    if renderer_engine == "external":
        renderer_reason = (
            "External provider policy is explicitly enabled."
            if allow_external
            else "Enable an external provider policy before using an external renderer."
        )
    else:
        renderer_reason = (
            f"Install {renderer_label} and qualify its local adapter before rendering."
        )
        if not renderer_installed:
            blockers.append(f"{renderer_label} is not installed in the local model catalog.")
        blockers.append(
            f"{renderer_label} local execution is not release-qualified yet; readiness remains blocked until its adapter passes validation."
        )
    if renderer_engine == "external" and not allow_external:
        blockers.append(renderer_reason)

    director_selection = _selection(
        role="director",
        engine="qwen3_vl",
        model_id=director_model,
        label="Qwen3-VL-30B-A3B"
        if director_model == HIGH_TIER_DIRECTOR_MODEL_ID
        else "Qwen3-VL-8B",
        profile=director_profile,
        installed=director_installed,
        adapter_ready=director_runtime_ready,
        reason=director_reason,
    )
    renderer_selection = _selection(
        role="renderer",
        engine=renderer_engine,
        model_id=renderer_model,
        label=renderer_label,
        profile=renderer_profile,
        installed=renderer_installed,
        adapter_ready=renderer_adapter_ready,
        reason=renderer_reason,
    )
    actions: list[str] = []
    if not director_installed:
        actions.append(
            "Open Models and install the resolved Director model, then refresh readiness."
        )
    elif not director_runtime_ready:
        actions.append(
            "Use the standard Qwen3-VL-8B Director lane until the high-tier adapter is qualified."
        )
    if renderer_engine != "external":
        actions.append(
            "Keep generation in draft/prepare mode until the selected local renderer adapter is qualified."
        )
    elif not allow_external:
        actions.append(
            "Enable external fallback explicitly in Advanced renderer settings if that is intended."
        )

    return DirectorReadiness(
        requested_mode=normalized_mode,
        requested_engine=requested_engine,
        resolved_mode=normalized_mode,
        profile=renderer_profile,
        hardware_tier=tier,
        hardware=_hardware_summary(hw),
        director=director_selection,
        renderer=renderer_selection,
        ready=director_selection.ready and renderer_selection.ready and not blockers,
        blockers=list(dict.fromkeys(blockers)),
        warnings=list(dict.fromkeys(warnings)),
        actions=list(dict.fromkeys(actions)),
    )
