from __future__ import annotations

from pathlib import Path

import yaml


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    return payload if isinstance(payload, dict) else {}


def render_query(query_str: str, **kwargs: str) -> str:
    return query_str.format(**kwargs)
