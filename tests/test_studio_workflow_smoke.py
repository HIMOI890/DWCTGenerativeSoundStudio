from __future__ import annotations

from pathlib import Path

from scripts.studio_proxy_workflow_smoke import run_smoke



def test_studio_proxy_workflow_smoke(tmp_path: Path):
    result = run_smoke(tmp_path)

    assert result["ok"] is True
    assert result["render_mode"] == "proxy"
    assert result["history_count"] >= 2
    assert result["checkpoint_status"] == "complete"
    assert int(result["checkpoint_chunks"] or 1) >= 1
    assert str(result["video"]).endswith(".mp4")
    assert result["resume_action"] == "resume_from_checkpoint"
    assert str(result["detail_checkpoint_path"]).endswith(".checkpoint.json")
    assert int(result["detail_log_lines"] or 0) > 0
    assert "frames_dir" in (result["cache_clear_removed"] or [])
    assert result["checkpoint_exists_after_drop"] is False
    assert result["resume_ready_after_drop"] is False
