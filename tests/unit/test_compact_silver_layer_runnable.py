from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_runnable_module():
    path = Path(__file__).resolve().parents[2] / "python-runnables" / "compact-silver-layer" / "runnable.py"
    spec = importlib.util.spec_from_file_location("compact_silver_layer_runnable", path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _storage_context(connection_type: str) -> SimpleNamespace:
    return SimpleNamespace(
        folder_id="resolved-folder-id",
        connection_name="resolved-connection",
        connection_type=connection_type,
        folder_lookup="partitioned_data",
        folder_root="redacted/root",
        bucket_or_container="secret-bucket",
        blob_header="s3",
        cached_connection_info={
            "type": connection_type,
            "params": {"secretKey": "top-secret", "accessKey": "also-secret"},
        },
    )


def _selected_partition(*, day: str) -> SimpleNamespace:
    relative_base = f"/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day={day}"
    full_base = f"bucket/root/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day={day}"
    records = [
        SimpleNamespace(
            relative_path=f"{relative_base}/source-a.parquet",
            full_path=f"{full_base}/source-a.parquet",
            base_name="source-a.parquet",
            layer="silver",
            category="event_mapping",
            module="administration",
            instance_name="mazzei_pulse",
            year="2026",
            month="08",
            day=day,
        ),
        SimpleNamespace(
            relative_path=f"{relative_base}/source-b.parquet",
            full_path=f"{full_base}/source-b.parquet",
            base_name="source-b.parquet",
            layer="silver",
            category="event_mapping",
            module="administration",
            instance_name="mazzei_pulse",
            year="2026",
            month="08",
            day=day,
        ),
    ]
    return SimpleNamespace(
        year="2026",
        month="08",
        day=day,
        selected_records=records,
        full_paths=[record.full_path for record in records],
        relative_paths=[record.relative_path for record in records],
    )


def _selected_partition_batch() -> SimpleNamespace:
    return SimpleNamespace(
        total_matched_paths=11,
        filtered_matching_paths=8,
        skipped_compact_outputs=1,
        excluded_recent_paths=3,
        eligible_paths=4,
        cutoff_date=date(2026, 8, 24),
        minimum_age_days=3,
        selected_partitions=[_selected_partition(day="23"), _selected_partition(day="22")],
    )


def _outcome(*, day: str, status: str = "succeeded", replay_mode: str = "generic_compaction") -> SimpleNamespace:
    return SimpleNamespace(
        year="2026",
        month="08",
        day=day,
        day_scope=f"2026/08/{day}",
        replay_mode=replay_mode,
        status=status,
        message="ok" if status == "succeeded" else "current mapper produced no output",
        run_epoch_ms=1786510805000 + int(day),
        files_read=2,
        raw_rows=4,
        rows_after_drop_duplicates=3,
        output_column_count=5,
        input_rows=3,
        input_columns=5,
        plan_count=1 if status == "succeeded" else 0,
        rehydrated_rows=3 if replay_mode == "event_mapping_replay" else None,
        rehydrated_columns=47 if replay_mode == "event_mapping_replay" else None,
        mapper_rows=3 if replay_mode == "event_mapping_replay" and status == "succeeded" else 0 if replay_mode == "event_mapping_replay" else None,
        mapper_columns=13 if replay_mode == "event_mapping_replay" else None,
        mapper_groups=1 if replay_mode == "event_mapping_replay" and status == "succeeded" else 0,
        metrics=() if status != "succeeded" else (SimpleNamespace(module_name="administration", rows=3, columns=5, dq_ok=True, dq_errors=()),),
        written_count=1 if status == "succeeded" else 0,
        verified_count=1 if status == "succeeded" else 0,
        deleted_count=2 if status == "succeeded" else 0,
        retained_count=0 if status == "succeeded" else 2,
    )


def test_run_sequential_uses_two_partition_selector_and_processes_both_days(monkeypatch):
    module = _load_runnable_module()
    seen: dict[str, object] = {"suppressed": 0}

    monkeypatch.setattr(module, "suppress_inherited_provider_debug_logging", lambda: seen.__setitem__("suppressed", 1))
    monkeypatch.setattr(module, "build_storage_context", lambda **kwargs: _storage_context("EC2"))

    def fake_selector(storage_ctx, *, relative_prefix, suffix=None, partition_filters, partition_count, minimum_age_days, utc_today=None):
        seen["selector_args"] = (relative_prefix, suffix, partition_filters, partition_count, minimum_age_days)
        return _selected_partition_batch()

    def fake_run_jobs(*, storage_ctx, target, selected_partitions, normalize_silver_mode, do_parallel, n_jobs, batch_size):
        seen["run_jobs"] = {
            "count": len(selected_partitions),
            "normalize_silver_mode": normalize_silver_mode,
            "do_parallel": do_parallel,
            "n_jobs": n_jobs,
            "batch_size": batch_size,
            "days": [(item.year, item.month, item.day) for item in selected_partitions],
        }
        return [_outcome(day="23"), _outcome(day="22")]

    monkeypatch.setattr(module, "select_latest_partition_paths_batch", fake_selector)
    monkeypatch.setattr(module, "_run_partition_jobs", fake_run_jobs)

    runnable = module.MyRunnable("DASHBOARD_PROJECT", {}, {"pulse_primary": {"do_parallel": False, "cores": 3, "batch_size": 25}})
    result = runnable.run(progress_callback=None)

    assert seen["suppressed"] == 1
    assert seen["selector_args"] == (
        "silver/category=event_mapping/",
        ".parquet",
        {
            "category": "event_mapping",
            "module": "administration",
            "instance_name": "mazzei_pulse",
        },
        2,
        3,
    )
    assert seen["run_jobs"] == {
        "count": 2,
        "normalize_silver_mode": False,
        "do_parallel": False,
        "n_jobs": 3,
        "batch_size": 25,
        "days": [("2026", "08", "23"), ("2026", "08", "22")],
    }
    assert [record[0] for record in result.records[:9]] == [
        "Resolve Folder",
        "Connection Name",
        "Connection Type",
        "All Parquet Found",
        "Full DataFrame",
        "Filtered Subset",
        "Recent Partitions Excluded",
        "Eligible Subset",
        "Selected Partitions",
    ]
    assert result.records[8] == [
        "Selected Partitions",
        "2",
        "category=event_mapping; module=administration; instance_name=mazzei_pulse",
        "info",
        "newest to oldest: 2026/08/23, 2026/08/22",
    ]
    assert result.records[-1] == [
        "Partition Totals",
        "partitions=2",
        "category=event_mapping; module=administration; instance_name=mazzei_pulse",
        "success",
        "written=2; verified=2; deleted=4; retained=0",
    ]


def test_run_parallel_uses_joblib_threads_with_configured_workers(monkeypatch):
    module = _load_runnable_module()
    seen: dict[str, object] = {}
    selected_batch = _selected_partition_batch()

    monkeypatch.setattr(module, "build_storage_context", lambda **kwargs: _storage_context("EC2"))
    monkeypatch.setattr(module, "select_latest_partition_paths_batch", lambda *args, **kwargs: selected_batch)
    monkeypatch.setattr(module, "suppress_inherited_provider_debug_logging", lambda: None)

    class FakeParallel:
        def __init__(self, *, n_jobs, prefer):
            seen["parallel_init"] = {"n_jobs": n_jobs, "prefer": prefer}

        def __call__(self, tasks):
            task_list = list(tasks)
            seen["parallel_tasks"] = len(task_list)
            return [task() for task in task_list]

    monkeypatch.setattr(module, "Parallel", FakeParallel)
    monkeypatch.setattr(module, "delayed", lambda fn: (lambda **kwargs: (lambda: fn(**kwargs))))
    monkeypatch.setattr(
        module,
        "process_compact_selected_partition",
        lambda **kwargs: _outcome(day=kwargs["selected_partition"].day),
    )

    runnable = module.MyRunnable("P", {}, {"pulse_primary": {"do_parallel": True, "cores": 2, "batch_size": 25}})
    result = runnable.run(progress_callback=None)

    assert seen["parallel_init"] == {"n_jobs": 2, "prefer": "threads"}
    assert seen["parallel_tasks"] == 2
    assert result.records[-1][3] == "success"


def test_run_partition_jobs_sequential_branch_processes_both_days(monkeypatch):
    module = _load_runnable_module()
    seen = []

    monkeypatch.setattr(
        module,
        "_process_partition_job",
        lambda **kwargs: seen.append(kwargs["selected_partition"].day) or _outcome(day=kwargs["selected_partition"].day),
    )

    outcomes = module._run_partition_jobs(
        storage_ctx=object(),
        target=object(),
        selected_partitions=[_selected_partition(day="23"), _selected_partition(day="22")],
        normalize_silver_mode=False,
        do_parallel=False,
        n_jobs=4,
        batch_size=25,
    )

    assert seen == ["23", "22"]
    assert [outcome.day for outcome in outcomes] == ["23", "22"]


def test_parallel_day_failures_remain_isolated_and_aggregate_partial(monkeypatch):
    module = _load_runnable_module()
    monkeypatch.setattr(module, "suppress_inherited_provider_debug_logging", lambda: None)
    monkeypatch.setattr(module, "build_storage_context", lambda **kwargs: _storage_context("EC2"))
    monkeypatch.setattr(module, "select_latest_partition_paths_batch", lambda *args, **kwargs: _selected_partition_batch())
    monkeypatch.setattr(
        module,
        "_run_partition_jobs",
        lambda **kwargs: [
            _outcome(day="23", status="no_mapped_output_retained", replay_mode="event_mapping_replay"),
            _outcome(day="22", status="succeeded", replay_mode="event_mapping_replay"),
        ],
    )

    runnable = module.MyRunnable("P", {"normalize_silver": True}, {"pulse_primary": {"do_parallel": True, "cores": 2, "batch_size": 25}})
    result = runnable.run(progress_callback=None)

    rendered_values = {str(value) for row in result.records for value in row}
    assert "bucket/root/silver/category=event_mapping" not in rendered_values
    assert "source-a.parquet" not in rendered_values
    assert "source-b.parquet" not in rendered_values
    assert result.records[-1] == [
        "Partition Totals",
        "partitions=2",
        "category=event_mapping; module=administration; instance_name=mazzei_pulse",
        "partial",
        "written=1; verified=1; deleted=2; retained=2",
    ]


def test_all_recent_matches_fail_before_worker_execution(monkeypatch):
    module = _load_runnable_module()
    monkeypatch.setattr(module, "suppress_inherited_provider_debug_logging", lambda: None)
    monkeypatch.setattr(module, "build_storage_context", lambda **kwargs: _storage_context("EC2"))
    monkeypatch.setattr(
        module,
        "select_latest_partition_paths_batch",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("All exact-filter matches are excluded by minimum_age_days=3; cutoff_date=2026-08-24")),
    )
    monkeypatch.setattr(module, "_run_partition_jobs", lambda **kwargs: (_ for _ in ()).throw(AssertionError("workers must not be called")))

    with pytest.raises(ValueError, match="All exact-filter matches are excluded by minimum_age_days=3; cutoff_date=2026-08-24"):
        module._build_result_table(
            project_key="PROJ",
            folder_lookup="partitioned_data",
            normalize_silver_mode=False,
            do_parallel=False,
            n_jobs=1,
            batch_size=25,
        )


def test_unknown_provider_is_visibly_unsupported(monkeypatch):
    module = _load_runnable_module()
    monkeypatch.setattr(module, "suppress_inherited_provider_debug_logging", lambda: None)
    monkeypatch.setattr(module, "build_storage_context", lambda **kwargs: _storage_context("LocalFS"))
    monkeypatch.setattr(module, "select_latest_partition_paths_batch", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("Unsupported managed-folder provider for native discovery: LocalFS")))

    with pytest.raises(RuntimeError, match="Unsupported managed-folder provider"):
        module._build_result_table(
            project_key="PROJ",
            folder_lookup="partitioned_data",
            normalize_silver_mode=False,
            do_parallel=False,
            n_jobs=1,
            batch_size=25,
        )


def test_runnable_results_do_not_render_paths_or_secret_values(monkeypatch):
    module = _load_runnable_module()
    monkeypatch.setattr(module, "suppress_inherited_provider_debug_logging", lambda: None)
    monkeypatch.setattr(module, "build_storage_context", lambda **kwargs: _storage_context("EC2"))
    monkeypatch.setattr(module, "select_latest_partition_paths_batch", lambda *args, **kwargs: _selected_partition_batch())
    monkeypatch.setattr(module, "_run_partition_jobs", lambda **kwargs: [_outcome(day="23"), _outcome(day="22")])

    result = module._build_result_table(
        project_key="PROJ",
        folder_lookup="partitioned_data",
        normalize_silver_mode=False,
        do_parallel=False,
        n_jobs=1,
        batch_size=25,
    )

    rendered_values = {str(value) for row in result.records for value in row}
    assert "secret-bucket" not in rendered_values
    assert "redacted/root" not in rendered_values
    assert "top-secret" not in rendered_values
    assert "also-secret" not in rendered_values
    assert "bucket/root/silver/category=event_mapping" not in rendered_values
    assert "source-a.parquet" not in rendered_values
    assert "source-b.parquet" not in rendered_values
    assert "boto3" not in module.__dict__
    assert "azure.storage.blob" not in sys.modules
    assert "google.cloud" not in sys.modules
