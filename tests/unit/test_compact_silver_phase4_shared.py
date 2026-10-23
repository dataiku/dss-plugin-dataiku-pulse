from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import io

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from data_collection.audit_logs_modules import event_mapping_replay as replay
import shared_storage_discovery as discovery


class _Ctx(SimpleNamespace):
    pass


class _MemoryFolder:
    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def get_download_stream(self, path: str):
        return io.BytesIO(self.files[path])

    def delete_path(self, path: str) -> None:
        self.deleted.append(path)
        self.files.pop(path, None)


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


def test_staged_normalized_chunks_preserve_all_null_string_schema(monkeypatch, tmp_path):
    database_path = tmp_path / "partition.duckdb"
    con = duckdb.connect(str(database_path))
    con.execute(
        """
        CREATE TABLE dedup_stage AS
        SELECT * FROM (VALUES
            (0, '2026-06-11T00:00:00Z', 'null-project'),
            (1, '2026-06-11T01:00:00Z', 'populated-project')
        ) AS t(__compact_row_order, timestamp, marker)
        """.strip()
    )
    con.close()

    def fake_mapper(df):
        return pd.DataFrame(
            [
                {
                    "dataiku_category": "mlops",
                    "timestamp": row.timestamp,
                    "project_key": pd.NA if row.marker == "null-project" else "PROJECT_A",
                    "event_type": row.marker,
                }
                for row in df.itertuples(index=False)
            ]
        )

    folder = _MemoryFolder()

    def fake_upload(**kwargs):
        buf = io.BytesIO()
        kwargs["df"].to_parquet(buf, index=False, engine="pyarrow", compression="snappy")
        folder.files[f"/{str(kwargs['output_path']).lstrip('/')}"] = buf.getvalue()
        return True

    monkeypatch.setattr(replay.event_mapping, "main", fake_mapper)
    monkeypatch.setattr(replay, "get_managed_folder_handle", lambda target: folder)
    monkeypatch.setattr(replay, "upload_parquet", fake_upload)

    result = replay.process_staged_compact_partition(
        target=object(),
        selected_records=[
            discovery.SelectedPathRecord(
                relative_path="/silver/category=event_mapping/module=mlops/instance_name=alpha/year=2026/month=06/day=11/compact_silver-1-0001.parquet",
                full_path="bucket/root/silver/category=event_mapping/module=mlops/instance_name=alpha/year=2026/month=06/day=11/compact_silver-1-0001.parquet",
                base_name="compact_silver-1-0001.parquet",
                layer="silver",
                category="event_mapping",
                module="mlops",
                instance_name="alpha",
                year="2026",
                month="06",
                day="11",
            )
        ],
        source_relative_paths=["/silver/category=event_mapping/module=mlops/instance_name=alpha/year=2026/month=06/day=11/compact_silver-1-0001.parquet"],
        database_path=database_path,
        normalize_silver_mode=True,
        run_epoch_ms=1790966945713,
        output_chunk_size=1,
    )

    assert result.apply_result.status == "succeeded"
    written_payloads = [folder.files[path] for path in result.apply_result.written_paths]
    assert len(written_payloads) == 2
    schemas = [pq.read_schema(io.BytesIO(payload)) for payload in written_payloads]
    assert [str(schema.field("project_key").type) for schema in schemas] == [
        "string",
        "string",
    ]
    assert all(str(schema.field("timestamp").type).startswith("timestamp") for schema in schemas)

    read_paths = []
    for index, payload in enumerate(written_payloads, start=1):
        parquet_path = tmp_path / f"out-{index}.parquet"
        parquet_path.write_bytes(payload)
        read_paths.append(str(parquet_path))
    rows = duckdb.connect(":memory:").execute(
        "SELECT project_key, extras FROM read_parquet(?) ORDER BY extras",
        [read_paths],
    ).fetchall()
    assert rows == [
        (None, '{"event_type": "null-project"}'),
        ("PROJECT_A", '{"event_type": "populated-project"}'),
    ]


def test_wrong_schema_replacement_fails_verification_before_source_delete(monkeypatch):
    folder = _MemoryFolder()
    output_path = "/silver/category=event_mapping/module=mlops/instance_name=alpha/year=2026/month=06/day=11/compact_silver-1-0001.parquet"
    wrong_df = pd.DataFrame({"project_key": [None], "run_ts": pd.to_datetime(["2026-06-11T00:00:00Z"], utc=True)})
    buf = io.BytesIO()
    wrong_df.to_parquet(buf, index=False, engine="pyarrow")
    folder.files[output_path] = buf.getvalue()

    monkeypatch.setattr(replay, "upload_parquet", lambda **kwargs: True)
    monkeypatch.setattr(replay, "delete_managed_folder_file", lambda folder, path: folder.delete_path(path))

    apply_result, plan_count, _metrics = replay.apply_compact_replacement_plan_iterator(
        target=object(),
        folder=folder,
        source_relative_paths=["/silver/category=event_mapping/module=mlops/instance_name=alpha/year=2026/month=06/day=11/source.parquet"],
        plans=iter(
            [
                replay.ReplayWritePlan(
                    output_path=Path(output_path),
                    silver_df=pd.DataFrame({"project_key": pd.Series([pd.NA], dtype="string"), "run_ts": pd.to_datetime(["2026-06-11T00:00:00Z"], utc=True)}),
                    dq=replay.DQResult(ok=True, errors=[]),
                    module_name="mlops",
                    event_date=date(2026, 6, 11),
                    expected_duckdb_types={"project_key": "VARCHAR", "run_ts": "TIMESTAMP WITH TIME ZONE"},
                )
            ]
        ),
    )

    assert plan_count == 1
    assert apply_result.status == "verification_failed_cleaned"
    assert apply_result.deleted_source_paths == ()
    assert folder.deleted == [output_path]


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


def test_schema_read_failure_retains_sources_and_writes_no_replacements(monkeypatch):
    partition = _partition_with_count(2)
    events = []

    def fail_schema_read(storage_ctx, *, full_paths):
        events.append(("read", tuple(full_paths)))
        raise RuntimeError("parquet schema read failed")

    monkeypatch.setattr(replay, "read_s3_parquet_file_batch", fail_schema_read)
    monkeypatch.setattr(replay, "upload_event_mapping_replacements", lambda **kwargs: events.append(("upload",)))
    monkeypatch.setattr(replay, "delete_managed_folder_file", lambda folder, path: events.append(("delete", path)))

    with pytest.raises(RuntimeError, match="parquet schema read failed"):
        replay.process_compact_selected_partition(
            storage_ctx=SimpleNamespace(connection_type="EC2"),
            target=object(),
            selected_partition=partition,
            normalize_silver_mode=False,
            s3_read_batch_size=2,
        )

    assert events == [("read", tuple(partition.full_paths))]


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


def test_compact_stage_duckdb_config_caps_28_gib_memory_and_uses_partition_spill(monkeypatch, tmp_path):
    monkeypatch.setattr(replay, "_effective_memory_limit_bytes", lambda: (28 * 1024**3, "cgroup_v2"))
    spill_dir = tmp_path / "partition" / "duckdb-spill"

    config = replay._compact_stage_duckdb_config(spill_dir=spill_dir)

    assert config["memory_limit"] == "4096MiB"
    assert config["temp_directory"] == str(spill_dir)
    assert str(spill_dir).startswith(str(tmp_path / "partition"))
    assert spill_dir.is_dir()
    assert config["preserve_insertion_order"] == "false"


def test_compact_stage_duckdb_config_unknown_memory_leaves_limit_unset(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(replay, "_effective_memory_limit_bytes", lambda: (0, "unknown"))
    caplog.set_level("WARNING", logger=replay.__name__)

    config = replay._compact_stage_duckdb_config(spill_dir=tmp_path / "duckdb-spill")

    assert "memory_limit" not in config
    assert "effective memory unavailable" in caplog.text


def test_stage_duckdb_config_is_applied_before_staging_sql(monkeypatch, tmp_path):
    partition = _partition_with_count(1)
    events = []
    expected_spill_dir = tmp_path / "duckdb-spill"

    class FakeConnection:
        def __init__(self):
            self.tables = {"raw_stage"}

        def execute(self, sql, params=None):
            events.append(("execute", str(sql).strip().split()[0]))
            if "PRAGMA table_info" in str(sql):
                return SimpleNamespace(fetchall=lambda: [(0, replay._STAGE_ORDER_COLUMN), (1, "a")])
            if "SELECT COUNT(*) FROM dedup_stage" in str(sql):
                return SimpleNamespace(fetchone=lambda: [1])
            return SimpleNamespace(fetchall=lambda: [], fetchone=lambda: [1])

        def register(self, name, df):
            events.append(("register", name))

        def unregister(self, name):
            events.append(("unregister", name))

        def close(self):
            events.append(("close", ""))

    def fake_connect(path, config=None):
        events.append(("connect", path, dict(config or {})))
        return FakeConnection()

    monkeypatch.setattr(replay, "duckdb", SimpleNamespace(connect=fake_connect, DuckDBPyConnection=object))
    monkeypatch.setattr(replay, "_effective_memory_limit_bytes", lambda: (28 * 1024**3, "cgroup_v2"))
    monkeypatch.setattr(replay, "read_s3_parquet_file_batch", lambda storage_ctx, *, full_paths: pd.DataFrame([{"a": 1}]))

    replay._stage_compact_s3_partition(
        storage_ctx=SimpleNamespace(connection_type="EC2"),
        storage_ctx_factory=None,
        selected_partition=partition,
        database_path=tmp_path / "partition.duckdb",
        s3_read_batch_size=5_000,
    )

    assert events[0] == (
        "connect",
        str(tmp_path / "partition.duckdb"),
        {
            "temp_directory": str(expected_spill_dir),
            "preserve_insertion_order": "false",
            "memory_limit": "4096MiB",
        },
    )
    assert events[1][0] in {"register", "execute"}


def test_partial_write_message_includes_bounded_replacement_samples_and_omission_count():
    paths = tuple(f"/silver/category=event_mapping/module=folders/instance_name=alpha/year=2026/month=06/day=12/compact_silver-1-{index:04d}.parquet" for index in range(1, 8))

    message = replay.format_partial_write_message(
        base_message="Upload failed",
        written_paths=paths,
        cleanup_paths=paths[:6],
    )

    assert "written_count=7" in message
    assert "cleanup_count=6" in message
    for path in paths[:5]:
        assert path in message
    assert paths[5] not in message
    assert paths[6] not in message
    assert "written_sample_omitted_count=2" in message
    assert "cleanup_sample_omitted_count=1" in message


def test_upload_cleanup_failure_message_uses_bounded_replacement_evidence(monkeypatch):
    paths = [
        f"/silver/category=event_mapping/module=folders/instance_name=alpha/year=2026/month=06/day=12/compact_silver-1-{index:04d}.parquet"
        for index in range(1, 8)
    ]
    plans = [
        replay.ReplayWritePlan(
            output_path=Path(path),
            silver_df=pd.DataFrame([{"instance_name": "alpha", "run_ts": "2026-06-12T00:00:00Z"}]),
            dq=replay.DQResult(ok=True, errors=[]),
            module_name="folders",
            event_date=date(2026, 6, 12),
        )
        for path in paths
    ]

    def fake_upload(**kwargs):
        if str(kwargs["output_path"]) == paths[-1]:
            raise RuntimeError("upload failed")
        return True

    monkeypatch.setattr(replay, "upload_parquet", fake_upload)
    monkeypatch.setattr(replay, "cleanup_written_replacements", lambda *, folder, paths: ((), [f"{path}: cleanup failed" for path in paths]))

    result = replay.upload_event_mapping_replacements(target=object(), folder=object(), plans=plans)

    assert result.status == "upload_failed_cleanup_failed"
    assert "written_count=6" in result.message
    assert "cleanup_count" not in result.message
    for path in paths[:5]:
        assert path in result.message
    assert paths[5] not in result.message
    assert paths[6] not in result.message
    assert "written_sample_omitted_count=1" in result.message
    assert "cleanup failed for 6 replacement(s)" in result.message


def _users_selected_records(*, module: str, source_name: str = "source.parquet") -> list[discovery.SelectedPathRecord]:
    relative_path = f"/silver/category=users/module={module}/instance_name=alpha/year=2026/month=06/day=11/{source_name}"
    return [
        discovery.SelectedPathRecord(
            relative_path=relative_path,
            full_path=f"bucket/root{relative_path}",
            base_name=source_name,
            layer="silver",
            category="users",
            module=module,
            instance_name="alpha",
            year="2026",
            month="06",
            day="11",
        )
    ]


def test_users_instance_metadata_module_contract_adds_missing_string_fields_and_preserves_values(tmp_path):
    source_df = pd.DataFrame(
        [
            {
                "instance_name": "alpha",
                "run_ts": "2026-06-11T01:02:03Z",
                "users_login": "older",
                "users_enabled": "true",
                "extras": '{"legacy_field":"kept","users_lastsuccessfullogin":"2026-01-02T03:04:05Z"}',
            },
            {
                "instance_name": "alpha",
                "run_ts": "2026-06-11T04:05:06Z",
                "users_login": "populated",
                "users_enabled": "true",
                "users_lastsuccessfullogin": "2026-02-03T04:05:06Z",
                "users_lastfailedlogin": "2026-02-04T04:05:06Z",
                "users_lastsessionactivity": "2026-02-05T04:05:06Z",
                "users_first_commit_date": "2026-02-06T04:05:06Z",
                "users_last_commit_date": "2026-02-07T04:05:06Z",
                "unknown_top_level": "preserved",
            },
        ]
    )

    plans, summary = replay.plan_compact_selected_day(
        selected_records=_users_selected_records(module="instance_metadata"),
        selected_df=source_df,
        normalize_silver_mode=True,
        processing_mode="module_contract_normalization",
        run_epoch_ms=1790966945713,
    )

    assert summary.mode == "module_contract_normalization"
    assert len(plans) == 1
    plan = plans[0]
    assert str(plan.output_path) == "/silver/category=users/module=instance_metadata/instance_name=alpha/year=2026/month=06/day=11/compact_silver-1790966945713-0001.parquet"
    expected_columns = [
        "instance_name",
        "users_login",
        "users_enabled",
        "users_sourcetype",
        "users_displayname",
        "users_email",
        "users_groups",
        "users_userprofile",
        "users_resultinguserprofile",
        "users_creationdate",
        "users_lastsuccessfullogin",
        "users_lastfailedlogin",
        "users_lastsessionactivity",
        "users_first_commit_date",
        "users_last_commit_date",
        "users_trialstatus_exists",
        "users_trialstatus_expired",
        "users_trialstatus_illegal",
        "users_trialstatus_valid",
        "users_trialstatus_grantedon",
        "users_trialstatus_expireson",
        "run_ts",
        "extras",
    ]
    assert list(plan.silver_df.columns) == expected_columns
    assert plan.silver_df.loc[0, "users_lastsuccessfullogin"] == "2026-01-02T03:04:05Z"
    assert plan.silver_df.loc[1, "users_lastsuccessfullogin"] == "2026-02-03T04:05:06Z"
    assert list(plan.silver_df["run_ts"].astype(str)) == [
        "2026-06-11 01:02:03+00:00",
        "2026-06-11 04:05:06+00:00",
    ]
    assert plan.silver_df.loc[0, "extras"] == '{"legacy_field": "kept"}'
    assert plan.silver_df.loc[1, "extras"] == '{"unknown_top_level": "preserved"}'

    parquet_path = tmp_path / "users_instance_metadata.parquet"
    plan.silver_df.to_parquet(parquet_path, index=False, engine="pyarrow")
    schema = pq.read_schema(parquet_path)
    for column in [
        "users_lastsuccessfullogin",
        "users_lastfailedlogin",
        "users_lastsessionactivity",
        "users_first_commit_date",
        "users_last_commit_date",
    ]:
        assert str(schema.field(column).type) == "string"
    assert schema.field("run_ts").type == pa.timestamp("ns", tz="UTC")


def test_module_contract_plan_deduplicates_rows_after_normalization():
    source_df = pd.DataFrame(
        [
            {
                "instance_name": "alpha",
                "run_ts": "2026-06-11T01:02:03Z",
                "users_login": "duplicate",
                "users_enabled": "true",
                "legacy_field": "same",
            },
            {
                "instance_name": "alpha",
                "run_ts": "2026-06-11T01:02:03Z",
                "users_login": "duplicate",
                "users_enabled": "true",
                "extras": '{"legacy_field":"same"}',
            },
            {
                "instance_name": "alpha",
                "run_ts": "2026-06-11T01:02:03Z",
                "users_login": "different-extras",
                "users_enabled": "true",
                "extras": '{"legacy_field":"first"}',
            },
            {
                "instance_name": "alpha",
                "run_ts": "2026-06-11T01:02:03Z",
                "users_login": "different-extras",
                "users_enabled": "true",
                "extras": '{"legacy_field":"second"}',
            },
            {
                "instance_name": "alpha",
                "run_ts": "2026-06-11T04:05:06Z",
                "users_login": "duplicate",
                "users_enabled": "true",
                "legacy_field": "same",
            },
        ]
    )

    plans, summary = replay.plan_compact_selected_day(
        selected_records=_users_selected_records(module="instance_metadata"),
        selected_df=source_df,
        normalize_silver_mode=True,
        processing_mode="module_contract_normalization",
        run_epoch_ms=1790966945713,
    )

    assert len(plans) == 1
    assert summary.metrics[0].rows == 4
    rows = plans[0].silver_df[["users_login", "run_ts", "extras"]].sort_values(
        ["users_login", "run_ts", "extras"], ignore_index=True
    )
    assert rows.to_dict("records") == [
        {
            "users_login": "different-extras",
            "run_ts": pd.Timestamp("2026-06-11T01:02:03Z"),
            "extras": '{"legacy_field": "first"}',
        },
        {
            "users_login": "different-extras",
            "run_ts": pd.Timestamp("2026-06-11T01:02:03Z"),
            "extras": '{"legacy_field": "second"}',
        },
        {
            "users_login": "duplicate",
            "run_ts": pd.Timestamp("2026-06-11T01:02:03Z"),
            "extras": '{"legacy_field": "same"}',
        },
        {
            "users_login": "duplicate",
            "run_ts": pd.Timestamp("2026-06-11T04:05:06Z"),
            "extras": '{"legacy_field": "same"}',
        },
    ]


def test_generic_plan_deduplicates_exact_stored_rows_only():
    source_df = pd.DataFrame(
        [
            {"login": "alice", "count": 1, "run_ts": "2026-06-11T01:02:03Z"},
            {"login": "alice", "count": 1, "run_ts": "2026-06-11T01:02:03Z"},
            {"login": "alice", "count": 2, "run_ts": "2026-06-11T01:02:03Z"},
            {"login": "alice", "count": 1, "run_ts": "2026-06-11T04:05:06Z"},
        ]
    )

    plans, summary = replay.plan_compact_selected_day(
        selected_records=_users_selected_records(module="instance_metadata"),
        selected_df=source_df,
        normalize_silver_mode=False,
        processing_mode=replay.COMPACT_PROCESSING_MODE_GENERIC,
        run_epoch_ms=1790966945713,
    )

    assert len(plans) == 1
    assert summary.mode == replay.COMPACT_PROCESSING_MODE_GENERIC
    assert summary.input_rows == 4
    assert summary.metrics[0].rows == 3
    assert plans[0].silver_df.to_dict("records") == [
        {"login": "alice", "count": 1, "run_ts": "2026-06-11T01:02:03Z"},
        {"login": "alice", "count": 2, "run_ts": "2026-06-11T01:02:03Z"},
        {"login": "alice", "count": 1, "run_ts": "2026-06-11T04:05:06Z"},
    ]


def test_users_user_activity_module_contract_preserves_activity_fields_and_dataiku_category():
    source_df = pd.DataFrame(
        [
            {
                "instance_name": "alpha",
                "timestamp": "2026-06-11T01:00:00Z",
                "login": "alice",
                "project_key": "PROJECT_A",
                "viewing_actions_count": 2,
                "developing_actions_count": 3,
                "dataiku_category": "user_activity",
                "run_ts": "2026-06-11T02:00:00Z",
                "extras": '{"retained":"yes"}',
            }
        ]
    )

    plans, summary = replay.plan_compact_selected_day(
        selected_records=_users_selected_records(module="user_activity"),
        selected_df=source_df,
        normalize_silver_mode=True,
        processing_mode="module_contract_normalization",
        run_epoch_ms=1790966945713,
    )

    assert summary.mode == "module_contract_normalization"
    row = plans[0].silver_df.iloc[0]
    assert row["timestamp"].isoformat() == "2026-06-11T01:00:00+00:00"
    assert row["login"] == "alice"
    assert row["project_key"] == "PROJECT_A"
    assert row["viewing_actions_count"] == 2
    assert row["developing_actions_count"] == 3
    assert row["dataiku_category"] == "user_activity"
    assert row["run_ts"].isoformat() == "2026-06-11T02:00:00+00:00"
    assert row["extras"] == '{"retained": "yes"}'
    parquet_buffer = io.BytesIO()
    plans[0].silver_df.to_parquet(parquet_buffer, index=False, engine="pyarrow")
    schema = pq.read_schema(io.BytesIO(parquet_buffer.getvalue()))
    assert schema.field("timestamp").type == pa.timestamp("ns", tz="UTC")
    assert schema.field("run_ts").type == pa.timestamp("ns", tz="UTC")


def test_module_contract_normalization_rejects_missing_contract_and_conflicting_extras():
    with pytest.raises(replay.ReplaySkipError, match="missing canonical contract"):
        replay.plan_compact_selected_day(
            selected_records=_users_selected_records(module="unknown_module"),
            selected_df=pd.DataFrame([{"instance_name": "alpha", "run_ts": "2026-06-11T00:00:00Z"}]),
            normalize_silver_mode=True,
            processing_mode="module_contract_normalization",
            run_epoch_ms=1790966945713,
        )


def test_existing_extras_recover_noncontract_values_from_null_top_level_columns():
    source_df = pd.DataFrame(
        [
            {
                "users_login": "older",
                "extras": '{"legacy_field":"must-survive"}',
            },
            {
                "users_login": "newer",
                "legacy_field": "top-level-value",
                "extras": None,
            },
        ]
    )

    merged = replay._merge_existing_extras_into_source(
        source_df,
        required_columns=["instance_name", "run_ts", "users_login"],
    )

    assert merged.loc[0, "legacy_field"] == "must-survive"
    assert merged.loc[1, "legacy_field"] == "top-level-value"
    assert "extras" not in merged.columns


def test_existing_extras_reconcile_noncontract_absent_null_equal_and_conflicting_values():
    source_df = pd.DataFrame(
        [
            {"users_login": "absent", "extras": '{"absent_field":"extra-value"}'},
            {"users_login": "null", "nullable_field": pd.NA, "extras": '{"nullable_field":"extra-value"}'},
            {"users_login": "top", "top_field": "top-value", "extras": '{"other":"kept"}'},
            {"users_login": "equal", "equal_field": "same", "extras": '{"equal_field":"same"}'},
        ]
    )

    merged = replay._merge_existing_extras_into_source(
        source_df,
        required_columns=["instance_name", "run_ts", "users_login"],
    )

    assert merged.loc[0, "absent_field"] == "extra-value"
    assert merged.loc[1, "nullable_field"] == "extra-value"
    assert merged.loc[2, "top_field"] == "top-value"
    assert merged.loc[2, "other"] == "kept"
    assert merged.loc[3, "equal_field"] == "same"

    with pytest.raises(replay.ReplaySkipError, match="noncontract column 'legacy_field'"):
        replay._merge_existing_extras_into_source(
            pd.DataFrame(
                [
                    {
                        "users_login": "user",
                        "legacy_field": "top-value",
                        "extras": '{"legacy_field":"different-extra-value"}',
                    }
                ]
            ),
            required_columns=["instance_name", "run_ts", "users_login"],
        )

    with pytest.raises(replay.ReplaySkipError, match="nested extras payload key"):
        replay._merge_existing_extras_into_source(
            pd.DataFrame([{"users_login": "user", "extras": '{"extras":{"nested":"unsafe"}}'}]),
            required_columns=["instance_name", "run_ts", "users_login"],
        )


def test_users_instance_metadata_preserves_noncontract_extras_through_parquet_readback(tmp_path):
    source_df = pd.DataFrame(
        [
            {
                "instance_name": "alpha",
                "run_ts": "2026-06-11T01:02:03Z",
                "users_login": "older",
                "legacy_field": pd.NA,
                "extras": '{"legacy_field":"must-survive"}',
            },
            {
                "instance_name": "alpha",
                "run_ts": "2026-06-11T04:05:06Z",
                "users_login": "newer",
                "legacy_field": "top-level-value",
                "extras": None,
            },
        ]
    )

    plans, summary = replay.plan_compact_selected_day(
        selected_records=_users_selected_records(module="instance_metadata"),
        selected_df=source_df,
        normalize_silver_mode=True,
        processing_mode="module_contract_normalization",
        run_epoch_ms=1790966945713,
    )

    assert summary.mode == "module_contract_normalization"
    parquet_path = tmp_path / "users_instance_metadata_extras.parquet"
    plans[0].silver_df.to_parquet(parquet_path, index=False, engine="pyarrow")
    rows = duckdb.connect(":memory:").execute(
        "SELECT users_login, run_ts, extras FROM read_parquet(?) ORDER BY users_login",
        [str(parquet_path)],
    ).fetchall()

    assert rows == [
        ("newer", rows[0][1], '{"legacy_field": "top-level-value"}'),
        ("older", rows[1][1], '{"legacy_field": "must-survive"}'),
    ]
    assert str(rows[0][1]).startswith("2026-06-11 04:05:06")
    assert str(rows[1][1]).startswith("2026-06-11 01:02:03")


def test_staged_noncontract_extras_conflict_retains_sources_without_upload_or_delete(monkeypatch, tmp_path):
    database_path = tmp_path / "users_conflict.duckdb"
    con = duckdb.connect(str(database_path))
    con.execute(
        """
        CREATE TABLE dedup_stage AS
        SELECT * FROM (VALUES
            (0, 'alpha', '2026-06-11T01:02:03Z', 'user', 'top-value', '{"legacy_field":"different-extra-value"}')
        ) AS t(__compact_row_order, instance_name, run_ts, users_login, legacy_field, extras)
        """.strip()
    )
    con.close()

    source_path = "/silver/category=users/module=instance_metadata/instance_name=alpha/year=2026/month=06/day=11/source.parquet"
    uploads: list[dict[str, object]] = []
    deletes: list[str] = []

    monkeypatch.setattr(replay, "get_managed_folder_handle", lambda target: object())
    monkeypatch.setattr(replay, "upload_parquet", lambda **kwargs: uploads.append(kwargs))
    monkeypatch.setattr(replay, "delete_managed_folder_file", lambda folder, path: deletes.append(path))

    with pytest.raises(replay.ReplaySkipError, match="noncontract column 'legacy_field'"):
        replay.process_staged_compact_partition(
            target=object(),
            selected_records=_users_selected_records(module="instance_metadata"),
            source_relative_paths=[source_path],
            database_path=database_path,
            normalize_silver_mode=True,
            processing_mode="module_contract_normalization",
            run_epoch_ms=1790966945713,
            output_chunk_size=100,
        )

    assert uploads == []
    assert deletes == []


@pytest.mark.parametrize(
    ("actual_arrow_type", "expected_type"),
    [
        (pa.timestamp("us", tz="UTC"), "timestamp[us, tz=UTC]"),
        (pa.timestamp("ns"), "timestamp[ns]"),
        (pa.timestamp("ns", tz="Europe/Paris"), "timestamp[ns, tz=Europe/Paris]"),
    ],
)
def test_wrong_timestamp_arrow_schema_fails_verification_before_source_delete(monkeypatch, actual_arrow_type, expected_type):
    folder = _MemoryFolder()
    output_path = "/silver/category=users/module=instance_metadata/instance_name=alpha/year=2026/month=06/day=11/compact_silver-1-0001.parquet"
    source_path = "/silver/category=users/module=instance_metadata/instance_name=alpha/year=2026/month=06/day=11/source.parquet"
    wrong_table = pa.table(
        {
            "run_ts": pa.array(
                [datetime(2026, 6, 11, tzinfo=timezone.utc)],
                type=actual_arrow_type,
            )
        }
    )
    buffer = io.BytesIO()
    pq.write_table(wrong_table, buffer)
    folder.files[output_path] = buffer.getvalue()

    monkeypatch.setattr(replay, "upload_parquet", lambda **kwargs: True)
    monkeypatch.setattr(replay, "delete_managed_folder_file", lambda folder, path: folder.delete_path(path))

    apply_result, plan_count, _metrics = replay.apply_compact_replacement_plan_iterator(
        target=object(),
        folder=folder,
        source_relative_paths=[source_path],
        plans=iter(
            [
                replay.ReplayWritePlan(
                    output_path=Path(output_path),
                    silver_df=pd.DataFrame(
                        {
                            "run_ts": pd.Series(
                                pd.to_datetime(["2026-06-11T00:00:00Z"], utc=True),
                                dtype=pd.DatetimeTZDtype(unit="ns", tz="UTC"),
                            )
                        }
                    ),
                    dq=replay.DQResult(ok=True, errors=[]),
                    module_name="instance_metadata",
                    event_date=date(2026, 6, 11),
                    expected_arrow_types={"run_ts": pa.timestamp("ns", tz="UTC")},
                )
            ]
        ),
    )

    assert plan_count == 1
    assert apply_result.status == "verification_failed_cleaned"
    assert apply_result.deleted_source_paths == ()
    assert folder.deleted == [output_path]
    assert expected_type in apply_result.message
    assert "timestamp[ns, tz=UTC]" in apply_result.message

    with pytest.raises(replay.ReplaySkipError, match="Conflicting non-null values"):
        replay.plan_compact_selected_day(
            selected_records=_users_selected_records(module="instance_metadata"),
            selected_df=pd.DataFrame(
                [
                    {
                        "instance_name": "alpha",
                        "run_ts": "2026-06-11T00:00:00Z",
                        "users_login": "top-level",
                        "extras": '{"users_login":"extras-value"}',
                    }
                ]
            ),
            normalize_silver_mode=True,
            processing_mode="module_contract_normalization",
            run_epoch_ms=1790966945713,
        )


def test_staged_users_instance_metadata_normalization_writes_verified_schema_and_deletes_sources(monkeypatch, tmp_path):
    database_path = tmp_path / "users_partition.duckdb"
    con = duckdb.connect(str(database_path))
    con.execute(
        """
        CREATE TABLE dedup_stage AS
        SELECT * FROM (VALUES
            (0, 'alpha', '2026-06-11T01:02:03.987654321Z', 'older', NULL),
            (1, 'alpha', '2026-06-11T04:05:06.123456789Z', 'populated', '2026-02-03T04:05:06Z')
        ) AS t(__compact_row_order, instance_name, run_ts, users_login, users_lastsuccessfullogin)
        """.strip()
    )
    con.close()

    folder = _MemoryFolder()
    source_path = "/silver/category=users/module=instance_metadata/instance_name=alpha/year=2026/month=06/day=11/source.parquet"
    folder.files[source_path] = b"source"

    def fake_upload(**kwargs):
        buf = io.BytesIO()
        kwargs["df"].to_parquet(buf, index=False, engine="pyarrow", compression="snappy")
        folder.files[f"/{str(kwargs['output_path']).lstrip('/')}"] = buf.getvalue()
        return True

    monkeypatch.setattr(replay, "get_managed_folder_handle", lambda target: folder)
    monkeypatch.setattr(replay, "upload_parquet", fake_upload)

    result = replay.process_staged_compact_partition(
        target=object(),
        selected_records=_users_selected_records(module="instance_metadata"),
        source_relative_paths=[source_path],
        database_path=database_path,
        normalize_silver_mode=True,
        processing_mode="module_contract_normalization",
        run_epoch_ms=1790966945713,
        output_chunk_size=1,
    )

    assert result.plan_summary.mode == "module_contract_normalization"
    assert result.apply_result.status == "succeeded"
    assert result.apply_result.deleted_source_paths == (source_path,)
    assert folder.deleted == [source_path]
    written_payloads = [folder.files[path] for path in result.apply_result.written_paths]
    assert len(written_payloads) == 2
    expected_schema_names = [
        "instance_name",
        "users_login",
        "users_enabled",
        "users_sourcetype",
        "users_displayname",
        "users_email",
        "users_groups",
        "users_userprofile",
        "users_resultinguserprofile",
        "users_creationdate",
        "users_lastsuccessfullogin",
        "users_lastfailedlogin",
        "users_lastsessionactivity",
        "users_first_commit_date",
        "users_last_commit_date",
        "users_trialstatus_exists",
        "users_trialstatus_expired",
        "users_trialstatus_illegal",
        "users_trialstatus_valid",
        "users_trialstatus_grantedon",
        "users_trialstatus_expireson",
        "run_ts",
        "extras",
    ]
    readback_paths = []
    for index, payload in enumerate(written_payloads, start=1):
        schema = pq.read_schema(io.BytesIO(payload))
        assert schema.names == expected_schema_names
        assert schema.field("run_ts").type == pa.timestamp("ns", tz="UTC")
        readback_path = tmp_path / f"users-instance-{index}.parquet"
        readback_path.write_bytes(payload)
        readback_paths.append(str(readback_path))
    for column in [
        "users_lastsuccessfullogin",
        "users_lastfailedlogin",
        "users_lastsessionactivity",
        "users_first_commit_date",
        "users_last_commit_date",
    ]:
        assert all(str(pq.read_schema(path).field(column).type) == "string" for path in readback_paths)
    rows = duckdb.connect(":memory:").execute(
        "SELECT users_login, epoch_ns(run_ts) FROM read_parquet(?) ORDER BY users_login",
        [readback_paths],
    ).fetchall()
    expected_df = replay.normalize_module_contract_silver(
        df=pd.DataFrame(
            [
                {
                    "instance_name": "alpha",
                    "run_ts": "2026-06-11T01:02:03.987654321Z",
                    "users_login": "older",
                },
                {
                    "instance_name": "alpha",
                    "run_ts": "2026-06-11T04:05:06.123456789Z",
                    "users_login": "populated",
                    "users_lastsuccessfullogin": "2026-02-03T04:05:06Z",
                },
            ]
        ),
        category="users",
        module_name="instance_metadata",
        instance_name="alpha",
    ).sort_values("users_login")
    assert rows == list(zip(expected_df["users_login"], expected_df["run_ts"].astype("int64")))


def test_staged_module_contract_deduplicates_normalized_rows_across_chunks(monkeypatch, tmp_path):
    database_path = tmp_path / "users_partition.duckdb"
    con = duckdb.connect(str(database_path))
    con.execute(
        """
        CREATE TABLE dedup_stage AS
        SELECT * FROM (VALUES
            (0, 'alpha', '2026-06-11T01:02:03Z', 'duplicate', 'true', 'same', NULL),
            (1, 'alpha', '2026-06-11T01:02:03Z', 'duplicate', 'true', NULL, '{"legacy_field":"same"}'),
            (2, 'alpha', '2026-06-11T01:02:03Z', 'old-compact-only', 'true', 'old', NULL)
        ) AS t(__compact_row_order, instance_name, run_ts, users_login, users_enabled, legacy_field, extras)
        """.strip()
    )
    con.close()

    folder = _MemoryFolder()
    source_path = "/silver/category=users/module=instance_metadata/instance_name=alpha/year=2026/month=06/day=11/source.parquet"
    compact_path = "/silver/category=users/module=instance_metadata/instance_name=alpha/year=2026/month=06/day=11/compact_silver-1-0001.parquet"
    folder.files[source_path] = b"source"
    folder.files[compact_path] = b"compact"

    def fake_upload(**kwargs):
        buf = io.BytesIO()
        kwargs["df"].to_parquet(buf, index=False, engine="pyarrow", compression="snappy")
        folder.files[f"/{str(kwargs['output_path']).lstrip('/')}"] = buf.getvalue()
        return True

    selected_records = _users_selected_records(module="instance_metadata") + _users_selected_records(
        module="instance_metadata", source_name="compact_silver-1-0001.parquet"
    )

    monkeypatch.setattr(replay, "get_managed_folder_handle", lambda target: folder)
    monkeypatch.setattr(replay, "upload_parquet", fake_upload)

    result = replay.process_staged_compact_partition(
        target=object(),
        selected_records=selected_records,
        source_relative_paths=[source_path, compact_path],
        database_path=database_path,
        normalize_silver_mode=True,
        processing_mode="module_contract_normalization",
        run_epoch_ms=1790966945713,
        output_chunk_size=1,
    )

    assert result.apply_result.status == "succeeded"
    assert result.plan_summary.metrics[0].rows == 2
    assert result.apply_result.deleted_source_paths == (source_path, compact_path)
    assert len(result.apply_result.written_paths) == 2
    readback_paths = []
    for index, output_path in enumerate(result.apply_result.written_paths, start=1):
        readback_path = tmp_path / f"deduped-{index}.parquet"
        readback_path.write_bytes(folder.files[output_path])
        readback_paths.append(str(readback_path))
    rows = duckdb.connect(":memory:").execute(
        "SELECT users_login, extras FROM read_parquet(?) ORDER BY users_login",
        [readback_paths],
    ).fetchall()
    assert rows == [
        ("duplicate", '{"legacy_field": "same"}'),
        ("old-compact-only", '{"legacy_field": "old"}'),
    ]


def test_restored_users_partition_recompacts_with_prior_compact_output(monkeypatch, tmp_path):
    ordinary_path = "/silver/category=users/module=instance_metadata/instance_name=alpha/year=2026/month=06/day=11/source.parquet"
    compact_path = "/silver/category=users/module=instance_metadata/instance_name=alpha/year=2026/month=06/day=11/compact_silver-1-0001.parquet"
    selected_records = _users_selected_records(module="instance_metadata") + _users_selected_records(
        module="instance_metadata", source_name="compact_silver-1-0001.parquet"
    )
    partition = discovery.SelectedPartitionPaths(
        category="users",
        module="instance_metadata",
        instance_name="alpha",
        year="2026",
        month="06",
        day="11",
        selected_records=selected_records,
    )
    source_frames = {
        "bucket/root" + ordinary_path: pd.DataFrame(
            [
                {
                    "instance_name": "alpha",
                    "run_ts": "2026-06-11T01:02:03Z",
                    "users_login": "restored",
                    "users_enabled": "true",
                    "legacy_field": "same",
                },
                {
                    "instance_name": "alpha",
                    "run_ts": "2026-06-11T02:03:04Z",
                    "users_login": "new",
                    "users_enabled": "true",
                },
            ]
        ),
        "bucket/root" + compact_path: pd.DataFrame(
            [
                {
                    "instance_name": "alpha",
                    "run_ts": "2026-06-11T01:02:03Z",
                    "users_login": "restored",
                    "users_enabled": "true",
                    "extras": '{"legacy_field":"same"}',
                },
                {
                    "instance_name": "alpha",
                    "run_ts": "2026-06-11T00:00:00Z",
                    "users_login": "compact-only",
                    "users_enabled": "true",
                },
            ]
        ),
    }
    folder = _MemoryFolder()
    folder.files[ordinary_path] = b"source"
    folder.files[compact_path] = b"compact"
    source_parquet_paths = {}
    for index, (full_path, source_df) in enumerate(source_frames.items(), start=1):
        source_parquet_path = tmp_path / f"source-{index}.parquet"
        source_df.to_parquet(source_parquet_path, index=False, engine="pyarrow")
        source_parquet_paths[full_path] = source_parquet_path

    def fake_read(_storage_ctx, *, full_paths):
        return pd.concat(
            [pd.read_parquet(source_parquet_paths[path], engine="pyarrow") for path in full_paths],
            ignore_index=True,
        )

    def fake_upload(**kwargs):
        buf = io.BytesIO()
        kwargs["df"].to_parquet(buf, index=False, engine="pyarrow", compression="snappy")
        folder.files[f"/{str(kwargs['output_path']).lstrip('/')}"] = buf.getvalue()
        return True

    monkeypatch.setattr(replay, "read_s3_parquet_file_batch", fake_read)
    monkeypatch.setattr(replay, "get_managed_folder_handle", lambda target: folder)
    monkeypatch.setattr(replay, "upload_parquet", fake_upload)
    monkeypatch.setattr(replay, "next_compact_save_epoch_ms", lambda: 1790966945713)
    monkeypatch.setattr(replay, "check_silver_dq", lambda df: replay.DQResult(ok=True, errors=[]))

    outcome = replay.process_compact_selected_partition(
        storage_ctx=object(),
        target=object(),
        selected_partition=partition,
        normalize_silver_mode=True,
        processing_mode="module_contract_normalization",
        s3_read_batch_size=1,
        output_chunk_size=100,
    )

    assert outcome.status == "succeeded"
    assert outcome.files_read == 2
    assert outcome.raw_rows == 4
    assert outcome.input_rows == 4
    assert outcome.metrics[0].rows == 3
    assert outcome.deleted_count == 2
    assert set(folder.deleted) == {ordinary_path, compact_path}
    assert len(outcome.metrics) == 1
    assert outcome.written_count == 1

    replacement_paths = [path for path in folder.files if "compact_silver-1790966945713" in path]
    assert len(replacement_paths) == 1
    readback_path = tmp_path / "restored-overlap.parquet"
    readback_path.write_bytes(folder.files[replacement_paths[0]])
    schema = pq.read_schema(readback_path)
    assert schema.field("run_ts").type == pa.timestamp("ns", tz="UTC")
    rows = duckdb.connect(":memory:").execute(
        "SELECT users_login, extras FROM read_parquet(?) ORDER BY users_login",
        [str(readback_path)],
    ).fetchall()
    assert rows == [
        ("compact-only", None),
        ("new", None),
        ("restored", '{"legacy_field": "same"}'),
    ]
