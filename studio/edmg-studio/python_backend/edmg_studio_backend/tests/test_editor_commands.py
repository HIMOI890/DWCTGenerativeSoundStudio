from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from edmg_studio_backend.api.editor import create_editor_router
from edmg_studio_backend.domain.editor_commands import execute, normalize_timeline
from edmg_studio_backend.store.projects import ProjectStore


def timeline():
    return {
        "fps": 30,
        "extension": {"keep": True},
        "tracks": [
            {
                "id": "video",
                "type": "video",
                "clips": [
                    {
                        "id": "clip",
                        "start_s": 1,
                        "end_s": 5,
                        "data": {"source_in_s": 2, "source_sample_rate": 44100, "speed": 2},
                        "custom": "retained",
                    }
                ],
            }
        ],
    }


@pytest.fixture
def editor(tmp_path):
    store = ProjectStore(tmp_path)
    project = store.create("Editor")
    project.meta["timeline"] = timeline()
    store.save(project)
    app = FastAPI()
    app.include_router(create_editor_router(lambda: store))
    with TestClient(app) as client:
        yield store, project.id, client


def submit(editor, *, action="edit", operations=None, **kwargs):
    store, pid, client = editor
    body = {
        "operation_id": str(uuid4()),
        "expected_revision": store.get(pid).revision,
        "action": action,
        "operations": operations or [],
        **kwargs,
    }
    return client.post(f"/v1/projects/{pid}/editor/commands", json=body)


def test_grouped_edit_undo_redo_persists_across_store_reload(editor):
    store, pid, client = editor
    result = submit(
        editor,
        operations=[
            {"kind": "move", "track_id": "video", "clip_id": "clip", "position": "96000"},
            {
                "kind": "split",
                "track_id": "video",
                "clip_id": "clip",
                "position": "144000",
                "new_id": "right",
            },
        ],
    )
    assert result.status_code == 200, result.text
    clips = result.json()["timeline"]["tracks"][0]["clips"]
    assert clips[0]["start_sample"] == "96000"
    assert clips[0]["end_sample"] == "144000"
    assert clips[1]["data"]["source_offset_sample"] == "176400"
    assert clips[0]["data"]["source_end_sample"] == "176400"
    assert clips[1]["custom"] == "retained"
    assert result.json()["history"]["can_undo"]
    persisted = ProjectStore(store.project_dir(pid).parent.parent).get(pid)
    assert persisted is not None and persisted.meta["editor_history"]["undo"]
    undone = submit(editor, action="undo")
    assert undone.status_code == 200
    assert undone.json()["timeline"] == normalize_timeline(timeline())
    redone = submit(editor, action="redo")
    assert redone.json()["timeline"] == result.json()["timeline"]


def test_retry_is_idempotent_and_changed_reuse_rejected(editor):
    store, pid, client = editor
    body = {
        "operation_id": "retry",
        "expected_revision": store.get(pid).revision,
        "action": "edit",
        "operations": [{"kind": "duplicate", "track_id": "video", "clip_id": "clip"}],
    }
    url = f"/v1/projects/{pid}/editor/commands"
    first = client.post(url, json=body)
    second = client.post(url, json=body)
    assert first.status_code == second.status_code == 200
    assert first.json()["revision"] == second.json()["revision"]
    assert second.json()["replayed"]
    body["operations"][0]["kind"] = "delete"
    assert client.post(url, json=body).status_code == 409


def test_invalid_batch_has_no_partial_commit(editor):
    store, pid, _ = editor
    before = store.get(pid)
    result = submit(
        editor,
        operations=[
            {"kind": "delete", "track_id": "video", "clip_id": "clip"},
            {"kind": "delete", "track_id": "missing", "clip_id": "clip"},
        ],
    )
    assert result.status_code == 422
    assert store.get(pid).revision == before.revision
    assert store.get(pid).meta == before.meta


def test_external_edit_cannot_be_overwritten_by_undo(editor):
    store, pid, client = editor
    assert (
        submit(
            editor, operations=[{"kind": "duplicate", "track_id": "video", "clip_id": "clip"}]
        ).status_code
        == 200
    )
    store.mutate(pid, lambda p: p.meta["timeline"].update({"extension": "external"}))
    result = submit(editor, action="undo")
    assert result.status_code == 409
    assert client.get(f"/v1/projects/{pid}/editor").json()["history"]["external_change"]


def test_stale_revision_is_rejected(editor):
    store, pid, _ = editor
    response = submit(
        editor, action="replace", timeline=timeline(), expected_revision=store.get(pid).revision - 1
    )
    assert response.status_code == 409


def test_precise_sample_survives_legacy_round_trip():
    initial = timeline()
    clip = initial["tracks"][0]["clips"][0]
    clip["start_sample"], clip["end_sample"] = "9007199254740993", "9007199254788993"
    precise = normalize_timeline(initial)
    roundtrip = normalize_timeline(deepcopy(precise), precise)
    assert roundtrip["tracks"][0]["clips"][0]["start_sample"] == "9007199254740993"
    old_client = deepcopy(precise)
    del old_client["tracks"][0]["clips"][0]["start_sample"]
    del old_client["tracks"][0]["clips"][0]["end_sample"]
    assert (
        normalize_timeline(old_client, precise)["tracks"][0]["clips"][0]["start_sample"]
        == "9007199254740993"
    )
    edited = deepcopy(precise)
    edited["tracks"][0]["clips"][0]["start_s"] = 3
    assert normalize_timeline(edited, precise)["tracks"][0]["clips"][0]["start_sample"] == "144000"


def test_locked_tracks_and_media_paths_are_protected(editor):
    store, pid, _ = editor
    store.mutate(pid, lambda p: p.meta["timeline"]["tracks"][0].update({"locked": True}))
    assert (
        submit(
            editor, operations=[{"kind": "delete", "track_id": "video", "clip_id": "clip"}]
        ).status_code
        == 422
    )
    modified = timeline()
    modified["tracks"][0]["clips"] = []
    assert submit(editor, action="replace", timeline=modified).status_code == 422
    store.mutate(pid, lambda p: p.meta["timeline"]["tracks"][0].update({"locked": False}))
    modified = timeline()
    modified["tracks"][0]["clips"][0]["source_path"] = "../outside.mp4"
    assert submit(editor, action="replace", timeline=modified).status_code == 422


def test_history_is_bounded_and_new_edit_discards_redo():
    meta = {"timeline": timeline()}
    for index in range(205):
        execute(
            meta,
            {
                "operation_id": str(index),
                "action": "edit",
                "operations": [
                    {
                        "kind": "set_mute",
                        "track_id": "video",
                        "clip_id": "clip",
                        "value": bool(index % 2),
                    }
                ],
            },
        )
    assert len(meta["editor_history"]["undo"]) == 200
    execute(meta, {"operation_id": "undo", "action": "undo"})
    assert len(meta["editor_history"]["redo"]) == 1
    execute(
        meta,
        {
            "operation_id": "new",
            "action": "edit",
            "operations": [{"kind": "add_track", "track_type": "audio"}],
        },
    )
    assert not meta["editor_history"]["redo"]


def test_history_field_deltas_do_not_duplicate_whole_timeline():
    meta = {"timeline": normalize_timeline(timeline())}
    meta["timeline"]["extension"]["large_reference"] = "x" * 100000
    execute(
        meta,
        {
            "operation_id": "move",
            "action": "edit",
            "operations": [
                {"kind": "move", "track_id": "video", "clip_id": "clip", "position": "144000"}
            ],
        },
    )
    import json

    assert len(json.dumps(meta["editor_history"])) < 3000


def test_adding_clip_preserves_audio_track_and_batch_undo(editor):
    store, pid, _ = editor
    response = submit(
        editor,
        operations=[
            {"kind": "add_track", "track_type": "audio", "new_id": "audio"},
            {
                "kind": "add_clip",
                "track_id": "audio",
                "new_id": "audio-clip",
                "start_seconds": "1",
                "end_seconds": "2",
            },
        ],
    )
    assert response.status_code == 200, response.text
    audio = response.json()["timeline"]["tracks"][-1]
    assert audio["type"] == "audio"
    assert audio["clips"][0]["start_sample"] == "48000"
    assert submit(editor, action="undo").status_code == 200
    assert len(store.get(pid).meta["timeline"]["tracks"]) == 1


def test_repeated_source_trims_retain_fractional_resampling_phase():
    from fractions import Fraction

    from edmg_studio_backend.domain.editor_commands import _advance_source
    from edmg_studio_backend.domain.project_time import ProjectClock

    clip = {"data": {"source_in_s": 0, "source_sample_rate": 44100}}
    for _ in range(1000):
        _advance_source(clip, 1, ProjectClock())
    data = clip["data"]
    exact = Fraction(int(data["source_offset_sample"])) + Fraction(data["source_offset_remainder"])
    assert exact == Fraction(1000 * 44100, 48000)


def test_legacy_save_uses_same_history_and_exact_fields(editor):
    from edmg_studio_backend.api.routers import create_project_router

    store, pid, client = editor
    client.app.include_router(
        create_project_router(
            get_store=lambda: store,
            project_response=lambda p: {"project": p.__dict__},
            assess_health=lambda *_: {},
        )
    )
    assert (
        submit(
            editor,
            operations=[
                {"kind": "set_mute", "track_id": "video", "clip_id": "clip", "value": True}
            ],
        ).status_code
        == 200
    )
    current = deepcopy(store.get(pid).meta["timeline"])
    current["tracks"][0]["clips"][0]["start_s"] = 2
    response = client.post(
        f"/v1/projects/{pid}/timeline",
        json={"timeline": current, "expected_revision": store.get(pid).revision},
    )
    assert response.status_code == 200, response.text
    assert response.json()["timeline"]["tracks"][0]["clips"][0]["start_sample"] == "96000"
    undone = submit(editor, action="undo")
    assert undone.json()["timeline"]["tracks"][0]["clips"][0]["start_sample"] == "48000"
