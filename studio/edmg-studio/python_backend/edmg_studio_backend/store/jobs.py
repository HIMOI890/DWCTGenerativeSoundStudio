from __future__ import annotations

import json, time, uuid
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

Status = Literal["queued","running","succeeded","failed","canceled"]

@dataclass
class Job:
    id: str
    project_id: str
    type: str
    status: Status
    created_at: str
    updated_at: str
    payload: dict[str, Any]
    result: dict[str, Any] | None = None
    error: str | None = None
    progress: dict[str, Any] | None = None

class JobStore:
    def __init__(self, projects_dir: Path):
        self.projects_dir = projects_dir
        self._lock = threading.Lock()  # process-local claim lock

    def _jobs_dir(self, project_id: str) -> Path:
        d = self.projects_dir / project_id / "jobs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def create(self, project_id: str, job_type: str, payload: dict[str, Any]) -> Job:
        jid = uuid.uuid4().hex
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        job = Job(id=jid, project_id=project_id, type=job_type, status="queued", created_at=now, updated_at=now, payload=payload)
        self.save(job)
        return job

    def save(self, job: Job) -> None:
        d = self._jobs_dir(job.project_id)
        path = d / f"{job.id}.json"
        job.updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
        path.write_text(json.dumps(job.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")

    def get(self, project_id: str, job_id: str) -> Job | None:
        p = self._jobs_dir(project_id) / f"{job_id}.json"
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return Job(**data)
        except Exception:
            return None

    def log_path(self, project_id: str, job_id: str) -> Path:
        d = self._jobs_dir(project_id)
        return d / f"{job_id}.log"

    def append_log(self, project_id: str, job_id: str, line: str) -> None:
        lp = self.log_path(project_id, job_id)
        ts = time.strftime("%H:%M:%S")
        lp.parent.mkdir(parents=True, exist_ok=True)
        with lp.open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] {line.rstrip()}\n")

    def update_progress(
        self,
        project_id: str,
        job_id: str,
        *,
        stage: str,
        current: int,
        total: int,
        message: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Job | None:
        job = self.get(project_id, job_id)
        if not job:
            return None
        total_i = max(1, int(total))
        current_i = max(0, min(int(current), total_i))
        pct = max(0.0, min(100.0, (float(current_i) / float(total_i)) * 100.0))
        progress = {
            "stage": str(stage or "running"),
            "current": current_i,
            "total": total_i,
            "percent": round(pct, 1),
        }
        if message:
            progress["message"] = str(message)
        if extra:
            progress.update(extra)
        job.progress = progress
        self.save(job)
        return job

    def list_for_project(self, project_id: str) -> list[Job]:
        jdir = self.projects_dir / project_id / "jobs"
        if not jdir.exists():
            return []
        jobs: list[Job] = []
        for jpath in jdir.glob("*.json"):
            try:
                data = json.loads(jpath.read_text(encoding="utf-8"))
                jobs.append(Job(**data))
            except Exception:
                continue
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs

    def cancel(self, project_id: str, job_id: str) -> Job | None:
        job = self.get(project_id, job_id)
        if not job:
            return None
        if job.status in ("succeeded", "failed", "canceled"):
            return job
        job.status = "canceled"
        if isinstance(job.progress, dict):
            total = max(1, int(job.progress.get("total", 1) or 1))
            current = max(0, min(int(job.progress.get("current", 0) or 0), total))
            job.progress = {
                **job.progress,
                "stage": "canceled",
                "current": current,
                "total": total,
                "percent": round(max(0.0, min(100.0, (float(current) / float(total)) * 100.0)), 1),
                "message": "Cancel requested — waiting for current step to finish",
            }
        self.save(job)
        self.append_log(project_id, job_id, "Job canceled")
        return job

    def retry(self, project_id: str, job_id: str) -> Job | None:
        job = self.get(project_id, job_id)
        if not job:
            return None
        job.status = "queued"
        job.error = None
        job.result = None
        job.progress = None
        self.save(job)
        self.append_log(project_id, job_id, "Job retried (re-queued)")
        return job

    def list_all(self) -> list[Job]:
        jobs: list[Job] = []
        for proj_dir in self.projects_dir.iterdir():
            if not proj_dir.is_dir():
                continue
            jdir = proj_dir / "jobs"
            if not jdir.exists():
                continue
            for jpath in jdir.glob("*.json"):
                try:
                    data = json.loads(jpath.read_text(encoding="utf-8"))
                    jobs.append(Job(**data))
                except Exception:
                    continue
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs

    def next_queued(self) -> Job | None:
        """Compatibility helper. Prefer claim_next_queued() in worker loops."""
        queued = [j for j in self.list_all() if j.status == "queued"]
        return queued[0] if queued else None

    def claim_next_queued(self) -> Job | None:
        """Atomically claim the next queued job (process-local).

        This prevents multiple worker threads from starting the same job.
        """
        with self._lock:
            job = self.next_queued()
            if not job:
                return None
            # Re-load from disk to ensure latest status before claiming.
            latest = self.get(job.project_id, job.id)
            if not latest or latest.status != "queued":
                return None
            latest.status = "running"
            self.save(latest)
            self.append_log(latest.project_id, latest.id, "Claimed by worker")
            return latest
