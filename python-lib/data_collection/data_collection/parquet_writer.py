from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from data_collection.helper.parquet_engine import ensure_pyarrow
from data_collection.helper.output_layout import ensure_parent_dir


def write_parquet(
    *,
    output_path: Path,
    df: pd.DataFrame,
    write_empty: bool = False,
) -> Optional[Path]:
    """Write a DataFrame to parquet.

    If `df` is empty and `write_empty=False`, returns None.
    """

    if df.shape[0] == 0 and not write_empty:
        return None

    ensure_pyarrow()
    ensure_parent_dir(output_path)

    df.to_parquet(output_path, index=False, engine="pyarrow")
    return output_path
