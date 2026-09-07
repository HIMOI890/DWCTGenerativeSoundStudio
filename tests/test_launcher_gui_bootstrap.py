import importlib.util
import json
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific bootstrap path handling")
pytest.importorskip("tkinter")


def _load_launcher_gui():
    root = Path(__file__).resolve().parents[1]
    module_path = root / "studio" / "edmg-studio" / "tools" / "launcher_gui.py"
    spec = importlib.util.spec_from_file_location("launcher_gui_test_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_windows_tool_candidates_follow_configured_program_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    launcher_gui = _load_launcher_gui()
    primary = tmp_path / "program-files"
    legacy = tmp_path / "legacy-program-files"
    monkeypatch.setenv("ProgramFiles", str(primary))
    monkeypatch.setenv("ProgramFiles(x86)", str(legacy))

    assert launcher_gui._windows_program_files_dirs() == [primary, legacy]
    assert primary / "ffmpeg" / "bin" / "ffmpeg.exe" in launcher_gui._windows_ffmpeg_candidates()


def test_saved_path_if_usable_rejects_missing_windows_drive(monkeypatch):
    launcher_gui = _load_launcher_gui()

    monkeypatch.setattr(
        launcher_gui,
        "_windows_drive_usable",
        lambda path: not str(path).replace("/", "\\").upper().startswith("H:"),
    )
    monkeypatch.setattr(launcher_gui, "_discover_missing_drive_remaps", lambda _path: [])

    assert launcher_gui._saved_path_if_usable(r"H:\Repositories\DWCTGenerativeSoundStudio") is None


def test_discover_missing_drive_remaps_scans_mounted_hosts(monkeypatch, tmp_path):
    launcher_gui = _load_launcher_gui()
    host_root = tmp_path / "host_G"
    remapped = host_root / "Users" / "lanak" / "edmg-studio-home"
    remapped.mkdir(parents=True)

    monkeypatch.setattr(launcher_gui, "_available_windows_drive_letters", lambda: ["Z"])

    original_exists = Path.exists

    def fake_exists(self):
        normalized = str(self).replace("/", "\\").upper()
        if normalized in {"G:", "G:\\"}:
            return False
        if normalized == "Z:\\G" or normalized.startswith("Z:\\G\\"):
            relative = str(self).replace("/", "\\")[len("Z:\\G") :].lstrip("\\")
            probe = host_root / relative if relative else host_root
            return original_exists(probe)
        return original_exists(self)

    monkeypatch.setattr(Path, "exists", fake_exists, raising=False)

    found = launcher_gui._discover_missing_drive_remaps(Path(r"G:\Users\lanak\edmg-studio-home"))
    assert found == [Path(r"Z:\G\Users\lanak\edmg-studio-home")]
    assert launcher_gui._discover_missing_drive_remaps(Path(r"C:\G\Users\lanak\edmg-studio-home")) == []


def test_saved_path_if_usable_uses_discovered_remount(monkeypatch, tmp_path):
    launcher_gui = _load_launcher_gui()
    remapped = tmp_path / "Users" / "lanak" / "edmg-studio-home"
    remapped.mkdir(parents=True)

    monkeypatch.setattr(
        launcher_gui,
        "_windows_drive_usable",
        lambda path: not str(path).replace("/", "\\").upper().startswith("G:"),
    )
    monkeypatch.setattr(
        launcher_gui,
        "_discover_missing_drive_remaps",
        lambda path: [remapped] if str(path).upper().startswith("G:") else [],
    )

    usable = launcher_gui._saved_path_if_usable(r"G:\Users\lanak\edmg-studio-home")
    assert usable == remapped.resolve()


def test_ensure_data_dir_env_ignores_unreachable_saved_home(monkeypatch, tmp_path):
    launcher_gui = _load_launcher_gui()
    original_exists = launcher_gui.Path.exists

    def fake_exists(self):
        normalized = str(self).replace("/", "\\").upper()
        if normalized.rstrip("\\") == "H:" or normalized == "H:\\":
            return False
        if normalized.rstrip("\\") == "C:\\H" or normalized.startswith("C:\\H\\"):
            return False
        if normalized.rstrip("\\") == "E:\\H" or normalized.startswith("E:\\H\\"):
            return False
        return original_exists(self)

    bootstrap_path = tmp_path / "bootstrap.json"
    launcher_env_path = tmp_path / "launcher_env.json"
    bootstrap_path.write_text(json.dumps({"studioHome": r"H:\Repositories\DWCTGenerativeSoundStudio\studio\edmg-studio"}), encoding="utf-8")

    monkeypatch.setattr(launcher_gui.Path, "exists", fake_exists, raising=False)
    monkeypatch.setattr(launcher_gui, "LAUNCHER_ENV_PATH", launcher_env_path)
    monkeypatch.setattr(launcher_gui, "_bootstrap_config_path", lambda: bootstrap_path)
    monkeypatch.setattr(launcher_gui, "_available_windows_drive_letters", lambda: ["C", "E"])
    monkeypatch.delenv("EDMG_STUDIO_HOME", raising=False)
    monkeypatch.delenv("EDMG_STUDIO_DATA_DIR", raising=False)

    data_dir = launcher_gui._ensure_data_dir_env()

    assert data_dir == (launcher_gui.STUDIO_DIR / "data").resolve()

    persisted = json.loads(bootstrap_path.read_text(encoding="utf-8"))
    assert persisted["studioHome"] == str(launcher_gui.STUDIO_DIR.resolve())


def test_ensure_data_dir_env_ignores_unreachable_env_home(monkeypatch, tmp_path):
    """Regression: backend __init__ loads launcher_env into os.environ before Launcher runs."""
    launcher_gui = _load_launcher_gui()
    original_exists = launcher_gui.Path.exists

    def fake_exists(self):
        normalized = str(self).replace("/", "\\").upper()
        if normalized.rstrip("\\") == "G:" or normalized == "G:\\":
            return False
        if normalized.rstrip("\\") == "C:\\G" or normalized.startswith("C:\\G\\"):
            return False
        if normalized.rstrip("\\") == "E:\\G" or normalized.startswith("E:\\G\\"):
            return False
        return original_exists(self)

    bootstrap_path = tmp_path / "bootstrap.json"
    launcher_env_path = tmp_path / "launcher_env.json"
    bootstrap_path.write_text("{}", encoding="utf-8")
    launcher_env_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(launcher_gui.Path, "exists", fake_exists, raising=False)
    monkeypatch.setattr(launcher_gui, "LAUNCHER_ENV_PATH", launcher_env_path)
    monkeypatch.setattr(launcher_gui, "_bootstrap_config_path", lambda: bootstrap_path)
    monkeypatch.setattr(launcher_gui, "_available_windows_drive_letters", lambda: ["C", "E"])
    monkeypatch.setenv("EDMG_STUDIO_HOME", r"G:\Users\lanak\edmg-studio-home")
    monkeypatch.delenv("EDMG_STUDIO_DATA_DIR", raising=False)

    data_dir = launcher_gui._ensure_data_dir_env()

    assert data_dir == (launcher_gui.STUDIO_DIR / "data").resolve()
    persisted_env = json.loads(launcher_env_path.read_text(encoding="utf-8"))
    assert persisted_env["EDMG_STUDIO_HOME"] == str(launcher_gui.STUDIO_DIR.resolve())


def test_ensure_data_dir_env_persists_discovered_remount(monkeypatch, tmp_path):
    """Once a missing drive is discovered under another host, persist that path."""
    launcher_gui = _load_launcher_gui()
    remapped_home = tmp_path / "remount" / "Users" / "lanak" / "edmg-studio-home"
    remapped_home.mkdir(parents=True)

    bootstrap_path = tmp_path / "bootstrap.json"
    launcher_env_path = tmp_path / "launcher_env.json"
    bootstrap_path.write_text("{}", encoding="utf-8")
    launcher_env_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(launcher_gui, "LAUNCHER_ENV_PATH", launcher_env_path)
    monkeypatch.setattr(launcher_gui, "_bootstrap_config_path", lambda: bootstrap_path)
    monkeypatch.setattr(
        launcher_gui,
        "_windows_drive_usable",
        lambda path: not str(path).replace("/", "\\").upper().startswith("G:"),
    )
    monkeypatch.setattr(
        launcher_gui,
        "_discover_missing_drive_remaps",
        lambda path: [remapped_home] if str(path).upper().startswith("G:") else [],
    )
    monkeypatch.setenv("EDMG_STUDIO_HOME", r"G:\Users\lanak\edmg-studio-home")
    monkeypatch.delenv("EDMG_STUDIO_DATA_DIR", raising=False)

    data_dir = launcher_gui._ensure_data_dir_env()

    assert data_dir == (remapped_home / "data").resolve()
    persisted_env = json.loads(launcher_env_path.read_text(encoding="utf-8"))
    assert persisted_env["EDMG_STUDIO_HOME"] == str(remapped_home.resolve())
    persisted_bootstrap = json.loads(bootstrap_path.read_text(encoding="utf-8"))
    assert persisted_bootstrap["studioHome"] == str(remapped_home.resolve())


def test_persisted_studio_home_overrides_inherited_hugging_face_cache_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    launcher_gui = _load_launcher_gui()
    bootstrap_path = tmp_path / "bootstrap.json"
    launcher_env_path = tmp_path / "launcher_env.json"
    selected_home = tmp_path / "selected-studio-home"
    managed_keys = (
        "HF_HOME",
        "HF_HUB_CACHE",
        "HF_XET_CACHE",
        "HF_ASSETS_CACHE",
        "HUGGINGFACE_HUB_CACHE",
        "HUGGINGFACE_ASSETS_CACHE",
        "TRANSFORMERS_CACHE",
    )
    for key in managed_keys:
        monkeypatch.setenv(key, rf"G:\stale-cache\{key}")

    monkeypatch.setattr(launcher_gui, "LAUNCHER_ENV_PATH", launcher_env_path)
    monkeypatch.setattr(launcher_gui, "_bootstrap_config_path", lambda: bootstrap_path)

    launcher_gui._persist_studio_location(studio_home=selected_home)

    cache = selected_home.resolve() / "cache"
    huggingface = cache / "huggingface"
    expected = {
        "HF_HOME": str(huggingface),
        "HF_HUB_CACHE": str(huggingface / "hub"),
        "HF_XET_CACHE": str(huggingface / "xet"),
        "HF_ASSETS_CACHE": str(huggingface / "assets"),
        "HUGGINGFACE_HUB_CACHE": str(huggingface / "hub"),
        "HUGGINGFACE_ASSETS_CACHE": str(huggingface / "assets"),
        "TRANSFORMERS_CACHE": str(cache / "transformers"),
    }
    persisted_env = json.loads(launcher_env_path.read_text(encoding="utf-8"))
    for key, value in expected.items():
        assert launcher_gui.os.environ[key] == value
        assert persisted_env[key] == value


def test_launcher_accepts_only_python_312_for_the_locked_backend():
    launcher_gui = _load_launcher_gui()

    assert launcher_gui._is_supported_python_version((3, 12, 0))
    assert launcher_gui._is_supported_python_version((3, 12, 99))
    assert not launcher_gui._is_supported_python_version((3, 11, 9))
    assert not launcher_gui._is_supported_python_version((3, 13, 0))


def test_sync_locked_backend_uses_one_fixed_profile_and_capability_set(monkeypatch):
    launcher_gui = _load_launcher_gui()
    calls = {}

    def fake_sync(profile, *, capability_extras, install_uv):
        calls["sync"] = (profile, tuple(capability_extras), install_uv)
        return Path(r"C:\toolchain\uv.exe")

    def fake_run(profile, command, *, capability_extras):
        calls["run"] = (profile, tuple(command), tuple(capability_extras))
        return ["uv", "run", "--frozen", "python", "-c", "verify"], {"PROFILE": profile}

    monkeypatch.setattr(launcher_gui, "sync_frozen_project", fake_sync)
    monkeypatch.setattr(launcher_gui, "frozen_run_command", fake_run)
    monkeypatch.setattr(launcher_gui, "uv_version", lambda _uv: "0.11.28")
    monkeypatch.setattr(launcher_gui, "lock_sha256", lambda: "a" * 64)
    monkeypatch.setattr(launcher_gui, "_run_cmd", lambda *args, **kwargs: 0)

    launcher_gui._sync_locked_backend("cuda", lambda _message: None)

    assert calls["sync"][0] == "cuda"
    assert "core" in calls["sync"][1]
    assert calls["run"][0] == "cuda"
    verification_command = " ".join(calls["run"][1])
    assert "hf_transfer" in verification_command
    assert "hf_xet" in verification_command


def test_call_on_ui_thread_skips_callbacks_after_disposal() -> None:
    launcher_gui = _load_launcher_gui()
    invoked: list[str] = []
    scheduled: list[object] = []

    class DummyRoot:
        def __init__(self) -> None:
            self._disposed = False

        def _on_ui_thread(self) -> bool:
            return False

        def _ui_alive(self) -> bool:
            return not self._disposed

        def after(self, _delay: int, callback) -> str:
            scheduled.append(callback)
            return "after-token"

    root = DummyRoot()
    root._invoke_ui_callback = launcher_gui.Launcher._invoke_ui_callback.__get__(root, DummyRoot)

    assert launcher_gui.Launcher._call_on_ui_thread(root, invoked.append, "scheduled")
    assert len(scheduled) == 1

    root._disposed = True
    scheduled[0]()

    assert invoked == []


def test_run_bg_restores_busy_state_and_reports_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    launcher_gui = _load_launcher_gui()

    class ImmediateThread:
        def __init__(self, *, target, daemon):
            self._target = target
            self.daemon = daemon

        def start(self) -> None:
            self._target()

    monkeypatch.setattr(launcher_gui.threading, "Thread", ImmediateThread)

    class DummyLauncher:
        def __init__(self) -> None:
            self.busy: list[bool] = []
            self.logs: list[str] = []
            self.errors: list[tuple[str, str]] = []
            self.refreshes = 0

        def _set_busy(self, active: bool) -> None:
            self.busy.append(active)

        def _log(self, message: str) -> None:
            self.logs.append(message)

        def _show_error_dialog(self, title: str, message: str) -> None:
            self.errors.append((title, message))

        def _refresh_status(self) -> None:
            self.refreshes += 1

    launcher = DummyLauncher()

    def failing_work() -> None:
        raise RuntimeError("boom")

    launcher_gui.Launcher._run_bg(launcher, "Health test", failing_work)

    assert launcher.busy == [True, False]
    assert launcher.logs[0] == "== Health test =="
    assert launcher.logs[-1] == "!! Health test failed: boom"
    assert launcher.errors == [("Error", "Health test failed:\nboom")]
    assert launcher.refreshes == 1
