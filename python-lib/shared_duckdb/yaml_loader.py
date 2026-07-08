from __future__ import annotations

from pathlib import Path

import yaml


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    return payload if isinstance(payload, dict) else {}


def render_query(query_str: str, **kwargs: str) -> str:
    """Render a SQL template with values (credentials, regions, account names).

    Values are embedded inside single-quoted SQL string literals, so `'` is
    escaped by doubling. Braces are rejected outright with a clear error: a
    value containing `{`/`}` would previously make `str.format` (here or in a
    later re-format of the rendered SQL) fail with a cryptic KeyError.
    """

    rendered: dict[str, str] = {}
    for key, value in kwargs.items():
        text = "" if value is None else str(value)
        if "{" in text or "}" in text:
            raise ValueError(
                f"Query parameter {key!r} contains a brace character, which is not "
                "supported in credential/config values rendered into SQL"
            )
        rendered[key] = text.replace("'", "''")
    return query_str.format(**rendered)
