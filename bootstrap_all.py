#!/usr/bin/env python3
"""CLI orchestrator for installing/running EDMG + backends (CPU-first).

Examples:
  python bootstrap_all.py install --edmg --full
  python bootstrap_all.py install --a1111 --deforum --cpu-only
  python bootstrap_all.py install --comfyui --cpu-only
  python bootstrap_all.py run edmg-api
  python bootstrap_all.py run a1111
  python bootstrap_all.py verify

This is intentionally conservative: it will *attempt* to install, but does not manage GPU drivers.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen, Request

REPO_ROOT = Path(__file__).resolve().parent
EXTERNAL_ROOT = REPO_ROOT / "external"
VENV_DIR = REPO_ROOT / ".venv"
PYTHON_EXE = VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

CPU_A1111_FLAGS = "--use-cpu all --precision full --no-half --skip-torch-cuda-test --api --listen --port 7860"
CPU_COMFY_FLAGS = "--cpu --listen 127.0.0.1 --port 8188"

def sh(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    print("$", " ".join(cmd))
    subprocess.check_call(cmd, cwd=str(cwd) if cwd else None, env=env)

def http_ok(url: str, timeout_s: float = 2.0) -> bool:
    try:
        req = Request(url, headers={"User-Agent": "edmg-bootstrap"})
        with urlopen(req, timeout=timeout_s) as resp:
            return int(getattr(resp, "status", 200)) < 400
    except Exception:
        return False

def ensure_venv() -> None:
    if PYTHON_EXE.exists():
        return
    sh([sys.executable, "-m", "venv", str(VENV_DIR)], cwd=REPO_ROOT)
    sh([str(PYTHON_EXE), "-m", "pip", "install", "-U", "pip", "setuptools", "wheel"], cwd=REPO_ROOT)

def install_edmg(full: bool) -> None:
    ensure_venv()
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(REPO_ROOT / "src"), str(REPO_ROOT / "studio" / "edmg-studio" / "python_backend")])
    sh([str(PYTHON_EXE), "-m", "pip", "install", "-U", "torch", "torchvision", "torchaudio",
        "--index-url", "https://download.pytorch.org/whl/cpu"], cwd=REPO_ROOT, env=env)
    req = REPO_ROOT / ("requirements-full.txt" if full else "requirements-minimal.txt")
    sh([str(PYTHON_EXE), "-m", "pip", "install", "-r", str(req)], cwd=REPO_ROOT, env=env)
    sh([str(PYTHON_EXE), "-m", "pip", "install", "-e", "."], cwd=REPO_ROOT, env=env)

def git_clone(url: str, dest: Path) -> None:
    if dest.exists():
        return
    EXTERNAL_ROOT.mkdir(exist_ok=True)
    sh(["git", "clone", url, str(dest)], cwd=EXTERNAL_ROOT)

def install_a1111(cpu_only: bool, with_deforum: bool) -> None:
    a1111_dir = EXTERNAL_ROOT / "stable-diffusion-webui"
    git_clone("https://github.com/AUTOMATIC1111/stable-diffusion-webui.git", a1111_dir)
    if with_deforum:
        ext_dir = a1111_dir / "extensions" / "deforum"
        if not ext_dir.exists():
            (a1111_dir / "extensions").mkdir(exist_ok=True)
            sh(["git", "clone", "https://github.com/deforum-art/sd-webui-deforum.git", str(ext_dir)], cwd=a1111_dir)

    # Write webui-user.*
    if os.name == "nt":
        user_file = a1111_dir / "webui-user.bat"
        flags = CPU_A1111_FLAGS if cpu_only else "--api --listen --port 7860"
        user_file.write_text(f"@echo off\nset PYTHON=python\nset VENV_DIR=venv\nset COMMANDLINE_ARGS={flags}\ncall webui.bat\n", encoding="utf-8")
    else:
        user_file = a1111_dir / "webui-user.sh"
        flags = CPU_A1111_FLAGS if cpu_only else "--api --listen --port 7860"
        user_file.write_text(f"#!/usr/bin/env bash\nexport COMMANDLINE_ARGS=\"{flags}\"\n./webui.sh\n", encoding="utf-8")
        try:
            user_file.chmod(0o755)
        except Exception:
            pass

def install_comfyui(cpu_only: bool) -> None:
    comfy_dir = EXTERNAL_ROOT / "ComfyUI"
    git_clone("https://github.com/comfyanonymous/ComfyUI.git", comfy_dir)
    venv_dir = comfy_dir / ".venv"
    py = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not py.exists():
        sh([sys.executable, "-m", "venv", str(venv_dir)], cwd=comfy_dir)
        sh([str(py), "-m", "pip", "install", "-U", "pip", "setuptools", "wheel"], cwd=comfy_dir)
    req = comfy_dir / "requirements.txt"
    if req.exists():
        sh([str(py), "-m", "pip", "install", "-r", str(req)], cwd=comfy_dir)
    sh([str(py), "-m", "pip", "install", "-U", "torch", "torchvision", "torchaudio",
        "--index-url", "https://download.pytorch.org/whl/cpu"], cwd=comfy_dir)

def run_target(target: str, cpu_only: bool) -> None:
    ensure_venv()
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(REPO_ROOT / "src"), str(REPO_ROOT / "studio" / "edmg-studio" / "python_backend")])
    env["EDMG_CPU_ONLY"] = "1" if cpu_only else "0"

    if target == "edmg-ui":
        sh([str(PYTHON_EXE), "-m", "enhanced_deforum_music_generator", "ui"], cwd=REPO_ROOT, env=env)
    elif target == "edmg-api":
        sh([str(PYTHON_EXE), "-m", "uvicorn", "enhanced_deforum_music_generator.api.main:app", "--host", "127.0.0.1", "--port", "8000"], cwd=REPO_ROOT, env=env)
    elif target == "a1111":
        a1111_dir = EXTERNAL_ROOT / "stable-diffusion-webui"
        if os.name == "nt":
            sh(["cmd", "/c", "webui-user.bat"], cwd=a1111_dir, env=env)
        else:
            sh(["bash", "webui-user.sh"], cwd=a1111_dir, env=env)
    elif target == "comfyui":
        comfy_dir = EXTERNAL_ROOT / "ComfyUI"
        py = comfy_dir / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        flags = CPU_COMFY_FLAGS.split() if cpu_only else ["--listen", "127.0.0.1", "--port", "8188"]
        sh([str(py), "main.py"] + flags, cwd=comfy_dir, env=env)
    else:
        raise SystemExit(f"Unknown target: {target}")

def verify() -> int:
    urls = [
        ("edmg", "http://127.0.0.1:8000/health/"),
        ("a1111", "http://127.0.0.1:7860/sdapi/v1/progress"),
        ("comfyui", "http://127.0.0.1:8188/system_stats"),
    ]
    ok_all = True
    for name, url in urls:
        ok = http_ok(url)
        print(f"{name}: {'OK' if ok else 'NO'} - {url}")
        ok_all = ok_all and ok
    return 0 if ok_all else 2

def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    ap_i = sub.add_parser("install")
    ap_i.add_argument("--edmg", action="store_true")
    ap_i.add_argument("--full", action="store_true")
    ap_i.add_argument("--a1111", action="store_true")
    ap_i.add_argument("--deforum", action="store_true")
    ap_i.add_argument("--comfyui", action="store_true")
    ap_i.add_argument("--cpu-only", action="store_true", default=True)

    ap_r = sub.add_parser("run")
    ap_r.add_argument("target", choices=["edmg-ui", "edmg-api", "a1111", "comfyui"])
    ap_r.add_argument("--cpu-only", action="store_true", default=True)

    sub.add_parser("verify")

    args = ap.parse_args()

    if args.cmd == "install":
        if args.edmg:
            install_edmg(full=args.full)
        if args.a1111:
            install_a1111(cpu_only=args.cpu_only, with_deforum=args.deforum)
        if args.comfyui:
            install_comfyui(cpu_only=args.cpu_only)
        return 0

    if args.cmd == "run":
        run_target(args.target, cpu_only=args.cpu_only)
        return 0

    if args.cmd == "verify":
        return verify()

    return 2

if __name__ == "__main__":
    raise SystemExit(main())
