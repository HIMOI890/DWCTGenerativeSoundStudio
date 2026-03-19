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
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]


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
            "HUGGINGFACE_HUB_CACHE": str(paths["hf"] / "hub"),
            "TRANSFORMERS_CACHE": str(paths["transformers"]),
            "TORCH_HOME": str(paths["torch"]),
            "NLTK_DATA": str(paths["nltk"]),
            "WHISPER_CACHE_DIR": str(paths["whisper"]),
            "MPLCONFIGDIR": str(paths["matplotlib"]),
            "TMP": str(paths["tmp"]),
            "TEMP": str(paths["tmp"]),
        }
    )
    return env


def _run(cmd: Sequence[str], *, cwd: Optional[Path] = None, env: Optional[dict[str, str]] = None) -> int:
    p = subprocess.run(list(cmd), cwd=str(cwd) if cwd else None, env=env)
    return int(p.returncode)


def _pip(py: Path, args: Sequence[str], *, env: Optional[dict[str, str]] = None) -> int:
    return _run([str(py), "-m", "pip", *args], cwd=REPO_ROOT, env=env)


def _ensure_venv(venv_dir: Path, *, env: Optional[dict[str, str]] = None) -> Path:
    py = _venv_python(venv_dir)
    if py.exists():
        return py
    print(f"[edmg-installer] Creating venv: {venv_dir}")
    if _run([sys.executable, "-m", "venv", str(venv_dir)], cwd=REPO_ROOT, env=env) != 0:
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



def _install_whisper_no_deps(py: Path, *, env: Optional[dict[str, str]] = None) -> int:
    # Install Whisper without deps to avoid pulling a conflicting torch wheel.
    # FFmpeg must be available separately.
    return _pip(py, ["install", "--no-deps", "-U", "openai-whisper>=20230314"], env=env)


def _install_torch(py: Path, backend: str, *, env: Optional[dict[str, str]] = None) -> int:
    url = _torch_index_url(backend)
    print(f"[edmg-installer] Installing PyTorch ({backend}) from {url}")
    return _pip(
        py,
        [
            "install",
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
            [str(py), "-c", "import nltk; nltk.download('punkt', quiet=True); nltk.download('stopwords', quiet=True)"],
            cwd=REPO_ROOT,
            env=env,
        )
        _run([str(py), "-c", "import spacy; print('spacy ok')"], cwd=REPO_ROOT, env=env)

    if not skip_models:
        # Whisper cache corruption happens; keep best-effort.
        _run(
            [str(py), "-c", "import importlib.util as u;\nspec=u.find_spec('whisper');\nprint('whisper_installed', bool(spec));\nimport sys;\nif not spec: sys.exit(0);\nimport whisper;\ntry:\n  whisper.load_model('base');\n  print('whisper_warmup_ok');\nexcept Exception as e:\n  print('whisper_warmup_error', e);\nsys.exit(0)"],
            cwd=REPO_ROOT,
            env=env,
        )


def install(
    *,
    mode: str,
    backend: str,
    venv: Optional[str],
    cache_root: Optional[str],
    skip_torch: bool,
    skip_corpora: bool,
    skip_models: bool,
    skip_whisper: bool,
) -> int:
    managed_env = _managed_env(_resolve_path(cache_root) if cache_root else None)
    py = Path(sys.executable)
    resolved_venv: Optional[Path] = None
    if venv:
        resolved_venv = _resolve_path(venv)
        py = _ensure_venv(resolved_venv, env=managed_env)

    if _pip(py, ["install", "-U", "pip", "setuptools", "wheel"], env=managed_env) != 0:
        return 1

    if not skip_torch:
        if _install_torch(py, backend, env=managed_env) != 0:
            return 1

    req = _select_requirements(mode)
    print(f"[edmg-installer] Installing requirements from: {req.name}")
    if _pip(py, ["install", "-r", str(req)], env=managed_env) != 0:
        return 1

    # Whisper is optional and only installed for full/dev by default.
    if mode in ("full", "dev") and not skip_whisper:
        print("[edmg-installer] Installing Whisper (no-deps)")
        if _install_whisper_no_deps(py, env=managed_env) != 0:
            print("[edmg-installer] WARNING: Whisper install failed. Continuing.")

    # Editable install so `src/` packages are importable everywhere
    if _pip(py, ["install", "-e", "."], env=managed_env) != 0:
        return 1

    _post_install(py, skip_corpora=skip_corpora, skip_models=skip_models, env=managed_env)

    print("\n[edmg-installer] OK")
    if resolved_venv:
        if _is_windows():
            print(f"  Activate: {resolved_venv / 'Scripts' / 'activate'}")
        else:
            print(f"  Activate: source {resolved_venv / 'bin' / 'activate'}")
    if cache_root:
        print(f"  Cache:    {_resolve_path(cache_root)}")
    print("  Run UI:   python -m enhanced_deforum_music_generator ui --port 7860")
    print("  Deploy UI: python -m enhanced_deforum_music_generator ui --host 0.0.0.0 --port 7860")
    print("  Verify:   python scripts/edmg_installer.py verify")
    return 0


def verify() -> int:
    code = _run(
        [
            sys.executable,
            "-c",
            "import enhanced_deforum_music_generator as e, deforum_music as d; "
            "print('enhanced_deforum_music_generator:', e.__file__); "
            "print('deforum_music:', d.__file__)",
        ],
        cwd=REPO_ROOT,
    )
    if code != 0:
        return code

    # Verify public API + full Deforum template availability
    code = _run(
        [
            sys.executable,
            "-c",
            "from enhanced_deforum_music_generator.deforum_defaults import make_deforum_settings_template; "
            "d=make_deforum_settings_template(); "
            "print('deforum_template_keys', len(d)); "
            "assert 'W' in d and 'H' in d and 'prompts' in d",
        ],
        cwd=REPO_ROOT,
    )
    return int(code)


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="edmg-installer")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("install", help="Install dependencies + editable package")
    pi.add_argument("--mode", default="full", choices=["minimal", "standard", "full", "dev"])
    pi.add_argument("--venv", default="venv", help="Venv dir name (set empty to use current Python)")
    pi.add_argument("--cache-root", default="", help="Shared cache root for pip/HF/Torch/Whisper/temp files")
    pi.add_argument("--skip-torch", action="store_true", default=False)
    pi.add_argument("--backend", default="cpu", choices=["cpu", "cu118", "cu121", "cu124"])

    # Back-compat flags
    pi.add_argument("--cuda", action="store_true", default=False, help="(deprecated) same as --backend cu121")
    pi.add_argument("--cuda-version", default="", choices=["", "118", "121", "124"], help="(optional) convenience alias")

    pi.add_argument("--skip-corpora", action="store_true", default=False)
    pi.add_argument("--skip-models", action="store_true", default=False)
    pi.add_argument("--skip-whisper", action="store_true", default=False, help="Skip Whisper install (full/dev only)")

    pv = sub.add_parser("verify", help="Verify key imports and CLIs")

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
            cache_root=str(args.cache_root).strip() or None,
            skip_torch=bool(args.skip_torch),
            skip_corpora=bool(args.skip_corpora),
            skip_models=bool(args.skip_models),
            skip_whisper=bool(args.skip_whisper),
        )

    if args.cmd == "verify":
        return verify()

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
