from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd


def raw_to_dataframe(payload: Any, prefix: str) -> pd.DataFrame:
    """Convert raw API output to DataFrame (RAW layer).

    Important: this should be a minimal conversion step required to persist
    parquet. It must not rename columns or coerce types beyond what pandas/
    json_normalize does by default.
    """

    if payload is None:
        return pd.DataFrame()

    if isinstance(payload, (list, dict)):
        return pd.json_normalize(payload).add_prefix(prefix)

    return pd.DataFrame([{prefix.rstrip('_'): payload}])


def build_error_row(
    *,
    error: Exception,
    instance_name: str,
    project_key: str,
    run_ts: str,
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "instance_name": instance_name,
                "project_key": project_key,
                "run_ts": run_ts,
                "__error__": repr(error),
            }
        ]
    )
