from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List

import yaml


@dataclass(frozen=True)
class ExclusionConfig:
    excluded_methods: List[str]


def exclusions_dir() -> Path:
    return Path(__file__).resolve().parent / "collection_exclusions"


def load_exclusions(name: str) -> ExclusionConfig:
    """Load exclusion list YAML.

    `name` should be one of:
    - "projects_data"
    - "instance_data"
    - "audit_log_data"

    File convention: `{name}.yaml`
    """

    path = exclusions_dir() / f"{name}.yaml"
    return ExclusionConfig(excluded_methods=_load_method_list(path))


def load_inclusions(filename: str) -> List[str]:
    """Load an inclusion list YAML from `collection_exclusions/`.

    This is used for cases where we want to explicitly run a small curated
    subset of methods.
    """

    path = exclusions_dir() / filename
    return _load_method_list(path)


def _load_method_list(path: Path) -> List[str]:
    if not path.exists():
        return []

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]

    raise ValueError(f"Expected YAML list in {path}, got {type(raw)!r}")
