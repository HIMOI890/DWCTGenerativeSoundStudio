import os
import sys
import json
import re
import threading
import queue
import subprocess
import shutil
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
import urllib.request
import time

STUDIO_DIR = Path(__file__).resolve().parents[1]
ROOT = STUDIO_DIR.parents[1]
BACKEND_DIR = STUDIO_DIR / "python_backend"
BUNDLED_FFMPEG = STUDIO_DIR / "electron-resources" / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
PACKAGE_JSON_PATH = STUDIO_DIR / "package.json"
DEFAULT_PACKAGE_MANAGER = "pnpm"
DEFAULT_BACKEND_PORT = 7863
DEFAULT_BACKEND_HOST = "127.0.0.1"
DEFAULT_UI_PORT = 5173
LAUNCHER_ENV_PATH = STUDIO_DIR / "launcher_env.json"
BOOTSTRAP_CONFIG_BASENAME = "bootstrap.json"
SUPPORTED_PYTHON_MIN = (3, 12)
SUPPORTED_PYTHON_MAX_EXCLUSIVE = (3, 13)

# The source launcher is intentionally stdlib-only.  Import the backend's
# checked-in uv policy without requiring the project environment to exist yet.
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from edmg_studio_backend.uv_toolchain import (  # noqa: E402
    RUNTIME_CAPABILITY_EXTRAS,
    active_accelerator_profile,
    frozen_run_command,
    lock_sha256,
    resolve_uv,
    sync_frozen_project,
    uv_version,
)


class UiDispatchDisposedError(RuntimeError):
    """Raised when work is dispatched after the launcher window is closing."""


class _UiDispatchRequest:
    __slots__ = ("callback", "args", "kwargs", "done", "result", "error")

    def __init__(self, callback, args, kwargs, *, wait: bool):
        self.callback = callback
        self.args = args
        self.kwargs = kwargs
        self.done = threading.Event() if wait else None
        self.result = None
        self.error: Exception | None = None


class _TkMainThreadDispatcher:
    def __init__(self, scheduler, *, poll_ms: int = 25):
        self._scheduler = scheduler
        self._poll_ms = max(10, int(poll_ms))
        self._main_thread_id = threading.get_ident()
        self._queue: "queue.Queue[_UiDispatchRequest]" = queue.Queue()
        self._after_id = None
        self._disposed = False

    def is_ui_thread(self) -> bool:
        return threading.get_ident() == self._main_thread_id

    def start(self) -> None:
        if self._disposed or self._after_id is not None:
            return
        self._schedule_next()

    def post(self, callback, *args, **kwargs) -> bool:
        if self._disposed:
            return False
        if self.is_ui_thread():
            callback(*args, **kwargs)
            return True
        self._queue.put(_UiDispatchRequest(callback, args, kwargs, wait=False))
        return True

    def call(self, callback, *args, **kwargs):
        if self._disposed:
            raise UiDispatchDisposedError("launcher window is closing")
        if self.is_ui_thread():
            return callback(*args, **kwargs)
        request = _UiDispatchRequest(callback, args, kwargs, wait=True)
        self._queue.put(request)
        assert request.done is not None
        request.done.wait()
        if request.error is not None:
            raise request.error
        return request.result

    def drain_pending(self) -> None:
        while True:
            try:
                request = self._queue.get_nowait()
            except queue.Empty:
                return
            if self._disposed:
                if request.done is not None:
                    request.error = UiDispatchDisposedError(
                        "launcher window is closing"
                    )
                    request.done.set()
                continue
            try:
                request.result = request.callback(*request.args, **request.kwargs)
            except Exception as exc:  # pragma: no cover - bubbled to waiting caller
                request.error = exc
            finally:
                if request.done is not None:
                    request.done.set()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        after_id = self._after_id
        self._after_id = None
        if after_id is not None and hasattr(self._scheduler, "after_cancel"):
            try:
                self._scheduler.after_cancel(after_id)
            except Exception:
                pass
        self.drain_pending()

    def _pump(self) -> None:
        self._after_id = None
        if self._disposed:
            self.drain_pending()
            return
        self.drain_pending()
        self._schedule_next()

    def _schedule_next(self) -> None:
        if self._disposed:
            return
        try:
            self._after_id = self._scheduler.after(self._poll_ms, self._pump)
        except Exception:
            self.dispose()

# ── Machine optimiser ──────────────────────────────────────────────────────────
OPTIMIZE_STATE_PATH = STUDIO_DIR / ".optimize_state.json"

# High Performance power plan GUID (built-in Windows)
_POWER_HIGH_PERF = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
# Ultimate Performance (may need to be enabled first)
_POWER_ULTIMATE   = "e9a42b02-d5df-448d-aa00-03f14749eb61"

# Background user-space processes to kill during optimization
_OPT_KILL_PROCS: list[str] = [
    "OneDrive.exe",
    "Teams.exe", "ms-teams.exe",
    "XboxApp.exe", "XboxGamingOverlay.exe",
    "GameBarFTServer.exe", "GameBarPresenceWriter.exe",
    "Cortana.exe",
    "YourPhone.exe", "PhoneExperienceHost.exe",
    "MicrosoftEdgeUpdate.exe", "edgeupdate.exe", "edgeupdatem.exe",
    "Spotify.exe",
    "Discord.exe",
    "Slack.exe",
    "DropboxUpdate.exe", "Dropbox.exe",
    "AdobeUpdateService.exe", "AdobeARM.exe",
    "SteamService.exe",
    "WerFault.exe", "WerFaultSecure.exe",
]

# Windows services to stop during optimization (restored afterward)
_OPT_SERVICES: list[str] = [
    "SysMain",         # Superfetch – pre-loads apps we don't need
    "DiagTrack",       # Connected User Experiences / telemetry
    "WSearch",         # Windows Search indexer – heavy I/O
    "XblAuthManager",  # Xbox Live auth
    "XblGameSave",     # Xbox Live game save
    "XboxNetApiSvc",   # Xbox networking
    "XboxGipSvc",      # Xbox accessory mgr
    "MapsBroker",      # Downloaded Maps Manager
]


def _ps(cmd: str) -> tuple[int, str]:
    """Run a PowerShell command, return (returncode, stdout+stderr)."""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True, text=True, timeout=30,
        )
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:
        return -1, str(e)


def _ps_elevated(ps_block: str) -> tuple[int, str]:
    """Run a PowerShell block elevated via Start-Process -Verb RunAs.

    Writes exit code to a temp file so we can detect success/failure even
    though the elevated child process is separate from the caller's token.
    Returns (0, "") on success, (-1, reason) if UAC was cancelled or failed.
    """
    import tempfile
    with tempfile.NamedTemporaryFile(prefix="edmg-elevated-", suffix=".txt", delete=False) as result_file:
        tmp = Path(result_file.name)
    try:
        # Append exit code to temp file so we can read it after the elevated run.
        wrapped = ps_block.rstrip("; ") + f"; $null | Out-Null; [IO.File]::WriteAllText('{tmp}', $LASTEXITCODE)"
        escaped = wrapped.replace("'", "''")
        cmd = (
            f"Start-Process powershell "
            f"-Verb RunAs -Wait "
            f"-WindowStyle Hidden "
            f"-ArgumentList '-NoProfile', '-NonInteractive', '-Command', '{escaped}'"
        )
        rc, out = _ps(cmd)
        if rc != 0:
            return rc, out  # UAC declined or PowerShell not found
        try:
            result_code = int(tmp.read_text(encoding="utf-8").strip())
            return result_code, ""
        except Exception:
            return 0, ""  # elevated ran but result file missing — assume ok
    finally:
        tmp.unlink(missing_ok=True)


def _svc_query(name: str) -> str:
    """Return service state string: Running / Stopped / unknown."""
    rc, out = _ps(f"(Get-Service -Name '{name}' -ErrorAction SilentlyContinue).Status")
    return out.strip() or "unknown"


def _get_active_power_plan() -> str:
    rc, out = _ps("(powercfg /getactivescheme) -replace 'Power Scheme GUID: ',''")
    # out looks like: e9a42b02-... (Ultimate Performance)
    m = __import__("re").search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", out, __import__("re").I)
    return m.group(0).lower() if m else ""


def _save_optimize_state(state: dict) -> None:
    try:
        OPTIMIZE_STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception:
        pass


def _load_optimize_state() -> dict:
    try:
        if OPTIMIZE_STATE_PATH.exists():
            return json.loads(OPTIMIZE_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _resolve_ffmpeg_path() -> str:
    """Prefer this checkout's bundled FFmpeg over a stale user EDMG_FFMPEG_PATH."""
    if BUNDLED_FFMPEG.exists():
        return str(BUNDLED_FFMPEG)

    explicit = os.environ.get("EDMG_FFMPEG_PATH", "").strip()
    if explicit:
        if not os.path.isabs(explicit) or Path(explicit).exists():
            return explicit

    for candidate in _windows_ffmpeg_candidates():
        if candidate.is_file():
            return str(candidate)

    found = shutil.which("ffmpeg")
    if found:
        return found

    if explicit:
        return explicit

    return "ffmpeg"


def _windows_ffmpeg_candidates() -> list[Path]:
    if not sys.platform.startswith("win"):
        return []
    local = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    candidates = [local / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe"]
    candidates.extend(root / "ffmpeg" / "bin" / "ffmpeg.exe" for root in _windows_program_files_dirs())
    return candidates


def _windows_program_files_dirs() -> list[Path]:
    if not sys.platform.startswith("win"):
        return []
    roots: list[Path] = []
    for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
        raw = os.environ.get(env_name, "").strip()
        if raw:
            root = Path(raw)
            if root not in roots:
                roots.append(root)
    return roots


def _resolve_7z_path() -> str | None:
    explicit = os.environ.get("EDMG_7Z_PATH", "").strip()
    if explicit and Path(explicit).exists():
        return explicit
    if sys.platform.startswith("win"):
        for candidate in (
            root / "7-Zip" / "7z.exe" for root in _windows_program_files_dirs()
        ):
            if candidate.is_file():
                return str(candidate)
    return (
        shutil.which("7z")
        or shutil.which("7z.exe")
        or shutil.which("7zz")
        or shutil.which("7zz.exe")
    )


def _format_python_requirement() -> str:
    return (
        f"Python >= {SUPPORTED_PYTHON_MIN[0]}.{SUPPORTED_PYTHON_MIN[1]} "
        f"and < {SUPPORTED_PYTHON_MAX_EXCLUSIVE[0]}.{SUPPORTED_PYTHON_MAX_EXCLUSIVE[1]}"
    )


def _python_version_for_command(cmd: list[str]) -> tuple[int, int, int] | None:
    try:
        proc = subprocess.run(
            [*cmd, "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    raw = str(proc.stdout or "").strip()
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)$", raw)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _is_supported_python_version(version: tuple[int, int, int] | None) -> bool:
    if version is None:
        return False
    return SUPPORTED_PYTHON_MIN <= version[:2] < SUPPORTED_PYTHON_MAX_EXCLUSIVE


def _format_python_version(version: tuple[int, int, int] | None) -> str:
    if version is None:
        return "unknown"
    return ".".join(str(part) for part in version)


def _describe_python_command(cmd: list[str]) -> str:
    return " ".join(cmd)


def _supported_python_candidates() -> list[list[str]]:
    candidates: list[list[str]] = []
    explicit = os.environ.get("EDMG_STUDIO_PYTHON", "").strip()
    if explicit:
        candidates.append([explicit])
    candidates.append([sys.executable])
    candidates.append(["python"])
    if sys.platform.startswith("win"):
        for minor in range(SUPPORTED_PYTHON_MAX_EXCLUSIVE[1] - 1, SUPPORTED_PYTHON_MIN[1] - 1, -1):
            candidates.append(["py", f"-3.{minor}"])
        candidates.append(["py", "-3"])

    unique: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for cmd in candidates:
        key = tuple(cmd)
        if key in seen:
            continue
        seen.add(key)
        unique.append(cmd)
    return unique


def _resolve_supported_python_command() -> tuple[list[str], tuple[int, int, int]]:
    unsupported: list[str] = []
    for cmd in _supported_python_candidates():
        version = _python_version_for_command(cmd)
        if version is None:
            continue
        if _is_supported_python_version(version):
            return cmd, version
        unsupported.append(f"{_describe_python_command(cmd)} ({_format_python_version(version)})")
    detail = ""
    if unsupported:
        detail = " Unsupported candidates: " + ", ".join(unsupported)
    raise RuntimeError(f"Could not find a supported Python interpreter. Need {_format_python_requirement()}.{detail}")


def _read_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return default

def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _studio_package_manager_name() -> str:
    package_json = _read_json(PACKAGE_JSON_PATH, default={})
    if isinstance(package_json, dict):
        spec = str(package_json.get("packageManager", "")).strip()
        if spec:
            return spec.partition("@")[0] or DEFAULT_PACKAGE_MANAGER
    return DEFAULT_PACKAGE_MANAGER


def _windows_node_candidate_dirs() -> list[Path]:
    """Common Node install locations when the launcher was started without PATH."""
    if not sys.platform.startswith("win"):
        return []
    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA") or (home / "AppData" / "Local"))
    candidates = [root / "nodejs" for root in _windows_program_files_dirs()]
    candidates.extend([local / "Programs" / "nodejs", local / "nvm"])
    nvm_root = Path(os.environ.get("NVM_HOME") or (local / "nvm"))
    if nvm_root.is_dir():
        try:
            versions = sorted(
                (p for p in nvm_root.iterdir() if p.is_dir() and (p / "node.exe").exists()),
                key=lambda p: p.name,
                reverse=True,
            )
            candidates.extend(versions[:5])
        except OSError:
            pass
    seen: set[str] = set()
    out: list[Path] = []
    for d in candidates:
        try:
            key = str(d.resolve()).lower()
        except OSError:
            key = str(d).lower()
        if key in seen:
            continue
        seen.add(key)
        if d.is_dir():
            out.append(d)
    return out


def _windows_tool_candidate_dirs() -> list[Path]:
    """Extra dirs for tools the launcher may need when PATH is incomplete."""
    dirs = list(_windows_node_candidate_dirs())
    if not sys.platform.startswith("win"):
        return dirs
    local = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    extra = [root / "7-Zip" for root in _windows_program_files_dirs()]
    extra.extend(root / "ffmpeg" / "bin" for root in _windows_program_files_dirs())
    extra.extend([
        local / "Microsoft" / "WinGet" / "Links",
        BUNDLED_FFMPEG.parent if BUNDLED_FFMPEG.exists() else None,
    ])
    ffmpeg = _resolve_ffmpeg_path()
    try:
        ff_path = Path(ffmpeg)
        if ff_path.is_file():
            extra.append(ff_path.parent)
    except OSError:
        pass
    seven = _resolve_7z_path()
    if seven:
        try:
            extra.append(Path(seven).parent)
        except OSError:
            pass
    seen = {str(d.resolve()).lower() for d in dirs if d.is_dir()}
    for d in extra:
        if d is None:
            continue
        try:
            if not d.is_dir():
                continue
            key = str(d.resolve()).lower()
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        dirs.append(d)
    return dirs


def _which_on_path_or_node_dirs(exe: str) -> str | None:
    found = shutil.which(exe)
    if found:
        return found
    names = [exe]
    if sys.platform.startswith("win"):
        lower = exe.lower()
        if not lower.endswith((".exe", ".cmd", ".bat", ".com")):
            names = [f"{exe}.cmd", f"{exe}.exe", f"{exe}.bat", exe]
    for directory in _windows_tool_candidate_dirs():
        for name in names:
            candidate = directory / name
            if candidate.is_file():
                return str(candidate)
    return None


def _resolve_package_manager_command(name: str) -> tuple[list[str], str] | None:
    direct = _which_on_path_or_node_dirs(name)
    if direct:
        return [direct], direct
    if name == "pnpm":
        corepack = _which_on_path_or_node_dirs("corepack")
        if corepack:
            return [corepack, "pnpm"], f"{corepack} pnpm"
    return None


def _env_with_node_bin_dirs(env: dict[str, str] | None = None) -> dict[str, str]:
    """Ensure Node/pnpm/FFmpeg/7-Zip dirs are on PATH for child processes."""
    out = dict(env if env is not None else os.environ)
    path_key = "Path" if sys.platform.startswith("win") and "Path" in out and "PATH" not in out else "PATH"
    current = out.get(path_key) or out.get("PATH") or ""
    parts = [p for p in current.split(os.pathsep) if p]
    prepend: list[str] = []
    for directory in _windows_tool_candidate_dirs():
        s = str(directory)
        if s and s not in parts and s not in prepend:
            prepend.append(s)
    if prepend:
        out[path_key] = os.pathsep.join([*prepend, *parts]) if parts else os.pathsep.join(prepend)
        if path_key == "Path":
            out["PATH"] = out[path_key]
    ffmpeg_path = _resolve_ffmpeg_path()
    out["EDMG_FFMPEG_PATH"] = ffmpeg_path
    seven = _resolve_7z_path()
    if seven:
        out["EDMG_7Z_PATH"] = seven
    return out

def _user_appdata_dir() -> Path:
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))

def _bootstrap_config_path() -> Path:
    return _user_appdata_dir() / "EDMG Studio" / BOOTSTRAP_CONFIG_BASENAME

# Populated while resolving Studio storage paths; consumed by Launcher.__init__ for UI logs.
_PATH_RESOLUTION_NOTES: list[str] = []


def _note_path_resolution(message: str) -> None:
    text = str(message).strip()
    if text:
        _PATH_RESOLUTION_NOTES.append(text)


def _windows_drive_usable(path: Path) -> bool:
    """Return False when path is on a missing/unmounted Windows drive letter."""
    if os.name != "nt":
        return True
    anchor = path.anchor
    if not anchor:
        return True
    try:
        return bool(Path(anchor).exists())
    except Exception:
        return False


def _available_windows_drive_letters() -> list[str]:
    """Mounted Windows drive letters (A-Z) that currently exist."""
    if os.name != "nt":
        return []
    letters: list[str] = []
    for code in range(ord("A"), ord("Z") + 1):
        letter = chr(code)
        root = Path(f"{letter}:\\")
        try:
            if root.exists():
                letters.append(letter)
        except OSError:
            continue
    return letters


def _discover_missing_drive_remaps(path: Path) -> list[Path]:
    """Discover ``{host}:\\{letter}\\rest`` when ``{letter}:`` is missing.

    Scans mounted drives for a folder named like the missing letter (common when
    a volume is remounted under another drive). Prefer remaps whose full path
    exists; otherwise return letter-root hits so callers can still choose.
    """
    if os.name != "nt":
        return []
    match = re.match(r"^([A-Za-z]):[\\/]*(.*)$", str(path))
    if not match:
        return []
    letter = match.group(1).upper()
    rest = match.group(2).replace("/", "\\").strip("\\")

    existing: list[Path] = []
    letter_root_hits: list[Path] = []
    for host in _available_windows_drive_letters():
        if host == letter:
            continue
        letter_root = Path(f"{host}:\\{letter}")
        try:
            if not letter_root.exists():
                continue
        except OSError:
            continue
        remapped = letter_root / rest if rest else letter_root
        letter_root_hits.append(remapped)
        try:
            if remapped.exists():
                existing.append(remapped)
        except OSError:
            continue
    return existing or letter_root_hits


def _host_letter_root(remapped: Path) -> Path | None:
    """Return the former drive-letter mount root beneath a remapped host drive."""
    parts = remapped.parts
    if len(parts) < 2:
        return None
    return Path(remapped.anchor) / parts[1]


def _coerce_usable_path(path: Path) -> Path | None:
    """Return path if usable, or a discovered remount under another drive letter."""
    try:
        resolved = path.expanduser().resolve()
    except Exception:
        try:
            resolved = path.expanduser().absolute()
        except Exception:
            return None

    if _windows_drive_usable(resolved):
        return resolved

    candidates = _discover_missing_drive_remaps(resolved)
    if not candidates:
        _note_path_resolution(
            f"Ignoring unusable Studio path (missing drive, no remount discovered): {resolved}"
        )
        return None

    # Prefer a remap whose full path exists; otherwise first discovered candidate.
    chosen: Path | None = None
    for remapped in candidates:
        try:
            remapped_resolved = remapped.resolve()
        except Exception:
            remapped_resolved = remapped

        if not _windows_drive_usable(remapped_resolved):
            continue

        try:
            path_exists = bool(remapped_resolved.exists())
        except Exception:
            path_exists = False

        if path_exists:
            chosen = remapped_resolved
            break
        if chosen is None:
            chosen = remapped_resolved

    if chosen is None:
        _note_path_resolution(
            f"Ignoring unusable Studio path (missing drive, remount candidates unusable): {resolved}"
        )
        return None

    _note_path_resolution(f"Remapped missing-drive Studio path {resolved} -> {chosen}")
    return chosen


def _saved_path_if_usable(raw_value: str | None) -> Path | None:
    value = str(raw_value or "").strip()
    if not value:
        return None
    return _coerce_usable_path(Path(value))


def _local_fallback_studio_home() -> Path:
    """Usable default when configured Studio home is on a missing drive."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return (base / "EDMG Studio" / "home").resolve()
    xdg = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return (xdg / "edmg-studio" / "home").resolve()

def _derive_studio_home(data_dir: Path) -> Path:
    return data_dir.expanduser().resolve().parent

def _default_storage_env(studio_home: Path, data_dir: Path | None = None) -> dict[str, str]:
    home = studio_home.expanduser().resolve()
    data = data_dir.expanduser().resolve() if data_dir is not None else (home / "data").resolve()
    models = (home / "models").resolve()
    cache = (home / "cache").resolve()
    huggingface = cache / "huggingface"
    huggingface_hub = huggingface / "hub"
    huggingface_assets = huggingface / "assets"
    return {
        "EDMG_STUDIO_HOME": str(home),
        "EDMG_STUDIO_DATA_DIR": str(data),
        "EDMG_STUDIO_MODELS_DIR": str(models),
        "EDMG_STUDIO_CACHE_DIR": str(cache),
        "EDMG_STUDIO_LOGS_DIR": str((home / "logs").resolve()),
        "EDMG_STUDIO_EXTERNAL_DIR": str((home / "external").resolve()),
        "OLLAMA_MODELS": str((models / "ollama").resolve()),
        "HF_HOME": str(huggingface),
        "HF_HUB_CACHE": str(huggingface_hub),
        "HF_XET_CACHE": str(huggingface / "xet"),
        "HF_ASSETS_CACHE": str(huggingface_assets),
        "HUGGINGFACE_HUB_CACHE": str(huggingface_hub),
        "HUGGINGFACE_ASSETS_CACHE": str(huggingface_assets),
        "TRANSFORMERS_CACHE": str(cache / "transformers"),
    }

def _persist_studio_location(*, studio_home: Path | None = None, data_dir: Path | None = None) -> tuple[Path, Path]:
    if studio_home is None and data_dir is None:
        raise ValueError("studio_home or data_dir is required")

    if studio_home is not None:
        studio_home = studio_home.expanduser().resolve()
    if data_dir is not None:
        data_dir = data_dir.expanduser().resolve()

    if studio_home is None:
        assert data_dir is not None
        studio_home = _derive_studio_home(data_dir)
    if data_dir is None:
        data_dir = (studio_home / "data").resolve()

    storage_env = _default_storage_env(studio_home, data_dir)
    for key, value in storage_env.items():
        os.environ[key] = value

    cfg = _read_json(LAUNCHER_ENV_PATH, default={})
    if not isinstance(cfg, dict):
        cfg = {}
    cfg.update(storage_env)
    _write_json(LAUNCHER_ENV_PATH, cfg)

    bootstrap = _read_json(_bootstrap_config_path(), default={})
    if not isinstance(bootstrap, dict):
        bootstrap = {}
    bootstrap["studioHome"] = str(studio_home)
    bootstrap["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _write_json(_bootstrap_config_path(), bootstrap)

    return studio_home, data_dir

def _studio_path_set(studio_home: Path, data_dir: Path | None = None) -> dict[str, str]:
    home = studio_home.expanduser().resolve()
    data = data_dir.expanduser().resolve() if data_dir is not None else (home / "data").resolve()
    electron = (home / "electron").resolve()
    return {
        "studioHome": str(home),
        "dataDir": str(data),
        "modelsDir": str((home / "models").resolve()),
        "cacheRoot": str((home / "cache").resolve()),
        "externalDir": str((home / "external").resolve()),
        "electronUserData": str(electron),
        "sessionData": str((electron / "session").resolve()),
        "logsDir": str((home / "logs").resolve()),
    }

def _queue_studio_migration(source_home: Path, source_data_dir: Path, target_home: Path) -> bool:
    source = _studio_path_set(source_home, source_data_dir)
    target = _studio_path_set(target_home)
    if source["studioHome"] == target["studioHome"] and source["dataDir"] == target["dataDir"]:
        bootstrap = _read_json(_bootstrap_config_path(), default={})
        if isinstance(bootstrap, dict) and "pendingMigration" in bootstrap:
            del bootstrap["pendingMigration"]
            _write_json(_bootstrap_config_path(), bootstrap)
        return False

    bootstrap = _read_json(_bootstrap_config_path(), default={})
    if not isinstance(bootstrap, dict):
        bootstrap = {}
    bootstrap["pendingMigration"] = {
        "requestedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": source,
        "target": target,
    }
    _write_json(_bootstrap_config_path(), bootstrap)
    return True

def _default_data_dir() -> Path:
    # Keep runtime data OUTSIDE python_backend/ to avoid packaging issues.
    env_home = os.environ.get("EDMG_STUDIO_HOME", "").strip()
    if env_home:
        usable_home = _saved_path_if_usable(env_home)
        if usable_home is not None:
            return (usable_home / "data")

    cur = os.environ.get("EDMG_STUDIO_DATA_DIR", "").strip()
    if cur:
        usable_data = _saved_path_if_usable(cur)
        if usable_data is not None:
            return usable_data

    bootstrap = _read_json(_bootstrap_config_path(), default={})
    if isinstance(bootstrap, dict):
        saved_home = _saved_path_if_usable(bootstrap.get("studioHome"))
        if saved_home is not None:
            return (saved_home / "data")

    cfg = _read_json(LAUNCHER_ENV_PATH, default={})
    if isinstance(cfg, dict):
        saved_home = _saved_path_if_usable(cfg.get("EDMG_STUDIO_HOME"))
        if saved_home is not None:
            return (saved_home / "data")
        saved_data = _saved_path_if_usable(cfg.get("EDMG_STUDIO_DATA_DIR"))
        if saved_data is not None:
            return saved_data

    return (STUDIO_DIR / "data").resolve()

def _ensure_data_dir_env() -> Path:
    # Priority: explicit env -> Studio bootstrap -> launcher config -> default.
    # Env often comes from launcher_env.json via edmg_studio_backend.__init__;
    # never trust those paths blindly on a missing Windows drive letter.
    env_home = os.environ.get("EDMG_STUDIO_HOME", "").strip()
    if env_home:
        usable_home = _saved_path_if_usable(env_home)
        if usable_home is not None:
            _, p = _persist_studio_location(studio_home=usable_home)
            return p
        _note_path_resolution(f"Ignoring unusable EDMG_STUDIO_HOME from environment: {env_home}")

    cur = os.environ.get("EDMG_STUDIO_DATA_DIR", "").strip()
    if cur:
        usable_data = _saved_path_if_usable(cur)
        if usable_data is not None:
            _, p = _persist_studio_location(data_dir=usable_data)
            return p
        _note_path_resolution(f"Ignoring unusable EDMG_STUDIO_DATA_DIR from environment: {cur}")

    bootstrap = _read_json(_bootstrap_config_path(), default={})
    if isinstance(bootstrap, dict):
        saved_home = _saved_path_if_usable(bootstrap.get("studioHome"))
        if saved_home is not None:
            _, p = _persist_studio_location(studio_home=saved_home)
            return p

    cfg = _read_json(LAUNCHER_ENV_PATH, default={})
    if isinstance(cfg, dict):
        saved_home = _saved_path_if_usable(cfg.get("EDMG_STUDIO_HOME"))
        if saved_home is not None:
            _, p = _persist_studio_location(studio_home=saved_home)
            return p
        saved = _saved_path_if_usable(cfg.get("EDMG_STUDIO_DATA_DIR"))
        if saved is not None:
            _, p = _persist_studio_location(data_dir=saved)
            return p

    default_data = _default_data_dir()
    _note_path_resolution(
        f"No usable configured Studio home; using default data dir: {default_data}"
    )
    _, p = _persist_studio_location(data_dir=default_data)
    return p


def _mkdir_or_fallback(path: Path, *, label: str) -> Path:
    """Create path, or switch to a local fallback home if the drive is missing."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError as exc:
        winerror = getattr(exc, "winerror", None)
        _note_path_resolution(
            f"Failed to create {label} at {path} ({exc}); "
            f"falling back to a local Studio home"
            + (f" [winerror={winerror}]" if winerror is not None else "")
        )
        fallback_home = _local_fallback_studio_home()
        _, fallback_data = _persist_studio_location(studio_home=fallback_home)
        if label == "studio home":
            target = fallback_home
        elif label == "studio log dir":
            target = (fallback_data / "logs").resolve()
        else:
            target = fallback_data
        target.mkdir(parents=True, exist_ok=True)
        return target

def _safe_merge_copy(src: Path, dst: Path) -> tuple[int, int]:
    """Copy src -> dst, merging directories.

    Returns (files_copied, files_renamed) where renamed indicates name-collision renames.
    """
    files = 0
    renamed = 0
    if src.is_dir():
        dst.mkdir(parents=True, exist_ok=True)
        for child in src.iterdir():
            f, r = _safe_merge_copy(child, dst / child.name)
            files += f
            renamed += r
        return files, renamed

    dst.parent.mkdir(parents=True, exist_ok=True)
    target = dst
    if target.exists():
        # Avoid overwriting; add suffix.
        stem = target.stem
        suf = target.suffix
        i = 1
        while True:
            cand = target.with_name(f"{stem}_dup{i}{suf}")
            if not cand.exists():
                target = cand
                renamed += 1
                break
            i += 1
    shutil.copy2(src, target)
    files += 1
    return files, renamed


def _migrate_legacy_data_dir(new_data_dir: Path) -> str | None:
    """Migrate legacy runtime data into new_data_dir.

    Legacy locations we support:
      - studio/edmg-studio/python_backend/data   (must never be treated as project metadata)

    We never delete user data:
      - We merge-copy into new_data_dir
      - Then we move the legacy folder into studio/edmg-studio/_legacy_migrations/...
    """
    legacy = BACKEND_DIR / "data"
    try:
        legacy = legacy.resolve()
        new_data_dir = new_data_dir.resolve()
    except Exception:
        pass

    if not legacy.exists() or not legacy.is_dir():
        return None
    if legacy == new_data_dir:
        return None

    new_data_dir.mkdir(parents=True, exist_ok=True)
    files, renamed = _safe_merge_copy(legacy, new_data_dir)

    backup_root = STUDIO_DIR / "_legacy_migrations"
    backup_root.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    backup = backup_root / f"python_backend_data_{ts}"
    try:
        shutil.move(str(legacy), str(backup))
    except Exception:
        # If move fails, leave it in place but warn.
        return f"Copied {files} files to {new_data_dir} (renamed {renamed} on collisions). WARNING: could not move legacy folder {legacy}."

    return f"Migrated legacy data: copied {files} files to {new_data_dir} (renamed {renamed}), moved old folder to {backup}."



def _try_create_junction(link_path: Path, target: Path) -> bool:
    """Best-effort create a directory junction (Windows) for backwards-compat paths."""
    if not sys.platform.startswith("win"):
        return False
    try:
        link_path.parent.mkdir(parents=True, exist_ok=True)
        # mklink is a cmd builtin
        cmd = ["cmd", "/c", "mklink", "/J", str(link_path), str(target)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        return proc.returncode == 0
    except Exception:
        return False


def _migrate_repo_root_data_dir(new_data_dir: Path) -> str | None:
    """Migrate legacy repo-root ./data into new_data_dir.

    Why:
    - Older builds often wrote runtime artifacts into repo-root ./data.
    - Leaving it there can cause confusion; we centralize under Studio data dir.

    Safety:
    - Merge-copy into new_data_dir
    - Move legacy folder into studio/edmg-studio/_legacy_migrations/...
    - Recreate a junction at the original path to new_data_dir when possible.
    """
    legacy = ROOT / "data"
    try:
        legacy = legacy.resolve()
        new_data_dir = new_data_dir.resolve()
    except Exception:
        pass

    if not legacy.exists() or not legacy.is_dir():
        return None
    if legacy == new_data_dir:
        return None

    # If legacy is already a junction/symlink pointing at new_data_dir, do nothing.
    try:
        if legacy.samefile(new_data_dir):
            return None
    except Exception:
        pass

    new_data_dir.mkdir(parents=True, exist_ok=True)
    files, renamed = _safe_merge_copy(legacy, new_data_dir)

    backup_root = STUDIO_DIR / "_legacy_migrations"
    backup_root.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    backup = backup_root / f"repo_root_data_{ts}"
    try:
        shutil.move(str(legacy), str(backup))
    except Exception:
        return f"Copied {files} files from repo-root data/ to {new_data_dir} (renamed {renamed}). WARNING: could not move legacy folder {legacy}."

    # Recreate compatibility path (junction preferred).
    try:
        if _try_create_junction(ROOT / "data", new_data_dir):
            return f"Migrated repo-root data/: copied {files} files to {new_data_dir} (renamed {renamed}), moved old folder to {backup}, created junction data/ -> {new_data_dir}."
    except Exception:
        pass

    # Fallback: create stub folder with note.
    try:
        stub = ROOT / "data"
        stub.mkdir(parents=True, exist_ok=True)
        (stub / "MOVED_TO.txt").write_text(f"This folder was migrated to:\n{new_data_dir}\n", encoding="utf-8")
    except Exception:
        pass
    return f"Migrated repo-root data/: copied {files} files to {new_data_dir} (renamed {renamed}), moved old folder to {backup}."


def _migrate_work_dir(src: Path, dst: Path, *, label: str, create_junction: bool) -> str | None:
    """Migrate a legacy work/output directory into the canonical Studio data dir."""
    try:
        src = src.resolve()
        dst = dst.resolve()
    except Exception:
        pass

    if not src.exists() or not src.is_dir():
        return None
    if src == dst:
        return None
    try:
        if src.samefile(dst):
            return None
    except Exception:
        pass

    dst.mkdir(parents=True, exist_ok=True)
    files, renamed = _safe_merge_copy(src, dst)

    backup_root = STUDIO_DIR / "_legacy_migrations"
    backup_root.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    backup = backup_root / f"{label}_{ts}"
    try:
        shutil.move(str(src), str(backup))
    except Exception:
        return f"Copied {files} files from {label} to {dst} (renamed {renamed}). WARNING: could not move legacy folder {src}."

    if create_junction:
        try:
            if _try_create_junction(src, dst):
                return f"Migrated {label}: copied {files} files to {dst} (renamed {renamed}), moved old folder to {backup}, created junction {src} -> {dst}."
        except Exception:
            pass

    # Fallback: create stub note.
    try:
        src.mkdir(parents=True, exist_ok=True)
        (src / "MOVED_TO.txt").write_text(f"This folder was migrated to:\n{dst}\n", encoding="utf-8")
    except Exception:
        pass
    return f"Migrated {label}: copied {files} files to {dst} (renamed {renamed}), moved old folder to {backup}."


def _migrate_legacy_work_dirs(new_data_dir: Path) -> str | None:
    """Migrate common legacy work dirs like python_backend/.edmg_work into new_data_dir."""
    msgs = []
    # Legacy: inside python_backend (do NOT junction to avoid packaging/installation issues)
    m1 = _migrate_work_dir(BACKEND_DIR / ".edmg_work", new_data_dir / "work" / "edmg_work", label="python_backend_edmg_work", create_junction=False)
    if m1:
        msgs.append(m1)

    # Legacy: repo root .edmg_work (junction is OK for compatibility)
    m2 = _migrate_work_dir(ROOT / ".edmg_work", new_data_dir / "work" / "edmg_work", label="repo_root_edmg_work", create_junction=True)
    if m2:
        msgs.append(m2)

    return "\n".join(msgs) if msgs else None

def _migrate_legacy_data_dirs(new_data_dir: Path) -> str | None:
    msgs = []
    m1 = _migrate_legacy_data_dir(new_data_dir)
    if m1:
        msgs.append(m1)
    m2 = _migrate_repo_root_data_dir(new_data_dir)
    if m2:
        msgs.append(m2)
    m3 = _migrate_legacy_work_dirs(new_data_dir)
    if m3:
        msgs.append(m3)
    return "\n".join(msgs) if msgs else None


def _ensure_backend_env() -> tuple[str, int]:
    """Ensure backend host/port env vars are set and persisted.

    Priority: explicit env -> launcher_env.json -> defaults.
    """
    cfg = _read_json(LAUNCHER_ENV_PATH, default={})
    if not isinstance(cfg, dict):
        cfg = {}

    host = (os.environ.get("EDMG_STUDIO_BACKEND_HOST") or str(cfg.get("EDMG_STUDIO_BACKEND_HOST") or "")).strip()
    if not host:
        host = DEFAULT_BACKEND_HOST

    port_raw = (os.environ.get("EDMG_STUDIO_BACKEND_PORT") or str(cfg.get("EDMG_STUDIO_BACKEND_PORT") or "")).strip()
    try:
        port = int(port_raw) if port_raw else DEFAULT_BACKEND_PORT
    except Exception:
        port = DEFAULT_BACKEND_PORT
    if port == DEFAULT_UI_PORT:
        port = DEFAULT_BACKEND_PORT

    os.environ["EDMG_STUDIO_BACKEND_HOST"] = host
    os.environ["EDMG_STUDIO_BACKEND_PORT"] = str(port)

    cfg["EDMG_STUDIO_BACKEND_HOST"] = host
    cfg["EDMG_STUDIO_BACKEND_PORT"] = port
    if os.environ.get("EDMG_STUDIO_DATA_DIR"):
        cfg["EDMG_STUDIO_DATA_DIR"] = os.environ["EDMG_STUDIO_DATA_DIR"]
    if os.environ.get("EDMG_STUDIO_HOME"):
        cfg["EDMG_STUDIO_HOME"] = os.environ["EDMG_STUDIO_HOME"]
    _write_json(LAUNCHER_ENV_PATH, cfg)
    return host, port


def _is_port_bindable(host: str, port: int) -> bool:
    import socket
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, port))
        return True
    except Exception:
        return False
    finally:
        try:
            if s:
                s.close()
        except Exception:
            pass


def _listening_pid(port: int) -> int | None:
    """Best-effort PID of process listening on TCP port (Windows-first)."""
    try:
        if sys.platform.startswith("win"):
            proc = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True)
            if proc.returncode != 0:
                return None
            pat = re.compile(rf":{port}\s+LISTENING\s+(\d+)\s*$", re.IGNORECASE)
            pat6 = re.compile(rf"\]:{port}\s+LISTENING\s+(\d+)\s*$", re.IGNORECASE)
            for line in proc.stdout.splitlines():
                line = line.strip()
                m = pat.search(line) or pat6.search(line)
                if m:
                    return int(m.group(1))
            return None

        if shutil.which("lsof"):
            proc = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"], capture_output=True, text=True)
            if proc.returncode != 0:
                return None
            for line in proc.stdout.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    return int(parts[1])
    except Exception:
        return None
    return None


def _pid_name(pid: int) -> str | None:
    try:
        if sys.platform.startswith("win"):
            proc = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                return None
            line = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""
            if not line:
                return None
            if line.startswith('"'):
                return line.split('","')[0].strip('"')
            return line.split()[0]
        proc = subprocess.run(["ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True)
        if proc.returncode != 0:
            return None
        return proc.stdout.strip() or None
    except Exception:
        return None


def _port_doctor_line(name: str, host: str, port: int, *, health_url: str | None = None) -> tuple[bool, str]:
    """Return (ok, line). ok means service reachable (if health_url provided)."""
    url = f"http://{host}:{port}"
    pid = _listening_pid(port)
    proc_name = _pid_name(pid) if pid else None

    if health_url:
        try:
            _http_get(health_url, timeout=1.2)
            return True, f"{name}: OK ({url})"
        except Exception:
            if pid:
                return False, f"{name}: not reachable, port in use by PID {pid}{' ('+proc_name+')' if proc_name else ''} ({url})"
            return False, f"{name}: not reachable ({url})"

    if pid:
        return True, f"{name}: port open (PID {pid}{' ('+proc_name+')' if proc_name else ''}) ({url})"
    return False, f"{name}: not detected ({url})"


def _find_free_port(host: str, start_port: int, *, max_tries: int = 50) -> int:
    for p in range(start_port, start_port + max_tries):
        if _is_port_bindable(host, p):
            return p
    raise RuntimeError(f"No free port found in range {start_port}..{start_port+max_tries-1} for host {host}")


LOG_MAX_CHARS = 200_000

def _run_cmd(cmd, cwd=None, env=None, log_cb=None):
    p = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )
    for line in p.stdout:
        if log_cb:
            log_cb(line.rstrip("\n"))
    return p.wait()

def _http_get(url: str, timeout=3.0):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent":"EDMG-Studio-Launcher"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")

def _tail_file(path: Path, max_bytes: int = 200_000) -> str:
    """Return last max_bytes of a text file."""
    try:
        if not path.exists():
            return ""
        size = path.stat().st_size
        with path.open("rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            data = f.read()
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _sync_locked_backend(profile: str, log_cb) -> None:
    """Materialize and verify one lock-selected accelerator environment."""
    capabilities = ", ".join(RUNTIME_CAPABILITY_EXTRAS)
    log_cb(f"Checking uv.lock and syncing frozen `{profile}` profile ({capabilities})…")
    uv = sync_frozen_project(profile, capability_extras=RUNTIME_CAPABILITY_EXTRAS, install_uv=True)
    log_cb(f"uv {uv_version(uv)}: {uv}")
    verify, env = frozen_run_command(
        profile,
        [
            "python",
            "-c",
            (
                "import platform,hf_transfer,hf_xet,torch,torchvision,torchaudio;"
                "print('python', platform.python_version());"
                "print('hf_transfer', hf_transfer.__version__);"
                "print('hf_xet', hf_xet.__file__);"
                "print('torch', torch.__version__);"
                "print('torchvision', torchvision.__version__);"
                "print('torchaudio', torchaudio.__version__)"
            ),
        ],
        capability_extras=RUNTIME_CAPABILITY_EXTRAS,
    )
    if _run_cmd(verify, cwd=BACKEND_DIR, env=env, log_cb=log_cb) != 0:
        log_cb(
            f"Frozen `{profile}` verification found an incomplete package installation; "
            "reinstalling the locked environment…"
        )
        uv = sync_frozen_project(
            profile,
            capability_extras=RUNTIME_CAPABILITY_EXTRAS,
            install_uv=True,
            reinstall=True,
        )
        log_cb(f"uv {uv_version(uv)}: {uv}")
        if _run_cmd(verify, cwd=BACKEND_DIR, env=env, log_cb=log_cb) != 0:
            raise RuntimeError(f"Frozen `{profile}` environment verification failed after repair")
    log_cb(f"Frozen `{profile}` backend is ready (lock {lock_sha256()[:12]}…).")


def _parse_backend_url_from_logs(text: str) -> tuple[str, int] | None:
    """Parse EDMG_BACKEND_URL marker from Electron logs."""
    for pattern in (
        r"EDMG_BACKEND_URL=(https?://[^\s]+)",
        r"\[backend\][^\n]*?(https?://(?:127\.0\.0\.1|localhost):\d{4,5})",
    ):
        m = re.search(pattern, text)
        if not m:
            continue
        u = m.group(1).strip().rstrip("/")
        m2 = re.search(r"^https?://([^:/]+):(\d+)", u)
        if m2:
            return m2.group(1), int(m2.group(2))
    return None


class Launcher(tk.Tk):
    """Installer-style launcher for EDMG Studio.

    Features:
    - Migrates legacy .edmg_work and data/ into the canonical Studio data dir.
    - Port doctor + attach/switch/optional safe terminate.
    - Silent fix mode (attach/switch only; never kills processes).
    - Captures Electron dev logs and syncs backend host/port when Studio chooses a different port.
    """

    def __init__(self):
        super().__init__()
        self.title("EDMG Studio Launcher")
        self.geometry("920x620")
        self.minsize(920, 620)
        self._ui_thread_id = threading.get_ident()
        self._disposed = False
        self._busy_depth = 0
        self._busy_widgets: list[ttk.Button] = []
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.backend_proc: subprocess.Popen | None = None
        self.studio_proc: subprocess.Popen | None = None
        self._studio_log_fp = None

        self._refresh_in_progress = False

        _PATH_RESOLUTION_NOTES.clear()
        self.data_dir = _ensure_data_dir_env()
        env_home = os.environ.get("EDMG_STUDIO_HOME", "").strip()
        usable_home = _saved_path_if_usable(env_home) if env_home else None
        self.studio_home = usable_home or _derive_studio_home(self.data_dir)
        self.data_dir = _mkdir_or_fallback(self.data_dir, label="data dir")
        # mkdir fallback may have rewritten studio_home via persist
        env_home = os.environ.get("EDMG_STUDIO_HOME", "").strip()
        usable_home = _saved_path_if_usable(env_home) if env_home else None
        self.studio_home = usable_home or _derive_studio_home(self.data_dir)
        self.studio_log_path = (self.data_dir / "logs" / "studio_dev.log").resolve()
        log_parent = _mkdir_or_fallback(self.studio_log_path.parent, label="studio log dir")
        self.studio_log_path = (log_parent / "studio_dev.log").resolve()
        if log_parent != self.data_dir / "logs":
            self.data_dir = log_parent.parent
            env_home = os.environ.get("EDMG_STUDIO_HOME", "").strip()
            usable_home = _saved_path_if_usable(env_home) if env_home else None
            self.studio_home = usable_home or _derive_studio_home(self.data_dir)

        self._studio_log_pos = 0
        self._studio_log_poll_ms = 400
        self.var_follow_studio_log = tk.BooleanVar(value=True)

        self.backend_host, self.backend_port = _ensure_backend_env()

        self._startup_migration_msg = _migrate_legacy_data_dirs(self.data_dir)
        self._startup_path_notes = list(_PATH_RESOLUTION_NOTES)

        self._build_ui()
        self._refresh_status()

        self.after(500, self._poll_studio_log)

        self.after(250, self._auto_attach_backend_if_found)
        if self._startup_path_notes:
            notes = list(self._startup_path_notes)
            self.after(350, lambda: [self._log(n) for n in notes])
        if self._startup_migration_msg:
            self.after(400, lambda: self._log(self._startup_migration_msg))

    # ---------------- UI / logging ----------------

    def _on_close(self) -> None:
        self._disposed = True
        try:
            if self._studio_log_fp and not self._studio_log_fp.closed:
                self._studio_log_fp.flush()
                self._studio_log_fp.close()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass

    def _on_ui_thread(self) -> bool:
        return threading.get_ident() == self._ui_thread_id

    def _ui_alive(self) -> bool:
        if self._disposed:
            return False
        try:
            return bool(self.winfo_exists())
        except Exception:
            return False

    def _invoke_ui_callback(self, callback, *args, **kwargs) -> None:
        if not self._ui_alive():
            return
        callback(*args, **kwargs)

    def _call_on_ui_thread(self, callback, *args, delay_ms: int = 0, **kwargs) -> bool:
        if self._disposed:
            return False
        if self._on_ui_thread() and delay_ms <= 0:
            if not self._ui_alive():
                return False
            callback(*args, **kwargs)
            return True
        try:
            self.after(delay_ms, lambda: self._invoke_ui_callback(callback, *args, **kwargs))
            return True
        except Exception:
            return False

    def _show_error_dialog(self, title: str, message: str) -> None:
        def show() -> None:
            parent = self if self._ui_alive() else None
            messagebox.showerror(title, message, parent=parent)

        self._call_on_ui_thread(show)

    def _set_busy(self, active: bool) -> None:
        if not self._on_ui_thread():
            self._call_on_ui_thread(self._set_busy, active)
            return
        if not self._ui_alive():
            return

        if active:
            self._busy_depth += 1
        else:
            self._busy_depth = max(0, self._busy_depth - 1)
        busy = self._busy_depth > 0

        try:
            self.configure(cursor="watch" if busy else "")
        except Exception:
            pass

        for button in list(self._busy_widgets):
            try:
                if not button.winfo_exists():
                    continue
                if busy:
                    button.state(["disabled"])
                else:
                    button.state(["!disabled"])
            except Exception:
                pass

    def _append_text(self, widget: tk.Text, msg: str, *, max_lines: int, follow: bool = True) -> None:
        msg = str(msg).rstrip("\n")
        if not msg:
            return
        if len(msg) > 200_000:
            msg = msg[-200_000:]

        try:
            widget.configure(state="normal")
            widget.insert("end", msg + "\n")
            if follow:
                widget.see("end")

            try:
                lines = int(widget.index("end-1c").split(".")[0])
                if lines > max_lines:
                    widget.delete("1.0", f"{lines - max_lines}.0")
            except Exception:
                pass

            widget.configure(state="disabled")
        except Exception:
            print(msg)

    def _log(self, msg: str) -> None:
        if not self._on_ui_thread():
            self._call_on_ui_thread(self._log, msg)
            return
        if not self._ui_alive():
            print(msg)
            return
        if not hasattr(self, "txt"):
            print(msg)
            return
        self._append_text(self.txt, str(msg), max_lines=1500, follow=True)

    def _log_studio(self, msg: str) -> None:
        if not self._on_ui_thread():
            self._call_on_ui_thread(self._log_studio, msg)
            return
        if not self._ui_alive():
            return
        if not hasattr(self, "txt_studio"):
            return
        follow = True
        try:
            follow = bool(self.var_follow_studio_log.get())
        except Exception:
            pass
        self._append_text(self.txt_studio, str(msg), max_lines=2500, follow=follow)

    def _clear_studio_log_view(self) -> None:
        if not self._on_ui_thread():
            self._call_on_ui_thread(self._clear_studio_log_view)
            return
        if not self._ui_alive():
            return
        try:
            self.txt_studio.configure(state="normal")
            self.txt_studio.delete("1.0", "end")
            self.txt_studio.configure(state="disabled")
        except Exception:
            pass

    def _open_studio_log_file(self) -> None:
        try:
            p = str(self.studio_log_path)
            if sys.platform.startswith("win"):
                os.startfile(p)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", p])
            else:
                subprocess.Popen(["xdg-open", p])
        except Exception as e:
            self._log(f"Could not open log file: {e}")
    def _run_bg(self, title: str, fn) -> None:
        self._set_busy(True)

        def runner():
            try:
                self._log(f"== {title} ==")
                fn()
                self._log(f"== {title}: done ==")
            except Exception as e:
                self._log(f"!! {title} failed: {e}")
                try:
                    self._show_error_dialog("Error", f"{title} failed:\n{e}")
                except Exception:
                    pass
            finally:
                try:
                    self._refresh_status()
                except Exception:
                    pass
                self._set_busy(False)

        threading.Thread(target=runner, daemon=True).start()

    def _which(self, exe: str) -> str | None:
        return _which_on_path_or_node_dirs(exe)

    def _apply_studio_home(self, studio_home: Path, *, reason: str) -> None:
        migration_queued = _queue_studio_migration(self.studio_home, self.data_dir, studio_home)
        studio_home, data_dir = _persist_studio_location(studio_home=studio_home)
        self.studio_home = studio_home
        self.data_dir = _mkdir_or_fallback(data_dir, label="data dir")
        # mkdir fallback may have rewritten studio_home via persist
        env_home = os.environ.get("EDMG_STUDIO_HOME", "").strip()
        usable_home = _saved_path_if_usable(env_home) if env_home else None
        self.studio_home = usable_home or _derive_studio_home(self.data_dir)
        self.studio_log_path = (self.data_dir / "logs" / "studio_dev.log").resolve()
        log_parent = _mkdir_or_fallback(self.studio_log_path.parent, label="studio log dir")
        self.studio_log_path = (log_parent / "studio_dev.log").resolve()
        if log_parent != self.data_dir / "logs":
            self.data_dir = log_parent.parent
            env_home = os.environ.get("EDMG_STUDIO_HOME", "").strip()
            usable_home = _saved_path_if_usable(env_home) if env_home else None
            self.studio_home = usable_home or _derive_studio_home(self.data_dir)
        self._studio_log_pos = 0

        if hasattr(self, "var_studio_home"):
            self.var_studio_home.set(str(self.studio_home))
        if hasattr(self, "var_data_dir"):
            self.var_data_dir.set(str(self.data_dir))

        if hasattr(self, "txt"):
            self._log(f"Studio home set ({reason}): {self.studio_home}")
            if migration_queued:
                self._log("Existing Studio data will migrate into the new home on the next Studio launch.")
            if (self.backend_proc and self.backend_proc.poll() is None) or (self.studio_proc and self.studio_proc.poll() is None):
                self._log("Restart the running backend/Studio to apply the new storage location.")

    def _build_ui(self) -> None:
        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)

        top = ttk.Frame(frm)
        top.pack(fill="x")
        ttk.Label(top, text="EDMG Studio", font=("Segoe UI", 16, "bold")).pack(side="left")
        ttk.Label(top, text="Installer-style launcher (no CLI typing)", foreground="#555").pack(side="left", padx=12)

        # Paths
        path_row = ttk.LabelFrame(frm, text="Paths", padding=10)
        path_row.pack(fill="x", pady=(12, 8))

        self.var_root = tk.StringVar(value=str(ROOT))
        self.var_studio_home = tk.StringVar(value=str(self.studio_home))
        self.var_data_dir = tk.StringVar(value=str(self.data_dir))
        self.var_studio = tk.StringVar(value=str(STUDIO_DIR))
        self.var_backend = tk.StringVar(value=str(BACKEND_DIR))
        self.var_backend_host = tk.StringVar(value=str(self.backend_host))
        self.var_backend_port = tk.StringVar(value=str(self.backend_port))

        for label, var in [("Repo root", self.var_root), ("Studio dir", self.var_studio), ("Backend dir", self.var_backend)]:
            row = ttk.Frame(path_row)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=label, width=12).pack(side="left")
            ent = ttk.Entry(row, textvariable=var)
            ent.pack(side="left", fill="x", expand=True)

        # Backend host/port
        row = ttk.Frame(path_row)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="Backend host", width=12).pack(side="left")
        ttk.Entry(row, textvariable=self.var_backend_host, width=22).pack(side="left", padx=(0, 8))
        ttk.Label(row, text="Port").pack(side="left")
        ttk.Entry(row, textvariable=self.var_backend_port, width=10).pack(side="left", padx=(6, 0))

        def _apply_backend_host_port():
            host = self.var_backend_host.get().strip() or DEFAULT_BACKEND_HOST
            try:
                port = int(self.var_backend_port.get().strip() or str(DEFAULT_BACKEND_PORT))
            except Exception:
                port = DEFAULT_BACKEND_PORT
            self._set_backend_host_port(host, port, reason="manual")
        ttk.Button(row, text="Apply", command=_apply_backend_host_port).pack(side="left", padx=8)

        # Studio home
        row = ttk.Frame(path_row)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="Studio home", width=12).pack(side="left")
        ttk.Entry(row, textvariable=self.var_studio_home).pack(side="left", fill="x", expand=True)

        def _apply_studio_home():
            raw = self.var_studio_home.get().strip()
            if not raw:
                messagebox.showerror("Studio home", "Pick a valid Studio home folder first.")
                return
            self._apply_studio_home(Path(raw), reason="manual")
            self._refresh_status()

        def _browse_studio_home():
            d = filedialog.askdirectory(title="Select EDMG Studio home folder", initialdir=str(self.studio_home))
            if not d:
                return
            self._apply_studio_home(Path(d), reason="browse")
            self._refresh_status()

        ttk.Button(row, text="Apply", command=_apply_studio_home).pack(side="left", padx=6)
        ttk.Button(row, text="Browse…", command=_browse_studio_home).pack(side="left", padx=6)

        # Data dir
        row = ttk.Frame(path_row)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="Data dir", width=12).pack(side="left")
        ttk.Entry(row, textvariable=self.var_data_dir, state="readonly").pack(side="left", fill="x", expand=True)

        # Status
        stat = ttk.LabelFrame(frm, text="Status", padding=10)
        stat.pack(fill="x", pady=(0, 8))

        self.lbl_python = ttk.Label(stat, text="Python: …")
        self.lbl_node = ttk.Label(stat, text="Node: …")
        self.lbl_ollama = ttk.Label(stat, text="Ollama: …")
        self.lbl_comfyui = ttk.Label(stat, text="ComfyUI: …")
        self.lbl_backend = ttk.Label(stat, text="Backend: …")

        for w in [self.lbl_python, self.lbl_node, self.lbl_ollama, self.lbl_comfyui, self.lbl_backend]:
            w.pack(anchor="w")

        btn_stat = ttk.Frame(stat)
        btn_stat.pack(fill="x", pady=(8, 0))
        ttk.Button(btn_stat, text="Fix ports…", command=self.open_fix_ports_dialog).pack(side="left")
        ttk.Button(btn_stat, text="Fix ports (silent)", command=self.fix_ports_silent).pack(side="left", padx=8)
        ttk.Button(btn_stat, text="Rescan backend ports", command=self._auto_attach_backend_if_found).pack(side="left", padx=8)

        # Actions
        actions = ttk.LabelFrame(frm, text="Actions", padding=10)
        actions.pack(fill="x", pady=(0, 8))

        btn_row = ttk.Frame(actions)
        btn_row.pack(fill="x")

        ttk.Button(btn_row, text="Install/Update Backend (auto CUDA + TensorRT)", command=self.install_backend).pack(side="left")
        ttk.Button(btn_row, text="Refresh CUDA/TensorRT Runtime", command=self.install_cuda_torch).pack(side="left", padx=8)
        ttk.Button(btn_row, text=f"Install/Update Studio UI ({_studio_package_manager_name()} install)", command=self.install_ui).pack(side="left", padx=8)
        ttk.Button(btn_row, text="Start Backend", command=self.start_backend).pack(side="left", padx=8)
        ttk.Button(btn_row, text="Stop Backend", command=self.stop_backend).pack(side="left")
        ttk.Button(btn_row, text="Run Health Test", command=self.health_test).pack(side="left", padx=8)
        ttk.Button(btn_row, text="Start Studio (Electron dev)", command=self.start_studio).pack(side="left", padx=8)
        ttk.Button(btn_row, text="Restart Studio", command=self.restart_studio).pack(side="left", padx=8)
        ttk.Button(btn_row, text="Stop Studio", command=self.stop_studio).pack(side="left")

        # Machine optimisation toggle
        opt_row = ttk.Frame(actions)
        opt_row.pack(fill="x", pady=(8, 0))
        ttk.Label(opt_row, text="Machine Optimizer:").pack(side="left")
        self._opt_status_var = tk.StringVar(value=self._optimize_current_label())
        ttk.Label(opt_row, textvariable=self._opt_status_var, foreground="#888").pack(side="left", padx=6)
        ttk.Button(opt_row, text="⚡ Optimize for Dev (ON)",
                   command=self.optimize_machine).pack(side="left", padx=(8, 4))
        ttk.Button(opt_row, text="↩ Restore Normal (OFF)",
                   command=self.restore_machine).pack(side="left")

        # Packaging (Windows)
        pkg_row = ttk.Frame(actions)
        pkg_row.pack(fill="x", pady=(8, 0))
        ttk.Button(pkg_row, text="Get FFmpeg (bundle for Studio renderer)", command=self.get_ffmpeg).pack(side="left")
        ttk.Button(pkg_row, text="Build Installer (NSIS)", command=self.build_installer).pack(side="left", padx=8)
        ttk.Button(pkg_row, text="Build Inno Installer (large payload)", command=self.build_inno_installer).pack(side="left")
        ttk.Button(pkg_row, text="Open Release Folder", command=self.open_release_folder).pack(side="left")
        ttk.Button(pkg_row, text="Open Inno Folder", command=self.open_inno_folder).pack(side="left", padx=8)

        # Log
        logbox = ttk.LabelFrame(frm, text="Logs", padding=10)
        logbox.pack(fill="both", expand=True)

        nb = ttk.Notebook(logbox)
        nb.pack(fill="both", expand=True)

        tab_launcher = ttk.Frame(nb)
        tab_studio = ttk.Frame(nb)
        nb.add(tab_launcher, text="Launcher Log")
        nb.add(tab_studio, text="Studio Dev Log (live)")

        # Launcher log
        self.txt = tk.Text(tab_launcher, height=18, wrap="word")
        sb1 = ttk.Scrollbar(tab_launcher, command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb1.set)
        self.txt.pack(side="left", fill="both", expand=True)
        sb1.pack(side="right", fill="y")
        self.txt.configure(state="disabled")

        # Studio log toolbar
        studio_toolbar = ttk.Frame(tab_studio)
        studio_toolbar.pack(fill="x", pady=(0, 6))
        ttk.Checkbutton(studio_toolbar, text="Follow", variable=self.var_follow_studio_log).pack(side="left")
        ttk.Button(studio_toolbar, text="Open log file…", command=self._open_studio_log_file).pack(side="left", padx=8)
        ttk.Button(studio_toolbar, text="Clear view", command=self._clear_studio_log_view).pack(side="left")

        studio_body = ttk.Frame(tab_studio)
        studio_body.pack(fill="both", expand=True)

        self.txt_studio = tk.Text(studio_body, height=18, wrap="none")
        sb2 = ttk.Scrollbar(studio_body, command=self.txt_studio.yview)
        self.txt_studio.configure(yscrollcommand=sb2.set)
        self.txt_studio.pack(side="left", fill="both", expand=True)
        sb2.pack(side="right", fill="y")
        self.txt_studio.configure(state="disabled")

        ttk.Button(frm, text="Refresh status", command=self._refresh_status).pack(anchor="e", pady=(8, 0))
        self._busy_widgets = [widget for widget in self.winfo_children_recursive() if isinstance(widget, ttk.Button)]

    def winfo_children_recursive(self):
        stack = list(self.winfo_children())
        while stack:
            child = stack.pop(0)
            yield child
            try:
                stack.extend(child.winfo_children())
            except Exception:
                continue

    # ---------------- backend host/port persistence ----------------

    def _set_backend_host_port(self, host: str, port: int, *, reason: str) -> None:
        host = host.strip() or DEFAULT_BACKEND_HOST
        port = int(port)
        self.backend_host = host
        self.backend_port = port

        os.environ["EDMG_STUDIO_BACKEND_HOST"] = host
        os.environ["EDMG_STUDIO_BACKEND_PORT"] = str(port)

        cfg = _read_json(LAUNCHER_ENV_PATH, default={})
        if not isinstance(cfg, dict):
            cfg = {}
        cfg["EDMG_STUDIO_BACKEND_HOST"] = host
        cfg["EDMG_STUDIO_BACKEND_PORT"] = port
        cfg.update(_default_storage_env(self.studio_home, self.data_dir))
        _write_json(LAUNCHER_ENV_PATH, cfg)

        self._call_on_ui_thread(self._apply_backend_host_port_ui, host, port, reason)

    def _apply_backend_host_port_ui(self, host: str, port: int, reason: str) -> None:
        if not self._ui_alive():
            return
        if hasattr(self, "var_backend_host"):
            self.var_backend_host.set(host)
        if hasattr(self, "var_backend_port"):
            self.var_backend_port.set(str(port))
        self._log(f"Backend host/port set ({reason}): {host}:{port}")

    # ---------------- health / scan / sync ----------------

    def _backend_health_ok(self, host: str, port: int) -> bool:
        try:
            body = _http_get(f"http://{host}:{port}/health", timeout=0.9)
            data = json.loads(body)
            return isinstance(data, dict) and data.get("ok") is True
        except Exception:
            return False

    def _scan_for_running_backend(self, host: str, start_port: int, end_port: int) -> int | None:
        for p in range(start_port, end_port + 1):
            if self._backend_health_ok(host, p):
                return p
        return None

    def _sync_backend_from_studio_logs(self) -> bool:
        """If Electron dev logs show a backend URL, sync launcher to it."""
        parsed = _parse_backend_url_from_logs(_tail_file(self.studio_log_path))
        if not parsed:
            return False
        host, port = parsed
        try:
            port = int(port)
        except Exception:
            port = DEFAULT_BACKEND_PORT

        if (host != self.backend_host) or (port != int(self.backend_port)):
            self._set_backend_host_port(host, port, reason="studio-log-scan")
            if self._backend_health_ok(host, port):
                self._log(f"Synced backend from Studio logs: {host}:{port}.")
            else:
                self._log(f"Studio logs indicate backend at {host}:{port} (not healthy yet).")
        return True


    def _poll_studio_log(self) -> None:
        if self._disposed:
            return


        parsed = None  # hotfix: avoid UnboundLocalError if log parse fails

        """Tail Studio dev log into the GUI (live)."""
        parsed = None  # may remain None if no marker found yet
        try:
            p = self.studio_log_path
            if p.exists() and p.is_file():
                size = p.stat().st_size
                if size < self._studio_log_pos:
                    self._studio_log_pos = 0
                if size > self._studio_log_pos:
                    with p.open("rb") as f:
                        f.seek(self._studio_log_pos)
                        data = f.read()
                    self._studio_log_pos = size
                    chunk = data.decode("utf-8", errors="ignore")
                    if chunk:
                        self._log_studio(chunk.rstrip("\n"))
                        parsed = _parse_backend_url_from_logs(chunk)
                        if parsed:
                            host, port = parsed
                            try:
                                port = int(port)
                            except Exception:
                                port = 7863
                            if (host != self.backend_host) or (port != int(self.backend_port)):
                                self._set_backend_host_port(host, port, reason="studio-log-live")
                                if self._backend_health_ok(host, port):
                                    self._log(f"Synced backend from Studio logs: {host}:{port}.")
                                else:
                                    self._log(f"Studio logs indicate backend at {host}:{port} (not healthy yet).")
        except Exception:
            pass
        finally:
            try:
                self.after(int(self._studio_log_poll_ms), self._poll_studio_log)
            except Exception:
                pass


    def _ensure_backend_port_available(self) -> None:
        """Auto-pick a free backend port if current port is taken by something else."""
        host = self.backend_host
        port = int(self.backend_port)
        if port == DEFAULT_UI_PORT:
            self._log(f"Backend port {port} conflicts with the Studio UI port. Resetting to a backend-safe port…")
            new_port = _find_free_port(host, DEFAULT_BACKEND_PORT, max_tries=50)
            self._set_backend_host_port(host, new_port, reason="ui-port-conflict")
            return

        # If it responds as our backend, keep it.
        if self._backend_health_ok(host, port):
            return

        # If bindable, it's free → keep.
        if _is_port_bindable(host, port):
            return

        pid = _listening_pid(port)
        pname = _pid_name(pid) if pid else None
        self._log(f"Backend port {port} is in use by PID {pid}{' ('+pname+')' if pname else ''}. Picking a free port…")

        start_from = port if port >= DEFAULT_BACKEND_PORT else DEFAULT_BACKEND_PORT
        new_port = _find_free_port(host, start_from, max_tries=50)
        self._set_backend_host_port(host, new_port, reason="auto-port-pick")

    def _auto_attach_backend_if_found(self) -> None:
        """Attach to an existing backend (scan + studio logs)."""
        if self._disposed:
            return
        if self._refresh_in_progress:
            return

        # Prefer Studio logs (Electron may have picked a different port).
        if self._sync_backend_from_studio_logs():
            self._refresh_status()
            return

        host = self.backend_host
        port = int(self.backend_port)

        if self._backend_health_ok(host, port):
            self._refresh_status()
            return

        if self.backend_proc and self.backend_proc.poll() is None:
            self._refresh_status()
            return

        found = self._scan_for_running_backend(host, DEFAULT_BACKEND_PORT, DEFAULT_BACKEND_PORT + 10)
        if found and found != port:
            self._log(f"Detected already-running backend at {host}:{found}; attaching.")
            self._set_backend_host_port(host, found, reason="auto-detect scan")

        self._refresh_status()

    # ---------------- port doctor / fixes ----------------

    def _is_safe_kill(self, proc_name: str | None) -> bool:
        if not proc_name:
            return False
        name = proc_name.lower()
        deny = ["system", "svchost", "services", "wininit", "csrss", "lsass", "explorer", "dwm"]
        if any(d in name for d in deny):
            return False
        allow = [
            "python", "python.exe", "pythonw.exe", "uvicorn",
            "node", "node.exe", "ollama", "ollama.exe",
            "comfy", "comfyui", "edmg", "electron"
        ]
        return any(a in name for a in allow)

    def _terminate_pid(self, pid: int) -> None:
        if sys.platform.startswith("win"):
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, text=True)
        else:
            subprocess.run(["kill", "-9", str(pid)], capture_output=True, text=True)

    def fix_ports_silent(self) -> None:
        """Attach/switch only; never kills processes."""
        def work():
            if self._sync_backend_from_studio_logs():
                return
            host = self.backend_host
            port = int(self.backend_port)

            found = self._scan_for_running_backend(host, DEFAULT_BACKEND_PORT, DEFAULT_BACKEND_PORT + 10)
            if found and found != port:
                self._log(f"[silent-fix] Found backend at {host}:{found}; attaching.")
                self._set_backend_host_port(host, found, reason="silent-fix attach")
                return

            if self._backend_health_ok(host, port):
                self._log("[silent-fix] Backend healthy; nothing to do.")
                return

            new_port = _find_free_port(host, max(DEFAULT_BACKEND_PORT, port), max_tries=50)
            if new_port != port:
                self._log(f"[silent-fix] Switching backend port {port} -> {new_port}")
                self._set_backend_host_port(host, new_port, reason="silent-fix switch")

        self._run_bg("Fix ports (silent)", work)

    def open_fix_ports_dialog(self) -> None:
        """Port fixer UI: attach/switch, optionally terminate known-safe processes."""
        win = tk.Toplevel(self)
        win.title("Fix ports")
        win.geometry("820x460")
        win.transient(self)
        win.grab_set()

        frm = ttk.Frame(win, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Port Doctor", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(
            frm,
            text="Shows which processes occupy required ports. You can attach to an existing backend, auto-pick a new port, or terminate only known-safe processes.",
            wraplength=780,
            foreground="#444",
        ).pack(anchor="w", pady=(2, 10))

        host = self.backend_host
        cur_port = int(self.backend_port)

        services = [
            ("Studio backend", host, cur_port, f"http://{host}:{cur_port}/health"),
            ("Ollama", "127.0.0.1", 11434, "http://127.0.0.1:11434/api/tags"),
            ("ComfyUI", "127.0.0.1", 8188, "http://127.0.0.1:8188/"),
        ]

        rows = []
        kill_vars: dict[int, tk.BooleanVar] = {}

        table = ttk.Frame(frm)
        table.pack(fill="x", pady=(0, 8))

        hdr = ttk.Frame(table)
        hdr.pack(fill="x")
        for col, w in [("Service", 18), ("Host", 14), ("Port", 6), ("PID/Process", 38), ("Action", 12)]:
            ttk.Label(hdr, text=col, width=w, font=("Segoe UI", 9, "bold")).pack(side="left")

        body = ttk.Frame(table)
        body.pack(fill="x")

        def _render_rows():
            for child in body.winfo_children():
                child.destroy()
            rows.clear()
            kill_vars.clear()

            for name, h, p, health in services:
                pid = _listening_pid(p)
                pname = _pid_name(pid) if pid else None
                ok = False
                try:
                    _http_get(health, timeout=0.9)
                    ok = True
                except Exception:
                    ok = False

                row = ttk.Frame(body)
                row.pack(fill="x", pady=2)
                ttk.Label(row, text=name, width=18).pack(side="left")
                ttk.Label(row, text=h, width=14).pack(side="left")
                ttk.Label(row, text=str(p), width=6).pack(side="left")
                proc_txt = "free"
                if pid:
                    proc_txt = f"{pid}{' ('+pname+')' if pname else ''}" + ("  [OK]" if ok else "  [CONFLICT]")
                ttk.Label(row, text=proc_txt, width=38).pack(side="left")

                can_kill = bool(pid and self._is_safe_kill(pname) and not ok)
                if can_kill:
                    v = tk.BooleanVar(value=False)
                    kill_vars[p] = v
                    ttk.Checkbutton(row, text="Kill", variable=v).pack(side="left")
                else:
                    ttk.Label(row, text="—", width=12, foreground="#777").pack(side="left")

                rows.append((name, h, p, pid, pname, ok))

        def _attach_scan():
            found = self._scan_for_running_backend(host, DEFAULT_BACKEND_PORT, DEFAULT_BACKEND_PORT + 10)
            if found and found != int(self.backend_port):
                self._set_backend_host_port(host, found, reason="fix-ports attach-scan")
                services[0] = ("Studio backend", host, int(self.backend_port), f"http://{host}:{int(self.backend_port)}/health")
            _render_rows()

        def _attach_logs():
            if self._sync_backend_from_studio_logs():
                services[0] = ("Studio backend", self.backend_host, int(self.backend_port), f"http://{self.backend_host}:{int(self.backend_port)}/health")
            _render_rows()

        def _auto_switch_backend():
            if self._backend_health_ok(host, int(self.backend_port)):
                _render_rows()
                return
            new_port = _find_free_port(host, max(DEFAULT_BACKEND_PORT, int(self.backend_port)), max_tries=50)
            self._set_backend_host_port(host, new_port, reason="fix-ports auto-switch")
            services[0] = ("Studio backend", host, int(self.backend_port), f"http://{host}:{int(self.backend_port)}/health")
            _render_rows()

        def _terminate_selected():
            to_kill = []
            for _name, _h, p, pid, pname, ok in rows:
                if pid and not ok and kill_vars.get(p, tk.BooleanVar(value=False)).get():
                    to_kill.append((p, pid, pname))
            if not to_kill:
                messagebox.showinfo("Fix ports", "No processes selected for termination.")
                return
            msg = "Terminate these processes?\n\n" + "\n".join([f"port {p}: PID {pid} ({pname or 'unknown'})" for p, pid, pname in to_kill])
            if not messagebox.askyesno("Confirm terminate", msg):
                return
            for _, pid, _ in to_kill:
                self._terminate_pid(pid)
            time.sleep(0.2)
            _render_rows()

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="Rescan", command=_render_rows).pack(side="left")
        ttk.Button(btns, text="Attach (scan 7863–7873)", command=_attach_scan).pack(side="left", padx=8)
        ttk.Button(btns, text="Attach (Studio logs)", command=_attach_logs).pack(side="left")
        ttk.Button(btns, text="Auto-switch backend port", command=_auto_switch_backend).pack(side="left", padx=8)
        ttk.Button(btns, text="Silent fix", command=lambda: (self.fix_ports_silent(), _render_rows())).pack(side="left")
        ttk.Button(btns, text="Terminate selected", command=_terminate_selected).pack(side="right")
        ttk.Button(btns, text="Close", command=win.destroy).pack(side="right", padx=8)

        _render_rows()

    # ---------------- actions ----------------

    def _refresh_status(self) -> None:
        if not self._on_ui_thread():
            self._call_on_ui_thread(self._refresh_status)
            return
        if not self._ui_alive():
            return
        if self._refresh_in_progress:
            return
        self._refresh_in_progress = True
        try:
            py = sys.executable
            try:
                uv = resolve_uv(install=False)
                profile = active_accelerator_profile()
                bootstrap_note = (
                    f" | locked runtime: Python 3.12 | uv {uv_version(uv)} | "
                    f"profile: {profile} | lock: {lock_sha256()[:12]}…"
                )
            except Exception as e:
                bootstrap_note = f" | locked runtime: NOT READY ({e})"
            node = self._which("node")
            package_manager_name = _studio_package_manager_name()
            package_manager = _resolve_package_manager_command(package_manager_name)

            self.lbl_python.config(text=f"Python: {py}{bootstrap_note}")
            self.lbl_node.config(
                text=f"Node: {node or 'NOT FOUND'} ({package_manager_name}: {(package_manager[1] if package_manager else 'NOT FOUND')})"
            )

            _, line_ollama = _port_doctor_line("Ollama", "127.0.0.1", 11434, health_url="http://127.0.0.1:11434/api/tags")
            self.lbl_ollama.config(text=line_ollama)

            _, line_comfy = _port_doctor_line("ComfyUI", "127.0.0.1", 8188, health_url="http://127.0.0.1:8188/")
            self.lbl_comfyui.config(text=line_comfy)

            host = self.backend_host
            port = int(self.backend_port)
            ok_backend, line_backend = _port_doctor_line("Studio backend", host, port, health_url=f"http://{host}:{port}/health")
            self.lbl_backend.config(text=line_backend)

            # If not OK and we didn't start it, try syncing via logs or scan.
            if (not ok_backend) and (not self.backend_proc or self.backend_proc.poll() is not None):
                if self._sync_backend_from_studio_logs():
                    return
                found = self._scan_for_running_backend(host, DEFAULT_BACKEND_PORT, DEFAULT_BACKEND_PORT + 10)
                if found and found != port:
                    self._log(f"Detected backend on {host}:{found}; switching launcher to it.")
                    self._set_backend_host_port(host, found, reason="auto-detect refresh")
        finally:
            self._refresh_in_progress = False

    # ── Machine Optimiser ──────────────────────────────────────────────────────

    def _optimize_current_label(self) -> str:
        state = _load_optimize_state()
        if state.get("optimized"):
            return "● OPTIMIZED"
        return "○ normal"

    def _refresh_opt_label(self) -> None:
        if not self._on_ui_thread():
            self._call_on_ui_thread(self._refresh_opt_label)
            return
        if not self._ui_alive():
            return
        try:
            self._opt_status_var.set(self._optimize_current_label())
        except Exception:
            pass

    def optimize_machine(self) -> None:
        """Apply performance optimizations for AI dev work."""
        if not sys.platform.startswith("win"):
            messagebox.showinfo("Not supported", "Machine optimization is Windows-only.")
            return

        def work():
            self._log("=" * 60)
            self._log("Applying machine optimizations for AI development…")
            state: dict = {"optimized": True, "prev_power_plan": "", "stopped_services": [], "network_tweaked": False}

            # 1. Save + switch power plan ──────────────────────────────────────
            prev_plan = _get_active_power_plan()
            state["prev_power_plan"] = prev_plan
            self._log(f"Current power plan: {prev_plan or 'unknown'}")

            # Try Ultimate Performance first (needs to be unlocked on some SKUs)
            rc, out = _ps_elevated(
                f"powercfg -duplicatescheme {_POWER_ULTIMATE} > $null 2>&1; "
                f"powercfg /setactive {_POWER_ULTIMATE}"
            )
            if rc != 0:
                rc2, _ = _ps_elevated(f"powercfg /setactive {_POWER_HIGH_PERF}")
                active = _POWER_HIGH_PERF if rc2 == 0 else prev_plan
                self._log("Power plan → High Performance" if rc2 == 0 else "Power plan: no change (may need admin)")
            else:
                active = _POWER_ULTIMATE
                self._log("Power plan → Ultimate Performance")
            state["active_power_plan"] = active

            # 2. Kill unnecessary background processes ─────────────────────────
            killed: list[str] = []
            for proc_name in _OPT_KILL_PROCS:
                rc, _ = _ps(f"Stop-Process -Name '{proc_name.replace('.exe','')}' -Force -ErrorAction SilentlyContinue")
                if rc == 0:
                    killed.append(proc_name)
            if killed:
                self._log(f"Killed {len(killed)} background process(es): {', '.join(killed)}")
            else:
                self._log("No unnecessary processes were running.")
            state["killed_procs"] = killed

            # 3. Stop non-essential Windows services ───────────────────────────
            stopped: list[str] = []
            for svc in _OPT_SERVICES:
                before = _svc_query(svc)
                if before.lower() == "running":
                    rc, _ = _ps_elevated(f"Stop-Service -Name '{svc}' -Force -ErrorAction SilentlyContinue")
                    if rc == 0:
                        stopped.append(svc)
                        self._log(f"  Stopped service: {svc}")
                    else:
                        self._log(f"  Could not stop {svc} (may need admin or already stopped)")
            state["stopped_services"] = stopped

            # 4. NVIDIA GPU – max performance clocks ───────────────────────────
            if shutil.which("nvidia-smi"):
                _ps("nvidia-smi --auto-boost-default=0 2>$null")
                _ps("nvidia-smi -pm 1 2>$null")
                self._log("NVIDIA GPU: set to max-performance / persistence mode")

            # 5. Network tweaks – disable Nagle's algorithm ────────────────────
            nagle_ps = r"""
$base = 'HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces'
Get-ChildItem $base | ForEach-Object {
    Set-ItemProperty -Path $_.PSPath -Name TcpAckFrequency -Value 1 -Type DWord -Force -EA SilentlyContinue
    Set-ItemProperty -Path $_.PSPath -Name TCPNoDelay      -Value 1 -Type DWord -Force -EA SilentlyContinue
}
"""
            rc, _ = _ps_elevated(nagle_ps.strip().replace("\n", "; "))
            if rc == 0:
                state["network_tweaked"] = True
                self._log("Network: Nagle's algorithm disabled (lower TCP latency)")
            else:
                self._log("Network: skipped (needs admin)")

            # 6. Flush DNS cache ───────────────────────────────────────────────
            _ps("ipconfig /flushdns 2>$null")
            self._log("DNS cache flushed")

            # 7. Defender – exclude project folder ────────────────────────────
            proj_path = str(ROOT).replace("'", "''")
            rc, _ = _ps_elevated(
                f"Add-MpPreference -ExclusionPath '{proj_path}' -ErrorAction SilentlyContinue"
            )
            if rc == 0:
                state["defender_exclusion"] = proj_path
                self._log(f"Windows Defender: excluded project folder from real-time scan")
            else:
                self._log("Defender exclusion: skipped (needs admin or Defender not active)")

            # 8. Set timer resolution to 1 ms (better scheduler precision) ────
            _ps_elevated(
                "Add-Type -TypeDefinition '"
                "using System; using System.Runtime.InteropServices;"
                "public class WinMM { [DllImport(\"winmm.dll\")] public static extern int timeBeginPeriod(int p); }"
                "'; [WinMM]::timeBeginPeriod(1)"
            )
            self._log("Timer resolution: requested 1 ms")

            _save_optimize_state(state)
            self._refresh_opt_label()
            self._log("✓ Machine optimized. Run 'Restore Normal' to undo all changes.")

        self._run_bg("Optimize Machine (ON)", work)

    def restore_machine(self) -> None:
        """Undo all optimizations and return to normal operating mode."""
        if not sys.platform.startswith("win"):
            messagebox.showinfo("Not supported", "Machine optimization is Windows-only.")
            return

        def work():
            self._log("=" * 60)
            self._log("Restoring machine to normal mode…")
            state = _load_optimize_state()

            # 1. Restore power plan ────────────────────────────────────────────
            prev = state.get("prev_power_plan", "")
            if prev:
                rc, _ = _ps_elevated(f"powercfg /setactive {prev}")
                self._log(f"Power plan restored → {prev}" if rc == 0 else f"Power plan restore failed (rc={rc})")
            else:
                _ps_elevated(f"powercfg /setactive {_POWER_HIGH_PERF}")
                self._log("Power plan → High Performance (no saved plan found)")

            # 2. Restart stopped services ─────────────────────────────────────
            for svc in state.get("stopped_services", []):
                rc, _ = _ps_elevated(f"Start-Service -Name '{svc}' -ErrorAction SilentlyContinue")
                self._log(f"  Restarted service: {svc}" if rc == 0 else f"  Could not restart {svc}")

            # 3. Restore Nagle's algorithm ─────────────────────────────────────
            if state.get("network_tweaked"):
                nagle_restore = r"""
$base = 'HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces'
Get-ChildItem $base | ForEach-Object {
    Remove-ItemProperty -Path $_.PSPath -Name TcpAckFrequency -Force -EA SilentlyContinue
    Remove-ItemProperty -Path $_.PSPath -Name TCPNoDelay      -Force -EA SilentlyContinue
}
"""
                rc, _ = _ps_elevated(nagle_restore.strip().replace("\n", "; "))
                self._log("Network: Nagle's algorithm restored" if rc == 0 else "Network restore: needs admin")

            # 4. Remove Defender exclusion ─────────────────────────────────────
            excl = state.get("defender_exclusion", "")
            if excl:
                rc, _ = _ps_elevated(
                    f"Remove-MpPreference -ExclusionPath '{excl}' -ErrorAction SilentlyContinue"
                )
                self._log("Defender exclusion removed" if rc == 0 else "Defender exclusion removal: needs admin")

            # 5. Flush DNS again ───────────────────────────────────────────────
            _ps("ipconfig /flushdns 2>$null")
            self._log("DNS cache flushed")

            # 6. Clear saved state ─────────────────────────────────────────────
            try:
                OPTIMIZE_STATE_PATH.unlink(missing_ok=True)
            except Exception:
                pass
            self._refresh_opt_label()
            self._log("✓ Machine restored to normal. Restart may be needed for some services to fully reinitialize.")

        self._run_bg("Restore Machine (OFF)", work)

    def install_cuda_torch(self) -> None:
        """Sync the immutable CUDA/TensorRT profile selected by uv.lock."""
        def work():
            _sync_locked_backend("cuda", self._log)
            os.environ["EDMG_BACKEND_ACCELERATOR_PROFILE"] = "cuda"
            self._log("Active backend accelerator profile is now `cuda`.")

        self._run_bg("Sync locked CUDA/TensorRT profile", work)

    def install_backend(self) -> None:
        def work():
            BACKEND_DIR.mkdir(parents=True, exist_ok=True)
            profile = active_accelerator_profile()
            _sync_locked_backend(profile, self._log)

        self._run_bg("Install backend", work)

    def install_ui(self) -> None:
        def work():
            package_manager_name = _studio_package_manager_name()
            package_manager = _resolve_package_manager_command(package_manager_name)
            if not package_manager:
                raise RuntimeError(f"{package_manager_name} not found. Install Node.js LTS, enable Corepack if needed, then retry.")
            if not PACKAGE_JSON_PATH.exists():
                raise RuntimeError(f"package.json not found at {STUDIO_DIR}")
            rc = _run_cmd(
                [*package_manager[0], "install"],
                cwd=STUDIO_DIR,
                log_cb=self._log,
                env=_env_with_node_bin_dirs(),
            )
            if rc != 0:
                raise RuntimeError(f"{package_manager_name} install failed")

        self._run_bg("Install Studio UI deps", work)

    def start_backend(self) -> None:
        self._run_bg("Start backend", self._start_backend_impl)

    def _start_backend_impl(self) -> None:
        # Attach to an already-running backend on nearby ports (avoid duplicates).
        found = self._scan_for_running_backend(self.backend_host, DEFAULT_BACKEND_PORT, DEFAULT_BACKEND_PORT + 10)
        if found:
            if found != int(self.backend_port):
                self._log(f"Found existing backend at {self.backend_host}:{found}; attaching (not starting new).")
                self._set_backend_host_port(self.backend_host, found, reason="attach start-backend")
            else:
                self._log("Backend reachable on configured port; not starting a new one.")
            return

        if self.backend_proc and self.backend_proc.poll() is None:
            self._log("Backend already running.")
            return

        self._ensure_backend_port_available()

        profile = active_accelerator_profile()
        _sync_locked_backend(profile, self._log)
        cmd, env = frozen_run_command(
            profile,
            [
                "python",
                "-m",
                "edmg_studio_backend",
                "serve",
                "--host",
                self.backend_host,
                "--port",
                str(self.backend_port),
            ],
            capability_extras=RUNTIME_CAPABILITY_EXTRAS,
        )
        env = _env_with_node_bin_dirs(env)
        for key, value in _default_storage_env(self.studio_home, self.data_dir).items():
            env.setdefault(key, value)
        ffmpeg_path = env.get("EDMG_FFMPEG_PATH") or _resolve_ffmpeg_path()
        env["EDMG_FFMPEG_PATH"] = ffmpeg_path
        self._log(f"Using FFmpeg: {ffmpeg_path}")
        if env.get("EDMG_7Z_PATH"):
            self._log(f"Using 7-Zip: {env['EDMG_7Z_PATH']}")
        self._log("Starting backend: " + " ".join(cmd))
        self.backend_proc = subprocess.Popen(cmd, cwd=str(BACKEND_DIR), env=env)
        time.sleep(0.25)

    def stop_backend(self) -> None:
        self._run_bg("Stop backend", self._stop_backend_impl)

    def _stop_backend_impl(self) -> None:
        if not self.backend_proc or self.backend_proc.poll() is not None:
            self._log("Backend not running.")
            return
        self._log("Stopping backend…")
        try:
            self.backend_proc.terminate()
        except Exception:
            pass
        time.sleep(0.3)
        if self.backend_proc and self.backend_proc.poll() is None:
            try:
                self.backend_proc.kill()
            except Exception:
                pass
        self.backend_proc = None

    def health_test(self) -> None:
        def work():
            url = f"http://{self.backend_host}:{self.backend_port}/health"
            try:
                h = _http_get(url, timeout=1.2)
                self._log("Health OK: " + h)
            except Exception:
                self._log("Backend not running; starting it for test.")
                self._start_backend_impl()
                for _ in range(40):
                    try:
                        h = _http_get(url, timeout=1.2)
                        self._log("Health OK: " + h)
                        break
                    except Exception:
                        time.sleep(0.25)
                else:
                    raise RuntimeError("Backend did not become healthy in time.")
            try:
                s = _http_get(f"http://{self.backend_host}:{self.backend_port}/v1/setup/status", timeout=2.0)
                self._log("Setup status: " + s)
            except Exception as e:
                self._log(f"Setup status endpoint not available (ok): {e}")

        self._run_bg("Health test", work)

    def _run_powershell(self, ps1: Path, extra_args: list[str] | None = None) -> None:
        if not sys.platform.startswith("win"):
            raise RuntimeError("Windows-only action.")
        if not ps1.exists():
            raise RuntimeError(f"Script not found: {ps1}")
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)]
        if extra_args:
            cmd.extend(extra_args)
        rc = _run_cmd(cmd, cwd=STUDIO_DIR, log_cb=self._log)
        if rc != 0:
            raise RuntimeError(f"PowerShell script failed (exit {rc}): {ps1.name}")

    def get_ffmpeg(self) -> None:
        def work():
            ps1 = STUDIO_DIR / "packaging" / "windows" / "get_ffmpeg.ps1"
            self._log("This will download FFmpeg and stage it for the packaged Studio renderer/build.")
            self._run_powershell(ps1)

        self._run_bg("Get FFmpeg", work)

    def build_installer(self) -> None:
        def work():
            ps1 = STUDIO_DIR / "packaging" / "windows" / "build_all.ps1"
            self._log("Building installer (backend EXE + Electron installer)…")
            self._run_powershell(ps1)
            self._log("Build finished. Use 'Open Release Folder' to find the installer.")

        self._run_bg("Build installer", work)

    def build_inno_installer(self) -> None:
        def work():
            ps1 = STUDIO_DIR / "packaging" / "windows" / "build_inno_external.ps1"
            self._log("Building Inno external-payload installer (setup EXE + payload archive)…")
            self._run_powershell(ps1)
            self._log("Inno build finished. Use 'Open Inno Folder' and ship the setup EXE with the payload folder.")

        self._run_bg("Build Inno installer", work)

    def _open_folder(self, folder: Path, missing_title: str, missing_message: str) -> None:
        if not folder.exists():
            messagebox.showinfo(missing_title, missing_message)
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(folder))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except Exception as e:
            messagebox.showerror("Open folder failed", str(e))

    def open_release_folder(self) -> None:
        rel = STUDIO_DIR / "release"
        if not rel.exists():
            rel = STUDIO_DIR / "dist"
        self._open_folder(
            rel,
            "Release folder",
            f"Release output folder not found yet.\nExpected at:\n{STUDIO_DIR / 'release'}",
        )

    def open_inno_folder(self) -> None:
        folder = STUDIO_DIR / "dist-inno"
        self._open_folder(
            folder,
            "Inno release folder",
            f"Inno release folder not found yet.\nExpected at:\n{folder}",
        )

    def _start_studio_impl(self) -> None:
        package_manager_name = _studio_package_manager_name()
        package_manager = _resolve_package_manager_command(package_manager_name)
        if not package_manager:
            raise RuntimeError(f"{package_manager_name} not found. Install Node.js LTS, enable Corepack if needed, then retry.")
        self._log(f"Starting Studio ({package_manager_name} run dev)…")

        # Align backend first.
        self._auto_attach_backend_if_found()
        self._ensure_backend_port_available()

        env = _env_with_node_bin_dirs()
        env.setdefault("EDMG_STUDIO_SPAWN_BACKEND", "1")
        env.setdefault("EDMG_STUDIO_BACKEND_HOST", self.backend_host)
        env.setdefault("EDMG_STUDIO_BACKEND_PORT", str(self.backend_port))
        for key, value in _default_storage_env(self.studio_home, self.data_dir).items():
            env.setdefault(key, value)

        self.studio_log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if self._studio_log_fp and not self._studio_log_fp.closed:
                self._studio_log_fp.close()
        except Exception:
            pass

        self._studio_log_fp = open(self.studio_log_path, "a", encoding="utf-8", errors="ignore")
        self._studio_log_fp.write(f"\n=== launcher start_studio {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        self._studio_log_fp.flush()

        self.studio_proc = subprocess.Popen(
            [*package_manager[0], "run", "dev"],
            cwd=str(STUDIO_DIR),
            env=env,
            stdout=self._studio_log_fp,
            stderr=subprocess.STDOUT,
        )
        self._log(f"Studio logs: {self.studio_log_path}")

        # Prime live view with tail.
        try:
            self._studio_log_pos = max(0, self.studio_log_path.stat().st_size - 200_000)
            self._clear_studio_log_view()
            tail = _tail_file(self.studio_log_path, max_bytes=200_000)
            if tail:
                self._log_studio(tail.rstrip("\n"))
        except Exception:
            pass

    def _stop_studio_impl(self) -> None:
        if not self.studio_proc or self.studio_proc.poll() is not None:
            self._log("Studio not running.")
            return
        self._log("Stopping Studio…")
        try:
            self.studio_proc.terminate()
        except Exception:
            pass
        time.sleep(0.7)
        if self.studio_proc and self.studio_proc.poll() is None:
            try:
                self.studio_proc.kill()
            except Exception:
                pass
        self.studio_proc = None
        try:
            if self._studio_log_fp and not self._studio_log_fp.closed:
                self._studio_log_fp.flush()
        except Exception:
            pass

    def start_studio(self) -> None:
        self._run_bg("Start Studio UI", self._start_studio_impl)

    def restart_studio(self) -> None:
        def work():
            self._log("Restarting Studio…")
            try:
                self._stop_studio_impl()
            except Exception:
                pass
            self._start_studio_impl()
        self._run_bg("Restart Studio UI", work)

    def stop_studio(self) -> None:
        self._run_bg("Stop Studio UI", self._stop_studio_impl)


if __name__ == "__main__":
    try:
        import tkinter  # noqa: F401
    except Exception as e:
        print("Tkinter not available:", e)
        sys.exit(1)
    app = Launcher()
    app.mainloop()
