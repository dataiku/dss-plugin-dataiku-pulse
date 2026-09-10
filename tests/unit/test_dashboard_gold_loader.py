from __future__ import annotations

import duckdb
import pandas as pd
import pytest

from pulse_dashboard.pulse_duckdb.engine import gold_loader


@pytest.fixture()
def conn():
    connection = duckdb.connect(database=":memory:")
    try:
        yield connection
    finally:
        connection.close()


def test_build_gold_blob_paths_groups_fact_partitions_and_direct_tables(monkeypatch):
    class _StorageCtx:
        connection_type = "EC2"
        bucket_or_container = "bucket"
        folder_root = "root"
        blob_header = "s3"

    monkeypatch.setattr(gold_loader.settings, "PULSE_SOURCE_PROJECT_KEY", "TEST_PROJECT")
    monkeypatch.setattr(gold_loader.settings, "PULSE_GOLD_TABLES_FOLDER_ID", "")
    monkeypatch.setattr(gold_loader.settings, "PULSE_GOLD_TABLES_FOLDER_NAME", "gold_data")
    monkeypatch.setattr(gold_loader, "build_storage_context", lambda **kwargs: _StorageCtx())

    _ctx, grouped = gold_loader._build_gold_blob_paths(
        [
            "gold/base_license_addon_licenses_latest.parquet",
            "gold/dim_category_to_capability.parquet",
            "gold/fact_dev_activity_events/instance_name=feoperations/year=2026/month=06/day=21/data.parquet",
            "gold/fact_dev_activity_events/instance_name=feoperations/year=2026/month=06/day=22/data.parquet",
        ]
    )

    assert grouped == {
        "base_license_addon_licenses_latest": [
            "s3://bucket/root/gold/base_license_addon_licenses_latest.parquet"
        ],
        "dim_category_to_capability": [
            "s3://bucket/root/gold/dim_category_to_capability.parquet"
        ],
        "fact_dev_activity_events": [
            "s3://bucket/root/gold/fact_dev_activity_events/instance_name=feoperations/year=2026/month=06/day=21/data.parquet",
            "s3://bucket/root/gold/fact_dev_activity_events/instance_name=feoperations/year=2026/month=06/day=22/data.parquet",
        ],
    }


def test_load_remote_parquet_table_uses_create_or_replace_with_grouped_paths(conn, monkeypatch):
    class _ConnStub:
        def __init__(self):
            self.calls = []

        def execute(self, sql, params=None):
            self.calls.append((sql, params or []))

            class _Result:
                def fetchone(self):
                    return (2,)

            return _Result()

    stub = _ConnStub()
    rows = gold_loader._load_remote_parquet_table(
        stub,
        table_name="fact_dev_activity_events",
        blob_paths=[
            "s3://bucket/root/gold/fact_dev_activity_events/instance_name=feoperations/year=2026/month=06/day=21/data.parquet",
            "s3://bucket/root/gold/fact_dev_activity_events/instance_name=feoperations/year=2026/month=06/day=22/data.parquet",
        ],
    )

    assert rows == 2
    assert stub.calls[0][0].startswith('CREATE OR REPLACE TABLE "fact_dev_activity_events" AS SELECT * FROM read_parquet([')
    assert stub.calls[0][1] == [
        "s3://bucket/root/gold/fact_dev_activity_events/instance_name=feoperations/year=2026/month=06/day=21/data.parquet",
        "s3://bucket/root/gold/fact_dev_activity_events/instance_name=feoperations/year=2026/month=06/day=22/data.parquet",
    ]


@pytest.mark.parametrize(
    ("table_name", "input_columns", "expected_columns"),
    [
        (
            "fact_user_activity_daily",
            {
                "login_norm": ["alice", "bob"],
                "login": ["Alice", "Bob"],
                "viewing_actions_count": [1, 2],
                "developing_actions_count": [3, 4],
                "last_activity_at": ["2026-06-21T10:00:00", "2026-06-22T10:00:00"],
            },
            [
                "day",
                "instance_name",
                "login_norm",
                "login",
                "viewing_actions_count",
                "developing_actions_count",
                "last_activity_at",
            ],
        ),
        (
            "fact_formal_mau_daily",
            {
                "login_norm": ["alice", "bob"],
                "login": ["Alice", "Bob"],
                "application_open_count": [5, 6],
                "last_application_open_at": ["2026-06-21T11:00:00", "2026-06-22T11:00:00"],
            },
            [
                "day",
                "instance_name",
                "login_norm",
                "login",
                "application_open_count",
                "last_application_open_at",
            ],
        ),
    ],
)
def test_special_daily_fact_bulk_load_preserves_schema_and_hive_day(
    conn,
    tmp_path,
    table_name,
    input_columns,
    expected_columns,
):
    blob_paths = []
    for day_index, day in enumerate([21, 22]):
        partition_dir = tmp_path / table_name / "instance_name=feoperations" / "year=2026" / "month=06" / f"day={day:02d}"
        partition_dir.mkdir(parents=True)
        path = partition_dir / "data.parquet"
        pd.DataFrame({column: [values[day_index]] for column, values in input_columns.items()}).to_parquet(path)
        blob_paths.append(str(path))

    rows = gold_loader._load_remote_parquet_table(conn, table_name=table_name, blob_paths=blob_paths)

    assert rows == 2
    assert [row[1] for row in conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()] == expected_columns
    loaded = conn.execute(
        f'SELECT day, instance_name, login_norm FROM "{table_name}" ORDER BY day, login_norm;'
    ).fetchall()
    assert loaded == [
        (duckdb.execute("SELECT DATE '2026-06-21'").fetchone()[0], "feoperations", "alice"),
        (duckdb.execute("SELECT DATE '2026-06-22'").fetchone()[0], "feoperations", "bob"),
    ]


@pytest.mark.parametrize("table_name", ["fact_user_activity_daily", "fact_formal_mau_daily"])
def test_special_daily_fact_uses_one_bulk_read_for_multiple_paths(table_name):
    class _ConnStub:
        def __init__(self):
            self.calls = []

        def execute(self, sql, params=None):
            self.calls.append((sql, params or []))

            class _Result:
                def fetchone(self):
                    return (2,)

            return _Result()

    stub = _ConnStub()
    blob_paths = [
        f"s3://bucket/root/gold/{table_name}/instance_name=feoperations/year=2026/month=06/day=21/data.parquet",
        f"s3://bucket/root/gold/{table_name}/instance_name=feoperations/year=2026/month=06/day=22/data.parquet",
    ]

    rows = gold_loader._load_remote_parquet_table(stub, table_name=table_name, blob_paths=blob_paths)

    assert rows == 2
    load_calls = [call for call in stub.calls if "read_parquet" in call[0]]
    assert len(load_calls) == 1
    assert "CREATE OR REPLACE TABLE" in load_calls[0][0]
    assert "INSERT INTO" not in load_calls[0][0]
    assert "hive_partitioning = true" in load_calls[0][0]
    assert load_calls[0][1] == blob_paths


def test_load_gold_tables_handles_mixed_fact_dev_activity_event_partition_schemas(conn, monkeypatch, tmp_path):
    monkeypatch.setattr(gold_loader.settings, "PULSE_SOURCE_PROJECT_KEY", "TEST_PROJECT")
    monkeypatch.setattr(gold_loader.settings, "PULSE_GOLD_TABLES_FOLDER_ID", "")
    monkeypatch.setattr(gold_loader.settings, "PULSE_GOLD_TABLES_FOLDER_NAME", "gold_data")
    class _StorageCtx:
        connection_type = "EC2"
        bucket_or_container = "bucket"
        folder_root = "root"
        blob_header = "s3"

    monkeypatch.setattr(gold_loader, "build_storage_context", lambda **kwargs: _StorageCtx())

    created_tables = {}

    def _fake_load_remote_parquet_table(_conn, *, table_name, blob_paths):
        created_tables[table_name] = blob_paths
        return 2 if table_name == "fact_dev_activity_events" else 1

    monkeypatch.setattr(gold_loader, "_load_remote_parquet_table", _fake_load_remote_parquet_table)

    report = gold_loader.load_gold_tables(
        conn,
        replace=True,
        prefix="",
        name_glob="*",
        allowed_suffixes=(".parquet",),
        allowed_table_names={"fact_dev_activity_events", "base_license_addon_licenses_latest"},
        paths=[
            "gold/base_license_addon_licenses_latest.parquet",
            "gold/fact_dev_activity_events/instance_name=feoperations/year=2026/month=06/day=21/data.parquet",
            "gold/fact_dev_activity_events/instance_name=feoperations/year=2026/month=06/day=22/data.parquet",
        ],
    )

    assert report["ok"] is True
    assert len(report["failed"]) == 0
    assert len(report["failed"]) == 0
    assert created_tables == {
        "base_license_addon_licenses_latest": [
            "s3://bucket/root/gold/base_license_addon_licenses_latest.parquet"
        ],
        "fact_dev_activity_events": [
            "s3://bucket/root/gold/fact_dev_activity_events/instance_name=feoperations/year=2026/month=06/day=21/data.parquet",
            "s3://bucket/root/gold/fact_dev_activity_events/instance_name=feoperations/year=2026/month=06/day=22/data.parquet",
        ],
    }
    assert all("_history" not in name for name in created_tables)


def test_load_remote_parquet_table_rejects_invalid_table_identifier(conn):
    with pytest.raises(ValueError, match="Invalid GOLD physical table name"):
        gold_loader._load_remote_parquet_table(
            conn,
            table_name="bad-table-name",
            blob_paths=["s3://bucket/root/gold/base_license_addon_licenses_latest.parquet"],
        )
