from __future__ import annotations

from typing import Optional

import pandas as pd

from data_collection.data_normalizer.column_sanitize import sanitize_columns


def normalize_silver(
    *,
    df: pd.DataFrame,
    instance_name: str,
    include_instance_column: bool = True,
) -> pd.DataFrame:
    """Apply silver-layer normalization rules.

    Rules (first pass):
    1) Ensure `instance_name` is the first column.
    2) Sanitize column names (special chars -> `_`).

    Raw layer should never use this.
    """

    out = df.copy()

    # Rule 2: sanitize column names
    out.columns = sanitize_columns(out.columns)

    # Rule 1: ensure instance_name first
    if include_instance_column:
        # If present, normalize its name (could have been sanitized already)
        if "instance_name" not in out.columns:
            out.insert(0, "instance_name", instance_name)
        else:
            out["instance_name"] = instance_name
            cols = ["instance_name"] + [c for c in out.columns if c != "instance_name"]
            out = out[cols]

    return out
