from __future__ import annotations

from edmg_studio_backend import app as backend_app


def test_render_worker_command_uses_python_module_in_source_mode(monkeypatch) -> None:
    monkeypatch.setattr(backend_app.sys, "executable", r"C:\Python312\python.exe")
    monkeypatch.delattr(backend_app.sys, "frozen", raising=False)

    assert backend_app._render_worker_command("project-123", "job-456") == [
        r"C:\Python312\python.exe",
        "-m",
        "edmg_studio_backend",
        "run-job",
        "--project",
        "project-123",
        "--job",
        "job-456",
    ]


def test_render_worker_command_calls_frozen_backend_cli_directly(monkeypatch) -> None:
    monkeypatch.setattr(backend_app.sys, "executable", r"C:\EDMG Studio\edmg-studio-backend.exe")
    monkeypatch.setattr(backend_app.sys, "frozen", True, raising=False)

    assert backend_app._render_worker_command("project-123", "job-456") == [
        r"C:\EDMG Studio\edmg-studio-backend.exe",
        "run-job",
        "--project",
        "project-123",
        "--job",
        "job-456",
    ]


def test_render_worker_command_carries_the_claimed_attempt():
    assert backend_app._render_worker_command("project", "job", attempt=3)[-2:] == ["--attempt", "3"]
