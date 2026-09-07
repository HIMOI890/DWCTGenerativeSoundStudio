from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "edmg_installer.py"
INSTALLER_GUI_PATH = REPO_ROOT / "installer_gui.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "edmg_installer_test_module", SCRIPT_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_installer_gui_module():
    spec = importlib.util.spec_from_file_location(
        "installer_gui_test_module", INSTALLER_GUI_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _expected_hugging_face_cache_env(cache_root: Path) -> dict[str, str]:
    huggingface = cache_root / "huggingface"
    return {
        "HF_HOME": str(huggingface),
        "HF_HUB_CACHE": str(huggingface / "hub"),
        "HF_XET_CACHE": str(huggingface / "xet"),
        "HF_ASSETS_CACHE": str(huggingface / "assets"),
        "HUGGINGFACE_HUB_CACHE": str(huggingface / "hub"),
        "HUGGINGFACE_ASSETS_CACHE": str(huggingface / "assets"),
        "TRANSFORMERS_CACHE": str(cache_root / "transformers"),
    }


@pytest.mark.parametrize(
    ("windows_style", "expected_python_suffix"),
    [
        (False, "bin/python"),
        (True, "Scripts/python.exe"),
    ],
)
def test_install_uses_pinned_uv_for_venv_targets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    windows_style: bool,
    expected_python_suffix: str,
) -> None:
    module = _load_module()
    uv_bin = tmp_path / "toolchain" / "uv"
    venv_dir = tmp_path / "standalone-env"
    resolved_python = tmp_path / "managed-python" / expected_python_suffix
    requirements = tmp_path / "requirements-minimal.txt"
    requirements.write_text("requests>=2\n", encoding="utf-8")
    commands: list[list[str]] = []
    validated: list[tuple[Path, str]] = []

    monkeypatch.setattr(module, "_is_windows", lambda: windows_style)
    monkeypatch.setattr(module, "_resolve_uv", lambda env=None: uv_bin)
    monkeypatch.setattr(module, "_select_requirements", lambda _mode: requirements)
    monkeypatch.setattr(module, "_post_install", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        module,
        "_resolve_requested_python",
        lambda uv, request, *, env=None: resolved_python,
    )
    monkeypatch.setattr(
        module,
        "_validate_python_request",
        lambda py, request, *, env=None: validated.append((py, request)),
    )
    monkeypatch.setattr(
        module,
        "_run",
        lambda cmd, *, cwd=None, env=None: commands.append(list(cmd)) or 0,
    )

    rc = module.install(
        mode="minimal",
        backend="cpu",
        venv=str(venv_dir),
        python=None,
        cache_root=str(tmp_path / "cache"),
        skip_torch=True,
        skip_corpora=True,
        skip_models=True,
        skip_whisper=True,
    )

    assert rc == 0
    assert commands[0] == [
        str(uv_bin),
        "venv",
        "--python",
        str(resolved_python),
        "--seed",
        str(venv_dir),
    ]
    assert validated == [(module._venv_python(venv_dir), "3.12")]
    assert commands[1] == [
        str(uv_bin),
        "pip",
        "install",
        "--python",
        str(module._venv_python(venv_dir)),
        "-r",
        str(requirements),
    ]
    assert commands[2] == [
        str(uv_bin),
        "pip",
        "install",
        "--python",
        str(module._venv_python(venv_dir)),
        "-e",
        ".",
    ]


def test_install_uses_current_python_when_venv_is_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _load_module()
    uv_bin = tmp_path / "toolchain" / "uv"
    requirements = tmp_path / "requirements-standard.txt"
    requirements.write_text("requests>=2\n", encoding="utf-8")
    commands: list[list[str]] = []

    monkeypatch.setattr(module, "_resolve_uv", lambda env=None: uv_bin)
    monkeypatch.setattr(module, "_select_requirements", lambda _mode: requirements)
    monkeypatch.setattr(module, "_post_install", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        module, "_validate_python_request", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        module,
        "_run",
        lambda cmd, *, cwd=None, env=None: commands.append(list(cmd)) or 0,
    )

    rc = module.install(
        mode="standard",
        backend="cpu",
        venv=None,
        python=None,
        cache_root=None,
        skip_torch=True,
        skip_corpora=True,
        skip_models=True,
        skip_whisper=True,
    )

    assert rc == 0
    assert commands == [
        [
            str(uv_bin),
            "pip",
            "install",
            "--python",
            sys.executable,
            "-r",
            str(requirements),
        ],
        [
            str(uv_bin),
            "pip",
            "install",
            "--python",
            sys.executable,
            "-e",
            ".",
        ],
    ]


def test_install_uses_requested_python_when_venv_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_module()
    uv_bin = tmp_path / "toolchain" / "uv"
    requested_python = "3.12"
    resolved_python = tmp_path / "managed python" / "python.exe"
    requirements = tmp_path / "requirements-standard.txt"
    requirements.write_text("requests>=2\n", encoding="utf-8")
    commands: list[list[str]] = []
    post_install_python: list[Path] = []
    validated: list[tuple[Path, str]] = []
    requests: list[str] = []

    monkeypatch.setattr(module, "_resolve_uv", lambda env=None: uv_bin)
    monkeypatch.setattr(module, "_select_requirements", lambda _mode: requirements)
    monkeypatch.setattr(
        module,
        "_resolve_requested_python",
        lambda uv, request, *, env=None: requests.append(request) or resolved_python,
    )
    monkeypatch.setattr(
        module,
        "_validate_python_request",
        lambda py, request, *, env=None: validated.append((py, request)),
    )
    monkeypatch.setattr(
        module,
        "_post_install",
        lambda py, **kwargs: post_install_python.append(py),
    )
    monkeypatch.setattr(
        module,
        "_run",
        lambda cmd, *, cwd=None, env=None: commands.append(list(cmd)) or 0,
    )

    rc = module.install(
        mode="standard",
        backend="cpu",
        venv=None,
        python=requested_python,
        cache_root=None,
        skip_torch=True,
        skip_corpora=True,
        skip_models=True,
        skip_whisper=True,
    )

    assert rc == 0
    assert requests == [requested_python]
    assert validated == [(resolved_python, requested_python)]
    assert post_install_python == [resolved_python]
    assert commands == [
        [
            str(uv_bin),
            "pip",
            "install",
            "--python",
            str(resolved_python),
            "-r",
            str(requirements),
        ],
        [
            str(uv_bin),
            "pip",
            "install",
            "--python",
            str(resolved_python),
            "-e",
            ".",
        ],
    ]
    stdout = capsys.readouterr().out
    assert (
        f'"{resolved_python}" -m enhanced_deforum_music_generator ui --port 7860'
        in stdout
    )
    assert (
        "python scripts/edmg_installer.py verify --python "
        f'"{resolved_python}"'
        in stdout
    )


def test_install_fails_for_mismatched_requested_python(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_module()
    uv_bin = tmp_path / "toolchain" / "uv"
    venv_dir = tmp_path / "standalone-env"

    monkeypatch.setattr(module, "_resolve_uv", lambda env=None: uv_bin)
    monkeypatch.setattr(
        module, "_ensure_venv", lambda *args, **kwargs: module._venv_python(venv_dir)
    )
    monkeypatch.setattr(
        module,
        "_validate_python_request",
        lambda py, request, *, env=None: (_ for _ in ()).throw(
            RuntimeError("Requested Python '3.12' but resolved interpreter is 3.11.9.")
        ),
    )

    rc = module.install(
        mode="standard",
        backend="cpu",
        venv=str(venv_dir),
        python=None,
        cache_root=None,
        skip_torch=True,
        skip_corpora=True,
        skip_models=True,
        skip_whisper=True,
    )

    assert rc == 1
    assert (
        "Requested Python '3.12' but resolved interpreter is 3.11.9."
        in capsys.readouterr().err
    )


def test_verify_uses_requested_venv_python_instead_of_launcher(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _load_module()
    venv_dir = tmp_path / "verify-env"
    venv_python = module._venv_python(venv_dir)
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("", encoding="utf-8")
    commands: list[list[str]] = []
    validated: list[tuple[Path, str]] = []

    monkeypatch.setattr(
        module,
        "_validate_python_request",
        lambda py, request, *, env=None: validated.append((py, request)),
    )
    monkeypatch.setattr(
        module,
        "_run",
        lambda cmd, *, cwd=None, env=None: commands.append(list(cmd)) or 0,
    )

    rc = module.verify(venv=str(venv_dir), python=None, cache_root=None)

    assert rc == 0
    assert validated == [(venv_python, "3.12")]
    assert [command[0] for command in commands] == [str(venv_python), str(venv_python)]


def test_verify_uses_resolved_requested_python_for_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _load_module()
    uv_bin = tmp_path / "toolchain" / "uv"
    resolved_python = tmp_path / "managed-python" / "python.exe"
    commands: list[list[str]] = []
    requests: list[str] = []

    monkeypatch.setattr(module, "_resolve_uv", lambda env=None: uv_bin)
    monkeypatch.setattr(
        module,
        "_resolve_requested_python",
        lambda uv, request, *, env=None: requests.append(request) or resolved_python,
    )
    monkeypatch.setattr(
        module, "_validate_python_request", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        module,
        "_run",
        lambda cmd, *, cwd=None, env=None: commands.append(list(cmd)) or 0,
    )

    rc = module.verify(venv=None, python="3.12", cache_root=None)

    assert rc == 0
    assert requests == ["3.12"]
    assert [command[0] for command in commands] == [
        str(resolved_python),
        str(resolved_python),
    ]


def test_validate_python_request_rejects_mismatched_versions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _load_module()
    py = tmp_path / "python.exe"

    monkeypatch.setattr(module, "_python_version", lambda *args, **kwargs: "3.11.9")

    with pytest.raises(RuntimeError, match="Requested Python '3.12'"):
        module._validate_python_request(py, "3.12")


def test_resolve_requested_python_uses_uv_find(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _load_module()
    uv_bin = tmp_path / "toolchain" / "uv"
    resolved_python = tmp_path / "managed-python" / "python.exe"
    resolved_python.parent.mkdir(parents=True, exist_ok=True)
    resolved_python.write_text("", encoding="utf-8")
    captured_commands: list[list[str]] = []

    def fake_run_capture(cmd, *, cwd=None, env=None):
        captured_commands.append(list(cmd))
        return subprocess.CompletedProcess(
            list(cmd),
            0,
            stdout=f"{resolved_python}\n",
            stderr="",
        )

    monkeypatch.setattr(module, "_run_capture", fake_run_capture)

    actual = module._resolve_requested_python(uv_bin, "3.12")

    assert actual == resolved_python
    assert captured_commands == [
        [str(uv_bin), "python", "find", "--no-project", "3.12"]
    ]


def test_managed_env_routes_uv_python_and_hugging_face_caches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    cache_root = tmp_path / "cache-root"
    expected_hugging_face = _expected_hugging_face_cache_env(cache_root)
    for key in expected_hugging_face:
        monkeypatch.setenv(key, rf"G:\stale-cache\{key}")

    env = module._managed_env(cache_root)

    assert env is not None
    assert env["UV_CACHE_DIR"] == str(cache_root / "uv")
    assert env["UV_PYTHON_INSTALL_DIR"] == str(cache_root / "python")
    assert env["EDMG_UV_INSTALL_ROOT"] == str(cache_root / "toolchain" / "uv")
    for key, value in expected_hugging_face.items():
        assert env[key] == value


def test_legacy_gui_installer_overrides_inherited_hugging_face_caches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_installer_gui_module()
    cache_root = tmp_path / "installer-cache"
    expected = _expected_hugging_face_cache_env(cache_root)
    for key in expected:
        monkeypatch.setenv(key, rf"G:\stale-cache\{key}")

    env = module.build_managed_env(cache_root)

    for key, value in expected.items():
        assert env[key] == value
