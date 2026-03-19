import os
import json
from pathlib import Path
import subprocess
import sys

def test_fetch_comfyui_workflows_supports_file_scheme(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "scripts" / "fetch_comfyui_workflows.py"

    # Create a minimal workflow JSON
    wf = {"last_node_id": 1, "nodes": [], "links": []}
    src = tmp_path / "wf.json"
    src.write_text(json.dumps(wf), encoding="utf-8")

    # Create a minimal manifest pointing to file://
    manifest = {
        "items": [
            {
                "id": "test_wf",
                "title": "Test Workflow",
                "family": "test",
                "source_url": f"file://{src.as_posix()}",
                "file_name": "test_wf.json",
                "model_hints": {},
            }
        ]
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    out_dir = tmp_path / "out"
    out_dir.mkdir()

    # Run fetcher with custom manifest path if supported, else we patch via env var.
    env = dict(**os.environ)
    env["EDMG_WORKFLOW_MANIFEST"] = str(manifest_path)

    cmd = [sys.executable, str(script), "--out", str(out_dir), "--manifest", str(manifest_path)]
    proc = subprocess.run(cmd, cwd=str(repo_root), env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr

    fetched = out_dir / "test_wf.json"
    assert fetched.exists()
    got = json.loads(fetched.read_text(encoding="utf-8"))
    assert "nodes" in got
