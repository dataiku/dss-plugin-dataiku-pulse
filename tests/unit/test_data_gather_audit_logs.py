from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd


RUNNABLE_PATH = Path(__file__).resolve().parents[2] / "python-runnables" / "data-gather-audit-logs" / "runnable.py"


def _load_runnable_module():
    spec = importlib.util.spec_from_file_location("data_gather_audit_logs_runnable", RUNNABLE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_audit_log(audit_dir: Path, rows: list[dict]) -> Path:
    audit_dir.mkdir(parents=True, exist_ok=True)
    path = audit_dir / "audit.log"
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    os.utime(path, (1_788_300_000, 1_788_300_000))
    return path


def _install_common_fakes(monkeypatch, runnable_module, audit_dir: Path, *, chunk_size: int = 1):
    updates = []
    target = object()
    ctx = SimpleNamespace(
        param_set={
            "pulse_audit_logs_chunk_size": chunk_size,
            "pulse_backup_audit_logs": False,
        },
        local_client=object(),
        remote_client=object(),
    )

    monkeypatch.setattr(runnable_module, "build_context", lambda plugin_config: ctx)
    monkeypatch.setattr(runnable_module, "datetime", _FixedDatetime)
    monkeypatch.setattr(runnable_module, "resolve_audit_logs_dir", lambda client, repo_root: audit_dir)
    monkeypatch.setattr(runnable_module, "get_instance_name", lambda client: "dss-prod")
    monkeypatch.setattr(runnable_module, "ensure_output_folder", lambda param_set, remote_client: target)
    monkeypatch.setattr(
        runnable_module.MyRunnable,
        "_read_audit_delta",
        lambda self, client: pd.Timestamp("2026-09-01T00:00:00Z"),
    )
    monkeypatch.setattr(
        runnable_module.MyRunnable,
        "_update_audit_delta",
        lambda self, client, value: updates.append(value),
    )
    return target, updates


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 2, 14, 0, tzinfo=tz or timezone.utc)


def _ok_dq(runnable_module):
    return SimpleNamespace(ok=True, errors=[])


def test_event_mapping_writes_each_chunk_directly_without_final_concat(monkeypatch, tmp_path):
    runnable_module = _load_runnable_module()
    audit_dir = tmp_path / "audit"
    _write_audit_log(
        audit_dir,
        [
            {
                "timestamp": "2026-09-02T10:00:00Z",
                "topic": "generic",
                "message_msgType": "WEBAPP_VIEW",
            },
            {
                "timestamp": "2026-09-02T10:01:00Z",
                "topic": "generic",
                "message_msgType": "WEBAPP_VIEW",
            },
        ],
    )
    target, updates = _install_common_fakes(monkeypatch, runnable_module, audit_dir, chunk_size=1)
    uploads = []

    processor = SimpleNamespace(
        main=lambda df: pd.DataFrame(
            [
                {
                    "dataiku_category": "webapps",
                    "timestamp": df.iloc[0]["timestamp"],
                    "message_msgType": df.iloc[0]["message_msgType"],
                }
            ]
        )
    )
    monkeypatch.setattr(runnable_module, "_load_processor_names", lambda: ["event_mapping"])
    monkeypatch.setattr(runnable_module, "_load_processors", lambda names: ({"event_mapping": processor}, {}))
    monkeypatch.setattr(runnable_module, "check_silver_dq", lambda df: _ok_dq(runnable_module))
    monkeypatch.setattr(
        runnable_module,
        "upload_parquet",
        lambda **kwargs: uploads.append(kwargs),
    )
    monkeypatch.setattr(
        runnable_module.pd,
        "read_parquet",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("final read_parquet must not run")),
    )
    monkeypatch.setattr(
        runnable_module.pd,
        "concat",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("final concat must not run")),
    )

    result = runnable_module.MyRunnable(project_key="P", config={}, plugin_config={}).run(progress_callback=None)

    assert len(uploads) == 2
    assert [call["target"] for call in uploads] == [target, target]
    assert [call["compression"] for call in uploads] == ["snappy", "snappy"]
    paths = [call["output_path"].as_posix() for call in uploads]
    assert paths == [
        "partitioned_data/silver/category=event_mapping/module=webapps/instance_name=dss-prod/year=2026/month=09/day=02/audit_logs-1788357600000-1.parquet",
        "partitioned_data/silver/category=event_mapping/module=webapps/instance_name=dss-prod/year=2026/month=09/day=02/audit_logs-1788357600000-2.parquet",
    ]
    assert [call["output_base_dir"].as_posix() for call in uploads] == ["partitioned_data", "partitioned_data"]
    assert len({path.rsplit("/", 1)[-1] for path in paths}) == 2
    assert updates == ["2026-09-02T10:01:00+00:00"]
    event_row = next(record for record in result.records if record[0] == "event_mapping")
    assert event_row[1:4] == ["2", "2", "2"]
    summary = next(record for record in result.records if record[0] == "__summary__")
    assert "silver_write_failures=0" in summary[4]
    assert "final_event_mapping_upload_failures" not in summary[4]


def test_event_mapping_uses_silver_event_date_and_non_event_processor_is_unchanged(monkeypatch, tmp_path):
    runnable_module = _load_runnable_module()
    audit_dir = tmp_path / "audit"
    _write_audit_log(
        audit_dir,
        [
            {
                "timestamp": "2026-09-02T23:59:00Z",
                "topic": "generic",
                "message_msgType": "MIXED_EVENT",
                "message_authSource": "USER_FROM_UI",
                "message_login": "alice",
            }
        ],
    )
    _install_common_fakes(monkeypatch, runnable_module, audit_dir, chunk_size=50)
    uploads = []

    processors = {
        "event_mapping": SimpleNamespace(
            main=lambda df: pd.DataFrame(
                [
                    {
                        "dataiku_category": "webapps",
                        "timestamp": "2026-09-03T00:01:00Z",
                    }
                ]
            )
        ),
        "users": SimpleNamespace(
            main=lambda df: pd.DataFrame(
                [
                    {
                        "dataiku_category": "user_login_activity",
                        "timestamp": "2026-09-02T23:59:00Z",
                    }
                ]
            )
        ),
    }

    def fake_normalize_silver(*, df, instance_name, run_ts, category, module, todo_section, flatten_base, flatten_variant):
        out = df.copy()
        out.insert(0, "instance_name", instance_name)
        out["run_ts"] = run_ts
        out["normalized_category"] = category
        out["normalized_module"] = module
        return out

    monkeypatch.setattr(runnable_module, "_load_processor_names", lambda: ["event_mapping", "users"])
    monkeypatch.setattr(runnable_module, "_load_processors", lambda names: (processors, {}))
    monkeypatch.setattr(runnable_module, "normalize_silver", fake_normalize_silver)
    monkeypatch.setattr(runnable_module, "check_silver_dq", lambda df: _ok_dq(runnable_module))
    monkeypatch.setattr(runnable_module, "upload_parquet", lambda **kwargs: uploads.append(kwargs))

    runnable_module.MyRunnable(project_key="P", config={}, plugin_config={}).run(progress_callback=None)

    assert [call["output_path"].as_posix() for call in uploads] == [
        "partitioned_data/silver/category=event_mapping/module=webapps/instance_name=dss-prod/year=2026/month=09/day=03/audit_logs-1788357600000-1.parquet",
        "partitioned_data/silver/category=users/module=user_login_activity/instance_name=dss-prod/year=2026/month=09/day=02/audit_logs-1788357600000-1.parquet",
    ]
    assert uploads[0]["df"].iloc[0]["normalized_category"] == "event_mapping"
    assert uploads[0]["df"].iloc[0]["normalized_module"] == "webapps"
    assert uploads[1]["df"].iloc[0]["normalized_category"] == "users"
    assert uploads[1]["df"].iloc[0]["normalized_module"] == "user_login_activity"


def test_event_mapping_upload_failure_is_recorded_and_skips_cursor(monkeypatch, tmp_path, caplog):
    runnable_module = _load_runnable_module()
    audit_dir = tmp_path / "audit"
    _write_audit_log(
        audit_dir,
        [
            {
                "timestamp": "2026-09-02T10:00:00Z",
                "topic": "generic",
                "message_msgType": "WEBAPP_VIEW",
            }
        ],
    )
    _target, updates = _install_common_fakes(monkeypatch, runnable_module, audit_dir, chunk_size=1)

    processor = SimpleNamespace(
        main=lambda df: pd.DataFrame(
            [
                {
                    "dataiku_category": "webapps",
                    "timestamp": df.iloc[0]["timestamp"],
                }
            ]
        )
    )
    monkeypatch.setattr(runnable_module, "_load_processor_names", lambda: ["event_mapping"])
    monkeypatch.setattr(runnable_module, "_load_processors", lambda names: ({"event_mapping": processor}, {}))
    monkeypatch.setattr(runnable_module, "check_silver_dq", lambda df: _ok_dq(runnable_module))

    def fail_upload(**kwargs):
        raise RuntimeError("managed folder write failed")

    monkeypatch.setattr(runnable_module, "upload_parquet", fail_upload)

    with caplog.at_level("ERROR"):
        result = runnable_module.MyRunnable(project_key="P", config={}, plugin_config={}).run(progress_callback=None)

    assert updates == []
    event_row = next(record for record in result.records if record[0] == "event_mapping")
    assert event_row[1:4] == ["1", "1", "0"]
    assert "managed folder write failed" in caplog.text
    summary = next(record for record in result.records if record[0] == "__summary__")
    assert "write_failures=1" in summary[4]
    assert "silver_write_failures=1" in summary[4]
    assert "event_mapping_write_failures=1" in summary[4]
    assert summary[4].endswith("cursor_to=")


def test_successful_multi_chunk_event_mapping_updates_cursor_after_outputs(monkeypatch, tmp_path):
    runnable_module = _load_runnable_module()
    audit_dir = tmp_path / "audit"
    _write_audit_log(
        audit_dir,
        [
            {
                "timestamp": "2026-09-02T10:00:00Z",
                "topic": "generic",
                "message_msgType": "WEBAPP_VIEW",
            },
            {
                "timestamp": "2026-09-02T10:05:00Z",
                "topic": "generic",
                "message_msgType": "WEBAPP_VIEW",
            },
        ],
    )
    _target, updates = _install_common_fakes(monkeypatch, runnable_module, audit_dir, chunk_size=1)
    events = []

    processor = SimpleNamespace(
        main=lambda df: pd.DataFrame(
            [
                {
                    "dataiku_category": "webapps",
                    "timestamp": df.iloc[0]["timestamp"],
                }
            ]
        )
    )
    monkeypatch.setattr(runnable_module, "_load_processor_names", lambda: ["event_mapping"])
    monkeypatch.setattr(runnable_module, "_load_processors", lambda names: ({"event_mapping": processor}, {}))
    monkeypatch.setattr(runnable_module, "check_silver_dq", lambda df: _ok_dq(runnable_module))
    monkeypatch.setattr(
        runnable_module,
        "upload_parquet",
        lambda **kwargs: events.append(("upload", kwargs["output_path"].name)),
    )
    monkeypatch.setattr(
        runnable_module.MyRunnable,
        "_update_audit_delta",
        lambda self, client, value: events.append(("cursor", value)) or updates.append(value),
    )

    runnable_module.MyRunnable(project_key="P", config={}, plugin_config={}).run(progress_callback=None)

    assert events == [
        ("upload", "audit_logs-1788357600000-1.parquet"),
        ("upload", "audit_logs-1788357600000-2.parquet"),
        ("cursor", "2026-09-02T10:05:00+00:00"),
    ]
    assert updates == ["2026-09-02T10:05:00+00:00"]


def test_event_mapping_failure_between_successes_keeps_cursor_unchanged(monkeypatch, tmp_path, caplog):
    runnable_module = _load_runnable_module()
    audit_dir = tmp_path / "audit"
    _write_audit_log(
        audit_dir,
        [
            {
                "timestamp": "2026-09-02T10:00:00Z",
                "topic": "generic",
                "message_msgType": "WEBAPP_VIEW",
            },
            {
                "timestamp": "2026-09-02T10:01:00Z",
                "topic": "generic",
                "message_msgType": "WEBAPP_VIEW",
            },
            {
                "timestamp": "2026-09-02T10:05:00Z",
                "topic": "generic",
                "message_msgType": "WEBAPP_VIEW",
            },
        ],
    )
    _target, updates = _install_common_fakes(monkeypatch, runnable_module, audit_dir, chunk_size=1)
    events = []

    processor = SimpleNamespace(
        main=lambda df: pd.DataFrame(
            [
                {
                    "dataiku_category": "webapps",
                    "timestamp": df.iloc[0]["timestamp"],
                }
            ]
        )
    )
    monkeypatch.setattr(runnable_module, "_load_processor_names", lambda: ["event_mapping"])
    monkeypatch.setattr(runnable_module, "_load_processors", lambda names: ({"event_mapping": processor}, {}))
    monkeypatch.setattr(runnable_module, "check_silver_dq", lambda df: _ok_dq(runnable_module))

    def upload_side_effect(**kwargs):
        output_name = kwargs["output_path"].name
        events.append(("upload", output_name))
        if output_name.endswith("-2.parquet"):
            raise RuntimeError("second chunk write failed")

    monkeypatch.setattr(runnable_module, "upload_parquet", upload_side_effect)
    monkeypatch.setattr(
        runnable_module.MyRunnable,
        "_update_audit_delta",
        lambda self, client, value: events.append(("cursor", value)) or updates.append(value),
    )

    with caplog.at_level("WARNING"):
        result = runnable_module.MyRunnable(project_key="P", config={}, plugin_config={}).run(progress_callback=None)

    assert events == [
        ("upload", "audit_logs-1788357600000-1.parquet"),
        ("upload", "audit_logs-1788357600000-2.parquet"),
        ("upload", "audit_logs-1788357600000-3.parquet"),
    ]
    assert updates == []
    assert "Skipping cursor update because an event_mapping SILVER write failed" in caplog.text
    event_row = next(record for record in result.records if record[0] == "event_mapping")
    assert event_row[1:4] == ["3", "3", "2"]
    assert "second chunk write failed" in event_row[4]
    summary = next(record for record in result.records if record[0] == "__summary__")
    assert "event_mapping_write_failures=1" in summary[4]
    assert summary[4].endswith("cursor_to=")


def test_memory_diagnostics_log_required_stages_and_high_water(monkeypatch, tmp_path, caplog):
    runnable_module = _load_runnable_module()
    audit_dir = tmp_path / "audit"
    _write_audit_log(
        audit_dir,
        [
            {
                "timestamp": "2026-09-02T10:00:00Z",
                "topic": "generic",
                "message_msgType": "WEBAPP_VIEW",
            }
        ],
    )
    _target, updates = _install_common_fakes(monkeypatch, runnable_module, audit_dir, chunk_size=1)
    rss_values = iter([10, 20, 30, 40, 50, 60, 70, 80])
    memory_by_stage = {
        "json_chunk_yielded": 100,
        "timestamp_delta_filtered": 200,
        "generic_prepared": 300,
        "processor_input": 400,
        "processor_output": 500,
        "silver_upload_before": 900,
        "silver_upload_after": 700,
        "processor_silver_groups_done": 600,
    }

    processor = SimpleNamespace(
        main=lambda df: pd.DataFrame(
            [
                {
                    "dataiku_category": "webapps",
                    "timestamp": df.iloc[0]["timestamp"],
                }
            ]
        )
    )
    monkeypatch.setattr(runnable_module, "_load_processor_names", lambda: ["event_mapping"])
    monkeypatch.setattr(runnable_module, "_load_processors", lambda names: ({"event_mapping": processor}, {}))
    monkeypatch.setattr(runnable_module, "check_silver_dq", lambda df: _ok_dq(runnable_module))
    monkeypatch.setattr(runnable_module, "upload_parquet", lambda **kwargs: None)
    monkeypatch.setattr(runnable_module, "_process_rss_mb", lambda: next(rss_values))
    monkeypatch.setattr(
        runnable_module,
        "_dataframe_deep_memory_bytes",
        lambda df: memory_by_stage[getattr(runnable_module, "_active_test_stage")],
    )

    original_log_memory_stage = runnable_module._log_memory_stage

    def log_memory_stage_with_stage_memory(**kwargs):
        monkeypatch.setattr(runnable_module, "_active_test_stage", kwargs["stage"], raising=False)
        return original_log_memory_stage(**kwargs)

    monkeypatch.setattr(runnable_module, "_log_memory_stage", log_memory_stage_with_stage_memory)

    with caplog.at_level("INFO"):
        result = runnable_module.MyRunnable(project_key="P", config={}, plugin_config={}).run(progress_callback=None)

    stage_lines = [message for message in caplog.messages if message.startswith("Audit memory stage")]
    assert len(stage_lines) == 8
    for stage in memory_by_stage:
        assert any(f"stage={stage}" in message for message in stage_lines)
    assert all("chunk_idx=1" in message for message in stage_lines)
    assert all("file_name=audit.log" in message for message in stage_lines)
    assert all("input_rows=1" in message for message in stage_lines)
    assert any(
        "stage=silver_upload_before" in message
        and "processor=event_mapping" in message
        and "module=webapps" in message
        and "normalized_rows=1" in message
        for message in stage_lines
    )
    assert any(message.startswith("Audit memory high water max_rss_mb=80") for message in caplog.messages)
    summary = next(record for record in result.records if record[0] == "__summary__")
    assert "memory_max_rss_mb=80" in summary[4]
    assert "memory_max_rss_stage=processor_silver_groups_done" in summary[4]
    assert "memory_max_df_bytes=900" in summary[4]
    assert "memory_max_df_stage=silver_upload_before" in summary[4]
    assert "memory_max_df_processor=event_mapping" in summary[4]
    assert "memory_max_df_module=webapps" in summary[4]
    assert updates == ["2026-09-02T10:00:00+00:00"]


def test_memory_measurement_failures_are_non_fatal(monkeypatch, tmp_path, caplog):
    runnable_module = _load_runnable_module()
    audit_dir = tmp_path / "audit"
    _write_audit_log(
        audit_dir,
        [
            {
                "timestamp": "2026-09-02T10:00:00Z",
                "topic": "generic",
                "message_msgType": "WEBAPP_VIEW",
            }
        ],
    )
    _target, updates = _install_common_fakes(monkeypatch, runnable_module, audit_dir, chunk_size=1)
    uploads = []

    processor = SimpleNamespace(
        main=lambda df: pd.DataFrame(
            [
                {
                    "dataiku_category": "webapps",
                    "timestamp": df.iloc[0]["timestamp"],
                }
            ]
        )
    )
    monkeypatch.setattr(runnable_module, "_load_processor_names", lambda: ["event_mapping"])
    monkeypatch.setattr(runnable_module, "_load_processors", lambda names: ({"event_mapping": processor}, {}))
    monkeypatch.setattr(runnable_module, "check_silver_dq", lambda df: _ok_dq(runnable_module))
    monkeypatch.setattr(runnable_module, "upload_parquet", lambda **kwargs: uploads.append(kwargs))
    monkeypatch.setattr(
        runnable_module,
        "_process_rss_mb",
        lambda: (_ for _ in ()).throw(RuntimeError("rss unavailable")),
    )
    monkeypatch.setattr(
        runnable_module,
        "_dataframe_deep_memory_bytes",
        lambda df: (_ for _ in ()).throw(RuntimeError("df memory unavailable")),
    )

    with caplog.at_level("INFO"):
        result = runnable_module.MyRunnable(project_key="P", config={}, plugin_config={}).run(progress_callback=None)

    assert len(uploads) == 1
    assert updates == ["2026-09-02T10:00:00+00:00"]
    assert any(
        message.startswith("Audit memory stage")
        and "rss_mb=None" in message
        and "df_memory_bytes=None" in message
        for message in caplog.messages
    )
    summary = next(record for record in result.records if record[0] == "__summary__")
    assert "memory_max_rss_mb=;" in summary[4]
    assert "memory_max_df_bytes=;" in summary[4]
