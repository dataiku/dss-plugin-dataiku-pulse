from __future__ import annotations

from typing import Iterable, Sequence

import numpy as np
import pandas as pd


# Compact module-contract ingestion accepts only unambiguous Unix epoch lengths.
_EPOCH_UNIT_DIGIT_LENGTHS = {
    "s": {10},
    "ms": {13},
    "us": {16},
    "ns": {19},
}


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


def _epoch_units_by_string_value(values: pd.Series) -> dict[str, pd.Series]:
    string_values = values.astype("string").str.strip()
    numeric_mask = string_values.str.fullmatch(r"[+-]?\d+").fillna(False)
    units_by_name: dict[str, pd.Series] = {}
    for unit, lengths in _EPOCH_UNIT_DIGIT_LENGTHS.items():
        unit_mask = numeric_mask & string_values.str.removeprefix("-").str.removeprefix("+").str.len().isin(lengths)
        if unit_mask.any():
            units_by_name[unit] = unit_mask
    unsupported_mask = numeric_mask & ~pd.concat(units_by_name.values(), axis=1).any(axis=1) if units_by_name else numeric_mask
    if unsupported_mask.any():
        sample_values = string_values[unsupported_mask].head(3).tolist()
        raise ValueError(f"Unsupported epoch timestamp value length for values {sample_values!r}")
    return units_by_name


def _parse_epoch_strings_exact(values: pd.Series) -> pd.Series:
    parsed = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns, UTC]")
    for unit, mask in _epoch_units_by_string_value(values).items():
        parsed.loc[mask] = pd.to_datetime(values.loc[mask].astype("Int64"), unit=unit, utc=True, errors="coerce")
    return parsed


def cast_datetime_columns(
    df: pd.DataFrame,
    columns: Sequence[str],
    *,
    parse_mixed_epoch_strings: bool = False,
) -> pd.DataFrame:
    out = df
    for col in columns:
        if col not in out.columns:
            continue

        series = out[col]

        # If it's already datetime-like, normalize timezone + floor.
        if pd.api.types.is_datetime64_any_dtype(series):
            dt = pd.to_datetime(series, utc=True, errors="coerce")
            out[col] = dt.dt.floor("s")
            continue

        # Numeric epoch
        if pd.api.types.is_numeric_dtype(series):
            unit = _detect_epoch_unit(series)
            dt = pd.to_datetime(series, unit=unit, utc=True, errors="coerce")
            out[col] = dt.dt.floor("s")
            continue

        string_values = series.astype("string").str.strip()
        numeric_mask = string_values.str.fullmatch(r"[+-]?\d+").fillna(False)

        # Try string parse first. The compact opt-in path accepts mixed
        # readable timestamp strings produced by Arrow casts and prior writers.
        dt = pd.to_datetime(
            series,
            utc=True,
            errors="coerce",
            format="mixed" if parse_mixed_epoch_strings else None,
        )

        # If string parse failed for most rows but values are numeric-like strings,
        # retry as epoch.
        if dt.notna().sum() == 0 and series.notna().sum() > 0:
            if parse_mixed_epoch_strings and numeric_mask.any():
                dt = _parse_epoch_strings_exact(series)
            else:
                unit = _detect_epoch_unit(series)
                dt = pd.to_datetime(pd.to_numeric(series, errors="coerce"), unit=unit, utc=True, errors="coerce")
        elif parse_mixed_epoch_strings and numeric_mask.any():
            numeric_dt = _parse_epoch_strings_exact(series[numeric_mask])
            dt.loc[numeric_mask] = numeric_dt

        out[col] = dt.dt.floor("s")

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
