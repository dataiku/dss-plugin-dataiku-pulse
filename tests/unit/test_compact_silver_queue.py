from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from data_collection.audit_logs_modules.compact_silver_queue import (
    CompactSilverQueue,
    _compact_queue_memory_limit_bytes,
)
from shared_duckdb.create_conn import DUCKDB_MEMORY_PERCENTAGE, _duckdb_memory_limit_bytes


def test_compact_queue_memory_limit_caps_28_gib_container():
    effective = 28 * 1024**3

    assert _compact_queue_memory_limit_bytes(effective) == 4 * 1024**3
    assert DUCKDB_MEMORY_PERCENTAGE == 0.80
    assert _duckdb_memory_limit_bytes(effective) == int(effective * 0.80)


def test_compact_queue_memory_limit_uses_twenty_percent_for_smaller_container():
    effective = 10 * 1024**3

    assert _compact_queue_memory_limit_bytes(effective) == 2 * 1024**3


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
        assert summary.included_compact_outputs == 0
        assert summary.excluded_recent_paths == 1
        assert summary.eligible_paths == 3
        assert summary.eligible_partition_count == 2

        batch = queue.next_partition_batch(batch_size=1)
        assert len(batch.selected_partitions) == 1
        partition = batch.selected_partitions[0]
        assert (
            partition.partition_scope
            == "category=event_mapping; module=administration; instance_name=alpha; date=2026/08/23"
        )
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


def test_queue_can_include_compact_outputs_only_when_explicit(monkeypatch):
    import data_collection.audit_logs_modules.compact_silver_queue as queue_module

    monkeypatch.setattr(
        queue_module,
        "iter_managed_folder_paths",
        lambda *args, **kwargs: iter(
            [
                "/silver/category=event_mapping/module=mlops/instance_name=alpha/year=2026/month=06/day=11/compact_silver-1790966945713-0001.parquet",
                "/silver/category=event_mapping/module=mlops/instance_name=alpha/year=2026/month=06/day=11/audit_logs-1-0001.parquet",
            ]
        ),
    )
    ctx = _Ctx(connection_type="EC2", folder_root="root", bucket_or_container="bucket")

    queue = CompactSilverQueue.create()
    try:
        summary = queue.populate_from_discovery(
            storage_ctx=ctx,
            relative_prefix="silver/category=event_mapping/module=mlops/",
            suffix=".parquet",
            partition_filters={"category": "event_mapping", "module": "mlops"},
            minimum_age_days=1,
            include_compact_outputs=True,
            utc_today=date(2026, 6, 20),
        )

        assert summary.skipped_compact_outputs == 0
        assert summary.included_compact_outputs == 1
        assert summary.filtered_matching_paths == 2
        assert summary.eligible_paths == 2
        batch = queue.next_partition_batch(batch_size=1)
        assert batch.selected_partitions[0].relative_paths == [
            "/silver/category=event_mapping/module=mlops/instance_name=alpha/year=2026/month=06/day=11/audit_logs-1-0001.parquet",
            "/silver/category=event_mapping/module=mlops/instance_name=alpha/year=2026/month=06/day=11/compact_silver-1790966945713-0001.parquet",
        ]
    finally:
        queue.close()


def test_queue_includes_overlapping_non_event_mapping_compact_outputs_by_default(
    monkeypatch,
):
    import data_collection.audit_logs_modules.compact_silver_queue as queue_module

    ordinary_path = "/silver/category=users/module=instance_metadata/instance_name=alpha/year=2026/month=06/day=11/source.parquet"
    compact_path = "/silver/category=users/module=instance_metadata/instance_name=alpha/year=2026/month=06/day=11/compact_silver-1790966945713-0001.parquet"
    second_compact_path = "/silver/category=users/module=instance_metadata/instance_name=alpha/year=2026/month=06/day=11/compact_silver-1790966945714-0001.parquet"
    other_day_compact_path = "/silver/category=users/module=instance_metadata/instance_name=alpha/year=2026/month=06/day=10/compact_silver-1790966945713-0001.parquet"
    other_instance_compact_path = "/silver/category=users/module=instance_metadata/instance_name=beta/year=2026/month=06/day=11/compact_silver-1790966945713-0001.parquet"

    monkeypatch.setattr(
        queue_module,
        "iter_managed_folder_paths",
        lambda *args, **kwargs: iter(
            [
                compact_path,
                other_day_compact_path,
                other_instance_compact_path,
                second_compact_path,
                ordinary_path,
            ]
        ),
    )
    queue = CompactSilverQueue.create()
    try:
        summary = queue.populate_from_discovery(
            storage_ctx=_Ctx(
                connection_type="EC2", folder_root="root", bucket_or_container="bucket"
            ),
            relative_prefix="silver/category=users/module=instance_metadata/",
            suffix=".parquet",
            partition_filters={"category": "users", "module": "instance_metadata"},
            minimum_age_days=1,
            utc_today=date(2026, 6, 20),
            insert_batch_size=1,
        )

        assert summary.filtered_matching_paths == 3
        assert summary.skipped_compact_outputs == 2
        assert summary.included_compact_outputs == 2
        assert summary.eligible_paths == 3
        assert summary.eligible_partition_count == 1
        batch = queue.next_partition_batch(batch_size=1)
        assert batch.selected_partitions[0].relative_paths == [
            compact_path,
            second_compact_path,
            ordinary_path,
        ]
    finally:
        queue.close()


def test_queue_preserves_event_mapping_default_compact_skip_for_overlaps(monkeypatch):
    import data_collection.audit_logs_modules.compact_silver_queue as queue_module

    ordinary_path = "/silver/category=event_mapping/module=administration/instance_name=alpha/year=2026/month=06/day=11/source.parquet"
    compact_path = "/silver/category=event_mapping/module=administration/instance_name=alpha/year=2026/month=06/day=11/compact_silver-1790966945713-0001.parquet"
    monkeypatch.setattr(
        queue_module,
        "iter_managed_folder_paths",
        lambda *args, **kwargs: iter([compact_path, ordinary_path]),
    )
    queue = CompactSilverQueue.create()
    try:
        summary = queue.populate_from_discovery(
            storage_ctx=_Ctx(
                connection_type="EC2", folder_root="root", bucket_or_container="bucket"
            ),
            relative_prefix="silver/category=event_mapping/module=administration/",
            suffix=".parquet",
            partition_filters={"category": "event_mapping", "module": "administration"},
            minimum_age_days=1,
            utc_today=date(2026, 6, 20),
        )

        assert summary.filtered_matching_paths == 1
        assert summary.skipped_compact_outputs == 1
        assert summary.included_compact_outputs == 0
        assert summary.eligible_paths == 1
        batch = queue.next_partition_batch(batch_size=1)
        assert batch.selected_partitions[0].relative_paths == [ordinary_path]
    finally:
        queue.close()


def test_queue_leaves_non_event_mapping_compact_only_partitions_untouched_by_default(
    monkeypatch,
):
    import data_collection.audit_logs_modules.compact_silver_queue as queue_module

    monkeypatch.setattr(
        queue_module,
        "iter_managed_folder_paths",
        lambda *args, **kwargs: iter(
            [
                "/silver/category=users/module=instance_metadata/instance_name=alpha/year=2026/month=06/day=11/compact_silver-1790966945713-0001.parquet",
                "/silver/category=users/module=instance_metadata/instance_name=alpha/year=2026/month=06/day=11/compact_silver-1790966945713-0002.parquet",
            ]
        ),
    )
    queue = CompactSilverQueue.create()
    try:
        summary = queue.populate_from_discovery(
            storage_ctx=_Ctx(
                connection_type="EC2", folder_root="root", bucket_or_container="bucket"
            ),
            relative_prefix="silver/category=users/module=instance_metadata/",
            suffix=".parquet",
            partition_filters={"category": "users", "module": "instance_metadata"},
            minimum_age_days=1,
            utc_today=date(2026, 6, 20),
            raise_on_empty=False,
        )

        assert summary.filtered_matching_paths == 0
        assert summary.skipped_compact_outputs == 2
        assert summary.included_compact_outputs == 0
        assert summary.eligible_paths == 0
        assert summary.eligible_partition_count == 0
        assert queue.queued_path_count() == 0
    finally:
        queue.close()


def test_queue_returns_worker_sized_distinct_partitions_newest_first_with_stable_ties(
    monkeypatch,
):
    import data_collection.audit_logs_modules.compact_silver_queue as queue_module

    monkeypatch.setattr(
        queue_module,
        "iter_managed_folder_paths",
        lambda *args, **kwargs: iter(
            [
                "/silver/category=event_mapping/module=containers/instance_name=beta/year=2026/month=08/day=23/source-b.parquet",
                "/silver/category=event_mapping/module=administration/instance_name=alpha/year=2026/month=08/day=23/source-a.parquet",
                "/silver/category=event_mapping/module=administration/instance_name=gamma/year=2026/month=08/day=22/source-c.parquet",
            ]
        ),
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
        assert [
            partition.partition_scope for partition in batch.selected_partitions
        ] == [
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


def test_queue_stores_module_manifest_and_releases_module_paths(monkeypatch):
    import data_collection.audit_logs_modules.compact_silver_queue as queue_module

    monkeypatch.setattr(
        queue_module,
        "iter_managed_folder_paths",
        lambda *args, **kwargs: iter(
            [
                "/silver/category=event_mapping/module=administration/instance_name=alpha/year=2026/month=08/day=23/a.parquet",
                "/silver/category=event_mapping/module=containers/instance_name=beta/year=2026/month=08/day=23/b.parquet",
            ]
        ),
    )
    queue = CompactSilverQueue.create()
    try:
        manifest = queue.replace_module_manifest(
            module_prefixes=[
                "silver/category=event_mapping/module=containers/",
                "silver/category=event_mapping/module=administration/",
            ]
        )
        assert [entry.module for entry in manifest] == ["administration", "containers"]
        queue.mark_module_status(module="administration", status="listing")
        assert [entry.status for entry in queue.iter_module_manifest()][0] == "listing"

        queue.populate_from_discovery(
            storage_ctx=_Ctx(
                connection_type="EC2", folder_root="root", bucket_or_container="bucket"
            ),
            relative_prefix="silver/category=event_mapping/module=administration/",
            suffix=".parquet",
            partition_filters={"category": "event_mapping", "module": "administration"},
            minimum_age_days=3,
            utc_today=date(2026, 8, 27),
            raise_on_empty=False,
        )
        assert queue.queued_path_count() == 1
        assert queue.queued_path_count(module="administration") == 1
        queue.release_module_paths(module="administration")
        assert queue.queued_path_count(module="administration") == 0
    finally:
        queue.close()


def test_queue_keeps_same_date_cross_module_and_instance_partitions_independent(
    monkeypatch,
):
    import data_collection.audit_logs_modules.compact_silver_queue as queue_module

    monkeypatch.setattr(
        queue_module,
        "iter_managed_folder_paths",
        lambda *args, **kwargs: iter(
            [
                "/silver/category=event_mapping/module=administration/instance_name=alpha/year=2026/month=08/day=23/a.parquet",
                "/silver/category=event_mapping/module=administration/instance_name=beta/year=2026/month=08/day=23/b.parquet",
                "/silver/category=event_mapping/module=containers/instance_name=alpha/year=2026/month=08/day=23/c.parquet",
            ]
        ),
    )
    queue = CompactSilverQueue.create()
    try:
        summary = queue.populate_from_discovery(
            storage_ctx=_Ctx(
                connection_type="EC2", folder_root="root", bucket_or_container="bucket"
            ),
            relative_prefix="silver/category=event_mapping/",
            suffix=".parquet",
            partition_filters={"category": "event_mapping"},
            minimum_age_days=3,
            utc_today=date(2026, 8, 27),
        )
        batch = queue.next_partition_batch(batch_size=10)
        assert summary.eligible_partition_count == 3
        assert [
            partition.partition_scope for partition in batch.selected_partitions
        ] == [
            "category=event_mapping; module=administration; instance_name=alpha; date=2026/08/23",
            "category=event_mapping; module=administration; instance_name=beta; date=2026/08/23",
            "category=event_mapping; module=containers; instance_name=alpha; date=2026/08/23",
        ]
    finally:
        queue.close()
