from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict

import numpy as np
import pandas as pd


def _extras_default(o: Any) -> Any:
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.integer, np.floating)):
        return o.item()
    if isinstance(o, (pd.Timestamp, datetime, date)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    return str(o)


def extras_to_json(value: Any) -> str | None:
    """Canonicalize extras value into a JSON string (or None)."""

    if value is None or value is pd.NA:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    if isinstance(value, str):
        s = value.strip()
        if s == "":
            return None
        try:
            json.loads(s)
            return s
        except Exception:
            return json.dumps({"_value": s}, ensure_ascii=False)

    if isinstance(value, np.ndarray):
        value = value.tolist()

    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=_extras_default)

    return json.dumps(value, ensure_ascii=False, default=_extras_default)


def pack_extras(
    *,
    row: Dict[str, Any],
    required_columns: list[str],
) -> Dict[str, Any]:
    """Return a new row with required keys + extras json string."""

    out: Dict[str, Any] = {}
    extras: Dict[str, Any] = {}

    for key, value in row.items():
        if key in required_columns:
            out[key] = value
            continue

        if value is None or (isinstance(value, float) and pd.isna(value)):
            continue

        extras[key] = value

    out["extras"] = extras_to_json(extras) if extras else None
    return out
