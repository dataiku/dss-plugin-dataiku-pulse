from __future__ import annotations

import html
import logging
from collections import Counter
from dataclasses import dataclass

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
from shared_storage_discovery import iter_managed_folder_paths
from shared_duckdb.context import build_storage_context

logger = logging.getLogger(__name__)

SAMPLE_LIMIT = 5
PROGRESS_INTERVAL = 1000
TRACEBACK_SAMPLE_LIMIT = 3
NOISY_DEBUG_LOGGERS = ("botocore", "urllib3")


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


def _configure_runtime_logging() -> None:
    for logger_name in NOISY_DEBUG_LOGGERS:
        noisy_logger = logging.getLogger(logger_name)
        if noisy_logger.getEffectiveLevel() <= logging.DEBUG:
            noisy_logger.setLevel(logging.WARNING)


def _should_report_progress(*, processed: int, total: int) -> bool:
    if total <= 0:
        return False
    return processed == 1 or processed == total or processed % PROGRESS_INTERVAL == 0


def _report_progress(progress_callback, *, processed: int, total: int) -> None:
    if not _should_report_progress(processed=processed, total=total):
        return
    logger.info("Reload event-mapping progress: %s/%s processed", processed, total)
    progress_callback(processed)


def _record_outcome(accumulator: OutcomeAccumulator, *, status: str, message: str, source_path: str) -> None:
    accumulator.record(EventMappingReplayOutcome(status=status, message=message, source_path=source_path))


def _log_bounded_exception(
    *,
    prefix: str,
    path: str,
    exc: Exception,
    tracebacks_logged: int,
    suppressed_logged: bool,
) -> tuple[int, bool]:
    if tracebacks_logged < TRACEBACK_SAMPLE_LIMIT:
        logger.exception("%s %s", prefix, path)
        return tracebacks_logged + 1, suppressed_logged
    if not suppressed_logged:
        logger.error(
            "%s traceback logging suppressed after %s failures; latest source=%s error=%r",
            prefix,
            TRACEBACK_SAMPLE_LIMIT,
            path,
            exc,
        )
        return tracebacks_logged, True
    return tracebacks_logged, suppressed_logged


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


class MyRunnable(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config or {}
        self.plugin_config = plugin_config or {}

    def get_progress_target(self):
        return None

    def _resolve_folder_lookup(self) -> str:
        param_set = self.plugin_config.get("pulse_primary", {}) or {}
        return str(param_set.get("pulse_partitioned_data") or "partitioned_data")

    def run(self, progress_callback):
        _configure_runtime_logging()

        folder_lookup = self._resolve_folder_lookup()
        storage_ctx = build_storage_context(project_key=self.project_key, folder_lookup=folder_lookup)
        target = DSSFolderTarget(project_key=self.project_key, folder_lookup=storage_ctx.folder_lookup)
        folder = dataiku.Folder(
            lookup=storage_ctx.folder_id,
            project_key=self.project_key,
            ignore_flow=True,
        )

        source_paths = sorted(
            iter_managed_folder_paths(
                storage_ctx,
                relative_prefix="silver/category=event_mapping/",
                suffix=".parquet",
            )
        )
        logger.info("Discovered %s event-mapping parquet source file(s)", len(source_paths))

        outcomes = _new_outcome_accumulator()
        unexpected_failure_tracebacks = 0
        unexpected_failure_logs_suppressed = False

        for index, path in enumerate(source_paths, start=1):
            _report_progress(progress_callback, processed=index, total=len(source_paths))
            try:
                source = parse_event_mapping_source_path(path)
                source_df = read_managed_folder_parquet(folder, path)
                plans = plan_event_mapping_replay(source=source, source_df=source_df)
                if not plans:
                    delete_managed_folder_file(folder, path)
                    _record_outcome(
                        outcomes,
                        status="dropped",
                        message="All rows were intentionally dropped by current mapping",
                        source_path=path,
                    )
                    continue

                upload_result: ReplacementUploadResult = upload_event_mapping_replacements(target=target, folder=folder, plans=plans)
                if upload_result.status == "dq_failed":
                    _record_outcome(
                        outcomes,
                        status="failed",
                        message=f"DQ failed: {upload_result.message}",
                        source_path=path,
                    )
                    continue

                if upload_result.status == "upload_failed_cleaned":
                    _record_outcome(
                        outcomes,
                        status="partial_write_cleaned",
                        message=upload_result.message,
                        source_path=path,
                    )
                    continue

                if upload_result.status == "upload_failed_cleanup_failed":
                    _record_outcome(
                        outcomes,
                        status="partial_write_cleanup_failed",
                        message=upload_result.message,
                        source_path=path,
                    )
                    continue

                try:
                    delete_managed_folder_file(folder, path)
                except Exception as exc:
                    unexpected_failure_tracebacks, unexpected_failure_logs_suppressed = _log_bounded_exception(
                        prefix="Source delete failed after replacement writes for",
                        path=path,
                        exc=exc,
                        tracebacks_logged=unexpected_failure_tracebacks,
                        suppressed_logged=unexpected_failure_logs_suppressed,
                    )
                    _record_outcome(
                        outcomes,
                        status="delete_failed",
                        message=repr(exc),
                        source_path=path,
                    )
                    continue

                _record_outcome(
                    outcomes,
                    status="replaced",
                    message=upload_result.message,
                    source_path=path,
                )
            except ReplaySkipError as exc:
                _record_outcome(outcomes, status="skipped", message=str(exc), source_path=path)
            except Exception as exc:
                unexpected_failure_tracebacks, unexpected_failure_logs_suppressed = _log_bounded_exception(
                    prefix="Reload event-mapping failed for",
                    path=path,
                    exc=exc,
                    tracebacks_logged=unexpected_failure_tracebacks,
                    suppressed_logged=unexpected_failure_logs_suppressed,
                )
                _record_outcome(outcomes, status="failed", message=repr(exc), source_path=path)

        logger.info(
            "Reload event-mapping completed discovered=%s replaced=%s dropped=%s skipped=%s failed=%s partial_write_cleaned=%s partial_write_cleanup_failed=%s delete_failed=%s",
            len(source_paths),
            outcomes.counts.get("replaced", 0),
            outcomes.counts.get("dropped", 0),
            outcomes.counts.get("skipped", 0),
            outcomes.counts.get("failed", 0),
            outcomes.counts.get("partial_write_cleaned", 0),
            outcomes.counts.get("partial_write_cleanup_failed", 0),
            outcomes.counts.get("delete_failed", 0),
        )

        return _build_summary_html(
            project_key=target.project_key,
            folder_lookup=target.folder_lookup,
            outcomes=outcomes,
            discovered=len(source_paths),
        )
