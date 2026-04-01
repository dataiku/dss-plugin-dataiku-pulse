from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from .output_layout import ensure_parent_dir


def to_dataframe(payload: Any, prefix: str) -> pd.DataFrame:
    """Normalize API payload into a DataFrame.

    Uses `pd.json_normalize` for list/dict payloads and falls back to
    a single-row frame for scalars.
    """

    if payload is None:
        return pd.DataFrame()

    if isinstance(payload, (list, dict)):
        return pd.json_normalize(payload).add_prefix(prefix)

    return pd.DataFrame([{prefix.rstrip('_'): payload}])


def write_project_list_csv(
    *,
    output_path: Path,
    payload: Any,
    prefix: str,
    extra_metadata: Optional[Dict[str, Any]] = None,
    write_empty: bool = False,
) -> Optional[Path]:
    """Write a normalized list_* payload as CSV.

    By default, empty payloads do not produce files.

    - If the normalized dataframe is empty and `write_empty=False`, returns None.
    - If `write_empty=True`, writes a single-row file containing `extra_metadata`
      (if provided) or an empty file otherwise.
    """

    df = to_dataframe(payload, prefix=prefix)

    if df.shape[0] == 0:
        if not write_empty:
            return None
        if extra_metadata:
            df = pd.DataFrame([extra_metadata])
    else:
        if extra_metadata:
            for k, v in extra_metadata.items():
                df[k] = v

    ensure_parent_dir(output_path)
    df.to_csv(output_path, index=False)
    return output_path
