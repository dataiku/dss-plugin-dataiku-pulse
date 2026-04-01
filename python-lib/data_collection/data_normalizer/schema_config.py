from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

import yaml

from .column_sanitize import sanitize_column_name


@dataclass(frozen=True)
class CastingConfig:
    columns: List[str]


def schema_consistency_dir() -> Path:
    return Path(__file__).resolve().parent / "schema_consistency"


def casting_columns_dir() -> Path:
    return schema_consistency_dir() / "casting_columns"


def flatten_columns_dir() -> Path:
    return schema_consistency_dir() / "flatten_columns"


def flatten_columns_search_dirs() -> list[Path]:
    base = flatten_columns_dir()
    return [
        base / "project",
        base / "instance",
        base / "audit",
        base,
    ]


def normalize_column_names(cols: Iterable[str]) -> List[str]:
    out: List[str] = []
    for c in cols:
        if c is None:
            continue
        name = sanitize_column_name(str(c)).lower()
        if name:
            out.append(name)
    return out


def _load_yaml_list(path: Path) -> List[str]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"Expected YAML list in {path}, got {type(raw)!r}")
    return normalize_column_names(raw)


def load_casting_columns(
    *,
    name: str,
    casting_dir: Optional[Path] = None,
) -> CastingConfig:
    """Load casting columns list.

    Example: name="datetime" loads `casting_columns/datetime.yaml`.
    """

    if casting_dir is None:
        casting_dir = casting_columns_dir()

    path = casting_dir / f"{name}.yaml"
    cols = _load_yaml_list(path) if path.exists() else []
    return CastingConfig(columns=cols)
