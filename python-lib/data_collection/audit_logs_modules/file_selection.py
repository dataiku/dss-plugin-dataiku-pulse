"""Selection of audit log files eligible for a gather run.

Extracted from the audit runnable so the truncation behavior is unit-testable:
when `max_files` cuts off eligible files, the caller must clamp the cursor to
the oldest excluded eligible file so those files re-qualify next run.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class AuditFileSelection:
    paths: list[Path]
    available_count: int
    oldest_selected_mtime: pd.Timestamp | None
    newest_selected_mtime: pd.Timestamp | None
    # Files whose mtime is >= cursor (they may contain new rows) but which were
    # dropped by the max_files cap. Advancing the cursor past them loses data.
    excluded_eligible_count: int
    oldest_excluded_eligible_mtime: pd.Timestamp | None


def select_candidate_audit_files(
    file_list: list[Path],
    *,
    cursor_ts: pd.Timestamp,
    max_files: int,
) -> AuditFileSelection:
    """Pick up to `max_files` newest audit files, plus one pre-cursor boundary file.

    The boundary file (newest file older than the cursor) is included because it
    may contain rows newer than the cursor written before its final mtime.
    """

    file_entries: list[tuple[Path, float]] = []
    for path in file_list:
        try:
            if path.exists():
                file_entries.append((path, path.stat().st_mtime))
        except Exception:
            continue

    if not file_entries:
        return AuditFileSelection([], 0, None, None, 0, None)

    cursor_epoch = cursor_ts.timestamp()
    sorted_entries = sorted(file_entries, key=lambda item: item[1], reverse=True)
    selected_entries: list[tuple[Path, float]] = []
    boundary_added = False

    for path, mtime_epoch in sorted_entries:
        if len(selected_entries) >= max_files:
            break

        if mtime_epoch >= cursor_epoch:
            selected_entries.append((path, mtime_epoch))
            continue

        if not selected_entries:
            selected_entries.append((path, mtime_epoch))
            boundary_added = True
            continue

        if not boundary_added:
            selected_entries.append((path, mtime_epoch))
            boundary_added = True
        break

    if not selected_entries:
        selected_entries.append(sorted_entries[0])

    selected_set = {path for path, _ in selected_entries}
    excluded_eligible_mtimes = [
        mtime_epoch
        for path, mtime_epoch in sorted_entries
        if mtime_epoch >= cursor_epoch and path not in selected_set
    ]

    selected_mtimes = [
        pd.Timestamp.fromtimestamp(mtime_epoch, tz="UTC") for _, mtime_epoch in selected_entries
    ]
    return AuditFileSelection(
        paths=[path for path, _ in selected_entries],
        available_count=len(sorted_entries),
        oldest_selected_mtime=min(selected_mtimes) if selected_mtimes else None,
        newest_selected_mtime=max(selected_mtimes) if selected_mtimes else None,
        excluded_eligible_count=len(excluded_eligible_mtimes),
        oldest_excluded_eligible_mtime=(
            pd.Timestamp.fromtimestamp(min(excluded_eligible_mtimes), tz="UTC")
            if excluded_eligible_mtimes
            else None
        ),
    )
