from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Any

from ..store.jobs import JobStore, Job

@dataclass
class WorkerStatus:
    running: bool
    concurrency: int
    inflight: int
    last_error: str | None = None

class WorkerManager:
    """Always-on background worker loop with concurrency control.

    This runs inside the backend process and continuously pulls queued jobs.
    """

    def __init__(
        self,
        jobs: JobStore,
        run_job: Callable[[Job], None],
        concurrency: int = 1,
        poll_interval_s: float = 0.5,
    ):
        self.jobs = jobs
        self._run_job = run_job
        self._concurrency = max(1, int(concurrency))
        self._poll = float(poll_interval_s)

        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._inflight = 0
        self._lock = threading.Lock()
        self._last_error: str | None = None

    def start(self) -> None:
        if self._threads:
            return
        self._stop.clear()
        for i in range(self._concurrency):
            t = threading.Thread(target=self._loop, name=f"edmg-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        for t in list(self._threads):
            t.join(timeout=2.0)
        self._threads.clear()

    def status(self) -> WorkerStatus:
        with self._lock:
            inflight = self._inflight
            last_error = self._last_error
        return WorkerStatus(running=bool(self._threads) and not self._stop.is_set(), concurrency=self._concurrency, inflight=inflight, last_error=last_error)

    def _bump_inflight(self, delta: int) -> None:
        with self._lock:
            self._inflight = max(0, self._inflight + delta)

    def _set_error(self, msg: str) -> None:
        with self._lock:
            self._last_error = msg

    def _loop(self) -> None:
        while not self._stop.is_set():
            job = self.jobs.claim_next_queued()
            if not job:
                time.sleep(self._poll)
                continue

            self._bump_inflight(+1)
            try:
                self._run_job(job)
            except Exception as e:
                self._set_error(str(e))
            finally:
                self._bump_inflight(-1)
