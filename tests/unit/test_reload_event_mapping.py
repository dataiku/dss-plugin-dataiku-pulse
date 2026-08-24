from __future__ import annotations

import importlib.util
import io
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
    assert out.loc[0, "msgtype"] == "LOGIN"
    assert out.loc[0, "topic"] == "generic"
    assert out.loc[0, "custom"] == "value"


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

    class _Resp:
        def __init__(self):
            self.raw = io.BytesIO(payload)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class RemoteFolder:
        def __init__(self):
            self.deleted = []

        def list_contents(self):
            return {"items": [{"path": "/silver/category=event_mapping/module=webapps/instance_name=inst/year=2026/month=08/day=24/source.parquet"}]}

        def get_file(self, path):
            return _Resp()

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
    assert replay.read_managed_folder_parquet(remote, "/silver/category=event_mapping/module=webapps/instance_name=inst/year=2026/month=08/day=24/source.parquet").shape[0] == 1
    replay.delete_managed_folder_file(remote, "/silver/category=event_mapping/module=webapps/instance_name=inst/year=2026/month=08/day=24/source.parquet")
    assert remote.deleted == ["/silver/category=event_mapping/module=webapps/instance_name=inst/year=2026/month=08/day=24/source.parquet"]


def test_runnable_reports_delete_failed_after_successful_write(monkeypatch):
    runnable_module = _load_runnable_module()

    class FakeFolder:
        pass

    fake_folder = FakeFolder()
    monkeypatch.setattr(runnable_module, "build_context", lambda plugin_config: type("Ctx", (), {"param_set": {}, "remote_client": object()})())
    monkeypatch.setattr(runnable_module, "ensure_output_folder", lambda param_set, remote_client: type("Target", (), {"project_key": "P", "folder_lookup": "partitioned_data"})())
    monkeypatch.setattr(runnable_module, "get_managed_folder_handle", lambda target: fake_folder)
    monkeypatch.setattr(runnable_module, "discover_event_mapping_paths", lambda folder: ["/silver/category=event_mapping/module=dataset/instance_name=inst/year=2026/month=08/day=24/audit_logs-1786510805050-dataset.parquet"])
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

    monkeypatch.setattr(runnable_module, "build_context", lambda plugin_config: type("Ctx", (), {"param_set": {}, "remote_client": object()})())
    monkeypatch.setattr(runnable_module, "ensure_output_folder", lambda param_set, remote_client: target)
    monkeypatch.setattr(runnable_module, "get_managed_folder_handle", lambda target: folder)
    def fake_discover(found_folder):
        used.setdefault("discover", found_folder)
        return ["/silver/category=event_mapping/module=dataset/instance_name=inst/year=2026/month=08/day=24/audit_logs-1786510805050-dataset.parquet"]

    def fake_read(found_folder, path):
        used.setdefault("read", found_folder)
        return pd.DataFrame([{"msgtype": "X", "extras": '{"topic":"generic"}'}])

    monkeypatch.setattr(runnable_module, "discover_event_mapping_paths", fake_discover)
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

    monkeypatch.setattr(runnable_module, "build_context", lambda plugin_config: type("Ctx", (), {"param_set": {}, "remote_client": object()})())
    monkeypatch.setattr(runnable_module, "ensure_output_folder", lambda param_set, remote_client: target)
    monkeypatch.setattr(runnable_module, "get_managed_folder_handle", lambda target: folder)
    monkeypatch.setattr(runnable_module, "discover_event_mapping_paths", lambda folder: [source.path])
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
    monkeypatch.setattr(runnable_module, "build_context", lambda plugin_config: type("Ctx", (), {"param_set": {}, "remote_client": object()})())
    monkeypatch.setattr(runnable_module, "ensure_output_folder", lambda param_set, remote_client: target)
    monkeypatch.setattr(runnable_module, "get_managed_folder_handle", lambda target: folder)
    monkeypatch.setattr(runnable_module, "discover_event_mapping_paths", lambda folder: [source.path])
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
