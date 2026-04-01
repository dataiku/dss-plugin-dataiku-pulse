"""DuckDB-based GOLD table builder for Pulse.

This package lives under `data_collection` to share configuration and
utilities with the collection framework, but it intentionally uses the name
`pulse_duckdb` to avoid conflicting with the upstream `duckdb` module.
"""

from __future__ import annotations

from .duckdb_manager import DuckDBSetupResult, prepare_duckdb, query_df

__all__ = [
    "DuckDBSetupResult",
    "prepare_duckdb",
    "query_df",
]
