from __future__ import annotations

import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest

from edmg_studio_backend import app as backend
from edmg_studio_backend.services.model_load_coordinator import model_load_lock
from edmg_studio_backend.store.jobs import JobStore
from edmg_studio_backend.store.projects import ProjectStore


@pytest.fixture
def state(tmp_path, monkeypatch):
    projects = ProjectStore(tmp_path / "projects")
    jobs = JobStore(projects.projects_dir)
    project = projects.create("Model worker admission")
    monkeypatch.setattr(backend, "store", projects)
    monkeypatch.setattr(backend, "jobs", jobs)
    monkeypatch.setattr(backend, "settings", replace(backend.settings, models_dir=tmp_path / "models"))
    monkeypatch.setattr(backend, "release_cached_internal_pipelines", lambda: 0)
    yield jobs, project
    jobs.close()


def test_director_waits_for_renderer_and_cancellation_skips_the_child(state, monkeypatch):
    jobs, project = state
    renderer = jobs.create(project.id, "internal_video", {})
    director = jobs.create(project.id, "qwen_director", {})
    entered = threading.Event()
    release = threading.Event()
    waiting = threading.Event()
    calls = []

    def execute(job):
        calls.append(job.type)
        if job.id == renderer.id:
            entered.set()
            assert release.wait(10)

    progress = jobs.update_progress

    def report(*args, **kwargs):
        result = progress(*args, **kwargs)
        if kwargs.get("stage") == "waiting_for_model":
            waiting.set()
        return result

    monkeypatch.setattr(backend, "_dispatch_admitted_job", execute)
    monkeypatch.setattr(jobs, "update_progress", report)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(backend._dispatch_job, renderer)
        try:
            assert entered.wait(5)
            second = executor.submit(backend._dispatch_job, director)
            assert waiting.wait(5)
            assert calls == ["internal_video"]
            canceled = jobs.cancel(project.id, director.id)
            second.result(timeout=5)
            assert canceled.status == "canceled"
            assert calls == ["internal_video"]
            # An export that does not load a local model is not blocked by the gate.
            export = jobs.create(project.id, "assemble_variant", {})
            backend._dispatch_job(export)
            assert calls == ["internal_video", "assemble_variant"]
        finally:
            release.set()
            first.result(timeout=5)


def test_next_model_dispatches_only_after_predecessor_finishes(state, monkeypatch):
    jobs, project = state
    director = jobs.create(project.id, "qwen_director", {})
    entered = threading.Event()
    waiting = threading.Event()
    dispatched = []
    progress = jobs.update_progress

    def report(*args, **kwargs):
        result = progress(*args, **kwargs)
        if kwargs.get("stage") == "waiting_for_model":
            waiting.set()
        return result

    def execute(job):
        assert job.type == "qwen_director"
        dispatched.append(job.id)
        entered.set()

    monkeypatch.setattr(jobs, "update_progress", report)
    monkeypatch.setattr(backend, "_dispatch_admitted_job", execute)
    with ThreadPoolExecutor(max_workers=1) as executor:
        with model_load_lock(backend.settings.models_dir / ".runtime"):
            future = executor.submit(backend._dispatch_job, director)
            assert waiting.wait(5)
            assert dispatched == []
        future.result(timeout=5)
    assert entered.is_set()
    assert dispatched == [director.id]


def test_manual_tick_uses_isolated_director_dispatch(state, monkeypatch):
    jobs, project = state
    queued = jobs.create(project.id, "qwen_director", {})
    calls = []
    monkeypatch.setattr(backend, "_run_job_in_subprocess", lambda job: calls.append(job.id))
    monkeypatch.setattr(backend, "_execute_job", lambda job: pytest.fail("Director ran inside API"))
    backend.tick_worker()
    assert calls == [queued.id]


def test_director_launch_failure_is_terminal_and_does_not_fall_back(state, monkeypatch):
    jobs, project = state
    job = jobs.create(project.id, "qwen_director", {})

    def fail(_job):
        raise OSError("private path token=do-not-display")

    monkeypatch.setattr(backend, "_run_job_in_subprocess", fail)
    monkeypatch.setattr(backend, "_execute_job", lambda job: pytest.fail("Director ran inside API"))
    backend._dispatch_job(job)
    saved = jobs.get(project.id, job.id)
    assert saved.status == "failed"
    assert "do-not-display" not in saved.error
    with model_load_lock(backend.settings.models_dir / ".runtime", timeout_s=0):
        pass


def test_canceled_process_is_reaped_after_force_kill(state, monkeypatch):
    jobs, project = state
    job = jobs.create(project.id, "qwen_director", {})
    waits = []
    clock = iter([0.0, 2.0])

    class Process:
        returncode = None

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            waits.append(timeout)
            if timeout is None:
                self.returncode = -9
                return -9
            if timeout == 1.0:
                jobs.cancel(project.id, job.id)
            raise subprocess.TimeoutExpired("test worker", timeout)

        def terminate(self):
            waits.append("terminate")

        def kill(self):
            waits.append("kill")

    monkeypatch.setattr(backend, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    monkeypatch.setattr(backend, "settings", replace(backend.settings, render_subprocess_cancel_grace_s=1.0))
    monkeypatch.setattr(backend.subprocess, "Popen", lambda *_args, **_kwargs: Process())
    backend._run_job_in_subprocess(job)
    assert waits == [1.0, 1.0, "terminate", 5.0, "kill", None]
    assert jobs.get(project.id, job.id).status == "canceled"


def test_monitoring_error_never_runs_a_second_copy_in_process(state, monkeypatch):
    jobs, project = state
    job = jobs.create(project.id, "internal_video", {})
    stopped = []

    class Process:
        returncode = None

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            if timeout == 1.0:
                raise OSError("monitor failed")
            self.returncode = -1
            return -1

        def terminate(self):
            stopped.append(True)

    monkeypatch.setattr(backend.subprocess, "Popen", lambda *_args, **_kwargs: Process())
    monkeypatch.setattr(backend, "_job_in_subprocess_enabled", lambda: True)
    monkeypatch.setattr(backend, "_execute_job", lambda job: pytest.fail("Duplicate render execution"))
    backend._dispatch_job(job)
    assert stopped == [True]
    assert jobs.get(project.id, job.id).status == "failed"


def test_finalization_error_after_exit_never_reexecutes_the_job(state, monkeypatch):
    jobs, project = state
    job = jobs.create(project.id, "internal_video", {})

    class Process:
        returncode = 7

        def wait(self, timeout=None):
            return self.returncode

        def poll(self):
            return self.returncode

    def fail_finalization(*_args, **_kwargs):
        raise OSError("checkpoint unavailable")

    monkeypatch.setattr(backend.subprocess, "Popen", lambda *_args, **_kwargs: Process())
    monkeypatch.setattr(backend, "_job_in_subprocess_enabled", lambda: True)
    monkeypatch.setattr(backend, "_terminalize_failed_runtime_checkpoint", fail_finalization)
    monkeypatch.setattr(backend, "_execute_job", lambda job: pytest.fail("Duplicate render execution"))
    backend._dispatch_job(job)
    assert jobs.get(project.id, job.id).status == "failed"


def test_canceled_or_obsolete_cli_attempt_never_runs(state, monkeypatch):
    from edmg_studio_backend import cli

    jobs, project = state
    job = jobs.create(project.id, "qwen_director", {})
    monkeypatch.setattr(cli, "jobs", jobs)
    monkeypatch.setattr(cli, "_execute_job", lambda job: pytest.fail("Obsolete Director executed"))
    jobs.cancel(project.id, job.id)
    assert cli._run_single_job(project.id, job.id, attempt=0) == 0
    jobs.retry(project.id, job.id)
    assert cli._run_single_job(project.id, job.id, attempt=0) == 2
