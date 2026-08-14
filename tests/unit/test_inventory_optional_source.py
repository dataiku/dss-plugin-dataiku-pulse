from __future__ import annotations

import duckdb

from pulse_dashboard.pulse_duckdb.engine.init_db import _ensure_table_exists


def test_inventory_table_uses_empty_schema_when_optional_source_is_missing():
    conn = duckdb.connect(database=":memory:")
    try:
        created = _ensure_table_exists(
            conn,
            table_name="base_retrieval_augmented_llms_metadata",
        )

        assert created is True
        assert conn.execute(
            "SELECT COUNT(*) FROM base_retrieval_augmented_llms_metadata"
        ).fetchone()[0] == 0
    finally:
        conn.close()
