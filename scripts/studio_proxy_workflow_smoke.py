from __future__ import annotations

import json
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

import sys

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / 'studio' / 'edmg-studio' / 'python_backend'
if _BACKEND.exists() and str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
if str(_ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(_ROOT / 'src'))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from edmg_studio_backend import app as studio_app
from edmg_studio_backend.services import internal_video as internal_video_service
from edmg_studio_backend.store.jobs import JobStore
from edmg_studio_backend.store.projects import ProjectStore



def _fake_assemble_image_sequence(*, frames_dir, out_mp4, fps, glob_pattern, audio_path=None, ffmpeg_path=None):
    frames = sorted(frames_dir.glob(glob_pattern))
    if not frames:
        raise RuntimeError("expected rendered frames")
    out_mp4.write_bytes(b"proxy-raw-mp4")



def _fake_interpolate_video_fps(*, in_mp4, out_mp4, fps_out, engine, ffmpeg_path=None):
    out_mp4.write_bytes(in_mp4.read_bytes() + f"-fps{fps_out}".encode("utf-8"))



def run_smoke(work_root: Path | None = None) -> dict[str, Any]:
    owns_tmpdir = work_root is None
    tmp_ctx = tempfile.TemporaryDirectory(prefix="edmg-studio-smoke-") if owns_tmpdir else None
    root = Path(tmp_ctx.name) if tmp_ctx is not None else Path(work_root)
    root.mkdir(parents=True, exist_ok=True)

    store = ProjectStore(root / "data")
    jobs = JobStore(store.projects_dir)
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(studio_app.worker, "start", lambda: None))
            stack.enter_context(patch.object(studio_app.worker, "stop", lambda: None))
            stack.enter_context(patch.object(studio_app, "store", store))
            stack.enter_context(patch.object(studio_app, "jobs", jobs))
            stack.enter_context(patch.object(studio_app.models, "installed_path", lambda _mid: None))
            stack.enter_context(patch.object(studio_app.comfy_pool, "diagnose", lambda _req: {"compatible": [], "busy_compatible": []}))
            stack.enter_context(patch.object(internal_video_service, "assemble_image_sequence", _fake_assemble_image_sequence))
            stack.enter_context(patch.object(internal_video_service, "interpolate_video_fps", _fake_interpolate_video_fps))

            with TestClient(studio_app.app) as client:
                created = client.post("/v1/projects", json={"name": "Studio Proxy Smoke"})
                created.raise_for_status()
                project_id = created.json()["project"]["id"]

                proj = store.get(project_id)
                assert proj is not None
                proj.meta["analysis"] = {
                    "duration_s": 4.0,
                    "features": {
                        "duration_s": 4.0,
                        "tempo_bpm": 120.0,
                        "beats": [0.0, 1.0, 2.0, 3.0, 4.0],
                        "energy": [0.2, 0.6, 0.35, 0.8],
                    },
                }
                store.save(proj)

                planned = client.post(
                    f"/v1/projects/{project_id}/plan?mode=local",
                    json={
                        "title": "Studio Proxy Smoke",
                        "style_prefs": "neon geometric concert visuals",
                        "num_variants": 1,
                        "max_scenes": 3,
                    },
                )
                planned.raise_for_status()
                plan = planned.json()
                assert plan["variants"], "expected a generated local plan"

                applied = client.post(
                    f"/v1/projects/{project_id}/timeline/apply_plan",
                    json={"variant_index": 0, "overwrite": True},
                )
                applied.raise_for_status()
                timeline = applied.json()["timeline"]
                assert timeline.get("tracks"), "expected timeline tracks after apply_plan"

                launched = client.post(
                    f"/v1/projects/{project_id}/pipeline/run",
                    params={"variant_index": 0, "preset": "fast", "mode": "auto", "engine": "auto"},
                )
                launched.raise_for_status()
                launch_payload = launched.json()
                assert launch_payload["render_mode"] == "proxy"
                job_id = launch_payload["job"]["id"]

                tick = client.post("/v1/jobs/tick")
                tick.raise_for_status()
                tick_payload = tick.json()
                assert tick_payload["job"]["id"] == job_id
                assert tick_payload["job"]["status"] == "succeeded"
                checkpoint = (((tick_payload.get("job") or {}).get("progress") or {}).get("runtime_checkpoint") or {})
                assert checkpoint.get("status") == "complete"
                assert checkpoint.get("completed_frames", 0) > 0

                resumed = client.post(f"/v1/projects/{project_id}/jobs/{job_id}/resume_from_checkpoint")
                resumed.raise_for_status()
                resumed_payload = resumed.json()
                resumed_job_id = resumed_payload["job"]["id"]
                assert resumed_job_id != job_id
                assert resumed_payload["job"]["progress"]["queue_action"] == "resume_from_checkpoint"

                tick2 = client.post("/v1/jobs/tick")
                tick2.raise_for_status()
                tick2_payload = tick2.json()
                assert tick2_payload["job"]["id"] == resumed_job_id
                assert tick2_payload["job"]["status"] == "succeeded"

                detail = client.get(f"/v1/projects/{project_id}/jobs/{resumed_job_id}?tail_lines=40")
                detail.raise_for_status()
                detail_payload = detail.json()
                assert detail_payload["job"]["id"] == resumed_job_id
                assert detail_payload["runtime_checkpoint"]["status"] == "complete"
                assert detail_payload["outputs"]["checkpoint_exists"] is True
                assert detail_payload["log_line_count"] > 0

                cleared = client.post(f"/v1/projects/{project_id}/jobs/{resumed_job_id}/clear_cached_frames")
                cleared.raise_for_status()
                cleared_payload = cleared.json()
                assert "frames_dir" in (cleared_payload.get("removed") or [])
                assert cleared_payload["detail"]["resume_ready"] is False

                dropped = client.post(f"/v1/projects/{project_id}/jobs/{resumed_job_id}/drop_checkpoint")
                dropped.raise_for_status()
                dropped_payload = dropped.json()
                assert "checkpoint_json" in (dropped_payload.get("removed") or [])
                assert dropped_payload["detail"]["outputs"]["checkpoint_exists"] is False
                assert dropped_payload["detail"]["resume_ready"] is False

                outputs = client.get(f"/v1/projects/{project_id}/outputs")
                outputs.raise_for_status()
                out_payload = outputs.json()
                videos = out_payload["videos"]
                assert videos, "expected at least one rendered video"
                latest = out_payload["latest_internal_render"]
                assert latest and latest["mode"] == "proxy"
                history = out_payload["internal_render_history"]
                assert history and history[-1]["mode"] == "proxy"
                assert len(history) >= 2
                assert (history[-1].get("runtime_checkpoint") or {}).get("status") == "complete"
                assert out_payload.get("active_internal_jobs") == []

                rel_video = latest["video"]
                fetched = client.get(f"/v1/projects/{project_id}/file", params={"path": rel_video})
                fetched.raise_for_status()
                assert fetched.content.startswith(b"proxy-raw-mp4")

                return {
                    "ok": True,
                    "project_id": project_id,
                    "job_id": job_id,
                    "resume_job_id": resumed_job_id,
                    "render_mode": latest["mode"],
                    "video": rel_video,
                    "history_count": len(history),
                    "checkpoint_status": checkpoint.get("status"),
                    "checkpoint_chunks": checkpoint.get("estimated_chunks"),
                    "resume_action": resumed_payload["job"]["progress"].get("queue_action"),
                    "detail_checkpoint_path": detail_payload["outputs"].get("checkpoint_json_relpath"),
                    "detail_log_lines": detail_payload.get("log_line_count"),
                    "cache_clear_removed": cleared_payload.get("removed"),
                    "checkpoint_exists_after_drop": dropped_payload["detail"]["outputs"].get("checkpoint_exists"),
                    "resume_ready_after_drop": dropped_payload["detail"].get("resume_ready"),
                    "work_root": str(root),
                }
    finally:
        if tmp_ctx is not None:
            tmp_ctx.cleanup()



def main() -> int:
    summary = run_smoke()
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
