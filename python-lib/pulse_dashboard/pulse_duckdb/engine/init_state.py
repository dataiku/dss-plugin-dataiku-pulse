"""Shared in-process init state for Pulse DuckDB."""

from __future__ import annotations

import threading

_init_state_lock = threading.Lock()
_init_in_progress = False


def set_init_in_progress(value: bool) -> None:
    global _init_in_progress
    with _init_state_lock:
        _init_in_progress = bool(value)


def is_initialization_in_progress() -> bool:
    with _init_state_lock:
        return _init_in_progress
