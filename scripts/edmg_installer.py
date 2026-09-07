#!/usr/bin/env python3
"""
scripts/edmg_installer.py

Deterministic installer used by:
- install.ps1 / install.sh
- bootstrap_all.py
- installer_gui.py

This installer *does not* manage GPU drivers. It can, however, install the
appropriate PyTorch wheels (CPU or CUDA) into the EDMG venv.

Examples:
  python scripts/edmg_installer.py install --mode full --backend cpu  --venv venv
  python scripts/edmg_installer.py install --mode full --backend cu121 --venv venv
  python scripts/edmg_installer.py verify
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
_UV_TOOLCHAIN_PATH = (
    REPO_ROOT
    / "studio"
    / "edmg-studio"
    / "python_backend"
    / "edmg_studio_backend"
    / "uv_toolchain.py"
)


def _load_uv_toolchain():
    spec = importlib.util.spec_from_file_location(
        "edmg_installer_uv_toolchain", _UV_TOOLCHAIN_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Missing uv toolchain helper: {_UV_TOOLCHAIN_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_UV_TOOLCHAIN = _load_uv_toolchain()
ToolchainError = _UV_TOOLCHAIN.ToolchainError
PYTHON_REQUIRED_MINOR = tuple(_UV_TOOLCHAIN.PYTHON_REQUIRED_MINOR)


def _required_python_version() -> str:
    return ".".join(str(part) for part in PYTHON_REQUIRED_MINOR)


def _is_windows() -> bool:
    return os.name == "nt"


def _venv_python(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts/python.exe" if _is_windows() else "bin/python")


def _resolve_path(path_value: str | Path) -> Path:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


def _managed_env(cache_root: Optional[Path]) -> Optional[dict[str, str]]:
    if cache_root is None:
        return None

    cache_root = _resolve_path(cache_root)
    paths = {
        "tmp": cache_root / "tmp",
        "pip": cache_root / "pip",
        "xdg": cache_root / "xdg",
        "hf": cache_root / "huggingface",
        "transformers": cache_root / "transformers",
        "torch": cache_root / "torch",
        "nltk": cache_root / "nltk_data",
        "whisper": cache_root / "whisper",
        "matplotlib": cache_root / "matplotlib",
        "uv": cache_root / "uv",
        "uv_toolchain": cache_root / "toolchain" / "uv",
        "python_install": cache_root / "python",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update(
        {
            "EDMG_CACHE_ROOT": str(cache_root),
            "PIP_CACHE_DIR": str(paths["pip"]),
            "XDG_CACHE_HOME": str(paths["xdg"]),
            "HF_HOME": str(paths["hf"]),
            "HF_HUB_CACHE": str(paths["hf"] / "hub"),
            "HF_XET_CACHE": str(paths["hf"] / "xet"),
            "HF_ASSETS_CACHE": str(paths["hf"] / "assets"),
            "HUGGINGFACE_HUB_CACHE": str(paths["hf"] / "hub"),
            "HUGGINGFACE_ASSETS_CACHE": str(paths["hf"] / "assets"),
            "TRANSFORMERS_CACHE": str(paths["transformers"]),
            "TORCH_HOME": str(paths["torch"]),
            "NLTK_DATA": str(paths["nltk"]),
            "WHISPER_CACHE_DIR": str(paths["whisper"]),
            "MPLCONFIGDIR": str(paths["matplotlib"]),
            "TMP": str(paths["tmp"]),
            "TEMP": str(paths["tmp"]),
            "UV_CACHE_DIR": str(paths["uv"]),
            "UV_PYTHON_INSTALL_DIR": str(paths["python_install"]),
            "EDMG_UV_INSTALL_ROOT": str(paths["uv_toolchain"]),
        }
    )
    return env


def _run(
    cmd: Sequence[str],
    *,
    cwd: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
) -> int:
    p = subprocess.run(list(cmd), cwd=str(cwd) if cwd else None, env=env)
    return int(p.returncode)


def _run_capture(
    cmd: Sequence[str],
    *,
    cwd: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(cmd),
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _resolve_uv(env: Optional[dict[str, str]] = None) -> Path:
    if env:
        override_keys = [
            key
            for key in env
            if key.startswith("UV_")
            or key.startswith("EDMG_UV_")
            or key in {"XDG_CACHE_HOME", "TMP", "TEMP"}
        ]
    else:
        override_keys = []

    previous = {key: os.environ.get(key) for key in override_keys}
    try:
        for key in override_keys:
            os.environ[key] = env[key]
        return Path(_UV_TOOLCHAIN.resolve_uv(install=True))
    finally:
        for key in override_keys:
            prior = previous[key]
            if prior is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prior


def _uv_install(
    uv: Path,
    py: Path,
    args: Sequence[str],
    *,
    env: Optional[dict[str, str]] = None,
) -> int:
    return _run(
        [str(uv), "pip", "install", "--python", str(py), *args],
        cwd=REPO_ROOT,
        env=env,
    )


def _normalize_python_request(python: Optional[str]) -> Optional[str]:
    normalized = str(python or "").strip()
    return normalized or None


def _resolve_requested_python(
    uv: Path,
    request: str,
    *,
    env: Optional[dict[str, str]] = None,
) -> Path:
    candidate = Path(request).expanduser()
    if candidate.is_absolute() or any(sep in request for sep in ("/", "\\")):
        resolved = _resolve_path(candidate)
        if not resolved.exists():
            raise RuntimeError(
                f"Requested Python interpreter was not found: {resolved}"
            )
        return resolved

    result = _run_capture(
        [str(uv), "python", "find", "--no-project", request],
        cwd=REPO_ROOT,
        env=env,
    )
    output = str(result.stdout or "").strip()
    if result.returncode != 0 or not output:
        detail = str(result.stderr or output).strip()
        if detail:
            raise RuntimeError(
                f"Unable to resolve requested Python {request!r}: {detail}"
            )
        raise RuntimeError(f"Unable to resolve requested Python {request!r}.")

    resolved = Path(output.splitlines()[-1].strip())
    if not resolved.exists():
        raise RuntimeError(
            f"Requested Python {request!r} resolved to a missing interpreter: {resolved}"
        )
    return resolved


def _python_version(
    py: Path,
    *,
    env: Optional[dict[str, str]] = None,
) -> str:
    result = _run_capture(
        [
            str(py),
            "-c",
            "import sys; print('.'.join(str(part) for part in sys.version_info[:3]))",
        ],
        cwd=REPO_ROOT,
        env=env,
    )
    version = str(result.stdout or "").strip()
    if result.returncode != 0 or not version:
        detail = str(result.stderr or version).strip()
        if detail:
            raise RuntimeError(f"Unable to inspect Python interpreter {py}: {detail}")
        raise RuntimeError(f"Unable to inspect Python interpreter {py}.")
    return version


def _matches_python_request(request: str, actual_version: str) -> bool:
    if re.fullmatch(r"\d+(?:\.\d+){0,2}", request) is None:
        return True

    requested_parts = request.split(".")
    actual_parts = actual_version.split(".")
    return actual_parts[: len(requested_parts)] == requested_parts


def _validate_python_request(
    py: Path,
    request: str,
    *,
    env: Optional[dict[str, str]] = None,
) -> None:
    actual_version = _python_version(py, env=env)
    if _matches_python_request(request, actual_version):
        return
    raise RuntimeError(
        f"Requested Python {request!r} but resolved interpreter {py} is {actual_version}."
    )


def _resolve_target_python(
    uv: Optional[Path],
    *,
    venv: Optional[str],
    python: Optional[str],
    env: Optional[dict[str, str]] = None,
) -> tuple[Path, Optional[Path], bool]:
    normalized_python = _normalize_python_request(python)
    resolved_venv = _resolve_path(venv) if venv else None
    resolved_python: Optional[Path] = None

    if normalized_python:
        if uv is None:
            raise RuntimeError(
                "uv is required to resolve a requested Python interpreter"
            )
        resolved_python = _resolve_requested_python(uv, normalized_python, env=env)

    if resolved_venv is not None:
        python_request = (
            str(resolved_python)
            if resolved_python is not None
            else _required_python_version()
        )
        py = _ensure_venv(
            uv,
            resolved_venv,
            python_request=python_request,
            env=env,
        )
        _validate_python_request(
            py,
            normalized_python or _required_python_version(),
            env=env,
        )
        return py, resolved_venv, resolved_python is not None

    if resolved_python is not None:
        _validate_python_request(resolved_python, normalized_python, env=env)
        return resolved_python, None, True

    return Path(sys.executable), None, False


def _shell_hint(value: str) -> str:
    return f'"{value}"' if any(char.isspace() for char in value) else value


def _python_command_hint(
    py: Path,
    *,
    resolved_venv: Optional[Path],
    explicit_python: bool,
) -> str:
    if resolved_venv is not None or not explicit_python:
        return "python"
    return _shell_hint(str(py))


def _verify_command_hint(
    py: Path,
    *,
    resolved_venv: Optional[Path],
    explicit_python: bool,
) -> str:
    if resolved_venv is not None:
        return (
            "python scripts/edmg_installer.py verify --venv "
            f"{_shell_hint(str(resolved_venv))}"
        )
    if explicit_python:
        return (
            "python scripts/edmg_installer.py verify --python "
            f"{_shell_hint(str(py))}"
        )
    return "python scripts/edmg_installer.py verify"


def _ensure_venv(
    uv: Path,
    venv_dir: Path,
    *,
    python_request: Optional[str] = None,
    env: Optional[dict[str, str]] = None,
) -> Path:
    py = _venv_python(venv_dir)
    if py.exists():
        return py
    print(f"[edmg-installer] Creating venv: {venv_dir}")
    target_python = (
        _normalize_python_request(python_request) or _required_python_version()
    )
    if (
        _run(
            [
                str(uv),
                "venv",
                "--python",
                target_python,
                "--seed",
                str(venv_dir),
            ],
            cwd=REPO_ROOT,
            env=env,
        )
        != 0
    ):
        raise RuntimeError("Failed to create venv")
    return _venv_python(venv_dir)


def _select_requirements(mode: str) -> Path:
    candidates = []
    if mode == "minimal":
        candidates.append(REPO_ROOT / "requirements-minimal.txt")
    if mode == "standard":
        candidates.append(REPO_ROOT / "requirements.txt")
    if mode == "full":
        candidates.append(REPO_ROOT / "requirements-full.txt")
    if mode == "dev":
        candidates.append(REPO_ROOT / "requirements-dev.txt")
    # Fallback
    candidates.append(REPO_ROOT / "requirements.txt")

    for c in candidates:
        if c.exists() and c.stat().st_size > 0:
            return c
    raise FileNotFoundError("No requirements file found.")


def _torch_index_url(backend: str) -> str:
    backend = backend.strip().lower()
    if backend in {"cpu", "cpu-only"}:
        return "https://download.pytorch.org/whl/cpu"
    if backend in {"cu118", "cu121", "cu124"}:
        return f"https://download.pytorch.org/whl/{backend}"
    raise ValueError(f"Unsupported backend: {backend} (use cpu, cu118, cu121, cu124)")


def _install_whisper_no_deps(
    uv: Path,
    py: Path,
    *,
    env: Optional[dict[str, str]] = None,
) -> int:
    # Install Whisper without deps to avoid pulling a conflicting torch wheel.
    # FFmpeg must be available separately.
    return _uv_install(
        uv,
        py,
        ["--no-deps", "-U", "openai-whisper>=20230314"],
        env=env,
    )


def _install_torch(
    uv: Path,
    py: Path,
    backend: str,
    *,
    env: Optional[dict[str, str]] = None,
) -> int:
    url = _torch_index_url(backend)
    print(f"[edmg-installer] Installing PyTorch ({backend}) from {url}")
    return _uv_install(
        uv,
        py,
        [
            "-U",
            "torch",
            "torchvision",
            "torchaudio",
            "--index-url",
            url,
        ],
        env=env,
    )


def _post_install(
    py: Path,
    *,
    skip_corpora: bool,
    skip_models: bool,
    env: Optional[dict[str, str]] = None,
) -> None:
    # Best-effort lightweight post install steps.
    if not skip_corpora:
        _run(
            [
                str(py),
                "-c",
                "import nltk; nltk.download('punkt', quiet=True); nltk.download('stopwords', quiet=True)",
            ],
            cwd=REPO_ROOT,
            env=env,
        )
        _run([str(py), "-c", "import spacy; print('spacy ok')"], cwd=REPO_ROOT, env=env)

    if not skip_models:
        # Whisper cache corruption happens; keep best-effort.
        _run(
            [
                str(py),
                "-c",
                "import importlib.util as u;\nspec=u.find_spec('whisper');\nprint('whisper_installed', bool(spec));\nimport sys;\nif not spec: sys.exit(0);\nimport whisper;\ntry:\n  whisper.load_model('base');\n  print('whisper_warmup_ok');\nexcept Exception as e:\n  print('whisper_warmup_error', e);\nsys.exit(0)",
            ],
            cwd=REPO_ROOT,
            env=env,
        )


def install(
    *,
    mode: str,
    backend: str,
    venv: Optional[str],
    python: Optional[str] = None,
    cache_root: Optional[str] = None,
    skip_torch: bool = False,
    skip_corpora: bool = False,
    skip_models: bool = False,
    skip_whisper: bool = False,
) -> int:
    managed_env = _managed_env(_resolve_path(cache_root) if cache_root else None)
    try:
        uv = _resolve_uv(managed_env)
        py, resolved_venv, explicit_python = _resolve_target_python(
            uv, venv=venv, python=python, env=managed_env
        )
    except (RuntimeError, ToolchainError) as exc:
        print(f"[edmg-installer] ERROR: {exc}", file=sys.stderr)
        return 1

    if not skip_torch:
        if _install_torch(uv, py, backend, env=managed_env) != 0:
            return 1

    req = _select_requirements(mode)
    print(f"[edmg-installer] Installing requirements from: {req.name}")
    if _uv_install(uv, py, ["-r", str(req)], env=managed_env) != 0:
        return 1

    # Whisper is optional and only installed for full/dev by default.
    if mode in ("full", "dev") and not skip_whisper:
        print("[edmg-installer] Installing Whisper (no-deps)")
        if _install_whisper_no_deps(uv, py, env=managed_env) != 0:
            print("[edmg-installer] WARNING: Whisper install failed. Continuing.")

    # Editable install so `src/` packages are importable everywhere
    if _uv_install(uv, py, ["-e", "."], env=managed_env) != 0:
        return 1

    _post_install(
        py, skip_corpora=skip_corpora, skip_models=skip_models, env=managed_env
    )

    print("\n[edmg-installer] OK")
    if resolved_venv:
        if _is_windows():
            print(f"  Activate: {resolved_venv / 'Scripts' / 'activate'}")
        else:
            print(f"  Activate: source {resolved_venv / 'bin' / 'activate'}")
    if cache_root:
        print(f"  Cache:    {_resolve_path(cache_root)}")
    python_cmd = _python_command_hint(
        py, resolved_venv=resolved_venv, explicit_python=explicit_python
    )
    print(
        f"  Run UI:   {python_cmd} -m enhanced_deforum_music_generator ui --port 7860"
    )
    print(
        "  Deploy UI: "
        f"{python_cmd} -m enhanced_deforum_music_generator ui --host 0.0.0.0 --port 7860"
    )
    print(
        "  Verify:   "
        f"{_verify_command_hint(py, resolved_venv=resolved_venv, explicit_python=explicit_python)}"
    )
    return 0


def verify(
    *,
    venv: Optional[str] = None,
    python: Optional[str] = None,
    cache_root: Optional[str] = None,
) -> int:
    managed_env = _managed_env(_resolve_path(cache_root) if cache_root else None)
    try:
        normalized_python = _normalize_python_request(python)
        uv = _resolve_uv(managed_env) if normalized_python else None
        if venv:
            py = _venv_python(_resolve_path(venv))
            if not py.exists():
                raise RuntimeError(f"Requested venv interpreter was not found: {py}")
            _validate_python_request(
                py,
                normalized_python or _required_python_version(),
                env=managed_env,
            )
        else:
            py, _, _ = _resolve_target_python(
                uv, venv=None, python=python, env=managed_env
            )
    except (RuntimeError, ToolchainError) as exc:
        print(f"[edmg-installer] ERROR: {exc}", file=sys.stderr)
        return 1

    code = _run(
        [
            str(py),
            "-c",
            "import enhanced_deforum_music_generator as e, deforum_music as d; "
            "print('enhanced_deforum_music_generator:', e.__file__); "
            "print('deforum_music:', d.__file__)",
        ],
        cwd=REPO_ROOT,
        env=managed_env,
    )
    if code != 0:
        return code

    # Verify public API + full Deforum template availability
    code = _run(
        [
            str(py),
            "-c",
            "from enhanced_deforum_music_generator.deforum_defaults import make_deforum_settings_template; "
            "d=make_deforum_settings_template(); "
            "print('deforum_template_keys', len(d)); "
            "assert 'W' in d and 'H' in d and 'prompts' in d",
        ],
        cwd=REPO_ROOT,
        env=managed_env,
    )
    return int(code)


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="edmg-installer")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("install", help="Install dependencies + editable package")
    pi.add_argument(
        "--mode", default="full", choices=["minimal", "standard", "full", "dev"]
    )
    pi.add_argument(
        "--python",
        default="",
        help="Target Python interpreter path or version request (for example 3.12 or C:\\Python312\\python.exe)",
    )
    pi.add_argument(
        "--venv", default="venv", help="Venv dir name (set empty to use current Python)"
    )
    pi.add_argument(
        "--cache-root",
        default="",
        help="Shared cache root for pip/HF/Torch/Whisper/temp files",
    )
    pi.add_argument("--skip-torch", action="store_true", default=False)
    pi.add_argument(
        "--backend", default="cpu", choices=["cpu", "cu118", "cu121", "cu124"]
    )

    # Back-compat flags
    pi.add_argument(
        "--cuda",
        action="store_true",
        default=False,
        help="(deprecated) same as --backend cu121",
    )
    pi.add_argument(
        "--cuda-version",
        default="",
        choices=["", "118", "121", "124"],
        help="(optional) convenience alias",
    )

    pi.add_argument("--skip-corpora", action="store_true", default=False)
    pi.add_argument("--skip-models", action="store_true", default=False)
    pi.add_argument(
        "--skip-whisper",
        action="store_true",
        default=False,
        help="Skip Whisper install (full/dev only)",
    )

    pv = sub.add_parser("verify", help="Verify key imports and CLIs")
    pv.add_argument(
        "--python",
        default="",
        help="Target Python interpreter path or version request (for example 3.12 or C:\\Python312\\python.exe)",
    )
    pv.add_argument(
        "--venv",
        default="",
        help="Existing venv dir to verify instead of the launcher Python",
    )
    pv.add_argument(
        "--cache-root",
        default="",
        help="Shared cache root used to resolve managed interpreters",
    )

    args = p.parse_args(argv)

    if args.cmd == "install":
        venv = args.venv.strip() if isinstance(args.venv, str) else "venv"
        if venv == "":
            venv = None

        backend = str(args.backend)
        if args.cuda_version:
            backend = f"cu{args.cuda_version}"
        if bool(args.cuda) and not args.cuda_version and args.backend == "cpu":
            backend = "cu121"

        return install(
            mode=str(args.mode),
            backend=backend,
            venv=venv,
            python=str(args.python).strip() or None,
            cache_root=str(args.cache_root).strip() or None,
            skip_torch=bool(args.skip_torch),
            skip_corpora=bool(args.skip_corpora),
            skip_models=bool(args.skip_models),
            skip_whisper=bool(args.skip_whisper),
        )

    if args.cmd == "verify":
        venv = str(args.venv).strip() or None
        return verify(
            venv=venv,
            python=str(args.python).strip() or None,
            cache_root=str(args.cache_root).strip() or None,
        )

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
