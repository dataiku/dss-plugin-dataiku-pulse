from __future__ import annotations

import copy
import io
from dataclasses import dataclass

import duckdb
import pandas as pd
import pytest

from data_collection.pulse_duckdb import unload


@dataclass
class LocalGoldContext:
    bucket_or_container: str = "bucket"
    blob_header: str = "file:///tmp/pulse-gold-test"
    folder_root: str = ""


def _create_fact_table(
    conn: duckdb.DuckDBPyConnection, table_name: str, time_column: str
) -> None:
    conn.execute(f"""
        CREATE TABLE {table_name} (
            instance_name VARCHAR,
            {time_column} TIMESTAMP,
            value INTEGER
        );
        """)


def _insert_rows(
    conn: duckdb.DuckDBPyConnection,
    table_name: str,
    time_column: str,
    rows: list[tuple[str, str, int]],
) -> None:
    conn.executemany(
        f"INSERT INTO {table_name} (instance_name, {time_column}, value) VALUES (?, CAST(? AS TIMESTAMP), ?);",
        rows,
    )


def _selected_days(
    conn: duckdb.DuckDBPyConnection,
    *,
    table_name: str,
    time_column: str,
    adjusted_watermark: str | None,
) -> set[tuple[str, int, int, int]]:
    return set(
        unload.selected_fact_partition_rows(
            conn,
            table_name=table_name,
            time_column=time_column,
            adjusted_watermark=adjusted_watermark,
        )
    )


def test_first_run_without_manifest_selects_all_fact_partitions() -> None:
    conn = duckdb.connect(":memory:")
    _create_fact_table(conn, "fact_user_activity_daily", "day")
    _insert_rows(
        conn,
        "fact_user_activity_daily",
        "day",
        [
            ("a", "2026-01-01 00:00:00", 1),
            ("a", "2026-01-02 00:00:00", 2),
            ("b", "2026-01-02 00:00:00", 3),
        ],
    )

    prior_watermark, adjusted_watermark, mode = unload._fact_selection_watermark(
        manifest={},
        table_name="fact_user_activity_daily",
        incremental_enabled=True,
        lookback_days=3,
    )

    assert prior_watermark is None
    assert adjusted_watermark is None
    assert mode == "full"
    assert _selected_days(
        conn,
        table_name="fact_user_activity_daily",
        time_column="day",
        adjusted_watermark=adjusted_watermark,
    ) == {
        ("a", 2026, 1, 1),
        ("a", 2026, 1, 2),
        ("b", 2026, 1, 2),
    }


@pytest.mark.parametrize(
    ("table_name", "time_column", "watermark"),
    [
        ("fact_user_activity_daily", "day", "2026-01-04T00:00:00"),
        ("fact_license_utilization_daily", "snapshot_date", "2026-01-04T00:00:00"),
        ("fact_dev_activity_events", "timestamp", "2026-01-04T12:00:00"),
    ],
)
def test_incremental_selection_uses_fact_time_contract(
    table_name: str, time_column: str, watermark: str
) -> None:
    conn = duckdb.connect(":memory:")
    _create_fact_table(conn, table_name, time_column)
    _insert_rows(
        conn,
        table_name,
        time_column,
        [
            ("a", "2026-01-01 00:00:00", 1),
            ("a", "2026-01-02 00:00:00", 2),
            ("a", "2026-01-03 00:00:00", 3),
            ("b", "2026-01-03 15:00:00", 4),
            ("a", "2026-01-05 00:00:00", 5),
        ],
    )

    prior_watermark, adjusted_watermark, mode = unload._fact_selection_watermark(
        manifest={"watermarks": {table_name: watermark}},
        table_name=table_name,
        incremental_enabled=True,
        lookback_days=2,
    )

    assert prior_watermark == watermark
    assert (
        adjusted_watermark == "2026-01-02T00:00:00"
        if "12:00:00" not in watermark
        else "2026-01-02T12:00:00"
    )
    assert mode == "incremental"
    expected_partitions = {
        ("a", 2026, 1, 3),
        ("b", 2026, 1, 3),
        ("a", 2026, 1, 5),
    }
    if "12:00:00" not in watermark:
        expected_partitions.add(("a", 2026, 1, 2))
    assert (
        _selected_days(
            conn,
            table_name=table_name,
            time_column=time_column,
            adjusted_watermark=adjusted_watermark,
        )
        == expected_partitions
    )


def test_late_arriving_row_replaces_complete_selected_instance_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = duckdb.connect(":memory:")
    _create_fact_table(conn, "fact_dev_activity_events", "timestamp")
    _insert_rows(
        conn,
        "fact_dev_activity_events",
        "timestamp",
        [
            ("a", "2026-01-01 10:00:00", 1),
            ("a", "2026-01-03 00:00:00", 2),
            ("a", "2026-01-03 23:00:00", 99),
            ("b", "2026-01-03 23:00:00", 3),
            ("a", "2026-01-04 00:00:00", 4),
        ],
    )
    uploaded: dict[str, bytes] = {}
    cleared: list[str] = []

    class LocalFolder:
        def __init__(self, lookup: str):
            self.lookup = lookup

        def clear_path(self, path: str) -> None:
            cleared.append(path)

        def upload_stream(self, path: str, data) -> None:
            uploaded[path] = data.read()

    monkeypatch.setattr(unload.dataiku, "Folder", LocalFolder)

    unload._write_fact_table_partitions_duckdb(
        conn,
        gold_ctx=LocalGoldContext(),
        gold_folder_lookup="gold_data",
        table_name="fact_dev_activity_events",
        destination="gold/fact_dev_activity_events",
        time_column="timestamp",
        adjusted_watermark="2026-01-03T12:00:00",
    )

    assert set(uploaded) == {
        "gold/fact_dev_activity_events/instance_name=a/year=2026/month=01/day=03/data.parquet",
        "gold/fact_dev_activity_events/instance_name=b/year=2026/month=01/day=03/data.parquet",
        "gold/fact_dev_activity_events/instance_name=a/year=2026/month=01/day=04/data.parquet",
    }
    assert (
        "gold/fact_dev_activity_events/instance_name=a/year=2026/month=01/day=03/"
        in cleared
    )
    day_three_df = pd.read_parquet(
        io.BytesIO(
            uploaded[
                "gold/fact_dev_activity_events/instance_name=a/year=2026/month=01/day=03/data.parquet"
            ]
        )
    ).sort_values("value")
    assert day_three_df["value"].tolist() == [2, 99]


def test_rows_older_than_adjusted_watermark_are_not_unloaded() -> None:
    conn = duckdb.connect(":memory:")
    _create_fact_table(conn, "fact_object_activity_events", "timestamp")
    _insert_rows(
        conn,
        "fact_object_activity_events",
        "timestamp",
        [
            ("a", "2026-01-01 00:00:00", 1),
            ("a", "2026-01-02 11:59:59", 2),
            ("a", "2026-01-02 12:00:00", 3),
        ],
    )

    assert _selected_days(
        conn,
        table_name="fact_object_activity_events",
        time_column="timestamp",
        adjusted_watermark="2026-01-02T12:00:00",
    ) == {("a", 2026, 1, 2)}


def test_unload_failure_leaves_original_manifest_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = duckdb.connect(":memory:")
    _create_fact_table(conn, "fact_user_activity_daily", "day")
    _insert_rows(
        conn, "fact_user_activity_daily", "day", [("a", "2026-01-02 00:00:00", 1)]
    )
    original_manifest = {
        "watermarks": {"fact_user_activity_daily": "2026-01-01T00:00:00"},
        "updated_at": "old",
    }
    pending_manifest = copy.deepcopy(original_manifest)

    def fail_unload(*args, **kwargs):
        raise RuntimeError("simulated unload failure")

    monkeypatch.setattr(unload, "_write_fact_table_partitions_duckdb", fail_unload)

    unloaded_tables, failed_tables = unload.unload_gold_tables(
        conn,
        gold_ctx=LocalGoldContext(),
        gold_folder_lookup="gold_data",
        table_names=["fact_user_activity_daily"],
        unload_behavior="duckdb",
        manifest=pending_manifest,
        incremental_enabled=True,
        lookback_days=1,
    )

    assert unloaded_tables == []
    assert failed_tables == ["fact_user_activity_daily"]
    assert original_manifest == {
        "watermarks": {"fact_user_activity_daily": "2026-01-01T00:00:00"},
        "updated_at": "old",
    }
    assert pending_manifest == original_manifest


def test_successful_unload_writes_each_supported_fact_watermark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = duckdb.connect(":memory:")
    pending_manifest = {
        "watermarks": {
            "fact_user_activity_daily": "2026-01-01T00:00:00",
            "unrelated_table": "preserve-me",
        },
        "updated_at": "old",
    }
    expected_watermarks: dict[str, str] = {}

    for index, (table_name, time_column) in enumerate(
        unload.FACT_TIME_COLUMNS.items(), start=1
    ):
        _create_fact_table(conn, table_name, time_column)
        max_timestamp = f"2026-01-{index + 1:02d} 12:34:56"
        _insert_rows(
            conn,
            table_name,
            time_column,
            [
                ("a", "2026-01-01 00:00:00", index),
                ("a", max_timestamp, index + 100),
            ],
        )
        expected_watermarks[table_name] = conn.execute(
            f"SELECT CAST(MAX(CAST({time_column} AS TIMESTAMP)) AS VARCHAR) FROM {table_name};"
        ).fetchone()[0]

    monkeypatch.setattr(
        unload,
        "_write_fact_table_partitions_duckdb",
        lambda *_args, **_kwargs: None,
    )

    unloaded_tables, failed_tables = unload.unload_gold_tables(
        conn,
        gold_ctx=LocalGoldContext(),
        gold_folder_lookup="gold_data",
        table_names=list(unload.FACT_TIME_COLUMNS),
        unload_behavior="duckdb",
        manifest=pending_manifest,
        incremental_enabled=True,
        lookback_days=1,
    )

    assert unloaded_tables == list(unload.FACT_TIME_COLUMNS)
    assert failed_tables == []
    assert pending_manifest["watermarks"] == {
        **expected_watermarks,
        "unrelated_table": "preserve-me",
    }
    assert pending_manifest["updated_at"] == "old"


def test_incremental_disabled_selects_all_partitions() -> None:
    conn = duckdb.connect(":memory:")
    _create_fact_table(conn, "fact_formal_mau_daily", "day")
    _insert_rows(
        conn,
        "fact_formal_mau_daily",
        "day",
        [
            ("a", "2026-01-01 00:00:00", 1),
            ("a", "2026-01-05 00:00:00", 2),
        ],
    )

    prior_watermark, adjusted_watermark, mode = unload._fact_selection_watermark(
        manifest={"watermarks": {"fact_formal_mau_daily": "2026-01-05T00:00:00"}},
        table_name="fact_formal_mau_daily",
        incremental_enabled=False,
        lookback_days=1,
    )

    assert prior_watermark is None
    assert adjusted_watermark is None
    assert mode == "full"
    assert _selected_days(
        conn,
        table_name="fact_formal_mau_daily",
        time_column="day",
        adjusted_watermark=adjusted_watermark,
    ) == {("a", 2026, 1, 1), ("a", 2026, 1, 5)}
