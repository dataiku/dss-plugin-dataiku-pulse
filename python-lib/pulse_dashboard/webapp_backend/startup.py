from __future__ import annotations

import json
import logging
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, cast

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
    "freshnessSource": None,
    "freshnessTimestamp": None,
    "freshnessAgeSec": None,
    "freshnessToleranceSec": None,
    "rebuildOnStartupStale": None,
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
    from pulse_dashboard.pulse_duckdb.engine.init_db import _duckdb_init_lock, read_duckdb_metadata  # type: ignore
except Exception:
    try:
        repo_root = Path(__file__).resolve().parents[2]
        python_lib = repo_root / "python-lib"
        if python_lib.is_dir():
            sys.path.insert(0, str(python_lib))

        from pulse_dashboard import settings as pulse_settings  # type: ignore
        from pulse_dashboard.pulse_duckdb.engine import ReadOnlySQLError, create_connection, ensure_database_ready, is_initialization_in_progress, query_df  # type: ignore
        from pulse_dashboard.pulse_duckdb.engine.init_db import _duckdb_init_lock, read_duckdb_metadata  # type: ignore
    except Exception:
        logger.exception("Failed to import Pulse dashboard libraries")
        pulse_settings = None
        create_connection = None
        ensure_database_ready = None
        is_initialization_in_progress = None
        query_df = None
        read_duckdb_metadata = None
        _duckdb_init_lock = None
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


def _parse_iso8601_utc(value: Any) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).timestamp()


def _format_utc_timestamp(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


@contextmanager
def _startup_duckdb_init_lock() -> Iterator[None]:
    if _duckdb_init_lock is None:
        yield
        return

    with _duckdb_init_lock():
        yield


def _duckdb_init_in_progress() -> bool:
    if is_initialization_in_progress is None:
        return False
    try:
        return bool(is_initialization_in_progress())
    except Exception:
        return False

def _evaluate_startup_duckdb_state(duckdb_path: Path) -> dict[str, Any]:
    metadata_path = Path(getattr(pulse_settings, "DUCKDB_METADATA_PATH", f"{duckdb_path}.meta.json"))
    metadata = _safe_duckdb_metadata()
    exists = duckdb_path.exists()
    db_mtime = duckdb_path.stat().st_mtime if exists else None
    tolerance_sec = float(getattr(pulse_settings, "PULSE_DUCKDB_STARTUP_STALE_TOLERANCE_SEC", 86400.0) or 0.0)
    now = time.time()

    stale = False
    stale_reason = "missing"
    freshness_source = None
    freshness_timestamp = None
    freshness_age_sec = None
    if exists:
        stale_reason = None
        rebuild_on_restart = bool(getattr(pulse_settings, "PULSE_DUCKDB_REBUILD_ON_STARTUP_STALE", True))
        last_rebuild_at = _parse_iso8601_utc(metadata.get("lastRebuildAt"))
        if last_rebuild_at is not None:
            freshness_source = "metadata_lastRebuildAt"
            freshness_timestamp = last_rebuild_at
        elif db_mtime is not None:
            freshness_source = "mtime"
            freshness_timestamp = db_mtime
            stale_reason = "metadata_lastRebuildAt_unavailable"

        if freshness_timestamp is not None:
            freshness_age_sec = max(0.0, now - freshness_timestamp)

        if freshness_age_sec is not None and freshness_age_sec > tolerance_sec:
            stale = True
            if freshness_source == "metadata_lastRebuildAt":
                stale_reason = "lastRebuildAt_older_than_tolerance"
            elif stale_reason == "metadata_lastRebuildAt_unavailable":
                stale_reason = "metadata_lastRebuildAt_unavailable_mtime_older_than_tolerance"
            else:
                stale_reason = "mtime_older_than_tolerance"
            if not rebuild_on_restart:
                stale_reason = f"{stale_reason}_rebuild_disabled"

    return {
        "exists": exists,
        "dbMtime": db_mtime,
        "freshnessSource": freshness_source,
        "freshnessTimestamp": _format_utc_timestamp(freshness_timestamp),
        "freshnessAgeSec": round(freshness_age_sec, 3) if freshness_age_sec is not None else None,
        "freshnessToleranceSec": tolerance_sec,
        "rebuildOnStartupStale": bool(getattr(pulse_settings, "PULSE_DUCKDB_REBUILD_ON_STARTUP_STALE", True)),
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


def _resolve_startup_duckdb_location() -> Path:
    project_key, duckdb_path, metadata_path = pulse_settings.resolve_dashboard_duckdb_location()
    pulse_settings.PULSE_SOURCE_PROJECT_KEY = project_key
    pulse_settings.DUCKDB_PATH = duckdb_path
    pulse_settings.DUCKDB_METADATA_PATH = metadata_path
    logger.info(
        "Pulse webapp startup: resolved source_project=%s duckdb_path=%s metadata_path=%s",
        project_key,
        duckdb_path,
        metadata_path,
    )
    return duckdb_path


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

    duckdb_path = _resolve_startup_duckdb_location()
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
    with _startup_duckdb_init_lock():
        _startup_init_status["dbPath"] = str(duckdb_path)
        startup_db_state = _evaluate_startup_duckdb_state(duckdb_path)
        _startup_init_status.update(
            {
                "metadataPath": startup_db_state.get("metadataPath"),
                "dbMtime": startup_db_state.get("dbMtime"),
                "freshnessSource": startup_db_state.get("freshnessSource"),
                "freshnessTimestamp": startup_db_state.get("freshnessTimestamp"),
                "freshnessAgeSec": startup_db_state.get("freshnessAgeSec"),
                "freshnessToleranceSec": startup_db_state.get("freshnessToleranceSec"),
                "rebuildOnStartupStale": startup_db_state.get("rebuildOnStartupStale"),
                "startupCheckPerformed": True,
                "stale": bool(startup_db_state.get("stale", False)),
                "staleReason": startup_db_state.get("staleReason"),
            }
        )
        logger.info(
            "Pulse webapp startup: freshness checked exists=%s stale=%s source=%s timestamp=%s reason=%s",
            startup_db_state.get("exists"),
            startup_db_state.get("stale"),
            startup_db_state.get("freshnessSource"),
            startup_db_state.get("freshnessTimestamp"),
            startup_db_state.get("staleReason"),
        )

        if _startup_check_completed:
            return

        if startup_db_state.get("exists") and (
            not startup_db_state.get("stale") or not startup_db_state.get("rebuildOnStartupStale")
        ):
            _startup_init_status.update(
                {
                    "state": "ready",
                    "phase": "frontend_ready",
                    "message": "DuckDB file retained for startup",
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

    logger.info("Pulse webapp startup: scheduling DuckDB initialization for %s", duckdb_path)
    thread = threading.Thread(target=_run_startup_duckdb_init, name="pulse-duckdb-startup-init", daemon=True)
    thread.start()


def initialize_startup_ownership() -> None:
    if pulse_settings is not None:
        setattr(pulse_settings, "PULSE_INIT_STATUS_CALLBACK", _update_startup_init_phase)
        setattr(pulse_settings, "PULSE_BACKEND_STARTED_AT", _backend_started_at)


def run_backend_startup_check() -> None:
    if pulse_settings is not None:
        logger.info(
            "Pulse backend startup: auto_init=%s duckdb_path=%s metadata_path=%s lock_path=%s",
            getattr(pulse_settings, "PULSE_AUTO_INIT_DUCKDB", False),
            getattr(pulse_settings, "DUCKDB_PATH", None),
            getattr(pulse_settings, "DUCKDB_METADATA_PATH", None),
            getattr(pulse_settings, "PULSE_DUCKDB_INIT_LOCK_PATH", None),
        )
    _maybe_schedule_startup_duckdb_init()


def run_initial_local_startup() -> None:
    run_backend_startup_check()


def duckdb_init_in_progress() -> bool:
    return _duckdb_init_in_progress()
