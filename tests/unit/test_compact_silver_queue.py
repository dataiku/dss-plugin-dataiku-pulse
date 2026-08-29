from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from data_collection.audit_logs_modules.compact_silver_queue import CompactSilverQueue


class _Ctx(SimpleNamespace):
    pass


def test_queue_populates_incrementally_and_preserves_path_alignment(monkeypatch):
    import data_collection.audit_logs_modules.compact_silver_queue as queue_module

    iterated = {"count": 0}
    paths = [
        "/silver/category=event_mapping/module=administration/instance_name=alpha/year=2026/month=08/day=23/source-a.parquet",
        "/silver/category=event_mapping/module=administration/instance_name=alpha/year=2026/month=08/day=23/source-b.parquet",
        "/silver/category=event_mapping/module=administration/instance_name=alpha/year=2026/month=08/day=24/source-recent.parquet",
        "/silver/category=event_mapping/module=administration/instance_name=alpha/year=2026/month=08/day=22/compact_silver-1786510805000-0001.parquet",
        "/silver/category=event_mapping/module=containers/instance_name=beta/year=2026/month=08/day=23/source-c.parquet",
    ]

    def fake_iter(*args, **kwargs):
        for path in paths:
            iterated["count"] += 1
            yield path

    monkeypatch.setattr(queue_module, "iter_managed_folder_paths", fake_iter)
    ctx = _Ctx(connection_type="EC2", folder_root="root", bucket_or_container="bucket")

    queue = CompactSilverQueue.create()
    try:
        summary = queue.populate_from_discovery(
            storage_ctx=ctx,
            relative_prefix="silver/category=event_mapping/",
            suffix=".parquet",
            partition_filters={"category": "event_mapping"},
            minimum_age_days=3,
            utc_today=date(2026, 8, 27),
            insert_batch_size=1,
        )

        assert iterated["count"] == len(paths)
        assert summary.total_matched_paths == 5
        assert summary.filtered_matching_paths == 4
        assert summary.skipped_compact_outputs == 1
        assert summary.excluded_recent_paths == 1
        assert summary.eligible_paths == 3
        assert summary.eligible_partition_count == 2

        batch = queue.next_partition_batch(batch_size=1)
        assert len(batch.selected_partitions) == 1
        partition = batch.selected_partitions[0]
        assert partition.partition_scope == "category=event_mapping; module=administration; instance_name=alpha; date=2026/08/23"
        assert partition.relative_paths == [
            "/silver/category=event_mapping/module=administration/instance_name=alpha/year=2026/month=08/day=23/source-a.parquet",
            "/silver/category=event_mapping/module=administration/instance_name=alpha/year=2026/month=08/day=23/source-b.parquet",
        ]
        assert partition.full_paths == [
            "bucket/root/silver/category=event_mapping/module=administration/instance_name=alpha/year=2026/month=08/day=23/source-a.parquet",
            "bucket/root/silver/category=event_mapping/module=administration/instance_name=alpha/year=2026/month=08/day=23/source-b.parquet",
        ]
    finally:
        queue.close()


def test_queue_returns_worker_sized_distinct_partitions_newest_first_with_stable_ties(monkeypatch):
    import data_collection.audit_logs_modules.compact_silver_queue as queue_module

    monkeypatch.setattr(
        queue_module,
        "iter_managed_folder_paths",
        lambda *args, **kwargs: iter([
            "/silver/category=event_mapping/module=containers/instance_name=beta/year=2026/month=08/day=23/source-b.parquet",
            "/silver/category=event_mapping/module=administration/instance_name=alpha/year=2026/month=08/day=23/source-a.parquet",
            "/silver/category=event_mapping/module=administration/instance_name=gamma/year=2026/month=08/day=22/source-c.parquet",
        ]),
    )
    ctx = _Ctx(connection_type="EC2", folder_root="root", bucket_or_container="bucket")

    queue = CompactSilverQueue.create()
    try:
        queue.populate_from_discovery(
            storage_ctx=ctx,
            relative_prefix="silver/category=event_mapping/",
            suffix=".parquet",
            partition_filters={"category": "event_mapping"},
            minimum_age_days=3,
            utc_today=date(2026, 8, 27),
        )
        batch = queue.next_partition_batch(batch_size=2)
        assert [partition.partition_scope for partition in batch.selected_partitions] == [
            "category=event_mapping; module=administration; instance_name=alpha; date=2026/08/23",
            "category=event_mapping; module=containers; instance_name=beta; date=2026/08/23",
        ]
    finally:
        queue.close()


def test_queue_close_removes_temp_directory():
    queue = CompactSilverQueue.create()
    temp_dir = queue.runtime.temp_dir
    assert temp_dir.exists() is True
    queue.close()
    assert temp_dir.exists() is False
