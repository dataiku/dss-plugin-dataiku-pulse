from __future__ import annotations

import re
from typing import Iterable, List


_NON_ALNUM_UNDERSCORE = re.compile(r"[^0-9a-zA-Z_]+")


def sanitize_column_name(name: str) -> str:
    """Replace special characters with underscores.

    - Replaces any sequence of non [0-9a-zA-Z_] with `_`.
    - Collapses repeated underscores.
    - Strips leading/trailing underscores.
    """

    sanitized = _NON_ALNUM_UNDERSCORE.sub("_", name)
    sanitized = re.sub(r"_+", "_", sanitized)
    return sanitized.strip("_")


def sanitize_columns(columns: Iterable[str]) -> List[str]:
    return [sanitize_column_name(c) for c in columns]
