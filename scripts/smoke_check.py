from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str], cwd: Path, env: dict | None = None) -> int:
    print("> " + " ".join(cmd), flush=True)
    p = subprocess.run(cmd, cwd=str(cwd), env=env)
    return int(p.returncode)


def main() -> int:
    root = Path(__file__).resolve().parents[1]

    rc = 0
    python_targets = [
        "enhanced_deforum_music_generator",
        "deforum_music",
        "src",
        "scripts",
        "tests",
        "studio/edmg-studio/python_backend",
    ]
    rc |= run([sys.executable, "-m", "compileall", "-q", *python_targets], root)
    stable_pytests = [
        "tests/test_studio_proxy_fallback.py",
        "tests/test_studio_workflow_smoke.py",
        "tests/test_studio_render_tiers.py",
        "tests/test_api.py",
        "tests/test_preview_generator.py",
        "tests/test_style_transfer.py",
        "tests/test_selfcheck_script.py",
    ]
    rc |= run([sys.executable, "-m", "pytest", "-q", *stable_pytests], root)

    print("smoke_check rc:", rc)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
