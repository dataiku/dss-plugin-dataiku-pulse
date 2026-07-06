from __future__ import annotations

from pathlib import Path

import pandas as pd

from data_collection.data_normalizer.silver import normalize_silver

FLATTEN_COLUMNS_DIR = (
    Path(__file__).resolve().parents[2]
    / "python-lib"
    / "data_collection"
    / "data_normalizer"
    / "schema_consistency"
    / "flatten_columns"
)


def _snapshot_files() -> set[str]:
    return {
        str(p.relative_to(FLATTEN_COLUMNS_DIR))
        for p in FLATTEN_COLUMNS_DIR.rglob("*")
        if p.is_file()
    }


def test_p01_regression_extras_and_casting():
    """Required flatten columns get their real dtype casts; unknown columns
    are rolled into `extras` (JSON string) instead of leaking as raw columns
    (this is the P0.1 bug class: singular columns silently bypassing the
    flatten contract)."""

    df = pd.DataFrame(
        {
            "project_key": ["PROJ1"],
            "datasets_name": ["mydataset"],
            "datasets_smartname": ["PROJ1.mydataset"],
            "datasets_managed": ["true"],
            "datasets_versiontag_versionnumber": ["3"],
            "datasets_versiontag_lastmodifiedon": [1700000000000],
            "some_random_field": ["keep-me"],
        }
    )

    out = normalize_silver(
        df=df,
        instance_name="i1",
        run_ts="2026-07-02T00:00:00+00:00",
        category="datasets",
        module="project_metadata",
        todo_section="project",
    )

    # instance_name is first and equals the passed value.
    assert out.columns[0] == "instance_name"
    assert out.loc[0, "instance_name"] == "i1"

    # Unknown column does not survive as a raw column...
    assert "some_random_field" not in out.columns

    # ...but its value is preserved inside `extras`.
    assert "extras" in out.columns
    assert "keep-me" in out.loc[0, "extras"]
    assert "some_random_field" in out.loc[0, "extras"]

    # Boolean cast
    assert out["datasets_managed"].dtype == "boolean"
    assert out.loc[0, "datasets_managed"] == True  # noqa: E712

    # Numeric cast
    assert pd.api.types.is_numeric_dtype(out["datasets_versiontag_versionnumber"])
    assert out.loc[0, "datasets_versiontag_versionnumber"] == 3

    # Datetime cast, tz-aware
    assert pd.api.types.is_datetime64_any_dtype(out["datasets_versiontag_lastmodifiedon"])
    assert out["datasets_versiontag_lastmodifiedon"].dt.tz is not None
    assert out.loc[0, "datasets_versiontag_lastmodifiedon"] == pd.Timestamp(
        1700000000000, unit="ms", tz="UTC"
    )


def test_stats_out_known_category_has_required_columns():
    stats_out: dict = {}

    normalize_silver(
        df=pd.DataFrame({"project_key": ["PROJ1"], "datasets_name": ["ds1"]}),
        instance_name="i1",
        run_ts="2026-07-02T00:00:00+00:00",
        category="datasets",
        module="project_metadata",
        todo_section="project",
        stats_out=stats_out,
    )

    assert stats_out["flatten_config_missing"] is False
    assert len(stats_out["required_columns"]) > 0
    assert "datasets_name" in stats_out["required_columns"]


def test_stats_out_bogus_category_missing_and_no_todo_written(monkeypatch):
    # Default is off - make sure no auto-TODO env var is set for this test.
    monkeypatch.delenv("PULSE_AUTO_TODO_FLATTEN", raising=False)

    before = _snapshot_files()

    stats_out: dict = {}
    normalize_silver(
        df=pd.DataFrame({"foo": ["bar"]}),
        instance_name="i1",
        run_ts="2026-07-02T00:00:00+00:00",
        category="no_such_category_xyz",
        module="project_metadata",
        todo_section="project",
        stats_out=stats_out,
    )

    after = _snapshot_files()

    assert stats_out["flatten_config_missing"] is True
    assert stats_out["required_columns"] == []
    assert before == after, f"unexpected TODO file(s) written: {after - before}"


def test_instance_name_is_first_column_no_category():
    out = normalize_silver(
        df=pd.DataFrame({"a": [1], "instance_name": ["ignored"]}),
        instance_name="i1",
        run_ts=None,
    )

    assert out.columns[0] == "instance_name"
    assert out.loc[0, "instance_name"] == "i1"
