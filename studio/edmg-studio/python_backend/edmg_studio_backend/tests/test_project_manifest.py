from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from edmg_studio_backend.store import projects as projects_module
from edmg_studio_backend.store.projects import (
    CURRENT_SCHEMA_VERSION,
    Project,
    ProjectStore,
    migrate_project_document,
    validate_project_document,
)


def test_validate_and_migrate_legacy_project_document() -> None:
    legacy = {
        "id": "abc123",
        "name": "Legacy",
        "created_at": "2026-01-01 00:00:00",
        "meta": {"timeline": {"layers": []}},
    }
    migrated, changed, applied = migrate_project_document(legacy)
    assert changed is True
    assert applied == [1, 2, 3]
    assert migrated["schema_version"] == CURRENT_SCHEMA_VERSION
    validated = validate_project_document(migrated)
    assert validated["name"] == "Legacy"


def test_project_store_migrates_on_load_with_backup(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "data")
    project_id = "fixtureproj00000000000000000001"
    project_dir = store.project_dir(project_id)
    project_path = project_dir / "project.json"
    project_path.write_text(
        json.dumps(
            {
                "id": project_id,
                "name": "Needs Migration",
                "created_at": "2026-07-15 00:00:00",
                "meta": {"audio": {"filename": "a.wav", "size_bytes": 10}},
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    loaded = store.get(project_id)
    assert loaded is not None
    assert loaded.schema_version == CURRENT_SCHEMA_VERSION
    saved = json.loads(project_path.read_text(encoding="utf-8"))
    assert saved["schema_version"] == CURRENT_SCHEMA_VERSION
    backups = list(project_dir.glob("project.v0.*.bak.json"))
    assert len(backups) == 1


def test_project_store_save_is_atomic_and_versioned(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "data")
    proj = store.create("Atomic")
    proj.meta["width"] = 1280
    store.save(proj)

    path = store.project_dir(proj.id) / "project.json"
    assert not path.with_name("project.json.tmp").exists()
    assert not list(path.parent.glob("project.json.*.tmp"))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == CURRENT_SCHEMA_VERSION
    assert data["meta"]["width"] == 1280
    assert store.get(proj.id) == Project(
        id=proj.id,
        name="Atomic",
        created_at=proj.created_at,
        updated_at=proj.updated_at,
        revision=proj.revision,
        meta={"width": 1280},
        schema_version=CURRENT_SCHEMA_VERSION,
    )


def test_set_audio_archives_analysis_and_preserves_plan_and_authored_state(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "data")
    project = store.create("Replacement Audio")
    project.meta.update(
        {
            "audio": {"filename": "old.wav", "size_bytes": 10},
            "analysis": {"features": {"bpm": 120}},
            "last_plan": {"variants": [{"scenes": []}]},
            "timeline": {"layers": [{"id": "authored"}]},
            "visual_dna": {"palette": ["teal"]},
            "internal_render_history": [{"id": "render-1"}],
        }
    )
    store.save(project)

    store.set_audio(project.id, "replacement.wav", 2048)

    saved = store.get(project.id)
    assert saved is not None
    assert saved.meta["audio"] == {"filename": "replacement.wav", "size_bytes": 2048}
    assert "analysis" not in saved.meta
    assert saved.meta["last_plan"] == project.meta["last_plan"]
    assert saved.meta["analysis_history"][-1] == project.meta["analysis"]
    assert saved.meta["timeline"] == {"layers": [{"id": "authored"}]}
    assert saved.meta["visual_dna"] == {"palette": ["teal"]}
    assert saved.meta["internal_render_history"] == [{"id": "render-1"}]


def test_concurrent_project_writes_use_unique_same_directory_temp_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ProjectStore(tmp_path / "data")
    project_id = "concurrentproj00000000000000001"
    project_path = store.project_dir(project_id) / "project.json"
    payloads = [
        {
            "id": project_id,
            "name": f"Concurrent {index}",
            "created_at": "2026-08-01 00:00:00",
            "meta": {"writer": index},
            "schema_version": CURRENT_SCHEMA_VERSION,
        }
        for index in range(2)
    ]
    real_replace = os.replace
    replace_barrier = threading.Barrier(2)
    replace_lock = threading.Lock()
    replace_sources: list[Path] = []
    failures: list[BaseException] = []

    def synchronized_replace(source: str | bytes | os.PathLike[str] | os.PathLike[bytes], destination: object) -> None:
        replace_sources.append(Path(source))
        replace_barrier.wait(timeout=5)
        with replace_lock:
            real_replace(source, destination)

    monkeypatch.setattr(projects_module.os, "replace", synchronized_replace)

    def write(payload: dict[str, object]) -> None:
        try:
            store._write_atomic(project_path, payload)
        except BaseException as exc:
            failures.append(exc)

    threads = [threading.Thread(target=write, args=(payload,)) for payload in payloads]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not any(thread.is_alive() for thread in threads)
    assert failures == []
    assert len(replace_sources) == 2
    assert len(set(replace_sources)) == 2
    assert all(source.parent == project_path.parent for source in replace_sources)
    assert not any(source.exists() for source in replace_sources)
    assert json.loads(project_path.read_text(encoding="utf-8")) in payloads


def test_project_write_retries_transient_windows_replace_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ProjectStore(tmp_path / "data")
    project_id = "retryproj000000000000000000000001"
    project_path = store.project_dir(project_id) / "project.json"
    payload = {
        "id": project_id,
        "name": "Retry",
        "created_at": "2026-08-01 00:00:00",
        "meta": {},
        "schema_version": CURRENT_SCHEMA_VERSION,
    }
    real_replace = os.replace
    attempts = 0
    sleeps: list[float] = []

    def flaky_replace(source: object, destination: object) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            error = PermissionError("file is temporarily in use")
            error.winerror = 32
            raise error
        real_replace(source, destination)

    monkeypatch.setattr(projects_module.os, "replace", flaky_replace)
    monkeypatch.setattr(projects_module.time, "sleep", sleeps.append)

    store._write_atomic(project_path, payload)

    assert attempts == 3
    assert sleeps == [0.025, 0.05]
    assert json.loads(project_path.read_text(encoding="utf-8")) == payload
    assert not list(project_path.parent.glob("project.json.*.tmp"))


def test_project_write_exhausts_transient_replace_retries_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ProjectStore(tmp_path / "data")
    project_id = "lockedproj00000000000000000000001"
    project_path = store.project_dir(project_id) / "project.json"
    original = {"sentinel": "preserved"}
    project_path.write_text(json.dumps(original), encoding="utf-8")
    attempts = 0
    sleeps: list[float] = []

    def locked_replace(_source: object, _destination: object) -> None:
        nonlocal attempts
        attempts += 1
        error = PermissionError("file remains locked")
        error.winerror = 33
        raise error

    monkeypatch.setattr(projects_module.os, "replace", locked_replace)
    monkeypatch.setattr(projects_module.time, "sleep", sleeps.append)

    with pytest.raises(PermissionError, match="file remains locked"):
        store._write_atomic(project_path, {"sentinel": "replacement"})

    assert attempts == 6
    assert sleeps == [0.025, 0.05, 0.1, 0.2, 0.4]
    assert json.loads(project_path.read_text(encoding="utf-8")) == original
    assert not list(project_path.parent.glob("project.json.*.tmp"))


def test_unsupported_future_schema_is_rejected(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "data")
    project_id = "futureproj00000000000000000001"
    project_dir = store.project_dir(project_id)
    (project_dir / "project.json").write_text(
        json.dumps(
            {
                "id": project_id,
                "name": "Future",
                "created_at": "2026-07-15 00:00:00",
                "meta": {},
                "schema_version": CURRENT_SCHEMA_VERSION + 10,
            }
        ),
        encoding="utf-8",
    )
    assert store.get(project_id) is None


def test_project_store_rejects_project_id_path_traversal(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "data")

    with pytest.raises(ValueError, match="Invalid project identifier"):
        store.project_dir("../outside")

    assert not (tmp_path / "outside").exists()
