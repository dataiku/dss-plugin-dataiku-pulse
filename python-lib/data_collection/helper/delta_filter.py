from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

import pandas as pd

from data_collection.data_normalizer.casting import cast_datetime_columns


def find_timestamp_column(columns: Iterable[str]) -> str | None:
    """Find a best-effort timestamp column.

    Uses substring matching so it works with prefixed columns like `scenario_lastModifiedOn`.
    """

    # Prefer lastModified over created.
    for name in columns:
        if "lastModifiedOn" in name:
            return name

    for name in columns:
        if "createdOn" in name:
            return name

    return None


def filter_payload_by_delta(
    *,
    payload: Any,
    raw_df: pd.DataFrame,
    since: datetime,
) -> Any | None:
    """Filter a list/dict payload by a detected timestamp column.

    Returns:
    - `None` if we could not apply delta filtering (no timestamp column)
    - filtered payload if delta filtering was applied

    If filtering results in 0 rows, returns an empty list.
    """

    ts_col = find_timestamp_column(raw_df.columns)
    if ts_col is None:
        return None

    df = cast_datetime_columns(raw_df, [ts_col])

    since_ts = pd.Timestamp(since, tz="UTC")
    df_delta = df[df[ts_col] >= since_ts]

    if df_delta.shape[0] == 0:
        return []

    # Prefer filtering the original list payload when possible.
    if isinstance(payload, list) and len(payload) == raw_df.shape[0]:
        keep_idx = set(df_delta.index.tolist())
        return [row for i, row in enumerate(payload) if i in keep_idx]

    # Fallback: persist filtered rows as records.
    return df_delta.to_dict("records")
