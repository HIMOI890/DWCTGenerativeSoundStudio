from __future__ import annotations

import json
from pathlib import Path

import pytest

from edmg_ai_service.providers.fallback import RuleBasedPlanner
from edmg_ai_service.providers.ollama import OllamaPlanner
from edmg_ai_service.providers.openai_compat import OpenAICompatPlanner
from edmg_ai_service.providers.storyboard_contract import storyboard_system_prompt
from edmg_ai_service.schemas import PlanRequest
from edmg_studio_backend import app as app_module
from edmg_studio_backend.services import internal_video
from edmg_studio_backend.services.deforum_normalize import (
    CLIP_SAFE_RENDER_PROMPT_MAX_WORDS,
    build_deforum_render_context,
    render_prompt_from_scene,
)
from edmg_studio_backend.services.internal_video import InternalVideoSettings


def test_variant_save_reload_and_reorder_preserve_authored_contract(tmp_path, monkeypatch):
    from edmg_studio_backend.store.projects import ProjectStore

    project_store = ProjectStore(tmp_path / "projects")
    monkeypatch.setattr(app_module, "store", project_store)
    project = project_store.create("Continuity persistence")
    scenes = [
        {
            "start_s": index * 4, "end_s": (index + 1) * 4,
            "setting": f"platform {index}", "shot_type": "close tracking",
            "character_lock": "driver with a red scarf", "style_lock": "silver grain",
            "start_state": f"start {index}", "end_state": f"end {index}. Facing right",
            "prompt": f"Authored action. Setting: old platform. Start state: stale {index}. End state: old end.",
        } for index in range(3)
    ]
    project.meta["last_plan"] = {"variants": [{"scenes": scenes}], "duration_s": 12}
    project_store.save(project)
    request_type = app_module.StoryboardVariantUpdateRequest
    app_module.update_plan_variant(project.id, request_type(variant_index=0, scenes=scenes))
    saved = ProjectStore(tmp_path / "projects").get(project.id).meta["last_plan"]["variants"][0]["scenes"]
    for index, scene in enumerate(saved):
        for field in ("setting", "shot_type", "character_lock", "style_lock", "end_state"):
            assert scene[field] == scenes[index][field]
        assert scene["start_state"] == (saved[index - 1]["end_state"] if index else "start 0")
        assert "stale" not in scene["prompt"]
        assert "old platform" not in scene["prompt"]
    reordered = [dict(saved[index], start_s=slot * 4, end_s=(slot + 1) * 4)
                 for slot, index in enumerate((2, 0, 1))]
    app_module.update_plan_variant(project.id, request_type(variant_index=0, scenes=reordered))
    reloaded = ProjectStore(tmp_path / "projects").get(project.id).meta["last_plan"]["variants"][0]["scenes"]
    assert [scene["setting"] for scene in reloaded] == ["platform 2", "platform 0", "platform 1"]
    for index, scene in enumerate(reloaded):
        assert scene["storyboard"]["start_state"] == scene["start_state"]
        assert f"start state: {scene['start_state']}." in scene["prompt"]
        if index:
            assert scene["start_state"] == reloaded[index - 1]["end_state"]


def test_rule_based_planner_outputs_motion_and_continuity_storyboard_contract() -> None:
    response = RuleBasedPlanner().plan(
        PlanRequest(
            title="Copper Orchid",
            duration_s=16.0,
            bpm=128.0,
            tags=["automaton", "orchids", "glasshouse"],
            user_notes="Keep one copper robot recognizable throughout.",
            num_variants=1,
            max_scenes=4,
        )
    )

    scenes = response.variants[0].scenes
    assert len(scenes) >= 3
    assert scenes[0].start_s == 0.0
    assert scenes[-1].end_s == 16.0
    assert all(
        left.end_s == right.start_s
        for left, right in zip(scenes, scenes[1:], strict=False)
    )
    assert all(scene.subject for scene in scenes)
    assert all(scene.action for scene in scenes)
    assert all(scene.camera for scene in scenes)
    assert all(scene.motion for scene in scenes)
    assert all(scene.environment_motion for scene in scenes)
    assert all(scene.continuity for scene in scenes)
    assert all(scene.transition for scene in scenes)
    assert all(scene.setting for scene in scenes)
    assert all(scene.shot_type for scene in scenes)
    assert all(scene.character_lock for scene in scenes)
    assert all(scene.style_lock for scene in scenes)
    assert all(scene.start_state for scene in scenes)
    assert all(scene.end_state for scene in scenes)
    assert len({scene.subject for scene in scenes}) == 1
    assert len({scene.character_lock for scene in scenes}) == 1
    assert len({scene.style_lock for scene in scenes}) == 1
    assert all(scene.subject == scene.character_lock for scene in scenes)
    assert "one copper robot" in str(scenes[0].character_lock).lower()
    assert all(
        current.end_state == following.start_state
        for current, following in zip(scenes, scenes[1:], strict=False)
    )
    assert "preserve identity" in str(scenes[1].continuity).lower()
    assert "frozen pose" in str(scenes[0].negative_prompt).lower()
    assert "style drift" in str(scenes[0].negative_prompt).lower()
    assert "location jump" in str(scenes[0].negative_prompt).lower()
    assert "storyboard sheet" not in scenes[0].prompt.lower()


def test_rule_based_planner_honors_explicit_subject_setting_action_and_scene_cap() -> None:
    response = RuleBasedPlanner().plan(
        PlanRequest(
            title="The Last Glasshouse Crossing",
            duration_s=4.0,
            user_notes=(
                "One copper automaton with one blue glass eye crosses one flooded glasshouse from "
                "east to west. Preserve the same automaton and white orchid throughout."
            ),
            style_prefs="cinematic textured nocturnal realism, copper and moonlit blue",
            num_variants=1,
            max_scenes=2,
        )
    )

    scenes = response.variants[0].scenes
    assert len(scenes) == 2
    assert "copper automaton with one blue glass eye" in str(scenes[0].character_lock).lower()
    assert "white orchid" in str(scenes[0].character_lock).lower()
    assert "one flooded glasshouse" in str(scenes[0].setting).lower()
    assert "east entry" in str(scenes[0].setting).lower()
    assert "west exit" in str(scenes[1].setting).lower()
    assert "begins crossing" in str(scenes[0].action).lower()
    assert "completes crossing" in str(scenes[1].action).lower()
    assert "copper" in str(scenes[0].style_lock).lower()
    assert "moonlit blue" in str(scenes[0].style_lock).lower()
    assert scenes[1].start_state == scenes[0].end_state
    assert "resolved final image" in str(scenes[1].transition).lower()
    assert "white orchid" in scenes[0].prompt.lower()

    enriched = app_module._enrich_normalized_plan(response.model_dump(), {})
    prompt_pack = enriched["variants"][0]["scenes"][0]["prompt_pack"].lower()
    assert prompt_pack.count("character lock:") == 1
    assert prompt_pack.count("style lock:") == 1
    assert prompt_pack.count("start state:") == 1
    assert prompt_pack.count("end state:") == 1
    render_prompt = enriched["variants"][0]["scenes"][0]["render_prompt"]
    assert len(render_prompt.split()) <= CLIP_SAFE_RENDER_PROMPT_MAX_WORDS
    assert render_prompt.lower().startswith("one copper automaton")
    assert "white orchid" in render_prompt.lower()
    assert "single prominent subject" in render_prompt.lower()
    assert "begins crossing" in render_prompt.lower()
    assert "measured head and hand movement" in render_prompt.lower()
    assert "wide establishing shot" in render_prompt.lower()
    assert "slow push in" in render_prompt.lower()
    assert "flooded glasshouse" in render_prompt.lower()
    assert "nocturnal realism" in render_prompt.lower()
    assert "character lock:" not in render_prompt.lower()
    assert "start state:" not in render_prompt.lower()
    assert render_prompt_from_scene(enriched["variants"][0]["scenes"][0]) == render_prompt


def test_ai_storyboard_contract_requires_filmable_temporal_fields() -> None:
    contract = storyboard_system_prompt().lower()

    assert "strict json" in contract
    assert "subject" in contract
    assert "action" in contract
    assert "environment_motion" in contract
    assert "continuity" in contract
    assert "transition" in contract
    assert "setting" in contract
    assert "shot_type" in contract
    assert "character_lock" in contract
    assert "style_lock" in contract
    assert "start_state" in contract
    assert "end_state" in contract
    assert "repeat it verbatim in every scene" in contract
    assert "preceding end_state" in contract
    assert "never a collage" in contract
    assert "frozen poses" in contract


class _FakeProviderResponse:
    def __init__(self, payload: dict[str, object]):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


def _provider_storyboard_payload() -> dict[str, object]:
    character_lock = (
        "the same copper automaton with one blue glass eye, a narrow silhouette, "
        "weathered copper plating, and a white orchid in its right hand"
    )
    style_lock = (
        "moving oil-paint texture, copper and moonlit-blue palette, soft glasshouse light, "
        "restrained contrast, and one consistent 35mm lens family"
    )
    first_end_state = (
        "the automaton reaches the center window facing left-to-right, orchid raised at shoulder "
        "height, with the camera settled at a medium profile and the arched doorway behind it"
    )
    return {
        "variants": [
            {
                "name": "Glasshouse Passage",
                "logline": "One continuous crossing through a rain-soaked glasshouse.",
                "mood": "restrained and uncanny",
                "visual_motifs": ["white orchids", "rain on glass"],
                "color_palette": ["copper", "moonlit blue"],
                "scenes": [
                    {
                        "start_s": 0.0,
                        "end_s": 4.0,
                        "prompt": "The automaton crosses toward the center window.",
                        "negative_prompt": "identity drift, style drift, frozen pose",
                        "setting": "the east aisle of one rain-soaked glasshouse",
                        "shot_type": "medium profile tracking shot",
                        "character_lock": character_lock,
                        "style_lock": style_lock,
                        "start_state": (
                            "the automaton enters from screen left facing left-to-right, orchid held "
                            "at waist height, with the camera settled beside the arched doorway"
                        ),
                        "end_state": first_end_state,
                        "subject": character_lock,
                        "action": "walks to the center window and raises the orchid",
                        "camera": "one measured left-to-right tracking move",
                        "motion": "continuous walking, arm lift, and head turn",
                        "environment_motion": "rain travels down the glass and orchid leaves sway",
                        "continuity": "preserve identity, wardrobe, prop, axis, and landmarks",
                        "transition": "continue the tracking move across the window mullion",
                        "notes": "opening beat",
                    },
                    {
                        "start_s": 4.0,
                        "end_s": 8.0,
                        "prompt": "The automaton continues past the center window.",
                        "negative_prompt": "identity drift, style drift, frozen pose",
                        "setting": "the center aisle of the same rain-soaked glasshouse",
                        "shot_type": "medium-to-close profile tracking shot",
                        "character_lock": character_lock,
                        "style_lock": style_lock,
                        "start_state": first_end_state,
                        "end_state": (
                            "the automaton stops beside the west door facing left-to-right, orchid "
                            "touching the glass, with the camera settled in a close profile"
                        ),
                        "subject": character_lock,
                        "action": "continues to the west door and presses the orchid to the glass",
                        "camera": "the same tracking move easing into a close profile",
                        "motion": "continuous walking, reach, and natural breathing",
                        "environment_motion": "rain and leaves continue moving through the handoff",
                        "continuity": "preserve identity, wardrobe, prop, axis, and landmarks",
                        "transition": "resolve on the completed reach",
                        "notes": "resolution beat",
                    },
                ],
            }
        ]
    }


@pytest.mark.parametrize("provider_kind", ["ollama", "openai_compat"])
def test_remote_provider_schema_preserves_storyboard_locks_and_state_handoffs(
    monkeypatch: pytest.MonkeyPatch,
    provider_kind: str,
) -> None:
    generated = _provider_storyboard_payload()
    captured: dict[str, object] = {}

    def fake_post(*_args: object, **kwargs: object) -> _FakeProviderResponse:
        captured.update(kwargs)
        if provider_kind == "ollama":
            return _FakeProviderResponse({"response": json.dumps(generated)})
        return _FakeProviderResponse(
            {"choices": [{"message": {"content": json.dumps(generated)}}]}
        )

    if provider_kind == "ollama":
        monkeypatch.setattr("edmg_ai_service.providers.ollama.requests.post", fake_post)
        planner = OllamaPlanner("http://127.0.0.1:11434", "test-model")
    else:
        monkeypatch.setattr("edmg_ai_service.providers.openai_compat.requests.post", fake_post)
        planner = OpenAICompatPlanner("http://127.0.0.1:1234/v1", None, "test-model")

    response = planner.plan(
        PlanRequest(title="Copper Orchid", duration_s=8.0, num_variants=1, max_scenes=2)
    )

    assert response.provider == provider_kind
    scenes = response.variants[0].scenes
    assert len(scenes) == 2
    assert scenes[0].character_lock == scenes[1].character_lock
    assert scenes[0].style_lock == scenes[1].style_lock
    assert scenes[1].start_state == scenes[0].end_state
    assert scenes[0].setting == "the east aisle of one rain-soaked glasshouse"
    assert scenes[1].shot_type == "medium-to-close profile tracking shot"

    request_json = captured["json"]
    assert isinstance(request_json, dict)
    if provider_kind == "ollama":
        provider_instruction = str(request_json["prompt"])
    else:
        messages = request_json["messages"]
        assert isinstance(messages, list)
        provider_instruction = str(messages[1]["content"])
    instruction = provider_instruction.lower()
    assert "verbatim character_lock and style_lock" in instruction
    assert "preceding scene's end_state verbatim as its start_state" in instruction


def test_plan_enrichment_preserves_authored_motion_and_adds_render_contract() -> None:
    plan = {
        "variants": [
            {
                "visual_motifs": ["copper automaton"],
                "scenes": [
                    {
                        "start_s": 0.0,
                        "end_s": 4.0,
                        "prompt": "A copper automaton beside white orchids.",
                        "subject": "the same copper automaton with one blue glass eye",
                        "action": "turns its head and raises its right hand",
                        "camera": "slow left-to-right tracking move",
                        "motion": "progressive head, shoulder, and hand movement",
                        "environment_motion": "orchid petals sway and rain moves down the glass",
                        "continuity": "preserve the blue eye, copper plating, orchids, and screen direction",
                        "transition": "match-action continuation",
                    }
                ],
            }
        ]
    }

    enriched = app_module._enrich_normalized_plan(plan, {"tags": ["glasshouse"]})
    scene = enriched["variants"][0]["scenes"][0]

    assert scene["storyboard"]["shot_action"] == "turns its head and raises its right hand"
    assert scene["storyboard"]["camera_move"] == "slow left-to-right tracking move"
    assert scene["storyboard"]["environment_motion"] == (
        "orchid petals sway and rain moves down the glass"
    )
    assert scene["continuity_note"].startswith("preserve the blue eye")
    assert "visible action:" in scene["prompt_pack"].lower()
    assert "environment motion:" in scene["prompt_pack"].lower()
    assert "frozen pose" in scene["negative_prompt"].lower()


def test_plan_enrichment_preserves_structured_legacy_continuity_metadata() -> None:
    structured_continuity = {"subject": "performer", "wardrobe": "silver coat"}
    plan = {
        "variants": [
            {
                "scenes": [
                    {
                        "start_s": 0.0,
                        "end_s": 4.0,
                        "prompt": "A performer enters the light.",
                        "continuity": structured_continuity,
                        "continuity_note": "preserve the performer and silver coat",
                    }
                ]
            }
        ]
    }

    enriched = app_module._enrich_normalized_plan(plan, {})
    scene = enriched["variants"][0]["scenes"][0]

    assert scene["continuity"] == structured_continuity
    assert scene["continuity_note"] == "preserve the performer and silver coat"
    assert scene["storyboard"]["continuity"] == (
        "preserve the performer and silver coat"
    )


def test_plan_enrichment_enforces_locks_and_exact_boundary_handoffs() -> None:
    first_end_state = (
        "the copper automaton reaches the center window facing left-to-right, orchid at shoulder "
        "height, camera settled in a medium profile"
    )
    plan = {
        "variants": [
            {
                "mood": "restrained nocturnal mystery",
                "color_palette": ["copper", "moonlit blue"],
                "scenes": [
                    {
                        "start_s": 0.0,
                        "end_s": 4.0,
                        "prompt": "The copper automaton crosses the glasshouse.",
                        "character_lock": "one copper automaton with one blue glass eye and a white orchid",
                        "style_lock": "textured cinematic finish with copper and moonlit-blue light",
                        "setting": "the east aisle of one rain-soaked glasshouse",
                        "shot_type": "medium profile tracking shot",
                        "start_state": "the automaton enters from screen left with the orchid at waist height",
                        "end_state": first_end_state,
                        "action": "walks to the center window and raises the orchid",
                        "camera": "one measured left-to-right tracking move",
                    },
                    {
                        "start_s": 4.0,
                        "end_s": 8.0,
                        "prompt": "The automaton continues toward the west door.",
                        "character_lock": "a conflicting replacement character",
                        "style_lock": "a conflicting replacement style",
                        "setting": "the west aisle of the same rain-soaked glasshouse",
                        "start_state": "a conflicting reset pose",
                        "end_state": "the automaton reaches the west door and rests the orchid on the glass",
                        "action": "continues to the west door and reaches toward the glass",
                    },
                ],
            }
        ]
    }

    enriched = app_module._enrich_normalized_plan(plan, {})
    first, second = enriched["variants"][0]["scenes"]

    assert first["character_lock"] == second["character_lock"]
    assert first["style_lock"] == second["style_lock"]
    assert second["start_state"] == first_end_state == first["end_state"]
    assert second["storyboard"]["authored_start_state"] == "a conflicting reset pose"
    assert second["storyboard"]["character_lock"] == first["character_lock"]
    assert "setting:" in second["prompt_pack"].lower()
    assert "start state:" in second["prompt_pack"].lower()
    assert "camera teleport" in second["negative_prompt"].lower()
    assert "conflicting camera moves" in second["negative_prompt"].lower()
    assert len(first["render_prompt"].split()) <= CLIP_SAFE_RENDER_PROMPT_MAX_WORDS
    assert len(second["render_prompt"].split()) <= CLIP_SAFE_RENDER_PROMPT_MAX_WORDS


def test_plan_resave_preserves_authored_prompt_and_refreshes_derived_prompt_idempotently() -> None:
    authored_prompt = "A copper automaton carries a white orchid through one flooded glasshouse."
    raw_scenes = [
        {
            "start_s": 0.0,
            "end_s": 2.0,
            "prompt": authored_prompt,
            "render_prompt": "stale derived model prompt",
            "setting": "one flooded glasshouse",
            "shot_type": "medium tracking shot",
            "character_lock": "one copper automaton with one blue eye carrying a white orchid",
            "style_lock": "textured nocturnal realism with copper and moonlit blue",
            "start_state": "the automaton enters from screen left",
            "end_state": "the automaton reaches the center window facing screen right",
            "action": "walks toward the center window",
            "motion": "purposeful walking and a head turn",
        },
        {
            "start_s": 2.0,
            "end_s": 4.0,
            "prompt": "The same automaton continues toward the west exit.",
            "render_prompt": "another stale derived prompt",
            "setting": "the west side of the same flooded glasshouse",
            "shot_type": "profile medium shot",
            "character_lock": "conflicting character",
            "style_lock": "conflicting style",
            "start_state": "a conflicting reset pose",
            "end_state": "the automaton reaches the west exit facing screen right",
            "action": "continues toward the west exit",
            "motion": "walking with visible stride changes",
        },
    ]

    normalized_scenes = app_module._normalize_plan_scene_list(
        raw_scenes,
        duration_s=4.0,
        max_scenes=2,
    )
    assert normalized_scenes[0]["prompt"] == authored_prompt

    once = app_module._enrich_normalized_plan(
        {"variants": [{"mood": "nocturnal", "scenes": normalized_scenes}]},
        {},
    )
    twice = app_module._enrich_normalized_plan(once, {})
    first, second = twice["variants"][0]["scenes"]

    assert authored_prompt in first["prompt_pack"]
    assert "stale derived model prompt" not in first["render_prompt"]
    assert first["render_prompt"].startswith("one copper automaton")
    assert second["character_lock"] == first["character_lock"]
    assert second["style_lock"] == first["style_lock"]
    assert second["start_state"] == first["end_state"]
    assert first["prompt_pack"].lower().count("section role ") == 1
    assert first["prompt_pack"].lower().count("staging ") == 1
    assert first["prompt_pack"].lower().count("palette emphasis ") == 1


def test_internal_scene_prompt_timeline_rebuilds_legacy_stale_render_prompt() -> None:
    stale = " ".join(["obsolete"] * 180)
    scene = {
        "start_s": 0.0,
        "end_s": 4.0,
        "prompt": "The copper automaton carries a white orchid through the glasshouse.",
        "render_prompt": stale,
        "character_lock": "one copper automaton with one blue eye carrying one white orchid",
        "setting": "one flooded glasshouse",
        "shot_type": "wide tracking shot",
        "action": "walks from the east entry toward the west exit",
        "camera": "slow lateral track",
        "motion": "purposeful walking and a head turn",
        "environment_motion": "rain and reflected light move across the glass",
        "style_lock": "textured nocturnal realism in copper and moonlit blue",
    }

    context = build_deforum_render_context(
        scenes=[scene],
        timeline=None,
        variant=None,
        fps=2,
        default_negative_prompt="",
    )

    assert len(context.prompts) == 1
    frame, prompt = context.prompts[0]
    assert frame == 0
    assert prompt != stale
    assert len(prompt.split()) <= CLIP_SAFE_RENDER_PROMPT_MAX_WORDS
    assert prompt.lower().startswith("one copper automaton")
    assert "white orchid" in prompt.lower()
    assert "purposeful walking" in prompt.lower()


def test_storyboard_motion_plan_exposes_shot_phase_and_identity_contract() -> None:
    start_state = "the automaton enters from screen left with the orchid at waist height"
    end_state = (
        "the automaton reaches the center window facing left-to-right with the orchid raised at shoulder height"
    )
    scene = {
        "start_s": 0.0,
        "end_s": 8.0,
        "prompt": "Copper automaton in a rain-soaked glasshouse.",
        "setting": "the east aisle of one rain-soaked glasshouse",
        "shot_type": "medium profile tracking shot",
        "character_lock": "the same copper automaton with one blue glass eye",
        "style_lock": "textured cinematic finish with copper and moonlit-blue light",
        "start_state": start_state,
        "end_state": end_state,
        "subject": "the same copper automaton with one blue glass eye",
        "action": "walks past the orchids and reaches toward the window",
        "camera": "measured left-to-right tracking move",
        "motion": "walking, head turn, and reaching gesture",
        "environment_motion": "petals sway and rain travels down the glass",
        "continuity": "preserve the blue eye, copper plating, and left-to-right screen direction",
        "transition": "match dissolve",
    }
    settings = InternalVideoSettings(
        motion_strategy="storyboard_full_motion",
        storyboard_shot_max_s=4.0,
        keyframe_continuity_mode="project",
        temporal_mode="video_model",
        video_model_motion_score_mode="manual",
        video_model_manual_motion_score=5,
        video_model_scene_motion="scene",
        video_model_prompt_refine=True,
    )

    plan = internal_video.describe_storyboard_motion_plan(
        scenes=[scene],
        timeline=None,
        settings=settings,
        duration_s=8.0,
    )

    assert plan is not None
    assert plan["shot_count"] == 2
    first, second = plan["shots"]
    assert first["shot_phase"] == "establish"
    assert second["shot_phase"] == "resolve"
    assert first["subject_anchor"] == "the same copper automaton with one blue glass eye"
    assert first["setting"] == "the east aisle of one rain-soaked glasshouse"
    assert first["shot_type"] == "medium profile tracking shot"
    assert first["character_lock"] == scene["character_lock"]
    assert first["style_lock"] == scene["style_lock"]
    assert first["start_state"] == second["start_state"] == start_state
    assert first["end_state"] == second["end_state"] == end_state
    assert first["shot_action"] == "walks past the orchids and reaches toward the window"
    assert "copper automaton" in first["prompt"].lower()
    assert "same identity and props" in first["prompt"].lower()
    assert "walks past the orchids" in first["prompt"].lower()
    assert "walking, head turn" in first["prompt"].lower()
    assert "petals sway" in first["prompt"].lower()
    assert "rain" in first["prompt"].lower()
    assert first["transition"] == "start from generated visual anchor"
    assert second["transition"] == "technical_continue"
    assert len(first["prompt"].split()) <= CLIP_SAFE_RENDER_PROMPT_MAX_WORDS
    assert len(second["prompt"].split()) <= CLIP_SAFE_RENDER_PROMPT_MAX_WORDS


def test_generation_metadata_keeps_model_prompt_and_full_storyboard_source(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "metadata-test" / "outputs" / "frame.png"
    storyboard = {
        "character_lock": "one copper automaton with one blue eye and one white orchid",
        "start_state": "the automaton enters from screen left",
        "end_state": "the automaton reaches the west door",
    }
    metadata = app_module._build_generation_metadata(
        project_id="metadata-test",
        job_id="job-test",
        output_path=output_path,
        payload={
            "variant_index": 0,
            "scene_index": 0,
            "prompt": "Character lock: one copper automaton. Visible action: crosses the aisle.",
            "source_prompt": "The complete authored storyboard prompt with every continuity clause.",
            "storyboard": storyboard,
            "seed": 424242,
            "steps": 4,
            "cfg": 0.0,
            "width": 512,
            "height": 512,
        },
        workflow_family="txt2img",
        checkpoint="hf_flux1_schnell_internal",
        backend="diffusers_sequential_offload",
        engine="internal",
        model_family="flux",
        device="cuda",
    )

    assert metadata["prompt"].startswith("Character lock:")
    assert metadata["source_prompt"].startswith("The complete authored")
    assert metadata["storyboard_contract"] == storyboard
    assert metadata["base_model"]["model_id"] is None
    assert metadata["provenance"]["device"] == "cuda"
