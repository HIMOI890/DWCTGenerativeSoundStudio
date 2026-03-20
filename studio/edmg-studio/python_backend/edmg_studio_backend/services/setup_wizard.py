from __future__ import annotations

import importlib.util
import json
import re
import os
import platform
import subprocess
import shutil
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import requests

try:
    import py7zr  # type: ignore
except Exception:  # pragma: no cover
    py7zr = None


@dataclass
class SetupTask:
    id: str
    name: str
    status: str = "queued"  # queued|running|done|failed
    progress: Optional[float] = None
    last_log: str = ""
    error: Optional[str] = None
    started_at: Optional[float] = None
    ended_at: Optional[float] = None


class SetupTaskManager:
    """Very small in-memory task runner for installer operations."""

    def __init__(self):
        self._lock = threading.Lock()
        self._tasks: dict[str, SetupTask] = {}

    def list(self) -> list[SetupTask]:
        with self._lock:
            # newest first
            return sorted(self._tasks.values(), key=lambda t: (t.started_at or 0), reverse=True)

    def start(self, name: str, fn, *args, **kwargs) -> SetupTask:
        task = SetupTask(id=str(uuid.uuid4())[:8], name=name, status="queued")
        with self._lock:
            self._tasks[task.id] = task

        def runner():
            task.status = "running"
            task.started_at = time.time()
            try:
                fn(task, *args, **kwargs)
                task.status = "done"
            except Exception as e:
                task.status = "failed"
                task.error = str(e)
                task.last_log = (task.last_log + "\n" if task.last_log else "") + f"ERROR: {e}"
            finally:
                task.ended_at = time.time()

        threading.Thread(target=runner, daemon=True).start()
        return task

    @staticmethod
    def log(task: SetupTask, msg: str) -> None:
        task.last_log = msg

    @staticmethod
    def set_progress(task: SetupTask, v: Optional[float]) -> None:
        task.progress = v


# ------------------------------ Ollama ------------------------------

def _ollama_base(url: str) -> str:
    return (url or "http://127.0.0.1:11434").rstrip("/")


def check_ollama(ollama_url: str, model: str) -> dict[str, Any]:
    base = _ollama_base(ollama_url)
    try:
        r = requests.get(f"{base}/api/tags", timeout=2.5)
        r.raise_for_status()
        data = r.json()
        models = [m.get("name") for m in (data.get("models") or []) if isinstance(m, dict)]
        present = (model in models) if model else False
        return {
            "ok": True,
            "url": base,
            "model": model,
            "model_present": present,
            "models": models[:50],
        }
    except Exception as e:
        return {
            "ok": False,
            "url": base,
            "model": model,
            "model_present": False,
            "hint": "Install Ollama for Windows and ensure it is running (it exposes http://127.0.0.1:11434).",
            "error": str(e),
        }


def download_and_run_ollama_installer(task: SetupTask, dest_dir: Path) -> None:
    """Downloads OllamaSetup.exe and launches it (interactive installer)."""

    if platform.system().lower() != "windows":
        raise RuntimeError("Ollama installer automation is only implemented for Windows.")

    url = "https://ollama.com/download/OllamaSetup.exe"
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / "OllamaSetup.exe"

    SetupTaskManager.log(task, f"Downloading Ollama installer…")
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        got = 0
        with open(out, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                got += len(chunk)
                if total:
                    SetupTaskManager.set_progress(task, min(0.99, got / total))
                    SetupTaskManager.log(task, f"Downloading Ollama installer… {int((got/total)*100)}%")

    SetupTaskManager.set_progress(task, 1.0)
    SetupTaskManager.log(task, "Launching Ollama installer…")

    # Launch interactively.
    subprocess.Popen([str(out)], cwd=str(dest_dir))
    SetupTaskManager.log(task, "Ollama installer launched. Finish the installer, then return here and click Refresh.")


def pull_ollama_model(task: SetupTask, ollama_url: str, model: str) -> None:
    base = _ollama_base(ollama_url)
    if not model:
        raise RuntimeError("No model specified")

    SetupTaskManager.log(task, f"Pulling model {model}…")
    # Ollama supports streaming progress updates by default.
    # We parse JSON lines and surface the latest status.
    with requests.post(
        f"{base}/api/pull",
        json={"model": model, "stream": True},
        stream=True,
        timeout=60 * 60,
    ) as r:
        r.raise_for_status()
        last = ""
        for line in r.iter_lines(decode_unicode=True):
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            status = obj.get("status") or ""
            digest = obj.get("digest")
            total = obj.get("total")
            completed = obj.get("completed")

            msg = status
            if digest:
                msg += f" ({str(digest)[:12]})"
            if total and completed:
                try:
                    p = float(completed) / float(total)
                    SetupTaskManager.set_progress(task, max(0.0, min(0.99, p)))
                except Exception:
                    pass

            if msg and msg != last:
                SetupTaskManager.log(task, msg)
                last = msg

    SetupTaskManager.set_progress(task, 1.0)
    SetupTaskManager.log(task, f"Model {model} is ready.")


# ------------------------------ ComfyUI Portable ------------------------------

COMFY_REPO = "comfyanonymous/ComfyUI"


def _github_latest_assets(repo: str) -> list[dict[str, Any]]:
    r = requests.get(f"https://api.github.com/repos/{repo}/releases/latest", timeout=20)
    r.raise_for_status()
    data = r.json()
    return data.get("assets") or []


def _pick_portable_asset(assets: list[dict[str, Any]], flavor: str) -> dict[str, Any]:
    flavor = (flavor or "cpu").lower()
    # Heuristics across historical naming.
    candidates: list[dict[str, Any]] = []
    for a in assets:
        name = (a.get("name") or "").lower()
        if "portable" not in name:
            continue
        if not name.endswith((".7z", ".zip")):
            continue
        candidates.append(a)

    def score(a: dict[str, Any]) -> int:
        name = (a.get("name") or "").lower()
        s = 0
        if flavor == "cpu":
            if "cpu" in name:
                s += 5
            if "or_cpu" in name or "cpu_or" in name:
                s += 3
            if "nvidia" in name or "cu" in name:
                s -= 2
        else:
            if "nvidia" in name or "cu" in name:
                s += 5
            if "cpu" in name:
                s -= 2
        # prefer smaller artifacts when tie
        try:
            size = int(a.get("size") or 0)
            s += max(0, 3 - int(size / (1024 * 1024 * 1024)))
        except Exception:
            pass
        return s

    if not candidates:
        raise RuntimeError("No portable assets found in latest ComfyUI release.")

    candidates.sort(key=score, reverse=True)
    return candidates[0]


def _legacy_external_root(data_dir: Path | None) -> Path | None:
    if data_dir is None:
        return None
    return (data_dir / "third_party").resolve()


def comfy_portable_root(external_dir: Path, data_dir: Path | None = None) -> Path:
    preferred = (external_dir / "ComfyUI_windows_portable").resolve()
    if (preferred / "ComfyUI").exists() and (preferred / "python_embeded").exists():
        return preferred

    legacy_root = _legacy_external_root(data_dir)
    if legacy_root is not None:
        legacy = (legacy_root / "ComfyUI_windows_portable").resolve()
        if (legacy / "ComfyUI").exists() and (legacy / "python_embeded").exists():
            return legacy

    return preferred


def comfy_portable_installed(external_dir: Path, data_dir: Path | None = None) -> bool:
    root = comfy_portable_root(external_dir, data_dir)
    return (root / "ComfyUI").exists() and (root / "python_embeded").exists()



def _find_7z_exe(external_dir: Path, data_dir: Path | None = None) -> str:
    """Locate a 7-Zip CLI that supports BCJ2 (required for some .7z archives).

    Resolution order:
    1) EDMG_7Z_PATH env var
    2) bundled inside the Studio external-tools root (external/bin/7z.exe)
    3) legacy bundled path alongside the Studio data dir (data/third_party/bin/7z.exe)
    4) common system install paths
    5) PATH
    """
    env = os.environ.get("EDMG_7Z_PATH")
    if env and Path(env).exists():
        return env

    bundled = (external_dir / "bin" / ("7z.exe" if platform.system() == "Windows" else "7zz")).resolve()
    if bundled.exists():
        return str(bundled)

    legacy_root = _legacy_external_root(data_dir)
    if legacy_root is not None:
        legacy_bundled = (legacy_root / "bin" / ("7z.exe" if platform.system() == "Windows" else "7zz")).resolve()
        if legacy_bundled.exists():
            return str(legacy_bundled)

    candidates = []
    if platform.system() == "Windows":
        candidates += [
            Path(r"C:\Program Files\7-Zip\7z.exe"),
            Path(r"C:\Program Files (x86)\7-Zip\7z.exe"),
        ]
    for c in candidates:
        if c.exists():
            return str(c)

    which = shutil.which("7z") or shutil.which("7z.exe") or shutil.which("7zz") or shutil.which("7zz.exe")
    if which:
        return which

    raise RuntimeError("7-Zip CLI not found. Install 7-Zip or bundle 7z.exe and/or set EDMG_7Z_PATH.")


def _extract_7z_cli(task: SetupTask, external_dir: Path, archive: Path, out_parent: Path, data_dir: Path | None = None) -> None:
    seven = _find_7z_exe(external_dir, data_dir)
    SetupTaskManager.log(task, f"Using 7-Zip: {seven}")
    out_parent.mkdir(parents=True, exist_ok=True)
    # `x` preserves folders; `-y` assumes Yes on all queries.
    cmd = [seven, "x", str(archive), f"-o{str(out_parent)}", "-y"]
    SetupTaskManager.log(task, "Extract command: " + " ".join(cmd))
    subprocess.check_call(cmd)


def _yaml_quote(value: str) -> str:
    return "'" + str(value).replace("\\", "/").replace("'", "''") + "'"


def ensure_comfyui_model_paths(external_dir: Path, models_dir: Path, data_dir: Path | None = None) -> Path | None:
    root = comfy_portable_root(external_dir, data_dir)
    if not (root / "ComfyUI").exists():
        return None

    yaml_path = root / "ComfyUI" / "extra_model_paths.yaml"
    base_path = _yaml_quote(str(models_dir.resolve()))
    content = (
        "edmg_studio:\n"
        f"  base_path: {base_path}\n"
        "  checkpoints: checkpoints\n"
        "  loras: loras\n"
        "  embeddings: embeddings\n"
        "  vae: vae\n"
        "  controlnet: controlnet\n"
        "  upscale_models: upscale_models\n"
        "  clip: clip\n"
        "  clip_vision: clip_vision\n"
    )
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    yaml_path.write_text(content, encoding="utf-8")
    return yaml_path


def download_and_extract_portable(
    task: SetupTask,
    external_dir: Path,
    flavor: str,
    data_dir: Path | None = None,
    models_dir: Path | None = None,
) -> Path:

    assets = _github_latest_assets(COMFY_REPO)
    asset = _pick_portable_asset(assets, flavor)

    url = asset.get("browser_download_url")
    name = asset.get("name")
    if not url or not name:
        raise RuntimeError("Portable download URL not found.")

    dest_root = (external_dir / "ComfyUI_windows_portable").resolve()
    tmp_dir = (external_dir / "_downloads").resolve()
    tmp_dir.mkdir(parents=True, exist_ok=True)
    archive = tmp_dir / name

    SetupTaskManager.log(task, f"Downloading ComfyUI Portable ({flavor})…")
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        got = 0
        with open(archive, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                got += len(chunk)
                if total:
                    SetupTaskManager.set_progress(task, min(0.7, (got / total) * 0.7))
                    SetupTaskManager.log(task, f"Downloading… {int((got/total)*100)}%")

    # Extract
    SetupTaskManager.log(task, "Extracting ComfyUI Portable…")
    SetupTaskManager.set_progress(task, 0.75)

    # Clear existing
    if dest_root.exists():
        # keep user models if they already have
        # (best-effort; don't delete if there's a chance the user put models there)
        backup = dest_root.parent / f"ComfyUI_windows_portable_backup_{int(time.time())}"
        dest_root.rename(backup)

    dest_root.parent.mkdir(parents=True, exist_ok=True)

    if str(archive).lower().endswith(".7z"):
        _extract_7z_cli(task, external_dir, archive, dest_root.parent, data_dir)
    else:
        import zipfile

        with zipfile.ZipFile(archive, "r") as z:
            z.extractall(path=str(dest_root.parent))

    # Some archives include a top-level folder; normalize to expected name.
    # Find a folder that contains python_embeded + ComfyUI.
    parent = dest_root.parent
    found = None
    for p in parent.iterdir():
        if not p.is_dir():
            continue
        if (p / "python_embeded").exists() and (p / "ComfyUI").exists():
            found = p
            break
    if found and found.name != dest_root.name:
        # Move/rename into place
        if dest_root.exists():
            # should not, but guard
            pass
        found.rename(dest_root)

    if not comfy_portable_installed(external_dir, data_dir):
        raise RuntimeError("ComfyUI Portable extraction completed, but expected folders were not found.")

    if models_dir is not None:
        yaml_path = ensure_comfyui_model_paths(external_dir, models_dir, data_dir)
        if yaml_path is not None:
            SetupTaskManager.log(task, f"Configured external model paths: {yaml_path}")

    SetupTaskManager.set_progress(task, 1.0)
    SetupTaskManager.log(task, "ComfyUI Portable installed.")
    return dest_root


class ComfyPortableProcess:
    def __init__(self):
        self.proc: Optional[subprocess.Popen] = None
        self.root: Optional[Path] = None

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(
        self,
        task: SetupTask,
        external_dir: Path,
        flavor: str,
        host: str = "127.0.0.1",
        port: int = 8188,
        data_dir: Path | None = None,
        models_dir: Path | None = None,
    ) -> None:
        if platform.system().lower() != "windows":
            raise RuntimeError("ComfyUI Portable auto-start is currently implemented for Windows.")

        if not comfy_portable_installed(external_dir, data_dir):
            raise RuntimeError("ComfyUI Portable is not installed yet. Click Install first.")

        root = comfy_portable_root(external_dir, data_dir)
        py = root / "python_embeded" / "python.exe"
        main = root / "ComfyUI" / "main.py"
        if not py.exists() or not main.exists():
            raise RuntimeError("ComfyUI Portable install looks incomplete.")

        if models_dir is not None:
            ensure_comfyui_model_paths(external_dir, models_dir, data_dir)

        if self.running():
            SetupTaskManager.log(task, "ComfyUI is already running.")
            return

        args = [
            str(py),
            "-s",
            str(main),
            "--listen",
            host,
            "--port",
            str(port),
            "--windows-standalone-build",
        ]
        if (flavor or "cpu").lower() == "cpu":
            args.insert(3, "--cpu")

        # Hide console window.
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        SetupTaskManager.log(task, f"Starting ComfyUI Portable ({flavor})…")
        self.proc = subprocess.Popen(args, cwd=str(root), creationflags=creationflags)
        self.root = root

        # Wait a bit for port to open
        for _ in range(80):
            try:
                r = requests.get(f"http://{host}:{port}/system_stats", timeout=1.0)
                if r.status_code == 200:
                    SetupTaskManager.log(task, "ComfyUI is running.")
                    return
            except Exception:
                pass
            time.sleep(0.25)

        SetupTaskManager.log(task, "ComfyUI started (still warming up). If it doesn't come online, try again or install GPU-compatible build.")

    def stop(self) -> None:
        if self.proc and self.running():
            try:
                self.proc.terminate()
            except Exception:
                pass


def check_ffmpeg(ffmpeg_path: str) -> dict[str, Any]:
    hint = "Packaged EDMG Studio should include bundled FFmpeg. If this is a dev checkout, install FFmpeg and add it to PATH, or set EDMG_FFMPEG_PATH to the ffmpeg executable."
    try:
        r = subprocess.run([ffmpeg_path, "-version"], capture_output=True, text=True, timeout=3)
        ok = r.returncode == 0
        return {
            "ok": ok,
            "path": ffmpeg_path,
            "version": (r.stdout.splitlines()[0] if r.stdout else None),
            "hint": None if ok else hint,
        }
    except Exception as e:
        return {
            "ok": False,
            "path": ffmpeg_path,
            "error": str(e),
            "hint": hint,
        }


BACKEND_BUNDLE_MODULES: dict[str, dict[str, str]] = {
    "audio": {
        "librosa": "librosa",
        "soundfile": "soundfile",
    },
    "asr": {
        "faster-whisper": "faster_whisper",
    },
    "internal": {
        "diffusers": "diffusers",
        "transformers": "transformers",
        "accelerate": "accelerate",
        "safetensors": "safetensors",
        "torch": "torch",
    },
}

BACKEND_BUNDLE_ALIASES: dict[str, tuple[str, ...]] = {
    "full": ("audio", "asr", "internal"),
    "studio_bundle": ("audio", "asr", "internal"),
}


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _bundle_module_map(bundle: str) -> dict[str, str]:
    keys = BACKEND_BUNDLE_ALIASES.get(bundle, (bundle,))
    modules: dict[str, str] = {}
    for key in keys:
        modules.update(BACKEND_BUNDLE_MODULES.get(key, {}))
    return modules


def check_backend_bundle(bundle: str = "studio_bundle") -> dict[str, Any]:
    modules = _bundle_module_map(bundle)
    missing = sorted(
        package_name
        for package_name, module_name in modules.items()
        if importlib.util.find_spec(module_name) is None
    )
    return {
        "ok": not missing,
        "bundle": bundle,
        "python": sys.executable,
        "backend_root": str(_backend_root()),
        "missing": missing,
        "hint": None if not missing else (
            f"Install backend runtime deps with `pip install -e .[{bundle}]` from python_backend, "
            "or run Setup -> Full Setup."
        ),
    }


def install_backend_bundle(task: SetupTask, bundle: str = "studio_bundle") -> None:
    root = _backend_root()
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        raise RuntimeError(f"Backend pyproject.toml not found at {pyproject}")

    SetupTaskManager.log(task, f"Installing backend runtime bundle `{bundle}`...")
    SetupTaskManager.set_progress(task, 0.1)

    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"],
        cwd=str(root),
    )

    SetupTaskManager.set_progress(task, 0.35)
    SetupTaskManager.log(task, f"Running pip install -e .[{bundle}]")

    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-e", f".[{bundle}]"],
        cwd=str(root),
    )

    SetupTaskManager.set_progress(task, 0.9)
    status = check_backend_bundle(bundle)
    if not status["ok"]:
        missing = ", ".join(status["missing"]) or "unknown modules"
        raise RuntimeError(
            f"Backend runtime bundle `{bundle}` installed, but imports are still missing: {missing}"
        )

    SetupTaskManager.set_progress(task, 1.0)
    SetupTaskManager.log(task, f"Backend runtime bundle `{bundle}` is ready.")


def download_and_install_7zip(task: SetupTask, external_dir: Path, data_dir: Path | None = None) -> None:
    """Install 7-Zip on Windows (official source: 7-zip.org). 
    Uses installer EXE. No-op if 7z is already available.
    """
    if platform.system() != "Windows":
        SetupTaskManager.log(task, "7-Zip install is Windows-only; skipping.")
        return

    try:
        existing = _find_7z_exe(external_dir, data_dir)
        SetupTaskManager.log(task, f"7-Zip already available at: {existing}")
        return
    except Exception:
        pass

    dest_dir = (external_dir / "_installers").resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Fetch official download page and pick the x64 .exe link.
    page_url = "https://7-zip.org/download.html"
    SetupTaskManager.log(task, f"Fetching 7-Zip download page: {page_url}")
    r = requests.get(page_url, timeout=30)
    r.raise_for_status()
    html = r.text

    # Prefer x64 installer for modern Windows.
    m = re.search(r'href="(a/[^"]*?-x64\.exe)"', html, re.IGNORECASE)
    if not m:
        # fallback: any x64 exe
        m = re.search(r'href="(a/[^"]*?x64[^"]*?\.exe)"', html, re.IGNORECASE)
    if not m:
        raise RuntimeError("Could not locate 7-Zip x64 installer link on 7-zip.org download page.")

    rel = m.group(1)
    url = "https://7-zip.org/" + rel.lstrip("/")
    fname = Path(rel).name
    installer = dest_dir / fname

    SetupTaskManager.log(task, f"Downloading 7-Zip installer: {url}")
    with requests.get(url, stream=True, timeout=60) as rr:
        rr.raise_for_status()
        total = int(rr.headers.get("content-length") or "0")
        got = 0
        with open(installer, "wb") as f:
            for chunk in rr.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                got += len(chunk)
                if total > 0:
                    task.progress = min(0.95, got / total)

    task.progress = 0.96
    SetupTaskManager.log(task, f"Running 7-Zip installer: {installer}")
    # Silent install. /S is supported by 7-Zip installer EXE.
    subprocess.check_call([str(installer), "/S"], cwd=str(dest_dir))

    # Verify installation
    seven = _find_7z_exe(external_dir, data_dir)
    task.progress = 1.0
    SetupTaskManager.log(task, f"7-Zip installed successfully: {seven}")
