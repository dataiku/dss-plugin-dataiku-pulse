from __future__ import annotations

import json
import os

import pandas as pd

from data_collection.data_normalizer import normalize_silver


def _disable_auto_todo() -> None:
    os.environ["PULSE_AUTO_TODO_FLATTEN"] = "0"


def test_missing_flatten_config_packs_non_global_fields_into_extras():
    _disable_auto_todo()
    df = pd.DataFrame(
        [
            {
                "severity": "info",
                "dashboardid": "dash-1",
                "dashboardname": "Main dashboard",
                "new_future_field": "kept",
            }
        ]
    )

    out = normalize_silver(
        df=df,
        instance_name="inst-a",
        run_ts="2026-08-21T12:34:56Z",
        category="missing_category",
        module="missing_module",
        todo_section="audit",
    )

    assert list(out.columns) == ["instance_name", "run_ts", "extras"]
    row = out.iloc[0].to_dict()
    assert row["instance_name"] == "inst-a"
    assert str(row["run_ts"]) == "2026-08-21 12:34:56+00:00"

    extras = json.loads(row["extras"])
    assert extras == {
        "severity": "info",
        "dashboardid": "dash-1",
        "dashboardname": "Main dashboard",
        "new_future_field": "kept",
    }



def test_missing_flatten_config_with_only_globals_keeps_extras_column_as_none():
    _disable_auto_todo()
    df = pd.DataFrame([{"instance_name": "ignored"}])

    out = normalize_silver(
        df=df,
        instance_name="inst-b",
        run_ts="2026-08-21T12:34:56Z",
        category="missing_category",
        module="missing_module",
        todo_section="audit",
    )

    assert list(out.columns) == ["instance_name", "run_ts", "extras"]
    row = out.iloc[0].to_dict()
    assert row["instance_name"] == "inst-b"
    assert str(row["run_ts"]) == "2026-08-21 12:34:56+00:00"
    assert row["extras"] is None



def test_configured_flatten_path_keeps_yaml_columns_top_level_and_packs_rest():
    df = pd.DataFrame(
        [
            {
                "timestamp": "2026-07-21T15:12:26Z",
                "dataiku_category": "webapps",
                "project_key": "proj_1",
                "webappid": "webapp-1",
                "custom_field": "rolled-up",
            }
        ]
    )

    out = normalize_silver(
        df=df,
        instance_name="inst-c",
        run_ts="2026-08-21T12:34:56Z",
        category="audit_dataiku_usage",
        module="audit_metadata",
        todo_section="audit",
        flatten_base=("audit_dataiku_usage", "audit_metadata"),
        flatten_variant="webapps",
    )

    assert list(out.columns) == [
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
        "webappid",
        "webappid_source_call",
        "run_ts",
        "extras",
    ]
    row = out.iloc[0].to_dict()
    assert row["instance_name"] == "inst-c"
    assert row["project_key"] == "proj_1"
    assert row["webappid"] == "webapp-1"
    assert row["project_key_source_call"] is pd.NA or pd.isna(row["project_key_source_call"])
    extras = json.loads(row["extras"])
    assert extras == {"custom_field": "rolled-up"}



def test_project_key_canonicalization_and_casting_remain_unchanged_without_config():
    _disable_auto_todo()
    df = pd.DataFrame(
        [
            {
                "projectkey": "PROJ_2",
                "createdon": "2026-08-20T01:02:03Z",
                "status": "active",
            }
        ]
    )

    out = normalize_silver(
        df=df,
        instance_name="inst-d",
        run_ts="2026-08-21T12:34:56Z",
        category="missing_category",
        module="missing_module",
        todo_section="audit",
    )

    assert list(out.columns) == ["instance_name", "run_ts", "extras"]
    row = out.iloc[0].to_dict()
    extras = json.loads(row["extras"])
    assert extras["project_key"] == "PROJ_2"
    assert extras["createdon"] == "2026-08-20T01:02:03Z"
    assert extras["status"] == "active"
