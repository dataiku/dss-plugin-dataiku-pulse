from __future__ import annotations

import importlib.util
import io
import logging
import sys
from pathlib import Path

import pandas as pd
import pytest

import data_collection.helper.dss_folder_writer as dss_folder_writer
from data_collection.audit_logs_modules import event_mapping_replay as replay
from data_collection.helper.dss_folder_writer import DSSFolderTarget


def _load_runnable_module():
    path = Path(__file__).resolve().parents[2] / "python-runnables" / "reload-event-mapping" / "runnable.py"
    spec = importlib.util.spec_from_file_location("reload_event_mapping_runnable", path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _source_info(path: str = "/silver/category=event_mapping/module=oldcat/instance_name=inst/year=2026/month=08/day=24/source.parquet"):
    return replay.parse_event_mapping_source_path(path)


def _audit_source_info(
    path: str = "/silver/category=event_mapping/module=dataset/instance_name=tam-global/year=2026/month=08/day=12/audit_logs-1786510805050-dataset.parquet",
):
    return replay.parse_event_mapping_source_path(path)


def test_rehydrate_extras_preserves_top_level_fields_and_restores_mapper_input():
    df = pd.DataFrame(
        [
            {
                "msgtype": "LOGIN",
                "dataiku_category": "oldcat",
                "timestamp": "2026-08-24T10:00:00Z",
                "extras": '{"topic":"generic","authvia":"ticket:job:PROJ_1.abc","msgtype":"ignored","custom":"value"}',
            }
        ]
    )

    out = replay.rehydrate_event_mapping_source(df)

    assert "dataiku_category" not in out.columns
    assert "extras" not in out.columns
    assert out.loc[0, "message_msgType"] == "LOGIN"
    assert "msgtype" not in out.columns
    assert out.loc[0, "topic"] == "generic"
    assert out.loc[0, "custom"] == "value"


def test_rehydrate_event_mapping_source_drops_stale_mapper_fields_and_preserves_mapper_inputs():
    df = pd.DataFrame(
        [
            {
                "topic": "generic",
                "msgtype": "OLD_TYPE",
                "msgtypebase": "OLD_BASE",
                "dataiku_category": "old_category",
                "authvia": "ticket:job:PROJ_1.job",
                "timestamp": "2026-08-24T10:00:00Z",
                "extras": '{"message_msgType":"SHOULD_NOT_OVERRIDE","severity":"INFO","callpath":"/path"}',
            }
        ]
    )

    out = replay.rehydrate_event_mapping_source(df)

    assert out.loc[0, "message_msgType"] == "OLD_TYPE"
    assert "msgtype" not in out.columns
    assert "msgtypebase" not in out.columns
    assert "dataiku_category" not in out.columns
    assert "extras" not in out.columns
    assert out.loc[0, "topic"] == "generic"
    assert out.loc[0, "authvia"] == "ticket:job:PROJ_1.job"
    assert out.loc[0, "severity"] == "INFO"
    assert out.loc[0, "callpath"] == "/path"


@pytest.mark.parametrize("extras_value", [None, "", "   "])
def test_rehydrate_extras_treats_blank_values_as_empty_object(extras_value):
    df = pd.DataFrame([{"msgtype": "LOGIN", "extras": extras_value}])
    out = replay.rehydrate_event_mapping_source(df)
    assert out.loc[0, "message_msgType"] == "LOGIN"


@pytest.mark.parametrize("extras_value", ["{bad", "[]", 123])
def test_rehydrate_extras_rejects_invalid_or_unsupported_payloads(extras_value):
    df = pd.DataFrame([{"msgtype": "LOGIN", "extras": extras_value}])
    with pytest.raises(replay.ExtrasDecodeError):
        replay.rehydrate_event_mapping_source(df)


def test_plan_replay_groups_by_new_category_and_preserves_partition_date(monkeypatch):
    source_df = pd.DataFrame(
        [
            {
                "msgtype": "TYPE_A",
                "timestamp": "2026-08-24T10:00:00Z",
                "extras": '{"topic":"generic","field":"one"}',
                "run_ts": "2026-08-24T11:00:00Z",
            },
            {
                "msgtype": "TYPE_B",
                "timestamp": "2026-08-24T12:00:00Z",
                "extras": '{"topic":"generic","field":"two"}',
                "run_ts": "2026-08-24T11:00:00Z",
            },
        ]
    )

    captured = {}

    def fake_main(df: pd.DataFrame) -> pd.DataFrame:
        captured["columns"] = list(df.columns)
        return pd.DataFrame(
            [
                {"dataiku_category": "webapps", "timestamp": "2026-08-24T10:00:00Z", "msgtype": "TYPE_A"},
                {"dataiku_category": "projects", "timestamp": "2026-08-24T12:00:00Z", "msgtype": "TYPE_B"},
            ]
        )

    monkeypatch.setattr(replay.event_mapping, "main", fake_main)
    plans = replay.plan_event_mapping_replay(source=_audit_source_info(), source_df=source_df)

    assert "message_msgType" in captured["columns"]
    assert "dataiku_category" not in captured["columns"]
    assert {plan.module_name for plan in plans} == {"webapps", "projects"}
    assert all(str(plan.output_path).startswith("/silver/category=event_mapping/module=") for plan in plans)
    assert all("instance_name=tam-global" in str(plan.output_path) for plan in plans)


def test_plan_replay_uses_current_mapper_msgtypebase_without_duplicates(monkeypatch):
    source = _audit_source_info()
    source_df = pd.DataFrame(
        [
            {
                "topic": "generic",
                "msgtype": "OLD_TYPE",
                "msgtypebase": "OLD_BASE",
                "dataiku_category": "old_category",
                "authvia": "ticket:job:PROJ_1.job",
                "timestamp": "2026-08-24T10:00:00Z",
                "run_ts": "2026-08-24T11:00:00Z",
                "extras": '{"severity":"INFO","callpath":"/path"}',
            }
        ]
    )
    captured = {}

    def fake_main(df: pd.DataFrame) -> pd.DataFrame:
        captured["columns"] = list(df.columns)
        captured["row"] = df.iloc[0].to_dict()
        return pd.DataFrame(
            [
                {
                    "topic": "generic",
                    "msgtype": "NEW_TYPE",
                    "msgtypebase": "NEW_BASE",
                    "dataiku_category": "dataset",
                    "authvia": df.iloc[0]["authvia"],
                    "callpath": df.iloc[0]["callpath"],
                    "timestamp": df.iloc[0]["timestamp"],
                    "date": "2026-08-24",
                    "severity": df.iloc[0]["severity"],
                }
            ]
        )

    monkeypatch.setattr(replay.event_mapping, "main", fake_main)
    plans = replay.plan_event_mapping_replay(source=source, source_df=source_df)

    assert captured["row"]["message_msgType"] == "OLD_TYPE"
    assert "msgtype" not in captured["columns"]
    assert "msgtypebase" not in captured["columns"]
    assert "dataiku_category" not in captured["columns"]
    assert captured["row"]["topic"] == "generic"
    assert captured["row"]["authvia"] == "ticket:job:PROJ_1.job"

    assert len(plans) == 1
    silver_df = plans[0].silver_df
    assert not silver_df.columns.duplicated().any()
    assert list(silver_df.columns).count("msgtype") == 1
    assert list(silver_df.columns).count("msgtypebase") == 1
    assert silver_df.loc[0, "msgtypebase"] == "NEW_BASE"
    assert silver_df.loc[0, "msgtype"] == "NEW_TYPE"


def test_wide_source_without_extras_replays_and_packs_non_schema_fields(monkeypatch):
    source = _audit_source_info()
    source_df = pd.DataFrame(
        [
            {
                "topic": "generic",
                "msgtype": "DATASET_DELETE",
                "msgtypebase": "STALE_BASE",
                "dataiku_category": "dataset",
                "authvia": "ticket:job:PROJ_2.job",
                "timestamp": "2026-08-24T10:00:00Z",
                "run_ts": "2026-08-24T11:00:00Z",
                "severity": "WARN",
                "logger": "audit.logger",
                "calltime": 123,
                "audittopic": "generic",
                "clientip": "127.0.0.1",
                "datasetname": "ds1",
                "fulldatasetname": "PROJ_2.ds1",
                "callpath": "/projects/PROJ_2/datasets/ds1",
            }
        ]
    )

    def fake_main(df: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "topic": df.iloc[0]["topic"],
                    "msgtype": "DATASET_DELETE",
                    "msgtypebase": "DATASET",
                    "dataiku_category": "dataset",
                    "authvia": df.iloc[0]["authvia"],
                    "callpath": df.iloc[0]["callpath"],
                    "timestamp": df.iloc[0]["timestamp"],
                    "date": "2026-08-24",
                    "severity": df.iloc[0]["severity"],
                    "logger": df.iloc[0]["logger"],
                    "calltime": df.iloc[0]["calltime"],
                    "audittopic": df.iloc[0]["audittopic"],
                    "clientip": df.iloc[0]["clientip"],
                    "datasetname": df.iloc[0]["datasetname"],
                    "fulldatasetname": df.iloc[0]["fulldatasetname"],
                    "project_key_source_call": "PROJ_2",
                }
            ]
        )

    monkeypatch.setattr(replay.event_mapping, "main", fake_main)
    plans = replay.plan_event_mapping_replay(source=source, source_df=source_df)

    assert len(plans) == 1
    silver_df = plans[0].silver_df
    assert "extras" in silver_df.columns
    assert list(silver_df.columns) == [
        "instance_name",
        "project_key",
        "project_key_source_call",
        "authuser",
        "authsource",
        "authvia",
        "msgtype",
        "msgtypebase",
        "dataiku_category",
        "callpath",
        "timestamp",
        "date",
        "run_ts",
        "extras",
    ]
    extras = silver_df.loc[0, "extras"]
    assert extras is not None
    payload = __import__("json").loads(extras)
    assert payload["severity"] == "WARN"
    assert payload["logger"] == "audit.logger"
    assert payload["calltime"] == 123
    assert payload["audittopic"] == "generic"
    assert payload["clientip"] == "127.0.0.1"
    assert payload["datasetname"] == "ds1"
    assert payload["fulldatasetname"] == "PROJ_2.ds1"
    assert silver_df.loc[0, "msgtypebase"] == "DATASET"


def test_plan_replay_returns_empty_when_mapper_drops_all_rows(monkeypatch):
    monkeypatch.setattr(replay.event_mapping, "main", lambda df: pd.DataFrame(columns=["dataiku_category"]))
    plans = replay.plan_event_mapping_replay(
        source=_audit_source_info(),
        source_df=pd.DataFrame([{"msgtype": "DROP_ME", "extras": '{"topic":"generic"}'}]),
    )
    assert plans == []


def test_upload_failure_and_dq_failure_leave_deletion_to_caller(monkeypatch):
    source = _audit_source_info()
    plan = replay.ReplayWritePlan(
        output_path=replay.build_event_mapping_output_path(source=source, module_name="webapps", event_date=source.run_date),
        silver_df=pd.DataFrame([{"instance_name": "inst", "run_ts": "2026-08-24T11:00:00Z"}]),
        dq=replay.DQResult(ok=False, errors=["empty_dataframe"]),
        module_name="webapps",
        event_date=source.run_date,
    )
    result = replay.upload_event_mapping_replacements(target=object(), folder=object(), plans=[plan])
    assert result.status == "dq_failed"
    assert result.written_paths == ()
    assert result.dq_errors == ("empty_dataframe",)

    ok_plan = replay.ReplayWritePlan(
        output_path=plan.output_path,
        silver_df=pd.DataFrame([{"instance_name": "inst", "run_ts": "2026-08-24T11:00:00Z"}]),
        dq=replay.DQResult(ok=True, errors=[]),
        module_name="webapps",
        event_date=source.run_date,
    )

    def boom(**kwargs):
        raise RuntimeError("upload failed")

    monkeypatch.setattr(replay, "upload_parquet", boom)
    monkeypatch.setattr(replay, "cleanup_written_replacements", lambda folder, paths: (tuple(paths), []))
    result = replay.upload_event_mapping_replacements(target=object(), folder=object(), plans=[ok_plan])
    assert result.status == "upload_failed_cleaned"


def test_discover_read_and_delete_support_local_and_remote_folder_modes():
    df = pd.DataFrame([{"msgtype": "LOGIN", "extras": '{"topic":"generic"}'}])
    buf = io.BytesIO()
    df.to_parquet(buf, index=False, engine="pyarrow")
    payload = buf.getvalue()

    class LocalFolder:
        def __init__(self):
            self.deleted = []

        def list_paths_in_partition(self):
            return [
                "/silver/category=event_mapping/module=webapps/instance_name=inst/year=2026/month=08/day=24/source.parquet",
                "/silver/category=projects/module=metadata/instance_name=inst/year=2026/month=08/day=24/other.parquet",
            ]

        def get_download_stream(self, path):
            return io.BytesIO(payload)

        def delete_path(self, path):
            self.deleted.append(path)

    class RemoteFolder:
        def __init__(self):
            self.deleted = []

        def list_contents(self):
            return {"items": [{"path": "/silver/category=event_mapping/module=webapps/instance_name=inst/year=2026/month=08/day=24/source.parquet"}]}

        def delete_file(self, path):
            self.deleted.append(path)

    local = LocalFolder()
    remote = RemoteFolder()

    assert replay.discover_event_mapping_paths(local) == [
        "/silver/category=event_mapping/module=webapps/instance_name=inst/year=2026/month=08/day=24/source.parquet"
    ]
    assert replay.read_managed_folder_parquet(local, "/silver/category=event_mapping/module=webapps/instance_name=inst/year=2026/month=08/day=24/source.parquet").shape[0] == 1
    replay.delete_managed_folder_file(local, "/silver/category=event_mapping/module=webapps/instance_name=inst/year=2026/month=08/day=24/source.parquet")
    assert local.deleted == ["/silver/category=event_mapping/module=webapps/instance_name=inst/year=2026/month=08/day=24/source.parquet"]

    assert replay.discover_event_mapping_paths(remote) == [
        "/silver/category=event_mapping/module=webapps/instance_name=inst/year=2026/month=08/day=24/source.parquet"
    ]
    with pytest.raises(TypeError, match="Unsupported folder handle type for parquet read"):
        replay.read_managed_folder_parquet(remote, "/silver/category=event_mapping/module=webapps/instance_name=inst/year=2026/month=08/day=24/source.parquet")
    replay.delete_managed_folder_file(remote, "/silver/category=event_mapping/module=webapps/instance_name=inst/year=2026/month=08/day=24/source.parquet")
    assert remote.deleted == ["/silver/category=event_mapping/module=webapps/instance_name=inst/year=2026/month=08/day=24/source.parquet"]


def test_read_managed_folder_parquet_buffers_non_seekable_download_stream(monkeypatch):
    df = pd.DataFrame([{"msgtype": "LOGIN", "extras": '{"topic":"generic"}'}])
    buf = io.BytesIO()
    df.to_parquet(buf, index=False, engine="pyarrow")
    payload = buf.getvalue()
    captured = {}

    class NonSeekableStream:
        def __init__(self, data: bytes):
            self._buffer = io.BytesIO(data)

        def read(self, *args, **kwargs):
            return self._buffer.read(*args, **kwargs)

        def seek(self, *args, **kwargs):
            raise io.UnsupportedOperation("seek")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class LocalFolder:
        def get_download_stream(self, path):
            return NonSeekableStream(payload)

        def get_file(self, path):
            raise AssertionError("get_file should not be used for local replay reads")

    real_read_parquet = pd.read_parquet

    def wrapped_read_parquet(obj, *args, **kwargs):
        captured["type"] = type(obj)
        captured["seekable"] = hasattr(obj, "seek")
        return real_read_parquet(obj, *args, **kwargs)

    monkeypatch.setattr(pd, "read_parquet", wrapped_read_parquet)

    out = replay.read_managed_folder_parquet(
        LocalFolder(),
        "/silver/category=event_mapping/module=webapps/instance_name=inst/year=2026/month=08/day=24/source.parquet",
    )

    assert out.shape[0] == 1
    assert captured["type"] is io.BytesIO
    assert captured["seekable"] is True


def test_runnable_reports_delete_failed_after_successful_write(monkeypatch):
    runnable_module = _load_runnable_module()

    local_folder = object()
    monkeypatch.setattr(runnable_module, "build_storage_context", lambda project_key, folder_lookup: type("StorageCtx", (), {"folder_lookup": folder_lookup, "folder_id": "FOLDER_ID"})())
    monkeypatch.setattr(runnable_module.dataiku, "Folder", lambda lookup, project_key, ignore_flow: local_folder)
    monkeypatch.setattr(runnable_module, "iter_managed_folder_paths", lambda storage_ctx, relative_prefix, suffix: ["/silver/category=event_mapping/module=dataset/instance_name=inst/year=2026/month=08/day=24/audit_logs-1786510805050-dataset.parquet"])
    monkeypatch.setattr(runnable_module, "parse_event_mapping_source_path", lambda path: _audit_source_info(path))
    monkeypatch.setattr(runnable_module, "read_managed_folder_parquet", lambda folder, path: pd.DataFrame([{"msgtype": "X", "extras": '{"topic":"generic"}'}]))
    monkeypatch.setattr(
        runnable_module,
        "plan_event_mapping_replay",
        lambda source, source_df: [
            replay.ReplayWritePlan(
                output_path=replay.build_event_mapping_output_path(source=source, module_name="webapps", event_date=source.run_date),
                silver_df=pd.DataFrame([{"instance_name": "inst", "run_ts": "2026-08-24T11:00:00Z"}]),
                dq=replay.DQResult(ok=True, errors=[]),
                module_name="webapps",
                event_date=source.run_date,
            )
        ],
    )
    monkeypatch.setattr(
        runnable_module,
        "upload_event_mapping_replacements",
        lambda target, folder, plans: replay.ReplacementUploadResult(status="uploaded", written_paths=(str(plans[0].output_path),), message="ok"),
    )

    def delete_fail(folder, path):
        raise RuntimeError("cannot delete")

    monkeypatch.setattr(runnable_module, "delete_managed_folder_file", delete_fail)

    runner = runnable_module.MyRunnable(project_key="P", config={}, plugin_config={})
    html = runner.run(lambda _: None)

    assert "Delete Failed After Replacement" in html
    assert "cannot delete" in html




def test_runnable_runs_without_remote_credentials_and_uses_local_project_folder(monkeypatch):
    runnable_module = _load_runnable_module()
    seen = {}

    def fake_storage_context(*, project_key, folder_lookup):
        seen["storage"] = (project_key, folder_lookup)
        return type("StorageCtx", (), {"folder_lookup": folder_lookup, "folder_id": "LOCAL_FOLDER_ID"})()

    def fake_folder(*, lookup, project_key, ignore_flow):
        seen["folder"] = (lookup, project_key, ignore_flow)
        return object()

    monkeypatch.setattr(runnable_module, "build_storage_context", fake_storage_context)
    monkeypatch.setattr(runnable_module.dataiku, "Folder", fake_folder)
    def fake_iter(storage_ctx, relative_prefix, suffix):
        seen["discover_args"] = (storage_ctx, relative_prefix, suffix)
        return []

    monkeypatch.setattr(runnable_module, "iter_managed_folder_paths", fake_iter)

    runner = runnable_module.MyRunnable(
        project_key="LOCAL_PROJECT",
        config={},
        plugin_config={"pulse_primary": {"pulse_partitioned_data": "local_partitioned"}},
    )
    html = runner.run(lambda _: None)

    assert seen["storage"] == ("LOCAL_PROJECT", "local_partitioned")
    assert seen["folder"] == ("LOCAL_FOLDER_ID", "LOCAL_PROJECT", True)
    assert seen["discover_args"][1:] == ("silver/category=event_mapping/", ".parquet")
    assert "Discovered parquet files:" in html


def test_missing_local_folder_fails_without_create_upload_or_delete(monkeypatch):
    runnable_module = _load_runnable_module()
    calls = {"upload": 0, "delete": 0}

    monkeypatch.setattr(
        runnable_module,
        "build_storage_context",
        lambda project_key, folder_lookup: (_ for _ in ()).throw(ValueError("Managed folder 'missing' not found in project 'LOCAL_PROJECT' (by name or id)")),
    )
    monkeypatch.setattr(runnable_module, "upload_event_mapping_replacements", lambda *args, **kwargs: calls.__setitem__("upload", calls["upload"] + 1))
    monkeypatch.setattr(runnable_module, "delete_managed_folder_file", lambda *args, **kwargs: calls.__setitem__("delete", calls["delete"] + 1))

    runner = runnable_module.MyRunnable(
        project_key="LOCAL_PROJECT",
        config={},
        plugin_config={"pulse_primary": {"pulse_partitioned_data": "missing"}},
    )

    with pytest.raises(ValueError, match="Managed folder 'missing' not found"):
        runner.run(lambda _: None)

    assert calls == {"upload": 0, "delete": 0}


def test_local_target_uses_self_project_key_for_upload_and_folder_lookup(monkeypatch):
    runnable_module = _load_runnable_module()
    folder = object()
    source = _audit_source_info()
    captured = {}

    def fake_storage_context(project_key, folder_lookup):
        captured["storage"] = (project_key, folder_lookup)
        return type("StorageCtx", (), {"folder_lookup": folder_lookup, "folder_id": "FOLDER_ID"})()

    monkeypatch.setattr(runnable_module, "build_storage_context", fake_storage_context)
    monkeypatch.setattr(runnable_module.dataiku, "Folder", lambda lookup, project_key, ignore_flow: folder)
    monkeypatch.setattr(runnable_module, "iter_managed_folder_paths", lambda storage_ctx, relative_prefix, suffix: [source.path])
    monkeypatch.setattr(runnable_module, "parse_event_mapping_source_path", lambda path: source)
    monkeypatch.setattr(runnable_module, "read_managed_folder_parquet", lambda found_folder, path: pd.DataFrame([{"msgtype": "X", "extras": '{"topic":"generic"}'}]))
    monkeypatch.setattr(runnable_module, "plan_event_mapping_replay", lambda source, source_df: [])
    monkeypatch.setattr(runnable_module, "delete_managed_folder_file", lambda found_folder, path: captured.setdefault("delete", (found_folder, path)))

    runner = runnable_module.MyRunnable(
        project_key="LOCAL_PROJECT",
        config={},
        plugin_config={"pulse_primary": {"pulse_partitioned_data": "local_partitioned", "pulse_project_key": "REMOTE_PROJECT"}},
    )
    runner.run(lambda _: None)

    assert captured["storage"] == ("LOCAL_PROJECT", "local_partitioned")
    assert captured["delete"] == (folder, source.path)

def test_same_category_same_date_replay_uses_new_filename(monkeypatch):
    source = _audit_source_info()
    monkeypatch.setattr(replay, "next_replay_save_epoch_ms", lambda: 1786510805999)

    out_path = replay.build_event_mapping_output_path(source=source, module_name="dataset", event_date=source.run_date)

    assert str(out_path) != source.path
    assert str(out_path).endswith("/audit_logs-1786510805999-dataset.parquet")
    assert str(out_path).startswith(
        "/silver/category=event_mapping/module=dataset/instance_name=tam-global/year=2026/month=08/day=12/"
    )
    assert source.path.endswith("/audit_logs-1786510805050-dataset.parquet")


def test_changed_category_uses_target_module_name_in_new_filename(monkeypatch):
    source = _audit_source_info()
    monkeypatch.setattr(replay, "next_replay_save_epoch_ms", lambda: 1786510806001)

    out_path = replay.build_event_mapping_output_path(source=source, module_name="webapps", event_date=source.run_date)

    assert str(out_path).endswith("/audit_logs-1786510806001-webapps.parquet")
    assert "/module=webapps/" in str(out_path)


def test_two_replacement_writes_same_millisecond_get_distinct_paths(monkeypatch):
    source = _audit_source_info()
    monkeypatch.setattr(replay.time, "time_ns", lambda: 1786510805000 * 1_000_000)
    monkeypatch.setattr(replay, "_LAST_REPLAY_SAVE_EPOCH_MS", 0)

    first_path = replay.build_event_mapping_output_path(source=source, module_name="dataset", event_date=source.run_date)
    second_path = replay.build_event_mapping_output_path(source=source, module_name="dataset", event_date=source.run_date)

    assert str(first_path).endswith("/audit_logs-1786510805000-dataset.parquet")
    assert str(second_path).endswith("/audit_logs-1786510805001-dataset.parquet")
    assert first_path != second_path


def test_normal_audit_gather_filename_logic_is_unchanged():
    runnable_path = Path(__file__).resolve().parents[2] / "python-runnables" / "data-gather-audit-logs" / "runnable.py"
    text = runnable_path.read_text(encoding="utf-8")

    assert 'parquet_name = f"audit_logs-{run_epoch_ms}-{str(module_name)}.parquet"' in text


def test_get_managed_folder_handle_returns_cached_local_and_remote_handles(monkeypatch):
    target_local = DSSFolderTarget(project_key="P", folder_lookup="partitioned_data")
    remote_client = type("RemoteClient", (), {"host": "https://remote.example"})()
    target_remote = DSSFolderTarget(project_key="P", folder_lookup="partitioned_data", client=remote_client)
    local_folder = object()
    remote_folder = object()

    monkeypatch.setattr(dss_folder_writer, "_FOLDER_HANDLE_CACHE", {})
    monkeypatch.setattr(dss_folder_writer, "_get_or_create_local_folder", lambda target: local_folder)
    monkeypatch.setattr(dss_folder_writer, "_get_or_create_remote_folder", lambda target: remote_folder)

    assert dss_folder_writer.get_managed_folder_handle(target=target_local) is local_folder
    assert dss_folder_writer.get_managed_folder_handle(target=target_local) is local_folder
    assert dss_folder_writer.get_managed_folder_handle(target=target_remote) is remote_folder
    assert dss_folder_writer.get_managed_folder_handle(target=target_remote) is remote_folder


def test_upload_helpers_still_use_existing_resolved_handles(monkeypatch):
    calls = []
    local_folder = type("LocalFolder", (), {"upload_stream": lambda self, path, data: calls.append(("local", path))})()
    remote_folder = type("RemoteFolder", (), {"put_file": lambda self, path, data: calls.append(("remote", path))})()
    monkeypatch.setattr(dss_folder_writer, "_get_or_create_folder", lambda target: local_folder if target.project_key == "L" else remote_folder)

    dss_folder_writer.upload_bytes(
        target=DSSFolderTarget(project_key="L", folder_lookup="partitioned_data"),
        output_path=Path("/silver/x.parquet"),
        output_base_dir=Path("/"),
        content=b"abc",
    )
    dss_folder_writer.upload_bytes(
        target=DSSFolderTarget(project_key="R", folder_lookup="partitioned_data", client=object()),
        output_path=Path("/silver/y.parquet"),
        output_base_dir=Path("/"),
        content=b"abc",
    )

    assert calls == [("local", "silver/x.parquet"), ("remote", "/silver/y.parquet")]


def test_runnable_uses_configured_target_handle_for_list_read_delete(monkeypatch):
    runnable_module = _load_runnable_module()
    folder = object()
    target = type("Target", (), {"project_key": "P", "folder_lookup": "partitioned_data"})()
    used = {}

    monkeypatch.setattr(runnable_module, "build_storage_context", lambda project_key, folder_lookup: type("StorageCtx", (), {"folder_lookup": folder_lookup, "folder_id": "FOLDER_ID"})())
    monkeypatch.setattr(runnable_module.dataiku, "Folder", lambda lookup, project_key, ignore_flow: folder)
    def fake_discover(found_folder):
        used.setdefault("discover", found_folder)
        return ["/silver/category=event_mapping/module=dataset/instance_name=inst/year=2026/month=08/day=24/audit_logs-1786510805050-dataset.parquet"]

    def fake_read(found_folder, path):
        used.setdefault("read", found_folder)
        return pd.DataFrame([{"msgtype": "X", "extras": '{"topic":"generic"}'}])

    monkeypatch.setattr(runnable_module, "iter_managed_folder_paths", lambda storage_ctx, relative_prefix, suffix: fake_discover(folder))
    monkeypatch.setattr(runnable_module, "parse_event_mapping_source_path", lambda path: _audit_source_info(path))
    monkeypatch.setattr(runnable_module, "read_managed_folder_parquet", fake_read)
    monkeypatch.setattr(runnable_module, "plan_event_mapping_replay", lambda source, source_df: [])
    monkeypatch.setattr(runnable_module, "delete_managed_folder_file", lambda found_folder, path: used.setdefault("delete", found_folder))

    runner = runnable_module.MyRunnable(project_key="P", config={}, plugin_config={})
    runner.run(lambda _: None)

    assert used == {"discover": folder, "read": folder, "delete": folder}


def test_dq_invalid_plan_performs_zero_uploads_and_retains_source(monkeypatch):
    plan = replay.ReplayWritePlan(
        output_path=Path("/silver/category=event_mapping/module=webapps/instance_name=inst/year=2026/month=08/day=24/audit_logs-1-webapps.parquet"),
        silver_df=pd.DataFrame([{"instance_name": "inst", "run_ts": "2026-08-24T11:00:00Z"}]),
        dq=replay.DQResult(ok=False, errors=["missing_column:run_ts"]),
        module_name="webapps",
        event_date=_audit_source_info().run_date,
    )
    upload_calls = []
    monkeypatch.setattr(replay, "upload_parquet", lambda **kwargs: upload_calls.append(kwargs))

    result = replay.upload_event_mapping_replacements(target=object(), folder=object(), plans=[plan])

    assert result.status == "dq_failed"
    assert upload_calls == []




def test_direct_upload_failure_cleans_first_written_path_with_configured_folder(monkeypatch):
    folder = object()
    target = object()
    cleanup_calls = []
    upload_calls = []
    plans = [
        replay.ReplayWritePlan(
            output_path=Path("/silver/category=event_mapping/module=dataset/instance_name=tam-global/year=2026/month=08/day=12/audit_logs-1-dataset.parquet"),
            silver_df=pd.DataFrame([{"instance_name": "inst", "run_ts": "2026-08-24T11:00:00Z"}]),
            dq=replay.DQResult(ok=True, errors=[]),
            module_name="dataset",
            event_date=_audit_source_info().run_date,
        ),
        replay.ReplayWritePlan(
            output_path=Path("/silver/category=event_mapping/module=webapps/instance_name=tam-global/year=2026/month=08/day=12/audit_logs-2-webapps.parquet"),
            silver_df=pd.DataFrame([{"instance_name": "inst", "run_ts": "2026-08-24T11:00:00Z"}]),
            dq=replay.DQResult(ok=True, errors=[]),
            module_name="webapps",
            event_date=_audit_source_info().run_date,
        ),
    ]

    def upload_side_effect(**kwargs):
        upload_calls.append(str(kwargs["output_path"]))
        if len(upload_calls) == 2:
            raise RuntimeError("second upload failed")

    def cleanup_side_effect(*, folder, paths):
        cleanup_calls.append((folder, list(paths)))
        return (tuple(paths), [])

    monkeypatch.setattr(replay, "upload_parquet", upload_side_effect)
    monkeypatch.setattr(replay, "cleanup_written_replacements", cleanup_side_effect)

    result = replay.upload_event_mapping_replacements(target=target, folder=folder, plans=plans)

    assert result.status == "upload_failed_cleaned"
    assert result.written_paths == (str(plans[0].output_path),)
    assert result.cleanup_paths == (str(plans[0].output_path),)
    assert cleanup_calls == [(folder, [str(plans[0].output_path)])]


def test_direct_upload_failure_preserves_written_path_evidence_when_cleanup_fails(monkeypatch):
    folder = object()
    target = object()
    plans = [
        replay.ReplayWritePlan(
            output_path=Path("/silver/category=event_mapping/module=dataset/instance_name=tam-global/year=2026/month=08/day=12/audit_logs-1-dataset.parquet"),
            silver_df=pd.DataFrame([{"instance_name": "inst", "run_ts": "2026-08-24T11:00:00Z"}]),
            dq=replay.DQResult(ok=True, errors=[]),
            module_name="dataset",
            event_date=_audit_source_info().run_date,
        ),
        replay.ReplayWritePlan(
            output_path=Path("/silver/category=event_mapping/module=webapps/instance_name=tam-global/year=2026/month=08/day=12/audit_logs-2-webapps.parquet"),
            silver_df=pd.DataFrame([{"instance_name": "inst", "run_ts": "2026-08-24T11:00:00Z"}]),
            dq=replay.DQResult(ok=True, errors=[]),
            module_name="webapps",
            event_date=_audit_source_info().run_date,
        ),
    ]

    def upload_side_effect(**kwargs):
        if str(kwargs["output_path"]) == str(plans[1].output_path):
            raise RuntimeError("second upload failed")

    monkeypatch.setattr(replay, "upload_parquet", upload_side_effect)
    monkeypatch.setattr(
        replay,
        "cleanup_written_replacements",
        lambda *, folder, paths: ((), [f"{paths[0]}: RuntimeError('cleanup failed')"]),
    )

    result = replay.upload_event_mapping_replacements(target=target, folder=folder, plans=plans)

    assert result.status == "upload_failed_cleanup_failed"
    assert result.written_paths == (str(plans[0].output_path),)
    assert result.cleanup_paths == ()
    assert str(plans[0].output_path) in result.message
    assert "cleanup failed" in result.message

def test_multi_category_source_uploads_all_before_source_deletion(monkeypatch):
    runnable_module = _load_runnable_module()
    events = []
    target = type("Target", (), {"project_key": "P", "folder_lookup": "partitioned_data"})()
    folder = object()
    source = _audit_source_info()
    plans = [
        replay.ReplayWritePlan(
            output_path=Path("/silver/category=event_mapping/module=dataset/instance_name=tam-global/year=2026/month=08/day=12/audit_logs-1-dataset.parquet"),
            silver_df=pd.DataFrame([{"instance_name": "inst", "run_ts": "2026-08-24T11:00:00Z"}]),
            dq=replay.DQResult(ok=True, errors=[]),
            module_name="dataset",
            event_date=source.run_date,
        ),
        replay.ReplayWritePlan(
            output_path=Path("/silver/category=event_mapping/module=webapps/instance_name=tam-global/year=2026/month=08/day=12/audit_logs-2-webapps.parquet"),
            silver_df=pd.DataFrame([{"instance_name": "inst", "run_ts": "2026-08-24T11:00:00Z"}]),
            dq=replay.DQResult(ok=True, errors=[]),
            module_name="webapps",
            event_date=source.run_date,
        ),
    ]

    monkeypatch.setattr(runnable_module, "build_storage_context", lambda project_key, folder_lookup: type("StorageCtx", (), {"folder_lookup": folder_lookup, "folder_id": "FOLDER_ID"})())
    monkeypatch.setattr(runnable_module.dataiku, "Folder", lambda lookup, project_key, ignore_flow: folder)
    monkeypatch.setattr(runnable_module, "iter_managed_folder_paths", lambda storage_ctx, relative_prefix, suffix: [source.path])
    monkeypatch.setattr(runnable_module, "parse_event_mapping_source_path", lambda path: source)
    monkeypatch.setattr(runnable_module, "read_managed_folder_parquet", lambda folder, path: pd.DataFrame([{"msgtype": "X", "extras": '{"topic":"generic"}'}]))
    monkeypatch.setattr(runnable_module, "plan_event_mapping_replay", lambda source, source_df: plans)
    monkeypatch.setattr(
        runnable_module,
        "upload_event_mapping_replacements",
        lambda target, folder, plans: events.append(("upload", tuple(str(plan.output_path) for plan in plans))) or replay.ReplacementUploadResult(status="uploaded", written_paths=tuple(str(plan.output_path) for plan in plans), message="ok"),
    )
    monkeypatch.setattr(runnable_module, "delete_managed_folder_file", lambda folder, path: events.append(("delete", path)))

    runner = runnable_module.MyRunnable(project_key="P", config={}, plugin_config={})
    runner.run(lambda _: None)

    assert events[0][0] == "upload"
    assert events[1] == ("delete", source.path)


def test_upload_failure_retains_source_attempts_cleanup_and_reports_partial_write(monkeypatch):
    runnable_module = _load_runnable_module()
    target = type("Target", (), {"project_key": "P", "folder_lookup": "partitioned_data"})()
    folder = object()
    source = _audit_source_info()
    monkeypatch.setattr(runnable_module, "build_storage_context", lambda project_key, folder_lookup: type("StorageCtx", (), {"folder_lookup": folder_lookup, "folder_id": "FOLDER_ID"})())
    monkeypatch.setattr(runnable_module.dataiku, "Folder", lambda lookup, project_key, ignore_flow: folder)
    monkeypatch.setattr(runnable_module, "iter_managed_folder_paths", lambda storage_ctx, relative_prefix, suffix: [source.path])
    monkeypatch.setattr(runnable_module, "parse_event_mapping_source_path", lambda path: source)
    monkeypatch.setattr(runnable_module, "read_managed_folder_parquet", lambda folder, path: pd.DataFrame([{"msgtype": "X", "extras": '{"topic":"generic"}'}]))
    monkeypatch.setattr(
        runnable_module,
        "plan_event_mapping_replay",
        lambda source, source_df: [
            replay.ReplayWritePlan(
                output_path=Path("/silver/category=event_mapping/module=dataset/instance_name=tam-global/year=2026/month=08/day=12/audit_logs-1-dataset.parquet"),
                silver_df=pd.DataFrame([{"instance_name": "inst", "run_ts": "2026-08-24T11:00:00Z"}]),
                dq=replay.DQResult(ok=True, errors=[]),
                module_name="dataset",
                event_date=source.run_date,
            )
        ],
    )
    monkeypatch.setattr(
        runnable_module,
        "upload_event_mapping_replacements",
        lambda target, folder, plans: replay.ReplacementUploadResult(
            status="upload_failed_cleanup_failed",
            written_paths=(str(plans[0].output_path),),
            cleanup_paths=(),
            message=replay.format_partial_write_message(
                base_message="Upload failed: RuntimeError('boom'); cleanup failed: /silver/...",
                written_paths=(str(plans[0].output_path),),
                cleanup_paths=(),
            ),
        ),
    )
    deleted = []
    monkeypatch.setattr(runnable_module, "delete_managed_folder_file", lambda folder, path: deleted.append(path))

    runner = runnable_module.MyRunnable(project_key="P", config={}, plugin_config={})
    html = runner.run(lambda _: None)

    assert deleted == []
    assert "Upload Failed, Replacement Cleanup Failed" in html
    assert "boom" in html
    assert str(Path("/silver/category=event_mapping/module=dataset/instance_name=tam-global/year=2026/month=08/day=12/audit_logs-1-dataset.parquet")) in html


def test_runnable_bounds_progress_counts_and_samples(monkeypatch):
    runnable_module = _load_runnable_module()
    source_paths = [
        f"/silver/category=event_mapping/module=dataset/instance_name=inst/year=2026/month=08/day=24/audit_logs-{1000 + index}-dataset.parquet"
        for index in range(2505)
    ]
    progress_calls = []
    info_logs = []

    class LocalFolder:
        def delete_path(self, path):
            return None

    monkeypatch.setattr(runnable_module, "build_storage_context", lambda project_key, folder_lookup: type("StorageCtx", (), {"folder_lookup": folder_lookup, "folder_id": "FOLDER_ID"})())
    monkeypatch.setattr(runnable_module.dataiku, "Folder", lambda lookup, project_key, ignore_flow: LocalFolder())
    monkeypatch.setattr(runnable_module, "iter_managed_folder_paths", lambda storage_ctx, relative_prefix, suffix: source_paths)
    monkeypatch.setattr(runnable_module, "parse_event_mapping_source_path", lambda path: _audit_source_info(path))
    monkeypatch.setattr(runnable_module, "read_managed_folder_parquet", lambda folder, path: pd.DataFrame([{"msgtype": "X", "extras": '{"topic":"generic"}'}]))
    monkeypatch.setattr(runnable_module, "plan_event_mapping_replay", lambda source, source_df: [])
    monkeypatch.setattr(runnable_module.logger, "info", lambda msg, *args: info_logs.append(msg % args if args else msg))

    runner = runnable_module.MyRunnable(project_key="P", config={}, plugin_config={})
    html = runner.run(progress_calls.append)

    assert progress_calls == [1, 1000, 2000, 2505]
    assert sum("Reload event-mapping progress:" in line for line in info_logs) == 4
    assert "<strong>Dropped:</strong> 2505" in html
    assert "... 2500 more" in html


def test_runnable_suppresses_noisy_debug_loggers_and_bounds_tracebacks(monkeypatch):
    runnable_module = _load_runnable_module()
    source_paths = [
        f"/silver/category=event_mapping/module=dataset/instance_name=inst/year=2026/month=08/day=24/audit_logs-{index}-dataset.parquet"
        for index in range(5)
    ]
    exception_logs = []
    error_logs = []
    botocore_logger = logging.getLogger("botocore")
    urllib3_logger = logging.getLogger("urllib3")
    original_botocore_level = botocore_logger.level
    original_urllib3_level = urllib3_logger.level
    botocore_logger.setLevel(logging.DEBUG)
    urllib3_logger.setLevel(logging.DEBUG)

    monkeypatch.setattr(runnable_module, "build_storage_context", lambda project_key, folder_lookup: type("StorageCtx", (), {"folder_lookup": folder_lookup, "folder_id": "FOLDER_ID"})())
    monkeypatch.setattr(runnable_module.dataiku, "Folder", lambda lookup, project_key, ignore_flow: object())
    monkeypatch.setattr(runnable_module, "iter_managed_folder_paths", lambda storage_ctx, relative_prefix, suffix: source_paths)
    monkeypatch.setattr(runnable_module, "parse_event_mapping_source_path", lambda path: _audit_source_info(path))
    monkeypatch.setattr(runnable_module, "read_managed_folder_parquet", lambda folder, path: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(runnable_module.logger, "exception", lambda msg, *args: exception_logs.append(msg % args if args else msg))
    monkeypatch.setattr(runnable_module.logger, "error", lambda msg, *args: error_logs.append(msg % args if args else msg))

    try:
        runner = runnable_module.MyRunnable(project_key="P", config={}, plugin_config={})
        html = runner.run(lambda _: None)
        botocore_level_during_run = botocore_logger.level
        urllib3_level_during_run = urllib3_logger.level
    finally:
        botocore_logger.setLevel(original_botocore_level)
        urllib3_logger.setLevel(original_urllib3_level)

    assert botocore_level_during_run == logging.WARNING
    assert urllib3_level_during_run == logging.WARNING
    assert len(exception_logs) == runnable_module.TRACEBACK_SAMPLE_LIMIT
    assert len(error_logs) >= 1
    assert "traceback logging suppressed" in error_logs[0]
    assert "<strong>Failed:</strong> 5" in html
