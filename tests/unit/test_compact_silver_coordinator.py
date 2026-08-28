from __future__ import annotations

from types import SimpleNamespace

import pytest

from data_collection.audit_logs_modules import compact_silver_coordinator as coordinator


def _selected_partition(day: str) -> SimpleNamespace:
    return SimpleNamespace(
        year="2026",
        month="08",
        day=day,
        relative_paths=[f"/silver/.../day={day}/source-a.parquet", f"/silver/.../day={day}/source-b.parquet"],
    )


def test_local_parallel_resolution_uses_preset_cores(monkeypatch):
    monkeypatch.setattr(coordinator.os, "cpu_count", lambda: 8)

    resolution = coordinator.resolve_worker_resolution(
        param_set={"do_parallel": True, "cores": 2},
        execution_environment="local",
    )

    assert resolution.resolution_source == "local_preset"
    assert resolution.parallel_enabled is True
    assert resolution.configured_cores == 2
    assert resolution.resolved_n_jobs == 2
    assert resolution.partition_cap == 2
    assert resolution.python_visible_cpu_count == 8


def test_local_sequential_resolution_retains_partition_cap(monkeypatch):
    monkeypatch.setattr(coordinator.os, "cpu_count", lambda: 8)

    resolution = coordinator.resolve_worker_resolution(
        param_set={"do_parallel": False, "cores": 2},
        execution_environment="local",
    )

    assert resolution.resolution_source == "local_preset"
    assert resolution.parallel_enabled is False
    assert resolution.configured_cores == 2
    assert resolution.resolved_n_jobs == 1
    assert resolution.partition_cap == 2


def test_container_resolution_overrides_preset_and_uses_visible_cpu_count(monkeypatch):
    monkeypatch.setattr(coordinator.os, "cpu_count", lambda: 8)

    resolution = coordinator.resolve_worker_resolution(
        param_set={"do_parallel": False, "cores": 2},
        execution_environment="container-name",
    )

    assert resolution.resolution_source == "container_auto"
    assert resolution.parallel_enabled is True
    assert resolution.configured_cores is None
    assert resolution.resolved_n_jobs == 7
    assert resolution.partition_cap == 7
    assert resolution.python_visible_cpu_count == 8


def test_container_resolution_handles_missing_or_one_cpu(monkeypatch):
    monkeypatch.setattr(coordinator.os, "cpu_count", lambda: None)
    resolution = coordinator.resolve_worker_resolution(param_set={}, execution_environment="container-name")
    assert resolution.resolved_n_jobs == 1
    assert resolution.partition_cap == 1

    monkeypatch.setattr(coordinator.os, "cpu_count", lambda: 1)
    resolution = coordinator.resolve_worker_resolution(param_set={}, execution_environment="container-name")
    assert resolution.resolved_n_jobs == 1
    assert resolution.partition_cap == 1


def test_run_partition_jobs_sequential_and_parallel_paths(monkeypatch):
    selected_partitions = [_selected_partition("23"), _selected_partition("22")]
    seen = {"processed": []}

    monkeypatch.setattr(
        coordinator,
        "_process_partition_job",
        lambda **kwargs: seen["processed"].append(kwargs["selected_partition"].day) or SimpleNamespace(status="succeeded", day=kwargs["selected_partition"].day),
    )

    execution_mode, outcomes = coordinator.run_partition_jobs(
        storage_ctx=object(),
        target=object(),
        selected_partitions=selected_partitions,
        normalize_silver_mode=False,
        do_parallel=False,
        n_jobs=3,
        batch_size=25,
    )

    assert execution_mode == "sequential"
    assert seen["processed"] == ["23", "22"]
    assert [outcome.day for outcome in outcomes] == ["23", "22"]

    seen_parallel = {}

    class FakeParallel:
        def __init__(self, *, n_jobs, prefer):
            seen_parallel["init"] = {"n_jobs": n_jobs, "prefer": prefer}

        def __call__(self, tasks):
            task_list = list(tasks)
            seen_parallel["task_count"] = len(task_list)
            return [task() for task in task_list]

    monkeypatch.setattr(coordinator, "Parallel", FakeParallel)
    monkeypatch.setattr(coordinator, "delayed", lambda fn: (lambda **kwargs: (lambda: fn(**kwargs))))

    execution_mode, outcomes = coordinator.run_partition_jobs(
        storage_ctx=object(),
        target=object(),
        selected_partitions=selected_partitions,
        normalize_silver_mode=False,
        do_parallel=True,
        n_jobs=2,
        batch_size=25,
    )

    assert execution_mode == "joblib_threads"
    assert seen_parallel == {"init": {"n_jobs": 2, "prefer": "threads"}, "task_count": 2}
    assert [outcome.day for outcome in outcomes] == ["23", "22"]


def test_run_compact_silver_uses_shared_selection_and_dispatch(monkeypatch):
    selected_batch = SimpleNamespace(filtered_matching_paths=4, selected_partitions=[_selected_partition("23"), _selected_partition("22")])
    storage_ctx = SimpleNamespace(connection_type="EC2")
    seen = {}

    monkeypatch.setattr(coordinator, "build_storage_context", lambda **kwargs: storage_ctx)
    monkeypatch.setattr(coordinator, "resolve_worker_resolution", lambda **kwargs: coordinator.WorkerResolution(execution_environment="local", resolution_source="local_preset", python_visible_cpu_count=8, configured_cores=2, parallel_enabled=True, resolved_n_jobs=2, partition_cap=2))

    def fake_selector(*args, **kwargs):
        seen["selector"] = kwargs
        return selected_batch

    monkeypatch.setattr(coordinator, "select_latest_partition_paths_batch", fake_selector)
    monkeypatch.setattr(coordinator, "run_partition_jobs", lambda **kwargs: ("sequential", [SimpleNamespace(status="succeeded"), SimpleNamespace(status="succeeded")]))

    result = coordinator.run_compact_silver(
        coordinator.CompactRunConfig(
            project_key="P",
            folder_lookup="partitioned_data",
            relative_prefix="silver/category=event_mapping/",
            partition_filters={"category": "event_mapping"},
            minimum_age_days=3,
            normalize_silver_mode=False,
            param_set={"do_parallel": True, "cores": 2},
            execution_environment="local",
            batch_size=25,
        )
    )

    assert seen["selector"]["partition_count"] == 2
    assert result.storage_ctx is storage_ctx
    assert result.selected_batch is selected_batch
    assert result.execution_mode == "sequential"
    assert result.provider_label == "AWS/S3"
    assert result.worker_resolution.partition_cap == 2


def test_run_compact_silver_recipe_mode_uses_all_eligible_selection_and_worker_sized_batches(monkeypatch):
    selected_batch = SimpleNamespace(filtered_matching_paths=6, selected_partitions=[_selected_partition("23"), _selected_partition("22"), _selected_partition("21")])
    storage_ctx = SimpleNamespace(connection_type="EC2")
    seen = {}

    monkeypatch.setattr(coordinator, "build_storage_context", lambda **kwargs: storage_ctx)
    monkeypatch.setattr(
        coordinator,
        "resolve_worker_resolution",
        lambda **kwargs: coordinator.WorkerResolution(
            execution_environment="container-name",
            resolution_source="container_auto",
            python_visible_cpu_count=8,
            configured_cores=None,
            parallel_enabled=True,
            resolved_n_jobs=7,
            partition_cap=7,
        ),
    )
    monkeypatch.setattr(
        coordinator,
        "select_all_eligible_partition_paths_batch",
        lambda *args, **kwargs: (seen.__setitem__("selector_kwargs", kwargs), selected_batch)[1],
    )

    def fake_run_partition_jobs(**kwargs):
        seen["run_partition_jobs"] = kwargs
        return "joblib_threads", [SimpleNamespace(status="succeeded") for _ in kwargs["selected_partitions"]]

    monkeypatch.setattr(coordinator, "run_partition_jobs", fake_run_partition_jobs)

    result = coordinator.run_compact_silver(
        coordinator.CompactRunConfig(
            project_key="P",
            folder_lookup="partitioned_data",
            relative_prefix="silver/category=event_mapping/",
            partition_filters={"category": "event_mapping"},
            minimum_age_days=3,
            normalize_silver_mode=False,
            param_set={"do_parallel": False, "cores": 2, "batch_size": 25},
            execution_environment="container-name",
            batch_size=25,
            selection_mode="all_eligible_filtered",
        )
    )

    assert seen["selector_kwargs"] == {
        "relative_prefix": "silver/category=event_mapping/",
        "suffix": ".parquet",
        "partition_filters": {"category": "event_mapping"},
        "minimum_age_days": 3,
    }
    assert seen["run_partition_jobs"]["batch_size"] == 7
    assert seen["run_partition_jobs"]["n_jobs"] == 7
    assert result.selection_mode == "all_eligible_filtered"
    assert result.dispatch_batch_size == 7


def test_run_compact_silver_local_sequential_recipe_mode_uses_partition_cap_for_batches(monkeypatch):
    selected_batch = SimpleNamespace(filtered_matching_paths=4, selected_partitions=[_selected_partition("23"), _selected_partition("22"), _selected_partition("21")])
    storage_ctx = SimpleNamespace(connection_type="EC2")
    seen = {}

    monkeypatch.setattr(coordinator, "build_storage_context", lambda **kwargs: storage_ctx)
    monkeypatch.setattr(
        coordinator,
        "resolve_worker_resolution",
        lambda **kwargs: coordinator.WorkerResolution(
            execution_environment="local",
            resolution_source="local_preset",
            python_visible_cpu_count=8,
            configured_cores=2,
            parallel_enabled=False,
            resolved_n_jobs=1,
            partition_cap=2,
        ),
    )
    monkeypatch.setattr(coordinator, "select_all_eligible_partition_paths_batch", lambda *args, **kwargs: selected_batch)

    def fake_run_partition_jobs(**kwargs):
        seen["run_partition_jobs"] = kwargs
        return "sequential", [SimpleNamespace(status="succeeded") for _ in kwargs["selected_partitions"]]

    monkeypatch.setattr(coordinator, "run_partition_jobs", fake_run_partition_jobs)

    result = coordinator.run_compact_silver(
        coordinator.CompactRunConfig(
            project_key="P",
            folder_lookup="partitioned_data",
            relative_prefix="silver/category=event_mapping/",
            partition_filters={"category": "event_mapping"},
            minimum_age_days=3,
            normalize_silver_mode=False,
            param_set={"do_parallel": False, "cores": 2, "batch_size": 25},
            execution_environment="local",
            batch_size=25,
            selection_mode="all_eligible_filtered",
        )
    )

    assert seen["run_partition_jobs"]["do_parallel"] is False
    assert seen["run_partition_jobs"]["n_jobs"] == 1
    assert seen["run_partition_jobs"]["batch_size"] == 2
    assert result.dispatch_batch_size == 2
