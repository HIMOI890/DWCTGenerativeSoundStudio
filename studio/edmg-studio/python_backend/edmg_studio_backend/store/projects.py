from __future__ import annotations

import errno
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = 2
_CORRUPTED_QUARANTINE_SUFFIX = ".__corrupted_quarantine"
_PROJECT_LOCK_FILENAME = ".project.lock"
_PROJECT_LOCK_POLL_S = 0.01
_PROJECT_LOCK_TIMEOUT_S = 15.0
_PROJECT_LOCK_STALE_S = 120.0


@dataclass
class Project:
    id: str
    name: str
    created_at: str
    updated_at: str
    revision: int
    meta: dict[str, Any]
    schema_version: int = CURRENT_SCHEMA_VERSION


MigrationFn = Callable[[dict[str, Any]], dict[str, Any]]


def _migrate_v0_to_v1(data: dict[str, Any]) -> dict[str, Any]:
    """Promote pre-versioned project.json documents to schema_version 1."""
    next_data = dict(data)
    next_data["schema_version"] = 1
    meta = dict(next_data.get("meta") or {})
    next_data["meta"] = meta
    return next_data


def _migrate_v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    """Add durable revision metadata to older project documents."""
    next_data = dict(data)
    created_at = str(next_data.get("created_at") or "").strip() or time.strftime("%Y-%m-%d %H:%M:%S")
    next_data["updated_at"] = str(next_data.get("updated_at") or "").strip() or created_at
    revision_raw = next_data.get("revision")
    if revision_raw is None:
        meta = next_data.get("meta")
        if isinstance(meta, dict):
            revision_raw = meta.get("revision")
    try:
        revision = max(1, int(revision_raw or 1))
    except (TypeError, ValueError):
        revision = 1
    next_data["revision"] = revision
    next_data["schema_version"] = 2
    return next_data


# Target version -> migration from previous version.
PROJECT_MIGRATIONS: dict[int, MigrationFn] = {
    1: _migrate_v0_to_v1,
    2: _migrate_v1_to_v2,
}


class StaleProjectRevisionError(RuntimeError):
    def __init__(self, project_id: str, expected_revision: int, actual_revision: int):
        self.project_id = project_id
        self.expected_revision = int(expected_revision)
        self.actual_revision = int(actual_revision)
        super().__init__(
            f"Project revision mismatch for {project_id}: expected {expected_revision}, current {actual_revision}"
        )


def validate_project_document(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("Project document must be an object")
    project_id = str(data.get("id") or "").strip()
    name = str(data.get("name") or "").strip()
    created_at = str(data.get("created_at") or "").strip()
    if not project_id:
        raise ValueError("Project document is missing id")
    if not name:
        raise ValueError("Project document is missing name")
    if not created_at:
        raise ValueError("Project document is missing created_at")
    meta = data.get("meta")
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        raise ValueError("Project meta must be an object")
    updated_at = str(data.get("updated_at") or "").strip() or created_at
    revision_raw = data.get("revision")
    if revision_raw is None:
        revision_raw = meta.get("revision")
    try:
        revision = max(1, int(revision_raw or 1))
    except (TypeError, ValueError) as exc:
        raise ValueError("Project revision must be an integer >= 1") from exc
    schema_version = int(data.get("schema_version") or 0)
    if schema_version < 0:
        raise ValueError("schema_version must be >= 0")
    if schema_version > CURRENT_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported project schema_version {schema_version}; "
            f"this Studio build supports up to {CURRENT_SCHEMA_VERSION}"
        )
    return {
        "id": project_id,
        "name": name,
        "created_at": created_at,
        "updated_at": updated_at,
        "revision": revision,
        "meta": meta,
        "schema_version": schema_version,
    }


def migrate_project_document(data: dict[str, Any]) -> tuple[dict[str, Any], bool, list[int]]:
    """Return (document, changed, applied_versions)."""
    current = validate_project_document(data)
    applied: list[int] = []
    version = int(current.get("schema_version") or 0)
    changed = False
    while version < CURRENT_SCHEMA_VERSION:
        target = version + 1
        migrator = PROJECT_MIGRATIONS.get(target)
        if migrator is None:
            raise ValueError(f"No migration registered for project schema_version {target}")
        current = validate_project_document(migrator(current))
        version = int(current["schema_version"])
        applied.append(target)
        changed = True
    if int(current["schema_version"]) != CURRENT_SCHEMA_VERSION:
        raise ValueError(
            f"Project migration stopped at schema_version {current['schema_version']}"
        )
    return current, changed, applied


class ProjectStore:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.projects_dir = self.base_dir / "projects"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self._locks_guard = threading.Lock()
        self._project_locks: dict[str, threading.RLock] = {}

    def _proj_dir(self, project_id: str) -> Path:
        d = self._resolve_project_dir(project_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "assets" / "audio").mkdir(parents=True, exist_ok=True)
        (d / "assets" / "overlays").mkdir(parents=True, exist_ok=True)
        (d / "assets" / "masks").mkdir(parents=True, exist_ok=True)
        (d / "assets" / "fonts").mkdir(parents=True, exist_ok=True)
        (d / "analysis").mkdir(parents=True, exist_ok=True)
        (d / "outputs" / "images").mkdir(parents=True, exist_ok=True)
        (d / "outputs" / "videos").mkdir(parents=True, exist_ok=True)
        (d / "outputs" / "deforum").mkdir(parents=True, exist_ok=True)
        (d / "outputs" / "unreal").mkdir(parents=True, exist_ok=True)
        (d / "jobs").mkdir(parents=True, exist_ok=True)
        return d

    def _project_path(self, project_id: str) -> Path:
        return self._resolve_project_dir(project_id) / "project.json"

    def _resolve_project_dir(self, project_id: str) -> Path:
        safe_project_id = self._validate_project_id(project_id)
        projects_root = os.path.realpath(os.fspath(self.projects_dir))
        candidate = os.path.realpath(os.path.join(projects_root, safe_project_id))
        if not candidate.startswith(projects_root + os.sep):
            raise ValueError("Project directory must stay inside the projects root")
        return Path(candidate)

    @staticmethod
    def _validate_project_id(project_id: str) -> str:
        value = str(project_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
            raise ValueError("Invalid project identifier")
        return value

    def _backup_before_migration(self, project_path: Path, from_version: int) -> Path:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = project_path.with_name(f"project.v{from_version}.{stamp}.bak.json")
        shutil.copy2(project_path, backup)
        return backup

    def _now(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S")

    def _project_lock(self, project_id: str) -> threading.RLock:
        safe_project_id = self._validate_project_id(project_id)
        with self._locks_guard:
            lock = self._project_locks.get(safe_project_id)
            if lock is None:
                lock = threading.RLock()
                self._project_locks[safe_project_id] = lock
            return lock

    def _lock_path(self, project_id: str) -> Path:
        project_dir = self._resolve_project_dir(project_id)
        project_dir.mkdir(parents=True, exist_ok=True)
        return project_dir / _PROJECT_LOCK_FILENAME

    def _try_break_stale_lock(self, lock_path: Path) -> bool:
        try:
            stat = lock_path.stat()
        except FileNotFoundError:
            return False
        except OSError:
            return False
        if time.time() - float(stat.st_mtime) < _PROJECT_LOCK_STALE_S:
            return False
        try:
            lock_path.unlink()
            logger.warning("Removed stale project lock: %s", lock_path)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            return False

    def _acquire_lock_file(self, project_id: str) -> tuple[Path, int]:
        lock_path = self._lock_path(project_id)
        deadline = time.monotonic() + _PROJECT_LOCK_TIMEOUT_S
        while True:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                payload = f"{os.getpid()} {time.time()}\n".encode("utf-8")
                os.write(fd, payload)
                try:
                    os.fsync(fd)
                except OSError:
                    pass
                return lock_path, fd
            except FileExistsError:
                if self._try_break_stale_lock(lock_path):
                    continue
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for project lock {lock_path}")
                time.sleep(_PROJECT_LOCK_POLL_S)

    @contextmanager
    def _synchronized_project(self, project_id: str):
        lock = self._project_lock(project_id)
        with lock:
            lock_path, fd = self._acquire_lock_file(project_id)
            try:
                yield
            finally:
                try:
                    os.close(fd)
                except OSError:
                    pass
                try:
                    lock_path.unlink(missing_ok=True)
                except OSError as exc:
                    logger.warning("Cannot remove project lock %s: %s", lock_path, exc)

    def _write_atomic(self, project_path: Path, payload: dict[str, Any]) -> None:
        project_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = project_path.with_name(f"{project_path.name}.{uuid.uuid4().hex}.tmp")
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        try:
            with tmp.open("x", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            for attempt in range(6):
                try:
                    os.replace(tmp, project_path)
                    break
                except OSError as exc:
                    winerror = getattr(exc, "winerror", None)
                    transient = (
                        isinstance(exc, PermissionError)
                        or exc.errno in {errno.EACCES, errno.EBUSY, errno.EPERM}
                        or winerror in {5, 32, 33}
                    )
                    if not transient or attempt == 5:
                        raise
                    time.sleep(0.025 * (2**attempt))
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Cannot remove temporary project document %s: %s", tmp, exc)

    def _to_project(self, data: dict[str, Any]) -> Project:
        validated = validate_project_document(data)
        return Project(
            id=validated["id"],
            name=validated["name"],
            created_at=validated["created_at"],
            updated_at=validated["updated_at"],
            revision=int(validated["revision"]),
            meta=dict(validated["meta"]),
            schema_version=int(validated["schema_version"]),
        )

    def _load_document(self, project_id: str, *, persist_migrations: bool = True) -> dict[str, Any] | None:
        safe_project_id = self._validate_project_id(project_id)
        project_path = self._project_path(safe_project_id)
        try:
            if not project_path.exists():
                return None
            raw = json.loads(project_path.read_text(encoding="utf-8"))
        except OSError as exc:
            logger.warning("Unreadable project document %s: %s", project_path, exc)
            return None
        from_version = int((raw or {}).get("schema_version") or 0)
        migrated, changed, _applied = migrate_project_document(raw)
        if changed and persist_migrations:
            with self._synchronized_project(safe_project_id):
                if not project_path.exists():
                    return migrated
                current_on_disk = json.loads(project_path.read_text(encoding="utf-8"))
                current_from_version = int((current_on_disk or {}).get("schema_version") or 0)
                current_migrated, current_changed, _ = migrate_project_document(current_on_disk)
                if current_changed:
                    self._backup_before_migration(project_path, current_from_version)
                    self._write_atomic(project_path, current_migrated)
                migrated = current_migrated
        return migrated

    def list(self) -> list[Project]:
        out: list[Project] = []
        try:
            entries = sorted(self.projects_dir.iterdir())
        except OSError as exc:
            logger.warning("Cannot list projects directory %s: %s", self.projects_dir, exc)
            return out
        for d in entries:
            if d.name.endswith(_CORRUPTED_QUARANTINE_SUFFIX):
                continue
            try:
                if not d.is_dir():
                    continue
                data = self._load_document(d.name)
                if data is None:
                    continue
                out.append(self._to_project(data))
            except OSError as exc:
                logger.warning("Skipping unreadable project directory %s: %s", d, exc)
                continue
            except Exception:
                continue
        return out

    def create(self, name: str) -> Project:
        pid = uuid.uuid4().hex
        created_at = self._now()
        proj = Project(
            id=pid,
            name=name,
            created_at=created_at,
            updated_at=created_at,
            revision=1,
            meta={},
            schema_version=CURRENT_SCHEMA_VERSION,
        )
        self.save(proj)
        self._proj_dir(pid)
        return proj

    def get(self, project_id: str) -> Project | None:
        try:
            data = self._load_document(project_id)
        except Exception:
            return None
        if data is None:
            return None
        return self._to_project(data)

    def _payload_for_project(
        self,
        proj: Project,
        *,
        created_at: str,
        updated_at: str,
        revision: int,
    ) -> dict[str, Any]:
        payload = {
            "id": proj.id,
            "name": proj.name,
            "created_at": created_at,
            "updated_at": updated_at,
            "revision": revision,
            "meta": dict(proj.meta or {}),
            "schema_version": CURRENT_SCHEMA_VERSION,
        }
        validated = validate_project_document(payload)
        return {
            "id": validated["id"],
            "name": validated["name"],
            "created_at": validated["created_at"],
            "updated_at": validated["updated_at"],
            "revision": int(validated["revision"]),
            "meta": dict(validated["meta"]),
            "schema_version": CURRENT_SCHEMA_VERSION,
        }

    def save(self, proj: Project, *, expected_revision: int | None = None) -> None:
        safe_project_id = self._validate_project_id(proj.id)
        with self._synchronized_project(safe_project_id):
            current = self._load_document(safe_project_id, persist_migrations=False)
            target = self._project_path(safe_project_id)
            if current is None:
                created_at = str(getattr(proj, "created_at", "") or "").strip() or self._now()
                next_revision = max(1, int(getattr(proj, "revision", 1) or 1))
            else:
                actual_revision = int(current.get("revision") or 1)
                effective_expected = expected_revision
                if effective_expected is None:
                    try:
                        effective_expected = int(getattr(proj, "revision", 0) or 0)
                    except (TypeError, ValueError):
                        effective_expected = 0
                if effective_expected and effective_expected != actual_revision:
                    raise StaleProjectRevisionError(
                        safe_project_id,
                        int(effective_expected),
                        actual_revision,
                    )
                created_at = str(current.get("created_at") or getattr(proj, "created_at", "") or self._now())
                next_revision = actual_revision + 1
            updated_at = self._now()
            payload = self._payload_for_project(
                proj,
                created_at=created_at,
                updated_at=updated_at,
                revision=next_revision,
            )
            self._write_atomic(target, payload)
            proj.created_at = created_at
            proj.updated_at = updated_at
            proj.revision = next_revision
            proj.meta = dict(payload["meta"])
            proj.schema_version = CURRENT_SCHEMA_VERSION

    def mutate(
        self,
        project_id: str,
        mutator: Callable[[Project], None],
        *,
        expected_revision: int | None = None,
    ) -> Project:
        safe_project_id = self._validate_project_id(project_id)
        with self._synchronized_project(safe_project_id):
            current = self._load_document(safe_project_id, persist_migrations=False)
            if current is None:
                raise KeyError("Project not found")
            actual_revision = int(current.get("revision") or 1)
            if expected_revision is not None and int(expected_revision) != actual_revision:
                raise StaleProjectRevisionError(
                    safe_project_id,
                    int(expected_revision),
                    actual_revision,
                )
            proj = self._to_project(current)
            mutator(proj)
            updated_at = self._now()
            payload = self._payload_for_project(
                proj,
                created_at=proj.created_at,
                updated_at=updated_at,
                revision=actual_revision + 1,
            )
            self._write_atomic(self._project_path(safe_project_id), payload)
            proj.updated_at = updated_at
            proj.revision = actual_revision + 1
            proj.meta = dict(payload["meta"])
            proj.schema_version = CURRENT_SCHEMA_VERSION
            return proj

    def project_dir(self, project_id: str) -> Path:
        return self._proj_dir(project_id)

    def set_audio(self, project_id: str, filename: str, bytes_len: int) -> None:
        def _apply(proj: Project) -> None:
            proj.meta["audio"] = {"filename": filename, "size_bytes": bytes_len}
            proj.meta.pop("analysis", None)
            proj.meta.pop("last_plan", None)

        try:
            self.mutate(project_id, _apply)
        except KeyError as exc:
            raise KeyError("Project not found") from exc
