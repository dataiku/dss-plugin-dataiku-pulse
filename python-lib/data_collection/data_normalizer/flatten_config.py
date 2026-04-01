from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import yaml

from .schema_config import flatten_columns_search_dirs, flatten_columns_dir


@dataclass(frozen=True)
class FlattenConfig:
    required_columns: List[str]


def default_flatten_columns_dir() -> Path:
    return flatten_columns_dir()


def normalize_required_columns(cols: list[str]) -> List[str]:
    # Backward-compatible wrapper; schema_config handles normalization.
    from .schema_config import normalize_column_names

    return normalize_column_names(cols)


def load_flatten_config(
    *,
    category: str,
    module: str,
    flatten_columns_dir: Optional[Path] = None,
) -> Optional[FlattenConfig]:
    """Load flatten config for an object.

    File convention: `{category}_{module}.yaml`

    Search behavior:
    - If `flatten_columns_dir` is provided, only that directory is used.
    - Otherwise, searches:
      - `.../flatten_columns/project/`
      - `.../flatten_columns/instance/`
      - `.../flatten_columns/audit/`
      - `.../flatten_columns/` (fallback)

    Returns None if the config file does not exist.
    """

    filename = f"{category}_{module}.yaml"

    search_dirs: list[Path]
    if flatten_columns_dir is not None:
        search_dirs = [flatten_columns_dir]
    else:
        search_dirs = flatten_columns_search_dirs()

    path: Optional[Path] = None
    for d in search_dirs:
        candidate = d / filename
        if candidate.exists():
            path = candidate
            break

    if path is None:
        return None

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        required: List[str] = []
    elif isinstance(raw, list):
        required = normalize_required_columns(raw)
    else:
        raise ValueError(f"Expected YAML list in {path}, got {type(raw)!r}")

    return FlattenConfig(required_columns=required)
