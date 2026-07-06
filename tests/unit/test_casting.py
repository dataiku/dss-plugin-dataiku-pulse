from __future__ import annotations

import pandas as pd

from data_collection.data_normalizer.casting import (
    cast_boolean_columns,
    cast_datetime_columns,
    cast_numeric_columns,
)


def test_cast_datetime_epoch_ms_plausible_value():
    ts = pd.Timestamp("2026-03-15T12:00:00", tz="UTC")
    epoch_ms = int(ts.timestamp() * 1000)

    df = pd.DataFrame({"ts": [epoch_ms]})
    out = cast_datetime_columns(df, ["ts"])

    assert pd.api.types.is_datetime64_any_dtype(out["ts"])
    assert out["ts"].iloc[0] == ts


def test_cast_datetime_epoch_seconds_around_2026_stays_intact():
    ts = pd.Timestamp("2026-06-01T00:00:00", tz="UTC")
    epoch_s = int(ts.timestamp())

    df = pd.DataFrame({"ts": [epoch_s]})
    out = cast_datetime_columns(df, ["ts"])

    assert pd.api.types.is_datetime64_any_dtype(out["ts"])
    assert out["ts"].iloc[0] == ts


def test_cast_datetime_out_of_range_becomes_nat():
    # Already-datetime64 branch: values outside [1990-01-01, now+10y] are
    # nulled by the sanity clamp instead of being stored.
    df = pd.DataFrame(
        {
            "ts": pd.Series(
                [
                    pd.Timestamp("1969-01-01", tz="UTC"),
                    pd.Timestamp("2025-06-01", tz="UTC"),
                ]
            )
        }
    )

    out = cast_datetime_columns(df, ["ts"])

    assert pd.isna(out["ts"].iloc[0])
    assert not pd.isna(out["ts"].iloc[1])
    assert out["ts"].iloc[1] == pd.Timestamp("2025-06-01", tz="UTC")


def test_cast_boolean_columns_maps_common_values():
    df = pd.DataFrame({"b": ["true", "false", 1, 0, None]})
    out = cast_boolean_columns(df, ["b"])

    assert out["b"].dtype == "boolean"
    assert out["b"].iloc[0] == True  # noqa: E712
    assert out["b"].iloc[1] == False  # noqa: E712
    assert out["b"].iloc[2] == True  # noqa: E712
    assert out["b"].iloc[3] == False  # noqa: E712
    assert pd.isna(out["b"].iloc[4])


def test_cast_numeric_columns_coerces_bad_strings_to_nan():
    df = pd.DataFrame({"n": ["3", "abc", None]})
    out = cast_numeric_columns(df, ["n"])

    assert pd.api.types.is_numeric_dtype(out["n"])
    assert out["n"].iloc[0] == 3
    assert pd.isna(out["n"].iloc[1])
    assert pd.isna(out["n"].iloc[2])
