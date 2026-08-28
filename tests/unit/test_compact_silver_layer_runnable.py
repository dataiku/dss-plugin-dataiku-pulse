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


def _run_result(*, provider_label: str | None = "AWS/S3", connection_type: str = "EC2", statuses: tuple[str, str] = ("succeeded", "succeeded")) -> SimpleNamespace:
    storage_ctx = SimpleNamespace(
        folder_id="resolved-folder-id",
        connection_name="resolved-connection",
        connection_type=connection_type,
    )
    selected_batch = SimpleNamespace(
        total_matched_paths=11,
        filtered_matching_paths=8,
        skipped_compact_outputs=1,
        excluded_recent_paths=3,
        eligible_paths=4,
        cutoff_date=date(2026, 8, 24),
        minimum_age_days=3,
        selected_partitions=[
            SimpleNamespace(year="2026", month="08", day="23"),
            SimpleNamespace(year="2026", month="08", day="22"),
        ],
    )

    def _outcome(day: str, status: str, replay_mode: str = "generic_compaction") -> SimpleNamespace:
        return SimpleNamespace(
            day_scope=f"2026/08/{day}",
            replay_mode=replay_mode,
            status=status,
            files_read=2,
            raw_rows=4,
            rows_after_drop_duplicates=3,
            output_column_count=5,
            input_rows=3,
            input_columns=5,
            rehydrated_rows=3 if replay_mode == "event_mapping_replay" else None,
            rehydrated_columns=47 if replay_mode == "event_mapping_replay" else None,
            mapper_rows=3 if status == "succeeded" and replay_mode == "event_mapping_replay" else 0 if replay_mode == "event_mapping_replay" else None,
            mapper_columns=13 if replay_mode == "event_mapping_replay" else None,
            mapper_groups=1 if status == "succeeded" and replay_mode == "event_mapping_replay" else 0,
            plan_count=1 if status == "succeeded" else 0,
            metrics=() if status != "succeeded" else (SimpleNamespace(module_name="administration", rows=3, columns=5, dq_ok=True, dq_errors=()),),
            written_count=1 if status == "succeeded" else 0,
            verified_count=1 if status == "succeeded" else 0,
            deleted_count=2 if status == "succeeded" else 0,
            retained_count=0 if status == "succeeded" else 2,
            run_epoch_ms=1786510805000 + int(day),
            message="ok" if status == "succeeded" else "current mapper produced no output",
        )

    return SimpleNamespace(
        storage_ctx=storage_ctx,
        provider_label=provider_label,
        selected_batch=selected_batch,
        outcomes=[_outcome("23", statuses[0]), _outcome("22", statuses[1])],
        execution_mode="joblib_threads",
    )


def test_run_resolves_pulse_primary_and_calls_shared_coordinator(monkeypatch):
    module = _load_runnable_module()
    seen = {"suppressed": 0}

    monkeypatch.setattr(module, "suppress_inherited_provider_debug_logging", lambda: seen.__setitem__("suppressed", 1))

    def fake_run_compact(config):
        seen["config"] = config
        return _run_result()

    monkeypatch.setattr(module, "run_compact_silver", fake_run_compact)

    runnable = module.MyRunnable("DASHBOARD_PROJECT", {}, {"pulse_primary": {"do_parallel": False, "cores": 3, "batch_size": 25}})
    result = runnable.run(progress_callback=None)

    assert seen["suppressed"] == 1
    assert seen["config"].project_key == "DASHBOARD_PROJECT"
    assert seen["config"].folder_lookup == "partitioned_data"
    assert seen["config"].relative_prefix == "silver/category=event_mapping/"
    assert seen["config"].partition_filters == {
        "category": "event_mapping",
        "module": "administration",
        "instance_name": "mazzei_pulse",
    }
    assert seen["config"].selected_partition_count == 2
    assert seen["config"].minimum_age_days == 3
    assert seen["config"].normalize_silver_mode is False
    assert seen["config"].do_parallel is False
    assert seen["config"].n_jobs == 3
    assert seen["config"].batch_size == 25
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


def test_run_uses_event_mapping_mode_when_config_true(monkeypatch):
    module = _load_runnable_module()
    monkeypatch.setattr(module, "suppress_inherited_provider_debug_logging", lambda: None)
    monkeypatch.setattr(
        module,
        "run_compact_silver",
        lambda config: _run_result(statuses=("succeeded", "succeeded"), provider_label="AWS/S3", connection_type="EC2").__class__(
        ),
    )

    def fake_run_compact(config):
        return SimpleNamespace(
            storage_ctx=SimpleNamespace(folder_id="resolved-folder-id", connection_name="resolved-connection", connection_type="EC2"),
            provider_label="AWS/S3",
            selected_batch=SimpleNamespace(
                total_matched_paths=11,
                filtered_matching_paths=8,
                skipped_compact_outputs=1,
                excluded_recent_paths=3,
                eligible_paths=4,
                cutoff_date=date(2026, 8, 24),
                minimum_age_days=3,
                selected_partitions=[SimpleNamespace(year="2026", month="08", day="23"), SimpleNamespace(year="2026", month="08", day="22")],
            ),
            outcomes=[
                SimpleNamespace(
                    day_scope="2026/08/23",
                    replay_mode="event_mapping_replay",
                    status="succeeded",
                    files_read=2,
                    raw_rows=4,
                    rows_after_drop_duplicates=3,
                    output_column_count=5,
                    input_rows=3,
                    input_columns=5,
                    rehydrated_rows=10,
                    rehydrated_columns=47,
                    mapper_rows=10,
                    mapper_columns=13,
                    mapper_groups=2,
                    plan_count=1,
                    metrics=(SimpleNamespace(module_name="administration", rows=10, columns=13, dq_ok=True, dq_errors=()),),
                    written_count=1,
                    verified_count=1,
                    deleted_count=2,
                    retained_count=0,
                    run_epoch_ms=1786510805000,
                    message="ok",
                ),
                SimpleNamespace(
                    day_scope="2026/08/22",
                    replay_mode="event_mapping_replay",
                    status="succeeded",
                    files_read=2,
                    raw_rows=4,
                    rows_after_drop_duplicates=3,
                    output_column_count=5,
                    input_rows=3,
                    input_columns=5,
                    rehydrated_rows=10,
                    rehydrated_columns=47,
                    mapper_rows=10,
                    mapper_columns=13,
                    mapper_groups=2,
                    plan_count=1,
                    metrics=(SimpleNamespace(module_name="administration", rows=10, columns=13, dq_ok=True, dq_errors=()),),
                    written_count=1,
                    verified_count=1,
                    deleted_count=2,
                    retained_count=0,
                    run_epoch_ms=1786510805001,
                    message="ok",
                ),
            ],
            execution_mode="joblib_threads",
        )

    monkeypatch.setattr(module, "run_compact_silver", fake_run_compact)
    result = module.MyRunnable("P", {"normalize_silver": True}, {}).run(progress_callback=None)

    assert [record for record in result.records if record[0] == "Replay Mode"][0] == [
        "Replay Mode",
        "event_mapping_replay",
        "2026/08/23",
        "info",
        "run_epoch_ms=1786510805000; status=succeeded",
    ]
    assert [record for record in result.records if record[0] == "Rehydrated DataFrame"][0] == [
        "Rehydrated DataFrame",
        "rows=10, columns=47",
        "2026/08/23",
        "info",
        "SILVER extras unpacked",
    ]


def test_run_partial_outcomes_render_without_paths_or_secrets(monkeypatch):
    module = _load_runnable_module()
    monkeypatch.setattr(module, "suppress_inherited_provider_debug_logging", lambda: None)
    monkeypatch.setattr(module, "run_compact_silver", lambda config: _run_result(statuses=("no_mapped_output_retained", "succeeded")))

    result = module.MyRunnable("P", {"normalize_silver": True}, {"pulse_primary": {"do_parallel": True, "cores": 2, "batch_size": 25}}).run(progress_callback=None)

    rendered_values = {str(value) for row in result.records for value in row}
    assert "bucket/root/silver/category=event_mapping" not in rendered_values
    assert "source-a.parquet" not in rendered_values
    assert "source-b.parquet" not in rendered_values
    assert "top-secret" not in rendered_values
    assert "also-secret" not in rendered_values
    assert result.records[-1] == [
        "Partition Totals",
        "partitions=2",
        "category=event_mapping; module=administration; instance_name=mazzei_pulse",
        "partial",
        "written=1; verified=1; deleted=2; retained=2",
    ]


def test_all_recent_matches_fail_from_shared_coordinator(monkeypatch):
    module = _load_runnable_module()
    monkeypatch.setattr(module, "suppress_inherited_provider_debug_logging", lambda: None)
    monkeypatch.setattr(
        module,
        "run_compact_silver",
        lambda config: (_ for _ in ()).throw(ValueError("All exact-filter matches are excluded by minimum_age_days=3; cutoff_date=2026-08-24")),
    )

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
    monkeypatch.setattr(module, "run_compact_silver", lambda config: _run_result(provider_label=None, connection_type="LocalFS"))

    result = module._build_result_table(
        project_key="PROJ",
        folder_lookup="partitioned_data",
        normalize_silver_mode=False,
        do_parallel=False,
        n_jobs=1,
        batch_size=25,
    )

    assert result.records[2] == [
        "Connection Type",
        "unsupported",
        "partitioned_data",
        "unsupported",
        "raw DSS type: LocalFS",
    ]
