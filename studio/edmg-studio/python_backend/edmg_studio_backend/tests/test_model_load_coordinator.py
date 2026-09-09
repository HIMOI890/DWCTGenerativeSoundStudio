from __future__ import annotations

import multiprocessing
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from edmg_studio_backend.services import model_load_coordinator as coordinator
from edmg_studio_backend.services.model_load_coordinator import (
    ModelLoadCanceled,
    ModelLoadTimeout,
    model_load_lock,
)


def _hold_worker_slot(root, entered, release, waiting):
    with model_load_lock(Path(root), on_wait=waiting.set):
        entered.set()
        if not release.wait(15):
            raise RuntimeError("Test did not release model worker")


@pytest.mark.parametrize("crash", [False, True])
def test_processes_share_admission_and_recover_after_exit(tmp_path, crash):
    context = multiprocessing.get_context("spawn")
    entered = [context.Event(), context.Event()]
    release = [context.Event(), context.Event()]
    waiting = [context.Event(), context.Event()]
    workers = [
        context.Process(target=_hold_worker_slot, args=(str(tmp_path), entered[i], release[i], waiting[i]))
        for i in range(2)
    ]
    try:
        workers[0].start()
        assert entered[0].wait(10)
        workers[1].start()
        assert waiting[1].wait(10)
        assert not entered[1].is_set()
        if crash:
            workers[0].terminate()
        else:
            release[0].set()
        workers[0].join(10)
        assert not workers[0].is_alive()
        assert entered[1].wait(10)
        release[1].set()
        workers[1].join(10)
        assert workers[1].exitcode == 0
    finally:
        for index, worker in enumerate(workers):
            if worker.pid is not None:
                if worker.is_alive():
                    release[index].set()
                    worker.join(5)
                    if worker.is_alive():
                        worker.terminate()
                worker.join(10)
                worker.close()


def test_waiting_thread_cancels_without_entering_or_releasing_owner(tmp_path):
    canceled = threading.Event()
    waiting = threading.Event()

    def contender():
        with model_load_lock(tmp_path, cancel_check=canceled.is_set, on_wait=waiting.set):
            pytest.fail("A canceled waiter must never enter the model worker")

    with ThreadPoolExecutor(max_workers=1) as executor:
        with model_load_lock(tmp_path):
            future = executor.submit(contender)
            assert waiting.wait(5)
            canceled.set()
            with pytest.raises(ModelLoadCanceled):
                future.result(timeout=5)
            with pytest.raises(ModelLoadTimeout):
                with model_load_lock(tmp_path, timeout_s=0):
                    pytest.fail("The original owner must still hold admission")
    with model_load_lock(tmp_path, timeout_s=0):
        pass


def test_timeout_releases_thread_lock_after_file_contention(tmp_path, monkeypatch):
    attempts = 0
    real_try = coordinator._try_file_lock

    def occupied(_fd):
        nonlocal attempts
        attempts += 1
        return False

    monkeypatch.setattr(coordinator, "_try_file_lock", occupied)
    with pytest.raises(ModelLoadTimeout):
        with model_load_lock(tmp_path, timeout_s=0.02, poll_s=0.005):
            pytest.fail("Should time out")
    assert attempts >= 1
    monkeypatch.setattr(coordinator, "_try_file_lock", real_try)
    with model_load_lock(tmp_path, timeout_s=0):
        pass


def test_unlock_failure_still_closes_descriptor_and_releases_thread_lock(tmp_path, monkeypatch):
    real_unlock = coordinator._unlock_file

    def broken_unlock(_fd):
        raise OSError("test unlock failure")

    monkeypatch.setattr(coordinator, "_unlock_file", broken_unlock)
    with pytest.raises(OSError, match="unlock failure"):
        with model_load_lock(tmp_path):
            pass
    monkeypatch.setattr(coordinator, "_unlock_file", real_unlock)
    with model_load_lock(tmp_path, timeout_s=0):
        pass


def test_cancel_before_admission_does_not_enter_and_allows_next_worker(tmp_path):
    with pytest.raises(ModelLoadCanceled):
        with model_load_lock(tmp_path, cancel_check=lambda: True):
            pytest.fail("Canceled before admission")
    with model_load_lock(tmp_path, timeout_s=0):
        raise_expected = True
    assert raise_expected


@pytest.mark.parametrize("kwargs", [{"timeout_s": -1}, {"timeout_s": float("nan")}, {"poll_s": 0}])
def test_invalid_wait_policy_is_rejected(tmp_path, kwargs):
    with pytest.raises(ValueError):
        with model_load_lock(tmp_path, **kwargs):
            pytest.fail("Invalid wait policy")
