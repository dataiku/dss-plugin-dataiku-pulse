from __future__ import annotations

import html
import logging
from collections import Counter
from dataclasses import dataclass
from dataiku.runnables import Runnable

from data_collection.audit_logs_modules.event_mapping_replay import (
    EventMappingReplayOutcome,
    ReplaySkipError,
    ReplacementUploadResult,
    delete_managed_folder_file,
    discover_event_mapping_paths,
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


@dataclass(frozen=True)
class OutcomeSample:
    path: str
    message: str


def _sample_list(items: list[OutcomeSample], *, limit: int = SAMPLE_LIMIT) -> str:
    if not items:
        return "<em>None</em>"
    rendered = "".join(
        f"<li><code>{html.escape(item.path)}</code>: {html.escape(item.message)}</li>" for item in items[:limit]
    )
    suffix = "" if len(items) <= limit else f"<li><em>... {len(items) - limit} more</em></li>"
    return f"<ul>{rendered}{suffix}</ul>"


def _build_summary_html(
    *,
    project_key: str,
    folder_lookup: str,
    outcomes: list[EventMappingReplayOutcome],
    discovered: int,
) -> str:
    counts = Counter(outcome.status for outcome in outcomes)
    grouped_samples: dict[str, list[OutcomeSample]] = {}
    for outcome in outcomes:
        grouped_samples.setdefault(outcome.status, [])
        if len(grouped_samples[outcome.status]) < SAMPLE_LIMIT:
            grouped_samples[outcome.status].append(OutcomeSample(path=outcome.source_path, message=outcome.message))

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
        lines.append(f"<li><strong>{label}:</strong> {counts.get(status, 0)}</li>")
    lines.append("</ul>")
    for status, label in statuses:
        lines.append(f"<h3>{label}</h3>{_sample_list(grouped_samples.get(status, []))}")
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
        folder_lookup = self._resolve_folder_lookup()
        storage_ctx = build_storage_context(project_key=self.project_key, folder_lookup=folder_lookup)
        target = DSSFolderTarget(project_key=self.project_key, folder_lookup=storage_ctx.folder_lookup)
        folder = storage_ctx.folder_handle

        source_paths = sorted(
            iter_managed_folder_paths(
                storage_ctx,
                relative_prefix="silver/category=event_mapping/",
                suffix=".parquet",
            )
        )
        outcomes: list[EventMappingReplayOutcome] = []

        for index, path in enumerate(source_paths, start=1):
            progress_callback(index)
            try:
                source = parse_event_mapping_source_path(path)
                source_df = read_managed_folder_parquet(folder, path)
                plans = plan_event_mapping_replay(source=source, source_df=source_df)
                if not plans:
                    delete_managed_folder_file(folder, path)
                    outcomes.append(
                        EventMappingReplayOutcome(
                            status="dropped",
                            message="All rows were intentionally dropped by current mapping",
                            source_path=path,
                        )
                    )
                    continue

                upload_result: ReplacementUploadResult = upload_event_mapping_replacements(target=target, folder=folder, plans=plans)
                if upload_result.status == "dq_failed":
                    outcomes.append(
                        EventMappingReplayOutcome(
                            status="failed",
                            message=f"DQ failed: {upload_result.message}",
                            source_path=path,
                        )
                    )
                    continue

                if upload_result.status == "upload_failed_cleaned":
                    outcomes.append(
                        EventMappingReplayOutcome(
                            status="partial_write_cleaned",
                            message=upload_result.message,
                            source_path=path,
                            replacement_paths=upload_result.written_paths,
                        )
                    )
                    continue

                if upload_result.status == "upload_failed_cleanup_failed":
                    outcomes.append(
                        EventMappingReplayOutcome(
                            status="partial_write_cleanup_failed",
                            message=upload_result.message,
                            source_path=path,
                            replacement_paths=upload_result.written_paths,
                        )
                    )
                    continue

                try:
                    delete_managed_folder_file(folder, path)
                except Exception as exc:
                    logger.exception("Source delete failed after replacement writes for %s", path)
                    outcomes.append(
                        EventMappingReplayOutcome(
                            status="delete_failed",
                            message=repr(exc),
                            source_path=path,
                            replacement_paths=upload_result.written_paths,
                        )
                    )
                    continue

                outcomes.append(
                    EventMappingReplayOutcome(
                        status="replaced",
                        message=upload_result.message,
                        source_path=path,
                        replacement_paths=upload_result.written_paths,
                    )
                )
            except ReplaySkipError as exc:
                outcomes.append(EventMappingReplayOutcome(status="skipped", message=str(exc), source_path=path))
            except Exception as exc:
                logger.exception("Reload event-mapping failed for %s", path)
                outcomes.append(EventMappingReplayOutcome(status="failed", message=repr(exc), source_path=path))

        return _build_summary_html(
            project_key=target.project_key,
            folder_lookup=target.folder_lookup,
            outcomes=outcomes,
            discovered=len(source_paths),
        )
