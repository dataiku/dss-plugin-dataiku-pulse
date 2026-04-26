from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd


def raw_to_dataframe(payload: Any, prefix: str) -> pd.DataFrame:
    """Convert raw API output to DataFrame (RAW layer).

    Important: this should be a minimal conversion step required to persist
    parquet. It must not rename columns or coerce types beyond what pandas/
    json_normalize does by default.

    Notes on common API shapes:
    - list[dict]: already row-oriented (ex: client.list_users())
    - dict[str, dict]: key-oriented mapping that should be turned into rows
      (ex: client.list_connections())
    """

    if payload is None:
        return pd.DataFrame()

    # Some DSS client methods return a dict keyed by object name, where values are
    # dict-like objects. Convert these to one row per key.
    if isinstance(payload, dict) and payload:
        values = list(payload.values())
        if values and all(isinstance(v, dict) for v in values):
            rows: list[dict[str, Any]] = []
            for key, raw_row in payload.items():
                row = dict(raw_row)
                row.setdefault("name", key)
                rows.append(row)
            return pd.json_normalize(rows).add_prefix(prefix)

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
