from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Sanity window for converted timestamps: values outside are almost always
# unit-detection mistakes (s vs ms vs ns) and would poison downstream
# partitioning/rollups, so they are nulled instead of stored.
_TS_SANITY_MIN = pd.Timestamp("1990-01-01", tz="UTC")


def _ts_sanity_max() -> pd.Timestamp:
    return pd.Timestamp(datetime.now(timezone.utc)) + pd.DateOffset(years=10)


def _apply_ts_sanity(dt: pd.Series, *, col: str) -> pd.Series:
    out_of_range = dt.notna() & ((dt < _TS_SANITY_MIN) | (dt > _ts_sanity_max()))
    count = int(out_of_range.sum())
    if count:
        logger.warning(
            "cast_datetime_columns: nulling %s out-of-range timestamps in %r "
            "(outside [1990-01-01, now+10y])",
            count,
            col,
        )
        dt = dt.mask(out_of_range)
    return dt


def _detect_epoch_unit(values: pd.Series) -> str:
    s = pd.to_numeric(values, errors="coerce").dropna()
    if s.empty:
        return "ms"
    v = float(np.median(s.values))
    if v >= 1e18:
        return "ns"
    if v >= 1e15:
        return "us"
    if v >= 1e12:
        return "ms"
    return "s"


def cast_datetime_columns(
    df: pd.DataFrame,
    columns: Sequence[str],
) -> pd.DataFrame:
    out = df
    for col in columns:
        if col not in out.columns:
            continue

        series = out[col]

        # If it's already datetime-like, normalize timezone + floor.
        if pd.api.types.is_datetime64_any_dtype(series):
            dt = pd.to_datetime(series, utc=True, errors="coerce")
            out[col] = _apply_ts_sanity(dt.dt.floor("s"), col=col)
            continue

        # Numeric epoch
        if pd.api.types.is_numeric_dtype(series):
            unit = _detect_epoch_unit(series)
            dt = pd.to_datetime(series, unit=unit, utc=True, errors="coerce")
            out[col] = _apply_ts_sanity(dt.dt.floor("s"), col=col)
            continue

        # Try string parse first.
        dt = pd.to_datetime(series, utc=True, errors="coerce")

        # If string parse failed for most rows but values are numeric-like strings,
        # retry as epoch.
        if dt.notna().sum() == 0 and series.notna().sum() > 0:
            unit = _detect_epoch_unit(series)
            dt = pd.to_datetime(pd.to_numeric(series, errors="coerce"), unit=unit, utc=True, errors="coerce")

        out[col] = _apply_ts_sanity(dt.dt.floor("s"), col=col)

    return out


def cast_numeric_columns(df: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
    out = df
    for col in columns:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def cast_boolean_columns(df: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
    TRUE_SET = {"true", "True", True, 1, "1"}
    FALSE_SET = {"false", "False", False, 0, "0"}

    def to_bool(x):
        if x is None or x is pd.NA:
            return None
        try:
            if pd.isna(x):
                return None
        except Exception:
            pass
        if x in TRUE_SET:
            return True
        if x in FALSE_SET:
            return False
        return None

    out = df
    for col in columns:
        if col in out.columns:
            out[col] = out[col].map(to_bool).astype("boolean")
    return out


def cast_string_columns(
    df: pd.DataFrame,
    columns: Iterable[str],
    *,
    uppercase: bool = False,
) -> pd.DataFrame:
    out = df
    for col in columns:
        if col not in out.columns:
            continue
        s = out[col].astype("string").str.strip()
        if uppercase:
            s = s.str.upper()
        out[col] = s
    return out
