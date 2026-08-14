from __future__ import annotations

import duckdb

from data_collection.pulse_duckdb.dev_activity import _event_mapping_extras_expr


def test_event_mapping_extras_expr_tolerates_legacy_silver_without_extras():
    conn = duckdb.connect(database=":memory:")
    try:
        conn.execute("CREATE VIEW event_mapping_legacy AS SELECT 'datasets' AS dataiku_category")

        expr = _event_mapping_extras_expr(conn, "event_mapping_legacy")
        value = conn.execute(f"SELECT {expr} FROM event_mapping_legacy").fetchone()[0]

        assert value is None
    finally:
        conn.close()


def test_event_mapping_extras_expr_preserves_current_silver_column():
    conn = duckdb.connect(database=":memory:")
    try:
        conn.execute("CREATE VIEW event_mapping_current AS SELECT '{\"event\": \"x\"}' AS extras")

        expr = _event_mapping_extras_expr(conn, "event_mapping_current")
        value = conn.execute(f"SELECT {expr} FROM event_mapping_current").fetchone()[0]

        assert value == '{"event": "x"}'
    finally:
        conn.close()
