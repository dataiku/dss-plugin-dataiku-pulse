from __future__ import annotations

import gzip
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from .output_layout import ensure_parent_dir


def _json_default(o: Any) -> Any:
    """Best-effort JSON serializer for RAW dumps.

    RAW is intended to be as close to the API payload as possible, but some
    payloads may contain objects that are not JSON serializable.
    """

    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.integer, np.floating)):
        return o.item()
    if isinstance(o, (pd.Timestamp, datetime, date)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    return str(o)


def write_json(
    *,
    output_path: Path,
    payload: Any,
    encoding: str = "utf-8",
    indent: Optional[int] = None,
) -> Path:
    """Write payload as JSON to `output_path`."""

    ensure_parent_dir(output_path)
    data = json.dumps(payload, ensure_ascii=False, default=_json_default, indent=indent)

    output_path.write_text(data, encoding=encoding)
    return output_path


def write_json_gzip(
    *,
    output_path: Path,
    payload: Any,
    encoding: str = "utf-8",
    indent: Optional[int] = None,
) -> Path:
    """Write payload as gzipped JSON to `output_path`."""

    ensure_parent_dir(output_path)
    data = json.dumps(payload, ensure_ascii=False, default=_json_default, indent=indent)

    with gzip.open(output_path, "wt", encoding=encoding) as f:
        f.write(data)

    return output_path
