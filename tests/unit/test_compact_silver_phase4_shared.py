from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from data_collection.audit_logs_modules import event_mapping_replay as replay
import shared_storage_discovery as discovery


class _Ctx(SimpleNamespace):
    pass


def _selected_records() -> list[discovery.SelectedPathRecord]:
    return [
        discovery.SelectedPathRecord(
            relative_path="/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/source-a.parquet",
            full_path="bucket/root/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/source-a.parquet",
            base_name="source-a.parquet",
            layer="silver",
            category="event_mapping",
            module="administration",
            instance_name="mazzei_pulse",
            year="2026",
            month="08",
            day="23",
        ),
        discovery.SelectedPathRecord(
            relative_path="/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/source-b.parquet",
            full_path="bucket/root/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/source-b.parquet",
            base_name="source-b.parquet",
            layer="silver",
            category="event_mapping",
            module="administration",
            instance_name="mazzei_pulse",
            year="2026",
            month="08",
            day="23",
        ),
    ]


def test_selected_source_records_preserve_aligned_relative_and_full_paths(monkeypatch):
    monkeypatch.setattr(
        discovery,
        "iter_managed_folder_paths",
        lambda *args, **kwargs: iter([
            "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/source-b.parquet",
            "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/compact_silver-1786510805000-0001.parquet",
            "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/source-a.parquet",
        ]),
    )

    selected = discovery.select_latest_partition_paths(
        _Ctx(connection_type="EC2", folder_root="root", bucket_or_container="bucket"),
        relative_prefix="silver/category=event_mapping/",
        suffix=".parquet",
        partition_filters={
            "category": "event_mapping",
            "module": "administration",
            "instance_name": "mazzei_pulse",
        },
        minimum_age_days=3,
        utc_today=date(2026, 8, 27),
    )

    assert selected.skipped_compact_outputs == 1
    assert selected.relative_paths == [
        "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/source-a.parquet",
        "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/source-b.parquet",
    ]
    assert selected.full_paths == [
        "bucket/root/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/source-a.parquet",
        "bucket/root/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/source-b.parquet",
    ]


def test_compact_filename_uses_one_epoch_and_ordered_suffixes():
    first = replay.build_compact_replacement_filename(run_epoch_ms=1786510805000, sequence_number=1)
    second = replay.build_compact_replacement_filename(run_epoch_ms=1786510805000, sequence_number=2)

    assert first == "compact_silver-1786510805000-0001.parquet"
    assert second == "compact_silver-1786510805000-0002.parquet"


def test_mixed_instance_selected_records_fail_before_planning():
    records = _selected_records()
    records[1] = discovery.SelectedPathRecord(
        relative_path="/silver/category=event_mapping/module=administration/instance_name=other/year=2026/month=08/day=23/source-b.parquet",
        full_path="bucket/root/silver/category=event_mapping/module=administration/instance_name=other/year=2026/month=08/day=23/source-b.parquet",
        base_name="source-b.parquet",
        layer="silver",
        category="event_mapping",
        module="administration",
        instance_name="other",
        year="2026",
        month="08",
        day="23",
    )

    with pytest.raises(replay.ReplaySkipError, match="multiple logical compact partitions"):
        replay.plan_compact_selected_day(
            selected_records=records,
            selected_df=pd.DataFrame([{"a": 1}]),
            normalize_silver_mode=False,
            run_epoch_ms=1786510805000,
        )


def test_generic_compaction_mode_builds_one_dq_checked_plan(monkeypatch):
    df = pd.DataFrame([{"instance_name": "mazzei_pulse", "run_ts": "2026-08-23T00:00:00Z", "value": 1}])
    monkeypatch.setattr(replay, "check_silver_dq", lambda incoming_df: replay.DQResult(ok=True, errors=[]))

    plans, summary = replay.plan_compact_selected_day(
        selected_records=_selected_records(),
        selected_df=df,
        normalize_silver_mode=False,
        run_epoch_ms=1786510805000,
    )

    assert summary.mode == "generic_compaction"
    assert summary.input_rows == 1
    assert len(plans) == 1
    assert str(plans[0].output_path).endswith("/compact_silver-1786510805000-0001.parquet")
    assert plans[0].dq.ok is True


def test_event_mapping_mode_rehydrates_maps_normalizes_and_reports_shapes(monkeypatch):
    source_df = pd.DataFrame([{"instance_name": "mazzei_pulse", "run_ts": "2026-08-23T00:00:00Z", "msgtype": "LOGIN", "extras": '{"topic":"generic","field":"value"}'}])
    monkeypatch.setattr(replay, "rehydrate_event_mapping_source", lambda df: pd.DataFrame([{"message_msgType": "LOGIN", "field": "value"}]))
    monkeypatch.setattr(replay.event_mapping, "main", lambda df: pd.DataFrame([{"dataiku_category": "administration", "timestamp": "2026-08-23T12:00:00Z", "field": "value"}]))
    monkeypatch.setattr(replay, "normalize_silver", lambda **kwargs: pd.DataFrame([{"instance_name": "mazzei_pulse", "run_ts": kwargs["run_ts"], "extras": "{}"}]))
    monkeypatch.setattr(replay, "check_silver_dq", lambda df: replay.DQResult(ok=True, errors=[]))

    plans, summary = replay.plan_compact_selected_day(
        selected_records=_selected_records(),
        selected_df=source_df,
        normalize_silver_mode=True,
        run_epoch_ms=1786510805000,
    )

    assert summary.mode == "event_mapping_replay"
    assert summary.rehydrated_rows == 1
    assert summary.mapper_rows == 1
    assert summary.mapper_groups == 1
    assert len(plans) == 1
    assert str(plans[0].output_path).endswith("/compact_silver-1786510805000-0001.parquet")


def test_zero_mapped_output_retains_all_sources_without_uploads_or_deletes(monkeypatch):
    uploads = []
    verifications = []
    deletions = []

    monkeypatch.setattr(replay, "upload_parquet", lambda **kwargs: uploads.append(kwargs))
    monkeypatch.setattr(replay, "verify_managed_folder_file", lambda folder, path: verifications.append(path))
    monkeypatch.setattr(replay, "delete_managed_folder_file", lambda folder, path: deletions.append(path))

    result = replay.apply_compact_replacement_plans(
        target=object(),
        folder=object(),
        source_relative_paths=[record.relative_path for record in _selected_records()],
        plans=[],
    )

    assert result.status == "no_mapped_output_retained"
    assert result.written_paths == ()
    assert result.verified_paths == ()
    assert result.deleted_source_paths == ()
    assert result.retained_source_paths == tuple(record.relative_path for record in _selected_records())
    assert "produced no replacement output" in result.message
    assert "written=0, verified=0, deleted=0, retained=2" in result.message
    assert uploads == []
    assert verifications == []
    assert deletions == []


def test_dq_failure_means_zero_uploads_and_zero_source_deletion(monkeypatch):
    folder = object()
    target = object()
    plan = replay.ReplayWritePlan(
        output_path=Path("/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/compact_silver-1786510805000-0001.parquet"),
        silver_df=pd.DataFrame([{"a": 1}]),
        dq=replay.DQResult(ok=False, errors=["bad"]),
        module_name="administration",
        event_date=date(2026, 8, 23),
    )
    uploads = []
    monkeypatch.setattr(replay, "upload_parquet", lambda **kwargs: uploads.append(kwargs))
    monkeypatch.setattr(replay, "delete_managed_folder_file", lambda folder, path: (_ for _ in ()).throw(AssertionError("source delete must not run")))

    result = replay.apply_compact_replacement_plans(
        target=target,
        folder=folder,
        source_relative_paths=[record.relative_path for record in _selected_records()],
        plans=[plan],
    )

    assert result.status == "dq_failed"
    assert uploads == []
    assert result.retained_source_paths == tuple(record.relative_path for record in _selected_records())


def test_upload_or_verification_failure_retains_sources_and_cleans_new_outputs(monkeypatch):
    folder = object()
    target = object()
    plan = replay.ReplayWritePlan(
        output_path=Path("/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/compact_silver-1786510805000-0001.parquet"),
        silver_df=pd.DataFrame([{"a": 1}]),
        dq=replay.DQResult(ok=True, errors=[]),
        module_name="administration",
        event_date=date(2026, 8, 23),
    )
    monkeypatch.setattr(replay, "upload_event_mapping_replacements", lambda **kwargs: replay.ReplacementUploadResult(status="uploaded", written_paths=(str(plan.output_path),), message="ok"))
    monkeypatch.setattr(replay, "verify_managed_folder_file", lambda folder, path: (_ for _ in ()).throw(RuntimeError("verify boom")))
    monkeypatch.setattr(replay, "cleanup_written_replacements", lambda folder, paths: (tuple(paths), []))

    result = replay.apply_compact_replacement_plans(
        target=target,
        folder=folder,
        source_relative_paths=[record.relative_path for record in _selected_records()],
        plans=[plan],
    )

    assert result.status == "verification_failed_cleaned"
    assert result.cleanup_paths == (str(plan.output_path),)
    assert result.retained_source_paths == tuple(record.relative_path for record in _selected_records())


def test_all_verified_writes_occur_before_any_relative_source_deletion(monkeypatch):
    events: list[tuple[str, str]] = []
    target = object()
    folder = object()
    plans = [
        replay.ReplayWritePlan(
            output_path=Path("/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/compact_silver-1786510805000-0001.parquet"),
            silver_df=pd.DataFrame([{"a": 1}]),
            dq=replay.DQResult(ok=True, errors=[]),
            module_name="administration",
            event_date=date(2026, 8, 23),
        ),
        replay.ReplayWritePlan(
            output_path=Path("/silver/category=event_mapping/module=dataset/instance_name=mazzei_pulse/year=2026/month=08/day=23/compact_silver-1786510805000-0002.parquet"),
            silver_df=pd.DataFrame([{"a": 1}]),
            dq=replay.DQResult(ok=True, errors=[]),
            module_name="dataset",
            event_date=date(2026, 8, 23),
        ),
    ]

    monkeypatch.setattr(replay, "upload_event_mapping_replacements", lambda **kwargs: replay.ReplacementUploadResult(status="uploaded", written_paths=tuple(str(plan.output_path) for plan in plans), message="ok"))
    monkeypatch.setattr(replay, "verify_managed_folder_file", lambda folder, path: events.append(("verify", path)))
    monkeypatch.setattr(replay, "delete_managed_folder_file", lambda folder, path: events.append(("delete", path)))

    result = replay.apply_compact_replacement_plans(
        target=target,
        folder=folder,
        source_relative_paths=[record.relative_path for record in _selected_records()],
        plans=plans,
    )

    assert result.status == "succeeded"
    assert events == [
        ("verify", str(plans[0].output_path)),
        ("verify", str(plans[1].output_path)),
        ("delete", _selected_records()[0].relative_path),
        ("delete", _selected_records()[1].relative_path),
    ]


def test_source_deletion_failure_is_distinct_and_stops_additional_uncertain_deletes(monkeypatch):
    events: list[tuple[str, str]] = []
    target = object()
    folder = object()
    plan = replay.ReplayWritePlan(
        output_path=Path("/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/compact_silver-1786510805000-0001.parquet"),
        silver_df=pd.DataFrame([{"a": 1}]),
        dq=replay.DQResult(ok=True, errors=[]),
        module_name="administration",
        event_date=date(2026, 8, 23),
    )

    monkeypatch.setattr(replay, "upload_event_mapping_replacements", lambda **kwargs: replay.ReplacementUploadResult(status="uploaded", written_paths=(str(plan.output_path),), message="ok"))
    monkeypatch.setattr(replay, "verify_managed_folder_file", lambda folder, path: events.append(("verify", path)))

    def delete_side_effect(folder, path):
        events.append(("delete", path))
        raise RuntimeError("cannot delete")

    monkeypatch.setattr(replay, "delete_managed_folder_file", delete_side_effect)

    result = replay.apply_compact_replacement_plans(
        target=target,
        folder=folder,
        source_relative_paths=[record.relative_path for record in _selected_records()],
        plans=[plan],
    )

    assert result.status == "delete_failed"
    assert result.deleted_source_paths == ()
    assert result.retained_source_paths == tuple(record.relative_path for record in _selected_records())
    assert events == [
        ("verify", str(plan.output_path)),
        ("delete", _selected_records()[0].relative_path),
    ]


def test_process_compact_selected_partition_isolates_one_day_and_returns_counts(monkeypatch):
    selected_partition = SimpleNamespace(
        year="2026",
        month="08",
        day="23",
        selected_records=_selected_records(),
        full_paths=[record.full_path for record in _selected_records()],
        relative_paths=[record.relative_path for record in _selected_records()],
    )

    monkeypatch.setattr(
        replay,
        "_stage_compact_s3_partition",
        lambda **kwargs: replay.CompactStageResult(
            database_path=kwargs["database_path"],
            files_read=2,
            raw_rows=3,
            rows_after_drop_duplicates=2,
            output_column_count=1,
        ),
    )
    monkeypatch.setattr(replay, "next_compact_save_epoch_ms", lambda: 1786510805000)
    monkeypatch.setattr(
        replay,
        "process_staged_compact_partition",
        lambda **kwargs: replay.CompactProcessingResult(
            plan_summary=replay.CompactPlanSummary(
                mode="generic_compaction",
                run_epoch_ms=1786510805000,
                input_rows=2,
                input_columns=1,
                metrics=(replay.CompactPlanMetric(module_name="administration", rows=2, columns=1, dq_ok=True, dq_errors=()),),
            ),
            apply_result=replay.CompactApplyResult(
                status="succeeded",
                written_paths=("/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/compact_silver-1786510805000-0001.parquet",),
                verified_paths=("/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/compact_silver-1786510805000-0001.parquet",),
                deleted_source_paths=tuple(selected_partition.relative_paths),
                message="ok",
            ),
            plan_count=1,
        ),
    )

    outcome = replay.process_compact_selected_partition(
        storage_ctx=object(),
        target=object(),
        selected_partition=selected_partition,
        normalize_silver_mode=False,
    )

    assert outcome.day_scope == "2026/08/23"
    assert outcome.status == "succeeded"
    assert outcome.files_read == 2
    assert outcome.raw_rows == 3
    assert outcome.rows_after_drop_duplicates == 2
    assert outcome.written_count == 1
    assert outcome.deleted_count == 2


def test_same_day_different_instances_process_independently_with_instance_only_outputs_and_deletes(monkeypatch):
    alpha_records = [
        discovery.SelectedPathRecord(
            relative_path="/silver/category=event_mapping/module=administration/instance_name=alpha/year=2026/month=08/day=23/source-a.parquet",
            full_path="bucket/root/silver/category=event_mapping/module=administration/instance_name=alpha/year=2026/month=08/day=23/source-a.parquet",
            base_name="source-a.parquet",
            layer="silver",
            category="event_mapping",
            module="administration",
            instance_name="alpha",
            year="2026",
            month="08",
            day="23",
        )
    ]
    beta_records = [
        discovery.SelectedPathRecord(
            relative_path="/silver/category=event_mapping/module=administration/instance_name=beta/year=2026/month=08/day=23/source-b.parquet",
            full_path="bucket/root/silver/category=event_mapping/module=administration/instance_name=beta/year=2026/month=08/day=23/source-b.parquet",
            base_name="source-b.parquet",
            layer="silver",
            category="event_mapping",
            module="administration",
            instance_name="beta",
            year="2026",
            month="08",
            day="23",
        )
    ]
    alpha_partition = discovery.SelectedPartitionPaths(
        category="event_mapping",
        module="administration",
        instance_name="alpha",
        year="2026",
        month="08",
        day="23",
        selected_records=alpha_records,
    )
    beta_partition = discovery.SelectedPartitionPaths(
        category="event_mapping",
        module="administration",
        instance_name="beta",
        year="2026",
        month="08",
        day="23",
        selected_records=beta_records,
    )

    observed_stages: list[list[str]] = []
    observed_plans: list[tuple[str, str]] = []
    observed_deletes: list[list[str]] = []

    def fake_stage(**kwargs):
        observed_stages.append([record.full_path for record in kwargs["selected_partition"].selected_records])
        return replay.CompactStageResult(
            database_path=kwargs["database_path"],
            files_read=len(kwargs["selected_partition"].selected_records),
            raw_rows=1,
            rows_after_drop_duplicates=1,
            output_column_count=1,
        )

    def fake_process(**kwargs):
        instance_name = kwargs["selected_records"][0].instance_name
        output_path = f"/silver/category=event_mapping/module=administration/instance_name={instance_name}/year=2026/month=08/day=23/compact_silver-{kwargs['run_epoch_ms']}-0001.parquet"
        observed_plans.append((instance_name, output_path))
        observed_deletes.append(list(kwargs["source_relative_paths"]))
        return replay.CompactProcessingResult(
            plan_summary=replay.CompactPlanSummary(
                mode="generic_compaction",
                run_epoch_ms=kwargs["run_epoch_ms"],
                input_rows=1,
                input_columns=1,
                metrics=(replay.CompactPlanMetric(module_name="administration", rows=1, columns=1, dq_ok=True, dq_errors=()),),
            ),
            apply_result=replay.CompactApplyResult(
                status="succeeded",
                written_paths=(output_path,),
                verified_paths=(output_path,),
                deleted_source_paths=tuple(kwargs["source_relative_paths"]),
                message="ok",
            ),
            plan_count=1,
        )

    epochs = iter([1786510805000, 1786510805001])
    monkeypatch.setattr(replay, "_stage_compact_s3_partition", fake_stage)
    monkeypatch.setattr(replay, "next_compact_save_epoch_ms", lambda: next(epochs))
    monkeypatch.setattr(replay, "process_staged_compact_partition", fake_process)

    alpha_outcome = replay.process_compact_selected_partition(
        storage_ctx=object(),
        target=object(),
        selected_partition=alpha_partition,
        normalize_silver_mode=False,
    )
    beta_outcome = replay.process_compact_selected_partition(
        storage_ctx=object(),
        target=object(),
        selected_partition=beta_partition,
        normalize_silver_mode=False,
    )

    assert observed_stages == [
        ["bucket/root/silver/category=event_mapping/module=administration/instance_name=alpha/year=2026/month=08/day=23/source-a.parquet"],
        ["bucket/root/silver/category=event_mapping/module=administration/instance_name=beta/year=2026/month=08/day=23/source-b.parquet"],
    ]
    assert observed_plans == [
        ("alpha", "/silver/category=event_mapping/module=administration/instance_name=alpha/year=2026/month=08/day=23/compact_silver-1786510805000-0001.parquet"),
        ("beta", "/silver/category=event_mapping/module=administration/instance_name=beta/year=2026/month=08/day=23/compact_silver-1786510805001-0001.parquet"),
    ]
    assert observed_deletes == [
        ["/silver/category=event_mapping/module=administration/instance_name=alpha/year=2026/month=08/day=23/source-a.parquet"],
        ["/silver/category=event_mapping/module=administration/instance_name=beta/year=2026/month=08/day=23/source-b.parquet"],
    ]
    assert alpha_outcome.deleted_count == 1
    assert beta_outcome.deleted_count == 1


def test_same_day_different_source_modules_process_independently_with_isolated_reads_outputs_and_deletes(monkeypatch):
    admin_records = [
        discovery.SelectedPathRecord(
            relative_path="/silver/category=event_mapping/module=administration/instance_name=alpha/year=2026/month=08/day=23/source-a.parquet",
            full_path="bucket/root/silver/category=event_mapping/module=administration/instance_name=alpha/year=2026/month=08/day=23/source-a.parquet",
            base_name="source-a.parquet",
            layer="silver",
            category="event_mapping",
            module="administration",
            instance_name="alpha",
            year="2026",
            month="08",
            day="23",
        )
    ]
    containers_records = [
        discovery.SelectedPathRecord(
            relative_path="/silver/category=event_mapping/module=containers/instance_name=alpha/year=2026/month=08/day=23/source-b.parquet",
            full_path="bucket/root/silver/category=event_mapping/module=containers/instance_name=alpha/year=2026/month=08/day=23/source-b.parquet",
            base_name="source-b.parquet",
            layer="silver",
            category="event_mapping",
            module="containers",
            instance_name="alpha",
            year="2026",
            month="08",
            day="23",
        )
    ]
    admin_partition = discovery.SelectedPartitionPaths(
        category="event_mapping",
        module="administration",
        instance_name="alpha",
        year="2026",
        month="08",
        day="23",
        selected_records=admin_records,
    )
    containers_partition = discovery.SelectedPartitionPaths(
        category="event_mapping",
        module="containers",
        instance_name="alpha",
        year="2026",
        month="08",
        day="23",
        selected_records=containers_records,
    )

    observed_stages: list[list[str]] = []
    observed_plans: list[tuple[str, str]] = []
    observed_deletes: list[list[str]] = []

    def fake_stage(**kwargs):
        observed_stages.append([record.full_path for record in kwargs["selected_partition"].selected_records])
        return replay.CompactStageResult(
            database_path=kwargs["database_path"],
            files_read=len(kwargs["selected_partition"].selected_records),
            raw_rows=1,
            rows_after_drop_duplicates=1,
            output_column_count=1,
        )

    def fake_process(**kwargs):
        module_name = kwargs["selected_records"][0].module
        output_path = f"/silver/category=event_mapping/module=shared_mapped/instance_name=alpha/year=2026/month=08/day=23/compact_silver-{kwargs['run_epoch_ms']}-0001.parquet"
        observed_plans.append((module_name, output_path))
        observed_deletes.append(list(kwargs["source_relative_paths"]))
        return replay.CompactProcessingResult(
            plan_summary=replay.CompactPlanSummary(
                mode="event_mapping_replay",
                run_epoch_ms=kwargs["run_epoch_ms"],
                input_rows=1,
                input_columns=1,
                metrics=(replay.CompactPlanMetric(module_name="shared_mapped", rows=1, columns=1, dq_ok=True, dq_errors=()),),
            ),
            apply_result=replay.CompactApplyResult(
                status="succeeded",
                written_paths=(output_path,),
                verified_paths=(output_path,),
                deleted_source_paths=tuple(kwargs["source_relative_paths"]),
                message="ok",
            ),
            plan_count=1,
        )

    epochs = iter([1786510805000, 1786510805001])
    monkeypatch.setattr(replay, "_stage_compact_s3_partition", fake_stage)
    monkeypatch.setattr(replay, "next_compact_save_epoch_ms", lambda: next(epochs))
    monkeypatch.setattr(replay, "process_staged_compact_partition", fake_process)

    admin_outcome = replay.process_compact_selected_partition(
        storage_ctx=object(),
        target=object(),
        selected_partition=admin_partition,
        normalize_silver_mode=True,
    )
    containers_outcome = replay.process_compact_selected_partition(
        storage_ctx=object(),
        target=object(),
        selected_partition=containers_partition,
        normalize_silver_mode=True,
    )

    assert observed_stages == [
        ["bucket/root/silver/category=event_mapping/module=administration/instance_name=alpha/year=2026/month=08/day=23/source-a.parquet"],
        ["bucket/root/silver/category=event_mapping/module=containers/instance_name=alpha/year=2026/month=08/day=23/source-b.parquet"],
    ]
    assert observed_plans == [
        ("administration", "/silver/category=event_mapping/module=shared_mapped/instance_name=alpha/year=2026/month=08/day=23/compact_silver-1786510805000-0001.parquet"),
        ("containers", "/silver/category=event_mapping/module=shared_mapped/instance_name=alpha/year=2026/month=08/day=23/compact_silver-1786510805001-0001.parquet"),
    ]
    assert observed_plans[0][1] != observed_plans[1][1]
    assert observed_deletes == [
        ["/silver/category=event_mapping/module=administration/instance_name=alpha/year=2026/month=08/day=23/source-a.parquet"],
        ["/silver/category=event_mapping/module=containers/instance_name=alpha/year=2026/month=08/day=23/source-b.parquet"],
    ]
    assert admin_outcome.deleted_count == 1
    assert containers_outcome.deleted_count == 1


def _partition_with_count(count: int) -> discovery.SelectedPartitionPaths:
    records = [
        discovery.SelectedPathRecord(
            relative_path=f"/silver/category=event_mapping/module=folders/instance_name=alpha/year=2026/month=06/day=12/source-{index}.parquet",
            full_path=f"bucket/root/silver/category=event_mapping/module=folders/instance_name=alpha/year=2026/month=06/day=12/source-{index}.parquet",
            base_name=f"source-{index}.parquet",
            layer="silver",
            category="event_mapping",
            module="folders",
            instance_name="alpha",
            year="2026",
            month="06",
            day="12",
        )
        for index in range(count)
    ]
    return discovery.SelectedPartitionPaths(
        category="event_mapping",
        module="folders",
        instance_name="alpha",
        year="2026",
        month="06",
        day="12",
        selected_records=records,
    )


def test_staging_batches_s3_reads_refreshes_context_and_dedupes_across_batches(monkeypatch, tmp_path, caplog):
    partition = _partition_with_count(3)
    read_calls = []
    context_markers = []
    batches = [
        pd.DataFrame([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]),
        pd.DataFrame([{"a": 1, "b": "x"}]),
    ]

    def fake_read(storage_ctx, *, full_paths):
        context_markers.append(storage_ctx.marker)
        read_calls.append(list(full_paths))
        return batches[len(read_calls) - 1]

    factory_index = {"value": 0}

    def context_factory():
        factory_index["value"] += 1
        return SimpleNamespace(connection_type="EC2", marker=f"ctx-{factory_index['value']}")

    monkeypatch.setattr(replay, "read_s3_parquet_file_batch", fake_read)
    caplog.set_level("INFO", logger=replay.__name__)

    stage = replay._stage_compact_s3_partition(
        storage_ctx=SimpleNamespace(connection_type="EC2", marker="initial"),
        storage_ctx_factory=context_factory,
        selected_partition=partition,
        database_path=tmp_path / "stage.duckdb",
        s3_read_batch_size=2,
    )

    assert [len(call) for call in read_calls] == [2, 1]
    assert context_markers == ["ctx-1", "ctx-2"]
    assert stage.files_read == 3
    assert stage.raw_rows == 3
    assert stage.rows_after_drop_duplicates == 2
    con = replay.duckdb.connect(str(stage.database_path))
    try:
        deduped = con.execute("SELECT a, b FROM dedup_stage ORDER BY a").fetchall()
    finally:
        con.close()
    assert deduped == [(1, "x"), (2, "y")]
    assert "source-0.parquet" not in caplog.text
    assert "bucket/root" not in caplog.text
    assert "s3_read_batch_size=2" in caplog.text


def test_staging_second_batch_failure_retains_sources_and_writes_no_replacements(monkeypatch, tmp_path):
    partition = _partition_with_count(3)
    calls = []

    def fake_read(storage_ctx, *, full_paths):
        calls.append(list(full_paths))
        if len(calls) == 2:
            raise RuntimeError("s3 read failed")
        return pd.DataFrame([{"a": 1}])

    monkeypatch.setattr(replay, "read_s3_parquet_file_batch", fake_read)
    with pytest.raises(RuntimeError, match="s3 read failed"):
        replay.process_compact_selected_partition(
            storage_ctx=SimpleNamespace(connection_type="EC2"),
            target=object(),
            selected_partition=partition,
            normalize_silver_mode=False,
            s3_read_batch_size=2,
        )

    assert [len(call) for call in calls] == [2, 1]


def test_staged_processing_writes_verifies_all_outputs_before_deleting_sources(monkeypatch, tmp_path):
    partition = _partition_with_count(3)
    database_path = tmp_path / "stage.duckdb"
    con = replay.duckdb.connect(str(database_path))
    con.execute("CREATE TABLE dedup_stage AS SELECT * FROM (VALUES (0, 1, 'alpha', '2026-06-12T00:00:00Z'), (1, 2, 'alpha', '2026-06-12T00:00:00Z'), (2, 3, 'alpha', '2026-06-12T00:00:00Z')) AS t(__compact_row_order, a, instance_name, run_ts)")
    con.close()
    events = []
    uploads = []

    def fake_upload(**kwargs):
        uploads.append(str(kwargs["output_path"]))
        events.append(("upload", str(kwargs["output_path"])))
        return True

    monkeypatch.setattr(replay, "upload_parquet", fake_upload)
    monkeypatch.setattr(replay, "get_managed_folder_handle", lambda target: object())
    monkeypatch.setattr(replay, "verify_managed_folder_file", lambda folder, path: events.append(("verify", path)))
    monkeypatch.setattr(replay, "delete_managed_folder_file", lambda folder, path: events.append(("delete", path)))

    result = replay.process_staged_compact_partition(
        target=object(),
        selected_records=partition.selected_records,
        source_relative_paths=partition.relative_paths,
        database_path=database_path,
        normalize_silver_mode=False,
        run_epoch_ms=1786510805000,
        output_chunk_size=2,
    )

    assert result.apply_result.status == "succeeded"
    assert result.plan_count == 2
    assert uploads == [
        "/silver/category=event_mapping/module=folders/instance_name=alpha/year=2026/month=06/day=12/compact_silver-1786510805000-0001.parquet",
        "/silver/category=event_mapping/module=folders/instance_name=alpha/year=2026/month=06/day=12/compact_silver-1786510805000-0002.parquet",
    ]
    assert [event[0] for event in events] == ["upload", "upload", "verify", "verify", "delete", "delete", "delete"]


def test_staged_processing_upload_failure_cleans_new_outputs_and_retains_sources(monkeypatch, tmp_path):
    partition = _partition_with_count(3)
    database_path = tmp_path / "stage.duckdb"
    con = replay.duckdb.connect(str(database_path))
    con.execute("CREATE TABLE dedup_stage AS SELECT * FROM (VALUES (0, 1, 'alpha', '2026-06-12T00:00:00Z'), (1, 2, 'alpha', '2026-06-12T00:00:00Z'), (2, 3, 'alpha', '2026-06-12T00:00:00Z')) AS t(__compact_row_order, a, instance_name, run_ts)")
    con.close()
    events = []

    def fake_upload(**kwargs):
        events.append(("upload", str(kwargs["output_path"])))
        if len([event for event in events if event[0] == "upload"]) == 2:
            raise RuntimeError("upload failed")
        return True

    monkeypatch.setattr(replay, "upload_parquet", fake_upload)
    monkeypatch.setattr(replay, "get_managed_folder_handle", lambda target: object())
    monkeypatch.setattr(replay, "delete_managed_folder_file", lambda folder, path: events.append(("delete", path)))

    result = replay.process_staged_compact_partition(
        target=object(),
        selected_records=partition.selected_records,
        source_relative_paths=partition.relative_paths,
        database_path=database_path,
        normalize_silver_mode=False,
        run_epoch_ms=1786510805000,
        output_chunk_size=2,
    )

    assert result.apply_result.status == "upload_failed_cleaned"
    assert result.apply_result.retained_source_paths == tuple(partition.relative_paths)
    assert [event[0] for event in events] == ["upload", "upload", "delete"]


def test_staged_processing_dq_failure_cleans_new_outputs_and_retains_sources(monkeypatch, tmp_path):
    partition = _partition_with_count(2)
    database_path = tmp_path / "stage.duckdb"
    con = replay.duckdb.connect(str(database_path))
    con.execute("CREATE TABLE dedup_stage AS SELECT * FROM (VALUES (0, 1, 'alpha', '2026-06-12T00:00:00Z'), (1, 2, 'alpha', '2026-06-12T00:00:00Z')) AS t(__compact_row_order, a, instance_name, run_ts)")
    con.close()
    events = []
    dq_results = iter([
        replay.DQResult(ok=True, errors=[]),
        replay.DQResult(ok=False, errors=["bad"]),
    ])

    monkeypatch.setattr(replay, "check_silver_dq", lambda df: next(dq_results))
    monkeypatch.setattr(replay, "upload_parquet", lambda **kwargs: events.append(("upload", str(kwargs["output_path"]))) or True)
    monkeypatch.setattr(replay, "get_managed_folder_handle", lambda target: object())
    monkeypatch.setattr(replay, "delete_managed_folder_file", lambda folder, path: events.append(("delete", path)))

    result = replay.process_staged_compact_partition(
        target=object(),
        selected_records=partition.selected_records,
        source_relative_paths=partition.relative_paths,
        database_path=database_path,
        normalize_silver_mode=False,
        run_epoch_ms=1786510805000,
        output_chunk_size=1,
    )

    assert result.apply_result.status == "dq_failed_cleaned"
    assert result.apply_result.retained_source_paths == tuple(partition.relative_paths)
    assert [event[0] for event in events] == ["upload", "delete"]


def test_staged_processing_verification_failure_cleans_outputs_and_retains_sources(monkeypatch, tmp_path):
    partition = _partition_with_count(2)
    database_path = tmp_path / "stage.duckdb"
    con = replay.duckdb.connect(str(database_path))
    con.execute("CREATE TABLE dedup_stage AS SELECT * FROM (VALUES (0, 1, 'alpha', '2026-06-12T00:00:00Z'), (1, 2, 'alpha', '2026-06-12T00:00:00Z')) AS t(__compact_row_order, a, instance_name, run_ts)")
    con.close()
    events = []

    monkeypatch.setattr(replay, "upload_parquet", lambda **kwargs: events.append(("upload", str(kwargs["output_path"]))) or True)
    monkeypatch.setattr(replay, "get_managed_folder_handle", lambda target: object())
    monkeypatch.setattr(replay, "verify_managed_folder_file", lambda folder, path: (_ for _ in ()).throw(RuntimeError("verify failed")))
    monkeypatch.setattr(replay, "delete_managed_folder_file", lambda folder, path: events.append(("delete", path)))

    result = replay.process_staged_compact_partition(
        target=object(),
        selected_records=partition.selected_records,
        source_relative_paths=partition.relative_paths,
        database_path=database_path,
        normalize_silver_mode=False,
        run_epoch_ms=1786510805000,
        output_chunk_size=1,
    )

    assert result.apply_result.status == "verification_failed_cleaned"
    assert result.apply_result.retained_source_paths == tuple(partition.relative_paths)
    assert [event[0] for event in events] == ["upload", "upload", "delete", "delete"]
