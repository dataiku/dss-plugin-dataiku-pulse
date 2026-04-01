from __future__ import annotations

import json
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class DQResult:
    ok: bool
    errors: list[str]


def check_silver_dq(
    df: pd.DataFrame,
    *,
    required_non_null: list[str] | None = None,
    sample_extras_rows: int = 10,
) -> DQResult:
    """Run minimal data quality checks for SILVER writes.

    This is intentionally lightweight and conservative; rules can be tightened
    later once we see real-world failure modes.
    """

    errors: list[str] = []

    if df is None or not isinstance(df, pd.DataFrame):
        return DQResult(ok=False, errors=["not_a_dataframe"])

    if df.shape[0] == 0:
        errors.append("empty_dataframe")

    if df.columns.duplicated().any():
        errors.append("duplicate_columns")

    if (df.columns.astype(str).str.len() == 0).any():
        errors.append("empty_column_name")

    required_non_null = required_non_null or ["instance_name", "run_ts"]
    for col in required_non_null:
        if col not in df.columns:
            errors.append(f"missing_column:{col}")
            continue

        null_count = int(df[col].isna().sum())
        if null_count:
            errors.append(f"nulls_in:{col}:{null_count}")

    # If present, `extras` should be JSON-parseable for a small sample.
    if "extras" in df.columns:
        sample = df["extras"].dropna().head(sample_extras_rows)
        bad = 0
        for v in sample.tolist():
            if v is pd.NA or v is None:
                continue
            if not isinstance(v, str):
                bad += 1
                continue
            try:
                json.loads(v)
            except Exception:
                bad += 1

        if bad:
            errors.append(f"extras_not_json:{bad}/{len(sample)}")

    return DQResult(ok=len(errors) == 0, errors=errors)
