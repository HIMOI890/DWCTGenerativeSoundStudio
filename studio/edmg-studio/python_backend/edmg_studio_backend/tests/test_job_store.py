from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import patch

from edmg_studio_backend.store.jobs import _CORRUPTED_QUARANTINE_SUFFIX, JobStore


def test_job_store_create_claim_and_idempotency(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "projects", db_path=tmp_path / "jobs.sqlite")
    first = store.create("proj1", "internal_video", {"fps": 24}, idempotency_key="render-a")
    second = store.create("proj1", "internal_video", {"fps": 30}, idempotency_key="render-a")
    assert first.id == second.id
    assert second.payload["fps"] == 24

    claimed = store.claim_next_queued(lease_seconds=60.0, owner="worker-1")
    assert claimed is not None
    assert claimed.id == first.id
    assert claimed.status == "running"
    assert store.claim_next_queued() is None

    claimed.status = "succeeded"
    claimed.result = {"ok": True}
    store.save(claimed)
    events = store.list_events("proj1", first.id)
    assert any(e["event_type"] == "created" for e in events)
    assert any(e["event_type"] == "claimed" for e in events)
    store.close()


def test_job_store_migrates_json_and_recovers_expired_lease(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    job_dir = projects / "proj2" / "jobs"
    job_dir.mkdir(parents=True)
    legacy_id = "legacyjob00000000000000000001"
    (job_dir / f"{legacy_id}.json").write_text(
        f'{{"id":"{legacy_id}","project_id":"proj2","type":"analyze","status":"queued",'
        '"created_at":"2026-07-15 00:00:00","updated_at":"2026-07-15 00:00:00",'
        '"payload":{"x":1}}',
        encoding="utf-8",
    )
    store = JobStore(projects, db_path=tmp_path / "jobs.sqlite")
    migrated = store.get("proj2", legacy_id)
    assert migrated is not None
    assert migrated.type == "analyze"

    claimed = store.claim_next_queued(lease_seconds=0.05, owner="worker-temp")
    assert claimed is not None
    time.sleep(0.08)
    recovered = store.claim_next_queued(lease_seconds=30.0, owner="worker-2")
    assert recovered is not None
    assert recovered.id == claimed.id
    assert recovered.status == "running"
    store.close()


def test_job_store_cancel_retry_and_progress(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "projects", db_path=tmp_path / "jobs.sqlite")
    job = store.create("proj3", "layered_animation", {})
    store.update_progress("proj3", job.id, stage="frames", current=2, total=10, message="rendering")
    canceled = store.cancel("proj3", job.id)
    assert canceled is not None
    assert canceled.status == "canceled"
    retried = store.retry("proj3", job.id)
    assert retried is not None
    assert retried.status == "queued"
    assert retried.attempt == 1
    store.close()


def test_job_store_retry_is_atomic_and_does_not_reset_active_work(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "projects", db_path=tmp_path / "jobs.sqlite")
    job = store.create("proj-retry", "internal_video", {"selection": "legacy"})
    job.status = "failed"
    store.save(job)

    retried = store.retry(
        "proj-retry",
        job.id,
        payload={"selection": "normalized"},
    )
    assert retried is not None
    assert retried.status == "queued"
    assert retried.payload == {"selection": "normalized"}

    claimed = store.claim_next_queued(owner="worker-retry")
    assert claimed is not None
    active_attempt = claimed.attempt
    claimed.progress = {"stage": "frames", "current": 1, "total": 2}
    store.save(claimed)

    rejected = store.retry(
        "proj-retry",
        job.id,
        payload={"selection": "stale-second-request"},
    )
    assert rejected is None
    current = store.get("proj-retry", job.id)
    assert current is not None
    assert current.status == "running"
    assert current.payload == {"selection": "normalized"}
    assert current.progress == {"stage": "frames", "current": 1, "total": 2}
    assert current.attempt == active_attempt
    store.close()


def test_job_store_pauses_queued_work_without_letting_a_worker_claim_it(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "projects", db_path=tmp_path / "jobs.sqlite")
    job = store.create("proj4", "internal_video", {})

    paused = store.pause("proj4", job.id)
    assert paused is not None
    assert paused.status == "paused"
    assert store.claim_next_queued() is None

    resumed = store.resume("proj4", job.id)
    assert resumed is not None
    assert resumed.status == "queued"
    claimed = store.claim_next_queued(owner="worker-1")
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == "running"

    event_types = [event["event_type"] for event in store.list_events("proj4", job.id)]
    assert "paused" in event_types
    assert "resumed" in event_types
    store.close()


def test_job_store_claim_is_atomic_across_store_instances(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    db_path = tmp_path / "jobs.sqlite"
    store_a = JobStore(projects, db_path=db_path)
    store_b = JobStore(projects, db_path=db_path)
    try:
        job = store_a.create("proj5", "internal_video", {})
        barrier = threading.Barrier(2)
        results: list[tuple[str, object | None]] = []
        errors: list[BaseException] = []

        def worker(store: JobStore, owner: str) -> None:
            try:
                barrier.wait(timeout=5)
                results.append((owner, store.claim_next_queued(owner=owner)))
            except BaseException as exc:  # pragma: no cover - surfaced by assertions
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(store_a, "worker-a")),
            threading.Thread(target=worker, args=(store_b, "worker-b")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        assert not errors
        assert all(not thread.is_alive() for thread in threads)
        claimed = [result for _, result in results if result is not None]
        assert len(claimed) == 1
        assert claimed[0].id == job.id
        assert any(result is None for _, result in results)
    finally:
        store_a.close()
        store_b.close()


def test_job_store_migrate_skips_unreadable_project_dirs(tmp_path: Path) -> None:
    """WinError 1392-style OSError on jobs_dir.exists() must not crash JobStore init."""
    projects = tmp_path / "projects"
    good = projects / "goodproj" / "jobs"
    good.mkdir(parents=True)
    legacy_id = "legacyjob00000000000000000002"
    (good / f"{legacy_id}.json").write_text(
        f'{{"id":"{legacy_id}","project_id":"goodproj","type":"analyze","status":"queued",'
        '"created_at":"2026-07-15 00:00:00","updated_at":"2026-07-15 00:00:00",'
        '"payload":{}}',
        encoding="utf-8",
    )
    bad = projects / "badproj"
    bad.mkdir()

    real_exists = Path.exists

    def exists_side_effect(self: Path) -> bool:
        # Simulate corrupt volume: listing yields the project, but jobs/ is unreadable.
        if self == bad / "jobs":
            raise OSError(1392, "The file or directory is corrupted and unreadable")
        return real_exists(self)

    with patch.object(Path, "exists", exists_side_effect):
        store = JobStore(projects, db_path=tmp_path / "jobs.sqlite")
    try:
        migrated = store.get("goodproj", legacy_id)
        assert migrated is not None
        quarantined = projects / f"badproj{_CORRUPTED_QUARANTINE_SUFFIX}"
        assert quarantined.is_dir()
        assert not (projects / "badproj").exists()
    finally:
        store.close()


def test_job_store_migrate_skips_already_quarantined_dirs(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    quarantined = projects / f"deadproj{_CORRUPTED_QUARANTINE_SUFFIX}"
    (quarantined / "jobs").mkdir(parents=True)
    (quarantined / "jobs" / "ignored.json").write_text("{}", encoding="utf-8")

    store = JobStore(projects, db_path=tmp_path / "jobs.sqlite")
    try:
        # Quarantined trees are skipped entirely (not double-renamed, not migrated).
        assert quarantined.is_dir()
        assert store.list_all() == []
    finally:
        store.close()


def test_lease_heartbeat_keeps_a_waiting_worker_from_being_reclaimed(tmp_path):
    store = JobStore(tmp_path / "projects")
    peer = JobStore(tmp_path / "projects")
    try:
        original = store.create("lease-project", "qwen_director", {})
        claimed = store.claim_next_queued(lease_seconds=0.2)
        assert claimed.id == original.id
        with store.maintain_lease(claimed, lease_seconds=0.2):
            # Wait across multiple lease lifetimes, like a queued GPU worker.
            time.sleep(0.55)
            assert peer.claim_next_queued() is None
            assert peer.get(original.project_id, original.id).attempt == claimed.attempt
        time.sleep(0.25)
        assert peer.claim_next_queued().id == original.id
    finally:
        peer.close()
        store.close()


def test_old_attempt_cannot_renew_publish_or_overwrite_a_retry(tmp_path):
    store = JobStore(tmp_path / "projects")
    try:
        original = store.create("retry-project", "qwen_director", {})
        old = store.claim_next_queued()
        store.cancel(original.project_id, original.id)
        store.retry(original.project_id, original.id)
        new = store.claim_next_queued()
        assert new.attempt > old.attempt
        assert not store.renew_lease(old)
        assert store.renew_lease(new)
        with store.publication_guard(old.project_id, old.id, attempt=old.attempt) as permitted:
            assert not permitted
        store.update_progress(new.project_id, new.id, stage="loading_model", current=0, total=1)
        assert store.update_progress(
            old.project_id, old.id, stage="draft_ready", current=1, total=1,
            expected_attempt=old.attempt,
        ) is None
        old.status = "succeeded"
        old.result = {"document": "stale draft"}
        store.save(old)
        assert old.attempt < new.attempt
        old.status = "failed"
        old.error = "late cleanup failure"
        store.save(old)
        saved = store.get(new.project_id, new.id)
        assert saved.status == "running"
        assert saved.result is None
        assert saved.progress["stage"] == "loading_model"
    finally:
        store.close()
