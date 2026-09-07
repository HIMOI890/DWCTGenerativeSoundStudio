from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from contextlib import contextmanager

logger = logging.getLogger(__name__)

Status = Literal["queued", "paused", "running", "succeeded", "failed", "canceled"]

# Renamed aside when a project tree is unreadable (e.g. WinError 1392 on USB).
_CORRUPTED_QUARANTINE_SUFFIX = ".__corrupted_quarantine"


def _quarantine_unreadable_project(proj_dir: Path) -> Path | None:
    """Best-effort rename of a corrupted project folder so later scans skip it."""
    name = proj_dir.name
    if name.endswith(_CORRUPTED_QUARANTINE_SUFFIX):
        return None
    target = proj_dir.with_name(f"{name}{_CORRUPTED_QUARANTINE_SUFFIX}")
    try:
        if target.exists():
            target = proj_dir.with_name(f"{name}.{int(time.time())}{_CORRUPTED_QUARANTINE_SUFFIX}")
    except OSError:
        target = proj_dir.with_name(f"{name}.{int(time.time())}{_CORRUPTED_QUARANTINE_SUFFIX}")
    try:
        proj_dir.rename(target)
        logger.warning("Quarantined unreadable project directory: %s -> %s", proj_dir, target)
        return target
    except OSError as exc:
        logger.warning("Could not quarantine unreadable project %s: %s", proj_dir, exc)
        return None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    result_json TEXT,
    error TEXT,
    progress_json TEXT,
    lease_owner TEXT,
    lease_expires_at REAL,
    attempt INTEGER NOT NULL DEFAULT 0,
    idempotency_key TEXT,
    PRIMARY KEY (project_id, id)
);
CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at, id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_idempotency
    ON jobs(project_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE TABLE IF NOT EXISTS job_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    detail_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_job_events_job ON job_events(project_id, job_id, event_id);
"""


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
    attempt: int = 0
    idempotency_key: str | None = None


class JobStore:
    """SQLite-backed job/event store with JSON compatibility migration."""

    def __init__(self, projects_dir: Path, *, db_path: Path | None = None):
        self.projects_dir = projects_dir
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path or (self.projects_dir.parent / "jobs.sqlite")
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._migrate_json_jobs()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _jobs_dir(self, project_id: str) -> Path:
        d = self.projects_dir / project_id / "jobs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _now(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S")

    def _row_to_job(self, row: sqlite3.Row) -> Job:
        return Job(
            id=row["id"],
            project_id=row["project_id"],
            type=row["type"],
            status=row["status"],  # type: ignore[arg-type]
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            payload=json.loads(row["payload_json"] or "{}"),
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            error=row["error"],
            progress=json.loads(row["progress_json"]) if row["progress_json"] else None,
            attempt=int(row["attempt"] or 0),
            idempotency_key=row["idempotency_key"],
        )

    def _record_event(self, project_id: str, job_id: str, event_type: str, detail: dict[str, Any]) -> None:
        self._conn.execute(
            """
            INSERT INTO job_events(project_id, job_id, event_type, created_at, detail_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (project_id, job_id, event_type, self._now(), json.dumps(detail, ensure_ascii=False)),
        )

    def _upsert_job(self, job: Job) -> None:
        self._conn.execute(
            """
            INSERT INTO jobs(
                id, project_id, type, status, created_at, updated_at,
                payload_json, result_json, error, progress_json,
                lease_owner, lease_expires_at, attempt, idempotency_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
            ON CONFLICT(project_id, id) DO UPDATE SET
                type=excluded.type,
                status=excluded.status,
                updated_at=excluded.updated_at,
                payload_json=excluded.payload_json,
                result_json=excluded.result_json,
                error=excluded.error,
                progress_json=excluded.progress_json,
                attempt=excluded.attempt,
                idempotency_key=excluded.idempotency_key
            WHERE jobs.status IN ('queued', 'paused', 'running')
            """,
            (
                job.id,
                job.project_id,
                job.type,
                job.status,
                job.created_at,
                job.updated_at,
                json.dumps(job.payload or {}, ensure_ascii=False),
                json.dumps(job.result, ensure_ascii=False) if job.result is not None else None,
                job.error,
                json.dumps(job.progress, ensure_ascii=False) if job.progress is not None else None,
                int(job.attempt or 0),
                job.idempotency_key,
            ),
        )

    def _migrate_json_jobs(self) -> None:
        try:
            if not self.projects_dir.exists():
                return
            proj_dirs = list(self.projects_dir.iterdir())
        except OSError as exc:
            logger.warning(
                "Cannot scan projects directory for job migration: %s (%s)",
                self.projects_dir,
                exc,
            )
            return

        for proj_dir in proj_dirs:
            if proj_dir.name.endswith(_CORRUPTED_QUARANTINE_SUFFIX):
                continue
            try:
                # WinError 1392 (corrupt/unreadable) can raise from is_dir/exists/glob
                # on flaky USB/external volumes — never abort backend startup for one project.
                if not proj_dir.is_dir():
                    continue
                jobs_dir = proj_dir / "jobs"
                if not jobs_dir.exists():
                    continue
                job_paths = list(jobs_dir.glob("*.json"))
            except OSError as exc:
                logger.warning(
                    "Skipping corrupted project during job migration: %s (%s)",
                    proj_dir,
                    exc,
                )
                _quarantine_unreadable_project(proj_dir)
                continue

            for jpath in job_paths:
                try:
                    data = json.loads(jpath.read_text(encoding="utf-8"))
                    job = Job(
                        id=str(data["id"]),
                        project_id=str(data.get("project_id") or proj_dir.name),
                        type=str(data.get("type") or "unknown"),
                        status=str(data.get("status") or "queued"),  # type: ignore[arg-type]
                        created_at=str(data.get("created_at") or self._now()),
                        updated_at=str(data.get("updated_at") or self._now()),
                        payload=dict(data.get("payload") or {}),
                        result=data.get("result"),
                        error=data.get("error"),
                        progress=data.get("progress"),
                        attempt=int(data.get("attempt") or 0),
                        idempotency_key=data.get("idempotency_key"),
                    )
                except Exception:
                    continue
                existing = self.get(job.project_id, job.id)
                if existing is None:
                    with self._lock:
                        self._upsert_job(job)
                        self._record_event(
                            job.project_id,
                            job.id,
                            "migrated_from_json",
                            {"path": str(jpath)},
                        )
                        self._conn.commit()

    def create(
        self,
        project_id: str,
        job_type: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> Job:
        key = str(idempotency_key or "").strip() or None
        with self._lock:
            if key:
                row = self._conn.execute(
                    """
                    SELECT * FROM jobs
                    WHERE project_id = ? AND idempotency_key = ?
                    LIMIT 1
                    """,
                    (project_id, key),
                ).fetchone()
                if row is not None:
                    return self._row_to_job(row)
            jid = uuid.uuid4().hex
            now = self._now()
            job = Job(
                id=jid,
                project_id=project_id,
                type=job_type,
                status="queued",
                created_at=now,
                updated_at=now,
                payload=payload,
                idempotency_key=key,
            )
            self._upsert_job(job)
            self._record_event(project_id, jid, "created", {"type": job_type})
            self._conn.commit()
            # Keep a JSON mirror for older tooling that reads jobs/*.json.
            self._mirror_json(job)
            return job

    def _mirror_json(self, job: Job) -> None:
        path = self._jobs_dir(job.project_id) / f"{job.id}.json"
        path.write_text(json.dumps(job.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")

    def save(self, job: Job) -> None:
        with self._lock:
            job.updated_at = self._now()
            self._upsert_job(job)
            persisted = self.get(job.project_id, job.id)
            if persisted is not None:
                job.__dict__.update(persisted.__dict__)
            self._record_event(
                job.project_id,
                job.id,
                "saved",
                {"status": job.status},
            )
            self._conn.commit()
            self._mirror_json(job)

    @contextmanager
    def publication_guard(self, project_id: str, job_id: str):
        """Serialize publication with cancellation across worker processes."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                current = self.get(project_id, job_id)
                yield bool(current and current.status in ("queued", "running"))
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise

    def get(self, project_id: str, job_id: str) -> Job | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE project_id = ? AND id = ?",
                (project_id, job_id),
            ).fetchone()
        return self._row_to_job(row) if row else None

    def log_path(self, project_id: str, job_id: str) -> Path:
        return self._jobs_dir(project_id) / f"{job_id}.log"

    def append_log(self, project_id: str, job_id: str, line: str) -> None:
        lp = self.log_path(project_id, job_id)
        ts = time.strftime("%H:%M:%S")
        lp.parent.mkdir(parents=True, exist_ok=True)
        with lp.open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] {line.rstrip()}\n")
        with self._lock:
            self._record_event(project_id, job_id, "log", {"line": line})
            self._conn.commit()

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
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM jobs
                WHERE project_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (project_id,),
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

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

    def _transition_status(
        self,
        project_id: str,
        job_id: str,
        *,
        expected_status: Status,
        next_status: Status,
        event_type: str,
    ) -> Job | None:
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE jobs
                SET status = ?,
                    updated_at = ?,
                    lease_owner = NULL,
                    lease_expires_at = NULL
                WHERE project_id = ? AND id = ? AND status = ?
                """,
                (next_status, self._now(), project_id, job_id, expected_status),
            )
            transitioned = cursor.rowcount == 1
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE project_id = ? AND id = ?",
                (project_id, job_id),
            ).fetchone()
            if row is None:
                self._conn.commit()
                return None
            if transitioned:
                self._record_event(
                    project_id,
                    job_id,
                    event_type,
                    {"from_status": expected_status, "to_status": next_status},
                )
            self._conn.commit()
            job = self._row_to_job(row)
        if transitioned:
            self._mirror_json(job)
        return job

    def pause(self, project_id: str, job_id: str) -> Job | None:
        return self._transition_status(
            project_id,
            job_id,
            expected_status="queued",
            next_status="paused",
            event_type="paused",
        )

    def resume(self, project_id: str, job_id: str) -> Job | None:
        return self._transition_status(
            project_id,
            job_id,
            expected_status="paused",
            next_status="queued",
            event_type="resumed",
        )

    def retry(
        self,
        project_id: str,
        job_id: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> Job | None:
        """Atomically requeue a terminal job, optionally replacing its payload."""

        payload_json = json.dumps(payload, ensure_ascii=False) if payload is not None else None
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE jobs
                SET status = 'queued',
                    updated_at = ?,
                    payload_json = COALESCE(?, payload_json),
                    result_json = NULL,
                    error = NULL,
                    progress_json = NULL,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    attempt = attempt + 1
                WHERE project_id = ?
                  AND id = ?
                  AND status IN ('succeeded', 'failed', 'canceled')
                """,
                (self._now(), payload_json, project_id, job_id),
            )
            if cursor.rowcount != 1:
                self._conn.commit()
                return None
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE project_id = ? AND id = ?",
                (project_id, job_id),
            ).fetchone()
            if row is None:  # pragma: no cover - guarded by the successful update
                self._conn.rollback()
                return None
            self._record_event(
                project_id,
                job_id,
                "retried",
                {"to_status": "queued"},
            )
            self._conn.commit()
            job = self._row_to_job(row)
        self._mirror_json(job)
        self.append_log(project_id, job_id, "Job retried (re-queued)")
        return job

    def list_all(self) -> list[Job]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM jobs
                ORDER BY created_at DESC, id DESC
                """
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

    def next_queued(self) -> Job | None:
        """Compatibility helper. Prefer claim_next_queued() in worker loops."""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM jobs
                WHERE status = 'queued'
                ORDER BY created_at ASC, id ASC
                LIMIT 1
                """
            ).fetchone()
        return self._row_to_job(row) if row else None

    def claim_next_queued(self, *, lease_seconds: float = 300.0, owner: str | None = None) -> Job | None:
        """Atomically claim the next queued job with a lease."""
        claim_owner = owner or f"worker-{uuid.uuid4().hex[:8]}"
        now = time.time()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                # Re-queue expired leases so interrupted workers can recover.
                self._conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'queued',
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        updated_at = ?
                    WHERE status = 'running'
                      AND lease_expires_at IS NOT NULL
                      AND lease_expires_at < ?
                    """,
                    (self._now(), now),
                )
                row = self._conn.execute(
                    """
                    SELECT * FROM jobs
                    WHERE status = 'queued'
                    ORDER BY created_at ASC, id ASC
                    LIMIT 1
                    """
                ).fetchone()
                if row is None:
                    self._conn.commit()
                    return None
                job = self._row_to_job(row)
                latest = self.get(job.project_id, job.id)
                if not latest or latest.status != "queued":
                    self._conn.commit()
                    return None
                latest.status = "running"
                latest.updated_at = self._now()
                lease_update = self._conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'running',
                        updated_at = ?,
                        lease_owner = ?,
                        lease_expires_at = ?,
                        attempt = attempt + 1
                    WHERE project_id = ? AND id = ? AND status = 'queued'
                    """,
                    (
                        latest.updated_at,
                        claim_owner,
                        now + float(lease_seconds),
                        latest.project_id,
                        latest.id,
                    ),
                )
                if lease_update.rowcount != 1:
                    self._conn.commit()
                    return None
                latest.attempt = int(latest.attempt or 0) + 1
                self._record_event(
                    latest.project_id,
                    latest.id,
                    "claimed",
                    {"owner": claim_owner, "lease_seconds": lease_seconds},
                )
                self._conn.commit()
                self._mirror_json(latest)
                return latest
            except Exception:
                self._conn.rollback()
                raise

    def list_events(self, project_id: str, job_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT event_id, event_type, created_at, detail_json
                FROM job_events
                WHERE project_id = ? AND job_id = ?
                ORDER BY event_id ASC
                """,
                (project_id, job_id),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "event_id": row["event_id"],
                    "event_type": row["event_type"],
                    "created_at": row["created_at"],
                    "detail": json.loads(row["detail_json"] or "{}"),
                }
            )
        return out
