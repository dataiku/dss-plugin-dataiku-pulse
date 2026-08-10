from __future__ import annotations

import duckdb
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
