from __future__ import annotations

import re

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_identifier(name: str) -> str:
    value = str(name or "").strip()
    if not value or not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"Invalid SQL identifier: {name!r}")
    return value


def quote_identifier(name: str) -> str:
    return '"' + validate_identifier(name) + '"'
