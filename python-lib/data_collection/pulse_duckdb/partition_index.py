from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import dataiku
import pandas as pd

from .context import StorageContext


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SilverPartitionIndex:
    df: pd.DataFrame

    @property
    def category_modules(self) -> list[tuple[str, str]]:
        if self.df.shape[0] == 0:
            return []
        keys = self.df[["category", "module"]].drop_duplicates().sort_values(["category", "module"])
        return list(map(tuple, keys.values.tolist()))


def _parse_hive_path(path: str) -> dict[str, str] | None:
    """Extract hive-style `key=value` segments from a managed-folder file path."""

    # Example: /silver/category=scenarios/module=project_metadata/instance_name=foo/year=2026/month=03/day=30/file.parquet
    parts = path.strip("/").split("/")

    if not parts:
        return None

    layer = parts[0]
    if layer not in {"raw", "silver", "raw_errors", "silver_fail"}:
        return None

    values: dict[str, str] = {"layer": layer}
    for part in parts[1:]:
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        if k and v:
            values[k] = v

    if "category" not in values or "module" not in values:
        return None

    return values


def build_silver_partition_index(
    *,
    ctx: StorageContext,
    layer: str = "silver",
) -> SilverPartitionIndex:
    """Build a partition index DataFrame by listing managed-folder paths.

    This is used to:
    - discover which `{category,module}` pairs actually exist
    - avoid `read_parquet()` failures due to empty glob patterns

    Note: this uses `Folder.list_paths_in_partition('NP')` because managed folders
    are not always configured as directory-based partitioned datasets.
    """

    folder = dataiku.Folder(
        lookup=ctx.folder_lookup,
        project_key=ctx.project_key,
        ignore_flow=True,
    )

    rows: list[dict[str, str]] = []

    for path in folder.list_paths_in_partition("NP"):
        parsed = _parse_hive_path(path)
        if not parsed:
            continue
        if parsed.get("layer") != layer:
            continue
        rows.append(parsed)

    df = pd.DataFrame(rows)
    if df.shape[0] == 0:
        return SilverPartitionIndex(df=df)

    # Keep a stable set of columns.
    keep = [
        c
        for c in ["layer", "category", "module", "instance_name", "year", "month", "day"]
        if c in df.columns
    ]
    df = df[keep].drop_duplicates().reset_index(drop=True)

    return SilverPartitionIndex(df=df)
