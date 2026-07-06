from __future__ import annotations

import pandas as pd

from data_collection.data_normalizer.dq import check_silver_dq


def test_ok_result_for_healthy_frame():
    df = pd.DataFrame(
        {
            "instance_name": ["i1", "i1"],
            "run_ts": [pd.Timestamp("2026-07-02", tz="UTC")] * 2,
        }
    )

    result = check_silver_dq(df)

    assert result.ok is True
    assert result.errors == []


def test_missing_required_non_null_column():
    df = pd.DataFrame({"instance_name": ["i1"]})

    result = check_silver_dq(df, required_non_null=["instance_name", "run_ts"])

    assert result.ok is False
    assert "missing_column:run_ts" in result.errors


def test_flatten_required_drift_majority_null_is_error():
    # 3 of 4 contract columns entirely NULL -> 75% > 50% threshold -> error.
    df = pd.DataFrame(
        {
            "instance_name": ["i1", "i1"],
            "run_ts": [pd.Timestamp("2026-07-02", tz="UTC")] * 2,
            "a": [None, None],
            "b": [None, None],
            "c": [None, None],
            "d": ["x", "y"],
        }
    )

    result = check_silver_dq(
        df, flatten_required=["instance_name", "run_ts", "a", "b", "c", "d"]
    )

    assert result.ok is False
    assert any(e.startswith("required_all_null:3/4") for e in result.errors)


def test_flatten_required_drift_minority_null_is_ok():
    # Only 1 of 4 contract columns entirely NULL -> 25% <= 50% threshold -> ok.
    df = pd.DataFrame(
        {
            "instance_name": ["i1", "i1"],
            "run_ts": [pd.Timestamp("2026-07-02", tz="UTC")] * 2,
            "a": [None, None],
            "b": ["x", "y"],
            "c": ["x", "y"],
            "d": ["x", "y"],
        }
    )

    result = check_silver_dq(
        df, flatten_required=["instance_name", "run_ts", "a", "b", "c", "d"]
    )

    assert result.ok is True
    assert result.errors == []
