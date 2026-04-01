"""Orchestration utilities for building the DuckDB from DSS sources."""

from __future__ import annotations

from .init_db import ensure_database_ready


def rebuild_gold_tables(*, replace: bool = True) -> dict:
    """Force refresh of DuckDB from managed folder + rebuild views."""

    report = ensure_database_ready(load_gold_tables=True, replace_gold_tables=replace)
    # Keep response compatible with callers expecting a loader report dict.
    if report.get("gold_loaded") and isinstance(report.get("report"), dict):
        return report["report"]
    return report
