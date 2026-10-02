from __future__ import annotations

import fcntl
import os
import threading
import time

import pytest

from pulse_dashboard import settings
from pulse_dashboard.pulse_duckdb.engine.init_db import _duckdb_init_lock


def _configure_lock(monkeypatch, tmp_path, *, timeout_sec: float = 1.0, stale_sec: float = 0.05):
    lock_path = tmp_path / ".duckdb_init.lock"
    db_path = tmp_path / "pulse.duckdb"
    metadata_path = tmp_path / "pulse.duckdb.meta.json"
    monkeypatch.setattr(settings, "PULSE_DUCKDB_INIT_LOCK_PATH", str(lock_path), raising=False)
    monkeypatch.setattr(settings, "PULSE_DUCKDB_INIT_TIMEOUT_SEC", timeout_sec, raising=False)
    monkeypatch.setattr(settings, "PULSE_DUCKDB_INIT_LOCK_STALE_SEC", stale_sec, raising=False)
    monkeypatch.setattr(settings, "DUCKDB_PATH", db_path, raising=False)
    monkeypatch.setattr(settings, "DUCKDB_METADATA_PATH", metadata_path, raising=False)
    return lock_path


def test_old_unlocked_lock_path_acquires_immediately(monkeypatch, tmp_path):
    lock_path = _configure_lock(monkeypatch, tmp_path)
    lock_path.write_text("old", encoding="utf-8")
    old_time = time.time() - 3600
    os.utime(lock_path, (old_time, old_time))

    start = time.monotonic()
    with _duckdb_init_lock():
        elapsed = time.monotonic() - start

    assert elapsed < 0.5
    assert lock_path.exists()


def test_live_old_lock_is_not_replaced_and_waiter_enters_after_release(monkeypatch, tmp_path):
    lock_path = _configure_lock(monkeypatch, tmp_path, timeout_sec=2.0, stale_sec=0.01)
    lock_path.write_text("held", encoding="utf-8")
    old_time = time.time() - 3600
    os.utime(lock_path, (old_time, old_time))
    original_stat = lock_path.stat()
    entered = []
    acquired = threading.Event()
    release_holder = threading.Event()

    def holder():
        with open(lock_path, "a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            acquired.set()
            release_holder.wait(timeout=2.0)
            fcntl.flock(handle, fcntl.LOCK_UN)

    def waiter():
        acquired.wait(timeout=1.0)
        with _duckdb_init_lock():
            entered.append(time.monotonic())

    holder_thread = threading.Thread(target=holder)
    waiter_thread = threading.Thread(target=waiter)
    holder_thread.start()
    assert acquired.wait(timeout=1.0)
    waiter_thread.start()
    time.sleep(0.2)

    assert entered == []
    assert lock_path.exists()
    assert lock_path.stat().st_ino == original_stat.st_ino

    release_holder.set()
    holder_thread.join(timeout=2.0)
    waiter_thread.join(timeout=2.0)

    assert len(entered) == 1
    assert not holder_thread.is_alive()
    assert not waiter_thread.is_alive()


def test_live_lock_timeout_does_not_delete_or_enter(monkeypatch, tmp_path):
    lock_path = _configure_lock(monkeypatch, tmp_path, timeout_sec=0.2, stale_sec=0.01)
    lock_path.write_text("held", encoding="utf-8")
    old_time = time.time() - 3600
    os.utime(lock_path, (old_time, old_time))
    original_inode = lock_path.stat().st_ino

    with open(lock_path, "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        with pytest.raises(TimeoutError, match="Timed out waiting for init lock"):
            with _duckdb_init_lock():
                pytest.fail("contended init lock must not enter critical section")
        fcntl.flock(handle, fcntl.LOCK_UN)

    assert lock_path.exists()
    assert lock_path.stat().st_ino == original_inode


def test_lock_released_after_exception(monkeypatch, tmp_path):
    lock_path = _configure_lock(monkeypatch, tmp_path, timeout_sec=1.0)

    with pytest.raises(RuntimeError, match="boom"):
        with _duckdb_init_lock():
            raise RuntimeError("boom")

    with _duckdb_init_lock():
        assert lock_path.exists()
