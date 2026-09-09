"""Cross-process admission for queued local model workers.

Model workers run in separate processes so a large Director or video load does
not block the API.  A process-local lock alone is therefore insufficient: two
workers can still load competing models at the same time and exhaust VRAM or
system commit. The dispatcher uses one lock root for all local model job types,
independent of the individual models' directories.

The parent holds the lock until its child exits, including cancellation cleanup.
Model adapters in that child must not acquire the parent's lock again. This is
worker admission, not a claim of hardware qualification or control over external
applications that also use the GPU.
"""

from __future__ import annotations

import errno
import math
import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from weakref import WeakValueDictionary


class ModelLoadCanceled(RuntimeError):
    """Raised when a waiting model load is canceled before it acquires the lock."""


class ModelLoadTimeout(RuntimeError):
    """Raised when a model load cannot acquire the shared lock in time."""


_THREAD_LOCKS: WeakValueDictionary[str, threading.Lock] = WeakValueDictionary()
_THREAD_LOCKS_GUARD = threading.Lock()


def _thread_lock_for(path: Path) -> threading.Lock:
    key = os.path.normcase(str(path))
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _THREAD_LOCKS[key] = lock
        return lock


def _try_file_lock(fd: int) -> bool:
    """Try to acquire byte zero of ``fd`` without blocking."""

    os.lseek(fd, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                return False
            raise

    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            return False
        raise


def _unlock_file(fd: int) -> None:
    os.lseek(fd, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_UN)


def _deadline(timeout_s: float | None) -> float | None:
    if timeout_s is None:
        return None
    timeout = float(timeout_s)
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError("Model worker timeout must be finite and nonnegative")
    return time.monotonic() + timeout


@contextmanager
def model_load_lock(
    lock_root: Path,
    *,
    timeout_s: float | None = None,
    poll_s: float = 0.1,
    cancel_check: Callable[[], bool] | None = None,
    on_wait: Callable[[], None] | None = None,
) -> Iterator[Path]:
    """Acquire admission for one local model worker; waiting is cancelable.

    ``lock_root`` is supplied by the dispatcher, never inferred from a snapshot.
    The lock file is intentionally kept on
    disk; the OS releases the advisory lock when a worker exits, so a crashed
    process cannot leave a stale lock marker that blocks future work.
    """

    root = Path(lock_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".edmg-model-worker.lock"
    thread_lock = _thread_lock_for(lock_path)
    interval = float(poll_s)
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError("Model worker poll interval must be finite and positive")
    deadline = _deadline(timeout_s)
    thread_acquired = False
    fd: int | None = None
    file_acquired = False
    notified = False

    def check_canceled() -> None:
        if cancel_check is not None and cancel_check():
            raise ModelLoadCanceled("Local model worker canceled before admission")

    def wait() -> None:
        nonlocal notified
        check_canceled()
        if not notified and on_wait is not None:
            notified = True
            on_wait()
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            raise ModelLoadTimeout("Timed out waiting for the internal model worker")
        time.sleep(interval if remaining is None else min(interval, remaining))

    try:
        while not thread_acquired:
            check_canceled()
            thread_acquired = thread_lock.acquire(blocking=False)
            if not thread_acquired:
                wait()

        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
        # Windows permits locking beyond EOF. Do not write the sentinel byte:
        # another process may already own that byte between open and write.
        while not file_acquired:
            check_canceled()
            file_acquired = _try_file_lock(fd)
            if file_acquired:
                break
            wait()

        check_canceled()
        yield lock_path
    finally:
        try:
            if file_acquired and fd is not None:
                _unlock_file(fd)
        finally:
            try:
                if fd is not None:
                    os.close(fd)
            finally:
                if thread_acquired:
                    thread_lock.release()
