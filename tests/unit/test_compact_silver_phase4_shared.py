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
