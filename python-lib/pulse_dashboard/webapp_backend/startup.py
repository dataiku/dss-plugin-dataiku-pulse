from __future__ import annotations

import json
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger(__name__)
if not logger.handlers:
    gunicorn_error_logger = logging.getLogger("gunicorn.error")
    if gunicorn_error_logger.handlers:
        logger.handlers = gunicorn_error_logger.handlers
        logger.setLevel(gunicorn_error_logger.level)
        logger.propagate = False

_startup_init_lock = threading.Lock()
_startup_init_started = False
_startup_check_completed = False
_backend_started_at = time.time()
_startup_init_status: dict[str, Any] = {
    "state": "idle",
    "normalizedState": "NOT_STARTED",
    "retryAllowed": True,
    "message": "Waiting to check DuckDB startup state",
    "phase": "idle",
    "startedAt": None,
    "finishedAt": None,
    "durationSec": None,
    "backendStartedAt": _backend_started_at,
    "dbPath": None,
    "metadataPath": None,
    "dbMtime": None,
    "currentFileNumber": None,
    "totalFileCount": None,
    "startupCheckPerformed": False,
    "stale": False,
    "staleReason": None,
    "rebuildTriggeredBy": None,
    "report": None,
    "error": None,
}


def _is_backend_local_timeout_error(exc: BaseException) -> bool:
    message = str(exc or "")
    lowered = message.lower()
    return any(
        token in lowered
        for token in (
            "timed out",
            "timeout",
            "read timed out",
            "bad gateway",
            "502",
        )
    )


def _update_startup_init_message(message: str) -> None:
    _startup_init_status["message"] = str(message)


def _update_startup_init_phase(phase: str, message: str) -> None:
    _startup_init_status["phase"] = str(phase)
    _startup_init_status["message"] = str(message)


def _normalized_startup_state(state: str | None) -> str:
    value = str(state or "").strip().lower()
    if value == "ready":
        return "READY"
    if value in {"running", "initializing"}:
        return "INITIALIZING"
    if value in {"failed", "unavailable"}:
        return "FAILED"
    return "NOT_STARTED"


def _refresh_startup_status_metadata() -> None:
    normalized_state = _normalized_startup_state(_startup_init_status.get("state"))
    _startup_init_status["normalizedState"] = normalized_state
    _startup_init_status["retryAllowed"] = normalized_state in {"NOT_STARTED", "FAILED"}

    report = _startup_init_status.get("report")
    if isinstance(report, dict):
        total = report.get("total")
        if total is None:
            loaded = report.get("loaded")
            failed = report.get("failed")
            if isinstance(loaded, list) or isinstance(failed, list):
                total = len(loaded or []) + len(failed or [])
        loaded_count = report.get("loadedCount")
        if loaded_count is None:
            loaded = report.get("loaded")
            if isinstance(loaded, list):
                loaded_count = len(loaded)
        _startup_init_status["totalFileCount"] = int(total) if isinstance(total, int) else None
        _startup_init_status["currentFileNumber"] = int(loaded_count) if isinstance(loaded_count, int) else None
    else:
        _startup_init_status["totalFileCount"] = None
        _startup_init_status["currentFileNumber"] = None


try:
    from pulse_dashboard import settings as pulse_settings  # type: ignore
    from pulse_dashboard.pulse_duckdb.engine import ReadOnlySQLError, create_connection, ensure_database_ready, is_initialization_in_progress, query_df  # type: ignore
    from pulse_dashboard.pulse_duckdb.engine.init_db import ensure_consumption_product_views, read_duckdb_metadata  # type: ignore
except Exception:
    try:
        repo_root = Path(__file__).resolve().parents[2]
        python_lib = repo_root / "python-lib"
        if python_lib.is_dir():
            sys.path.insert(0, str(python_lib))

        from pulse_dashboard import settings as pulse_settings  # type: ignore
        from pulse_dashboard.pulse_duckdb.engine import ReadOnlySQLError, create_connection, ensure_database_ready, is_initialization_in_progress, query_df  # type: ignore
        from pulse_dashboard.pulse_duckdb.engine.init_db import ensure_consumption_product_views, read_duckdb_metadata  # type: ignore
    except Exception:
        logger.exception("Failed to import Pulse dashboard libraries")
        pulse_settings = None
        create_connection = None
        ensure_database_ready = None
        is_initialization_in_progress = None
        query_df = None
        read_duckdb_metadata = None
        ReadOnlySQLError = None  # type: ignore[assignment,misc]

if pulse_settings is not None:
    setattr(pulse_settings, "PULSE_INIT_STATUS_CALLBACK", _update_startup_init_phase)
    setattr(pulse_settings, "PULSE_BACKEND_STARTED_AT", _backend_started_at)

def _run_startup_duckdb_init() -> None:
    global _startup_check_completed

    if ensure_database_ready is None:
        _startup_init_status.update(
            {
                "state": "unavailable",
                "phase": "unavailable",
                "message": "DuckDB engine unavailable",
                "finishedAt": time.time(),
                "error": "DuckDB engine unavailable",
            }
        )
        _refresh_startup_status_metadata()
        logger.warning("Pulse webapp startup init skipped: DuckDB engine unavailable")
        _startup_check_completed = True
        return

    try:
        started_at = time.time()
        _startup_init_status.update(
            {
                "state": "running",
                "phase": "bootstrap",
                "message": "Initializing DuckDB and loading GOLD tables",
                "startedAt": started_at,
                "finishedAt": None,
                "durationSec": None,
                "error": None,
                "report": None,
            }
        )
        _refresh_startup_status_metadata()
        logger.info("Pulse webapp startup: initializing DuckDB in background")
        report = cast(
            dict[str, Any],
            ensure_database_ready(
                load_gold_tables=True,
                replace_gold_tables=getattr(pulse_settings, "PULSE_AUTO_LOAD_REPLACE", False)
                if pulse_settings is not None
                else False,
            ),
        )
        finished_at = time.time()
        duration_sec = round(finished_at - started_at, 3)
        if bool(report.get("ok", False)):
            _startup_init_status.update(
                {
                    "state": "ready",
                    "phase": "frontend_ready",
                    "message": "DuckDB initialization complete",
                    "finishedAt": finished_at,
                    "durationSec": duration_sec,
                    "report": report,
                }
            )
            _refresh_startup_status_metadata()
            logger.info("Pulse webapp startup: DuckDB initialization finished in %ss", duration_sec)
        else:
            _startup_init_status.update(
                {
                    "state": "failed",
                    "phase": "failed",
                    "message": "DuckDB initialization reported a failure",
                    "finishedAt": finished_at,
                    "durationSec": duration_sec,
                    "report": report,
                    "error": json.dumps(report),
                }
            )
            _refresh_startup_status_metadata()
            logger.warning(
                "Pulse webapp startup: DuckDB initialization reported failure after %ss: %s",
                duration_sec,
                report,
            )
        _startup_check_completed = True
    except Exception:
        finished_at = time.time()
        duration_sec = None
        if _startup_init_status.get("startedAt") is not None:
            try:
                duration_sec = round(finished_at - float(_startup_init_status["startedAt"]), 3)
            except Exception:
                duration_sec = None
        _startup_init_status.update(
            {
                "state": "failed",
                "phase": "failed",
                "message": "DuckDB initialization failed",
                "finishedAt": finished_at,
                "durationSec": duration_sec,
                "error": "DuckDB initialization failed. Check backend logs.",
            }
        )
        _refresh_startup_status_metadata()
        logger.exception("Pulse webapp startup: DuckDB initialization failed")
        _startup_check_completed = True

def _safe_duckdb_metadata() -> dict[str, Any]:
    if read_duckdb_metadata is None:
        return {}
    try:
        payload = cast(dict[str, Any], read_duckdb_metadata())
    except Exception:
        logger.warning("Pulse webapp startup: failed reading DuckDB metadata", exc_info=True)
        return {}
    return payload if isinstance(payload, dict) else {}

def _evaluate_startup_duckdb_state(duckdb_path: Path) -> dict[str, Any]:
    metadata_path = Path(getattr(pulse_settings, "DUCKDB_METADATA_PATH", f"{duckdb_path}.meta.json"))
    metadata = _safe_duckdb_metadata()
    exists = duckdb_path.exists()
    db_mtime = duckdb_path.stat().st_mtime if exists else None
    tolerance_sec = float(getattr(pulse_settings, "PULSE_DUCKDB_STARTUP_STALE_TOLERANCE_SEC", 5.0) or 0.0)

    stale = False
    stale_reason = "missing"
    if exists:
        stale_reason = None
        rebuild_on_restart = bool(getattr(pulse_settings, "PULSE_DUCKDB_REBUILD_ON_STARTUP_STALE", True))
        if rebuild_on_restart and db_mtime is not None and db_mtime < (_backend_started_at - tolerance_sec):
            stale = True
            stale_reason = "older_than_backend_start"

    return {
        "exists": exists,
        "dbMtime": db_mtime,
        "metadataPath": str(metadata_path),
        "metadata": metadata,
        "stale": stale if exists else True,
        "staleReason": stale_reason,
    }

def _delete_stale_duckdb(duckdb_path: Path, metadata_path: Path) -> None:
    if duckdb_path.exists():
        duckdb_path.unlink()
    if metadata_path.exists():
        metadata_path.unlink()

def _maybe_schedule_startup_duckdb_init() -> None:
    global _startup_check_completed, _startup_init_started

    if pulse_settings is None or ensure_database_ready is None:
        _startup_init_status.update(
            {
                "state": "unavailable",
                "phase": "unavailable",
                "message": "DuckDB settings unavailable",
                "error": "DuckDB settings unavailable",
            }
        )
        _refresh_startup_status_metadata()
        return

    duckdb_path = Path(getattr(pulse_settings, "DUCKDB_PATH", "") or "")
    if not duckdb_path:
        _startup_init_status.update(
            {
                "state": "unavailable",
                "phase": "unavailable",
                "message": "DuckDB path is not configured",
                "error": "DuckDB path is not configured",
            }
        )
        return
    _startup_init_status["dbPath"] = str(duckdb_path)
    startup_db_state = _evaluate_startup_duckdb_state(duckdb_path)
    _startup_init_status.update(
        {
            "metadataPath": startup_db_state.get("metadataPath"),
            "dbMtime": startup_db_state.get("dbMtime"),
            "startupCheckPerformed": True,
            "stale": bool(startup_db_state.get("stale", False)),
            "staleReason": startup_db_state.get("staleReason"),
        }
    )

    if _startup_check_completed:
        return

    if startup_db_state.get("exists") and not startup_db_state.get("stale"):
        _startup_init_status.update(
            {
                "state": "ready",
                "phase": "frontend_ready",
                "message": "DuckDB file already present and fresh for this backend",
                "finishedAt": time.time(),
                "durationSec": 0.0,
                "error": None,
            }
        )
        _refresh_startup_status_metadata()
        _startup_check_completed = True
        return

    if startup_db_state.get("exists") and startup_db_state.get("stale"):
        try:
            _delete_stale_duckdb(duckdb_path, Path(str(startup_db_state.get("metadataPath") or f"{duckdb_path}.meta.json")))
            _startup_init_status["rebuildTriggeredBy"] = "startup_stale"
            logger.info(
                "Pulse webapp startup: deleted stale DuckDB at %s because %s",
                duckdb_path,
                startup_db_state.get("staleReason"),
            )
        except Exception as exc:
            _startup_init_status.update(
                {
                    "state": "failed",
                    "phase": "failed",
                    "message": "Failed deleting stale DuckDB before startup rebuild",
                    "finishedAt": time.time(),
                    "error": str(exc),
                    "stale": True,
                    "staleReason": "delete_failed",
                }
            )
            _startup_check_completed = True
            logger.exception("Pulse webapp startup: failed deleting stale DuckDB at %s", duckdb_path)
            return
    else:
        _startup_init_status["rebuildTriggeredBy"] = "missing"

    with _startup_init_lock:
        if _startup_init_started:
            return
        _startup_init_started = True

    logger.info("Pulse webapp startup: DuckDB missing at %s; initializing now", duckdb_path)
    thread = threading.Thread(target=_run_startup_duckdb_init, name="pulse-duckdb-startup-init", daemon=True)
    thread.start()


def initialize_startup_ownership() -> None:
    if pulse_settings is not None:
        setattr(pulse_settings, "PULSE_INIT_STATUS_CALLBACK", _update_startup_init_phase)
        setattr(pulse_settings, "PULSE_BACKEND_STARTED_AT", _backend_started_at)


def run_initial_local_startup() -> None:
    if pulse_settings is not None:
        logger.info(
            "Pulse local backend startup: auto_init=%s duckdb_path=%s metadata_path=%s lock_path=%s",
            getattr(pulse_settings, "PULSE_AUTO_INIT_DUCKDB", False),
            getattr(pulse_settings, "DUCKDB_PATH", None),
            getattr(pulse_settings, "DUCKDB_METADATA_PATH", None),
            getattr(pulse_settings, "PULSE_DUCKDB_INIT_LOCK_PATH", None),
        )
    _maybe_schedule_startup_duckdb_init()
