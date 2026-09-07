import json
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI
from fastapi.testclient import TestClient

from edmg_studio_backend.api.routers import create_project_router
from edmg_studio_backend.store.projects import ProjectStore
from edmg_studio_backend.store.jobs import JobStore
from edmg_studio_backend import app as backend


def test_autosave_requires_revision_rejects_reserved_fields_and_merges_recovery(tmp_path):
    store = ProjectStore(tmp_path)
    project = store.create("Revisions")
    project.meta = {"audio": {"filename": "server.wav"}, "notes": "keep"}
    store.save(project)
    app = FastAPI()
    app.include_router(create_project_router(get_store=lambda: store,
        project_response=lambda p: {"project": p.__dict__}, assess_health=lambda *args: {}))
    with TestClient(app) as client:
        route = f"/v1/projects/{project.id}/autosave"
        assert client.post(route, json={"meta": {}}).status_code == 428
        assert client.post(route, json={"expected_revision": 1, "meta": {}}).status_code == 409
        assert client.post(route, json={"expected_revision": project.revision,
            "meta": {"audio": {"filename": "../escape"}}}).status_code == 400
        saved = client.post(route, json={"expected_revision": project.revision, "meta": {"notes": "new"}})
        assert saved.status_code == 200, saved.text
        assert saved.json()["revision"] == project.revision + 1
        current = store.mutate(project.id, lambda p: p.meta.update({"unrelated": "retain"}))
        recovered = client.post(f"/v1/projects/{project.id}/recovery/apply",
            json={"source": "journal", "expected_revision": current.revision})
        assert recovered.status_code == 200, recovered.text
        meta = store.get(project.id).meta
        assert meta == {"audio": {"filename": "server.wav"}, "notes": "new", "unrelated": "retain"}


def test_unknown_document_fields_survive_save(tmp_path):
    store = ProjectStore(tmp_path)
    project = store.create("Unknown fields")
    path = store.project_dir(project.id) / "project.json"
    data = json.loads(path.read_text())
    data["future_extension"] = {"keep": True}
    path.write_text(json.dumps(data))
    project = store.get(project.id)
    store.save(project)
    assert json.loads(path.read_text())["future_extension"] == {"keep": True}


def test_canceled_worker_cannot_publish_or_change_terminal_status(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path)
    jobs = JobStore(store.projects_dir, db_path=tmp_path / "jobs.db")
    project = store.create("Cancel race")
    job = jobs.create(project.id, "internal_still_scene", {})
    monkeypatch.setattr(backend, "store", store)
    monkeypatch.setattr(backend, "jobs", jobs)

    def render(*args):
        snapshot = store.get(project.id)
        snapshot.meta["outputs"] = ["should-not-register.mp4"]
        store.save(snapshot)
        jobs.cancel(project.id, job.id)
        return {"video": "should-not-register.mp4"}

    monkeypatch.setattr(backend, "_run_internal_still_scene", render)
    backend._execute_job(job)
    assert jobs.get(project.id, job.id).status == "canceled"
    assert "outputs" not in store.get(project.id).meta
    assert store.get(project.id).revision == project.revision


def test_worker_merges_owned_fields_without_losing_concurrent_edit(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path)
    jobs = JobStore(store.projects_dir, db_path=tmp_path / "jobs.db")
    project = store.create("Owned merge")
    job = jobs.create(project.id, "internal_still_scene", {})
    monkeypatch.setattr(backend, "store", store)
    monkeypatch.setattr(backend, "jobs", jobs)

    def render(*args):
        snapshot = store.get(project.id)
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(store.mutate, project.id, lambda p: p.meta.update({"notes": "user edit"})).result()
        snapshot.meta["outputs"] = ["new.mp4"]
        store.save(snapshot)
        return {"video": "new.mp4"}

    monkeypatch.setattr(backend, "_run_internal_still_scene", render)
    backend._execute_job(job)
    assert jobs.get(project.id, job.id).status == "succeeded"
    assert store.get(project.id).meta == {"notes": "user edit", "outputs": ["new.mp4"]}
