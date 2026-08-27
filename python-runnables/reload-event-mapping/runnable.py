from __future__ import annotations

import html
import logging
import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Iterator

import dataiku
from dataiku.runnables import Runnable

from data_collection.audit_logs_modules.event_mapping_replay import (
    EventMappingReplayOutcome,
    ReplaySkipError,
    ReplacementUploadResult,
    delete_managed_folder_file,
    parse_event_mapping_source_path,
    plan_event_mapping_replay,
    read_managed_folder_parquet,
    upload_event_mapping_replacements,
)
from data_collection.helper import DSSFolderTarget
from shared_storage_discovery import collect_managed_folder_snapshot
from shared_duckdb.context import build_storage_context
from shared_runtime_logging import suppress_inherited_provider_debug_logging

logger = logging.getLogger(__name__)

SAMPLE_LIMIT = 5
DISCOVERY_PROGRESS_INTERVAL = 10_000
REPLAY_PROGRESS_INTERVAL = 100
REPLAY_BATCH_SIZE = 100
TRACEBACK_SAMPLE_LIMIT = 3
EVENT_MAPPING_PREFIX = "silver/category=event_mapping/"


@dataclass(frozen=True)
class OutcomeSample:
    path: str
    message: str


@dataclass
class OutcomeAccumulator:
    counts: Counter
    grouped_samples: dict[str, list[OutcomeSample]]

    def record(self, outcome: EventMappingReplayOutcome) -> None:
        self.counts[outcome.status] += 1
        samples = self.grouped_samples.setdefault(outcome.status, [])
        if len(samples) < SAMPLE_LIMIT:
            samples.append(OutcomeSample(path=outcome.source_path, message=outcome.message))


@dataclass(frozen=True)
class WorkerResult:
    outcome: EventMappingReplayOutcome
    unexpected_error: Exception | None = None
    log_prefix: str | None = None


@dataclass(frozen=True)
class TracebackState:
    logged: int = 0
    suppressed_logged: bool = False


def _sample_list(items: list[OutcomeSample], *, total: int, limit: int = SAMPLE_LIMIT) -> str:
    if not items:
        return "<em>None</em>"
    rendered = "".join(
        f"<li><code>{html.escape(item.path)}</code>: {html.escape(item.message)}</li>" for item in items[:limit]
    )
    suffix = "" if total <= limit else f"<li><em>... {total - limit} more</em></li>"
    return f"<ul>{rendered}{suffix}</ul>"


def _new_outcome_accumulator() -> OutcomeAccumulator:
    return OutcomeAccumulator(counts=Counter(), grouped_samples={})


def _resolve_parallel_enabled(param_set: dict) -> bool:
    return bool(param_set.get("do_parallel", False))


def _default_worker_count() -> int:
    return min(max((os.cpu_count() or 2) - 1, 1), 4)


def _resolve_worker_count(param_set: dict) -> int:
    if not _resolve_parallel_enabled(param_set):
        return 1
    raw_cores = param_set.get("cores")
    try:
        resolved_cores = int(raw_cores)
    except (TypeError, ValueError):
        return _default_worker_count()
    if resolved_cores <= 0:
        return _default_worker_count()
    return resolved_cores


def _format_outcome_counts(outcomes: OutcomeAccumulator) -> str:
    return (
        f"replaced={outcomes.counts.get('replaced', 0)} "
        f"dropped={outcomes.counts.get('dropped', 0)} "
        f"skipped={outcomes.counts.get('skipped', 0)} "
        f"failed={outcomes.counts.get('failed', 0)} "
        f"partial_write_cleaned={outcomes.counts.get('partial_write_cleaned', 0)} "
        f"partial_write_cleanup_failed={outcomes.counts.get('partial_write_cleanup_failed', 0)} "
        f"delete_failed={outcomes.counts.get('delete_failed', 0)}"
    )


def _should_report_replay_progress(*, completed: int, total: int) -> bool:
    if total <= 0:
        return False
    return completed == 1 or completed == total or completed % REPLAY_PROGRESS_INTERVAL == 0


def _report_replay_progress(progress_callback, *, completed: int, total: int, outcomes: OutcomeAccumulator) -> None:
    if not _should_report_replay_progress(completed=completed, total=total):
        return
    logger.info(
        "Reload event-mapping replay progress: %s/%s completed %s",
        completed,
        total,
        _format_outcome_counts(outcomes),
    )
    progress_callback(completed)


def _record_outcome(accumulator: OutcomeAccumulator, outcome: EventMappingReplayOutcome) -> None:
    accumulator.record(outcome)


def _log_bounded_exception(
    *,
    prefix: str,
    path: str,
    exc: Exception,
    state: TracebackState,
) -> TracebackState:
    if state.logged < TRACEBACK_SAMPLE_LIMIT:
        logger.exception("%s %s", prefix, path)
        return TracebackState(logged=state.logged + 1, suppressed_logged=state.suppressed_logged)
    if not state.suppressed_logged:
        logger.error(
            "%s traceback logging suppressed after %s failures; latest source=%s error=%r",
            prefix,
            TRACEBACK_SAMPLE_LIMIT,
            path,
            exc,
        )
        return TracebackState(logged=state.logged, suppressed_logged=True)
    return state


def _build_summary_html(
    *,
    project_key: str,
    folder_lookup: str,
    outcomes: OutcomeAccumulator,
    discovered: int,
) -> str:
    statuses = [
        ("replaced", "Replaced"),
        ("dropped", "Dropped"),
        ("skipped", "Skipped"),
        ("failed", "Failed"),
        ("partial_write_cleaned", "Upload Failed, Replacement Cleanup Succeeded"),
        ("partial_write_cleanup_failed", "Upload Failed, Replacement Cleanup Failed"),
        ("delete_failed", "Delete Failed After Replacement"),
    ]
    lines = [
        "<h2>Reload Event-Mapping SILVER</h2>",
        f"<p><strong>Project:</strong> <code>{html.escape(project_key)}</code><br/>",
        f"<strong>Folder:</strong> <code>{html.escape(folder_lookup)}</code><br/>",
        f"<strong>Discovered parquet files:</strong> {discovered}</p>",
        "<ul>",
    ]
    for status, label in statuses:
        lines.append(f"<li><strong>{label}:</strong> {outcomes.counts.get(status, 0)}</li>")
    lines.append("</ul>")
    for status, label in statuses:
        lines.append(
            f"<h3>{label}</h3>{_sample_list(outcomes.grouped_samples.get(status, []), total=outcomes.counts.get(status, 0))}"
        )
    return "\n".join(lines)


def _make_local_folder(*, project_key: str, folder_id: str):
    return dataiku.Folder(
        lookup=folder_id,
        project_key=project_key,
        ignore_flow=True,
    )


def _discover_source_snapshot(*, storage_ctx, folder_lookup: str) -> list[str]:
    logger.info(
        "Reload event-mapping discovery started folder=%s provider=%s prefix=%s",
        folder_lookup,
        storage_ctx.connection_type,
        EVENT_MAPPING_PREFIX,
    )
    started_at = time.monotonic()
    source_paths = collect_managed_folder_snapshot(
        storage_ctx,
        relative_prefix=EVENT_MAPPING_PREFIX,
        suffix=".parquet",
        progress_interval=DISCOVERY_PROGRESS_INTERVAL,
        progress_callback=lambda matched: logger.info(
            "Reload event-mapping discovery progress matched=%s prefix=%s elapsed=%.1fs",
            matched,
            EVENT_MAPPING_PREFIX,
            time.monotonic() - started_at,
        ),
    )
    logger.info(
        "Reload event-mapping discovery completed matched=%s prefix=%s elapsed=%.1fs",
        len(source_paths),
        EVENT_MAPPING_PREFIX,
        time.monotonic() - started_at,
    )
    return source_paths


def _process_source_path(*, source_path: str, project_key: str, folder_id: str, target: DSSFolderTarget) -> WorkerResult:
    folder = _make_local_folder(project_key=project_key, folder_id=folder_id)
    try:
        source = parse_event_mapping_source_path(source_path)
        source_df = read_managed_folder_parquet(folder, source_path)
        plans = plan_event_mapping_replay(source=source, source_df=source_df)
        if not plans:
            delete_managed_folder_file(folder, source_path)
            return WorkerResult(
                outcome=EventMappingReplayOutcome(
                    status="dropped",
                    message="All rows were intentionally dropped by current mapping",
                    source_path=source_path,
                )
            )

        upload_result: ReplacementUploadResult = upload_event_mapping_replacements(target=target, folder=folder, plans=plans)
        if upload_result.status == "dq_failed":
            return WorkerResult(
                outcome=EventMappingReplayOutcome(
                    status="failed",
                    message=f"DQ failed: {upload_result.message}",
                    source_path=source_path,
                )
            )

        if upload_result.status == "upload_failed_cleaned":
            return WorkerResult(
                outcome=EventMappingReplayOutcome(
                    status="partial_write_cleaned",
                    message=upload_result.message,
                    source_path=source_path,
                )
            )

        if upload_result.status == "upload_failed_cleanup_failed":
            return WorkerResult(
                outcome=EventMappingReplayOutcome(
                    status="partial_write_cleanup_failed",
                    message=upload_result.message,
                    source_path=source_path,
                )
            )

        try:
            delete_managed_folder_file(folder, source_path)
        except Exception as exc:
            return WorkerResult(
                outcome=EventMappingReplayOutcome(
                    status="delete_failed",
                    message=repr(exc),
                    source_path=source_path,
                ),
                unexpected_error=exc,
                log_prefix="Source delete failed after replacement writes for",
            )

        return WorkerResult(
            outcome=EventMappingReplayOutcome(
                status="replaced",
                message=upload_result.message,
                source_path=source_path,
            )
        )
    except ReplaySkipError as exc:
        return WorkerResult(
            outcome=EventMappingReplayOutcome(status="skipped", message=str(exc), source_path=source_path)
        )
    except Exception as exc:
        return WorkerResult(
            outcome=EventMappingReplayOutcome(status="failed", message=repr(exc), source_path=source_path),
            unexpected_error=exc,
            log_prefix="Reload event-mapping failed for",
        )


def _iter_batches(items: list[str], *, batch_size: int) -> Iterator[list[str]]:
    for start in range(0, len(items), batch_size):
        yield items[start : start + batch_size]


def _yield_worker_results(
    *,
    source_paths: list[str],
    worker_count: int,
    batch_size: int,
    worker_fn: Callable[[str], WorkerResult],
) -> Iterator[WorkerResult]:
    for batch in _iter_batches(source_paths, batch_size=batch_size):
        if worker_count == 1:
            for path in batch:
                yield worker_fn(path)
            continue

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(worker_fn, path) for path in batch]
            for future in as_completed(futures):
                yield future.result()


class MyRunnable(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config or {}
        self.plugin_config = plugin_config or {}
        self.param_set = self.plugin_config.get("pulse_primary", {}) or {}

    def get_progress_target(self):
        return None

    def _resolve_folder_lookup(self) -> str:
        return str(self.param_set.get("pulse_partitioned_data") or "partitioned_data")

    def run(self, progress_callback):
        suppress_inherited_provider_debug_logging()

        folder_lookup = self._resolve_folder_lookup()
        storage_ctx = build_storage_context(project_key=self.project_key, folder_lookup=folder_lookup)
        target = DSSFolderTarget(project_key=self.project_key, folder_lookup=storage_ctx.folder_lookup)
        source_paths = _discover_source_snapshot(storage_ctx=storage_ctx, folder_lookup=folder_lookup)

        worker_count = _resolve_worker_count(self.param_set)
        parallel_enabled = worker_count > 1
        logger.info(
            "Reload event-mapping replay started total=%s parallel=%s workers=%s batch_size=%s",
            len(source_paths),
            parallel_enabled,
            worker_count,
            REPLAY_BATCH_SIZE,
        )

        outcomes = _new_outcome_accumulator()
        traceback_state = TracebackState()
        replay_started_at = time.monotonic()

        worker_fn = lambda source_path: _process_source_path(
            source_path=source_path,
            project_key=self.project_key,
            folder_id=storage_ctx.folder_id,
            target=target,
        )

        completed = 0
        for result in _yield_worker_results(
            source_paths=source_paths,
            worker_count=worker_count,
            batch_size=REPLAY_BATCH_SIZE,
            worker_fn=worker_fn,
        ):
            completed += 1
            _record_outcome(outcomes, result.outcome)
            if result.unexpected_error is not None and result.log_prefix is not None:
                traceback_state = _log_bounded_exception(
                    prefix=result.log_prefix,
                    path=result.outcome.source_path,
                    exc=result.unexpected_error,
                    state=traceback_state,
                )
            _report_replay_progress(progress_callback, completed=completed, total=len(source_paths), outcomes=outcomes)

        logger.info(
            "Reload event-mapping completed discovered=%s completed=%s elapsed=%.1fs %s",
            len(source_paths),
            completed,
            time.monotonic() - replay_started_at,
            _format_outcome_counts(outcomes),
        )

        return _build_summary_html(
            project_key=target.project_key,
            folder_lookup=target.folder_lookup,
            outcomes=outcomes,
            discovered=len(source_paths),
        )
