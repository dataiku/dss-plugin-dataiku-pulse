from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class OutputLayout:
    """Partitioned output path layout.

    Layout:
      {base_dir}/{layer}/category={category}/module={module}/instance_name={instance_name}/year={YYYY}/month={MM}/day={DD}/{project_key}.parquet

    Where:
    - `layer` is "raw", "silver", or "silver_fail"
    - `category` is the method name with `list_` removed (ex: list_datasets -> datasets)
    - `module` is currently always "metadata"
    """

    base_dir: Path
    module: str = "project_metadata"

    @staticmethod
    def category_name(list_method_name: str) -> str:
        return list_method_name.replace("list_", "", 1)

    @staticmethod
    def prefix_base(list_method_name: str) -> str:
        """Column prefix base.

        Convention: use the `list_*` method name with `list_` removed.

        Example: list_datasets -> datasets
        """

        return OutputLayout.category_name(list_method_name)

    def category_dir(self, layer: str, list_method_name: str) -> Path:
        return self.base_dir / layer / f"category={self.category_name(list_method_name)}"

    def module_dir(self, layer: str, list_method_name: str) -> Path:
        return self.category_dir(layer, list_method_name) / f"module={self.module}"

    def instance_dir(self, layer: str, list_method_name: str, instance_name: str) -> Path:
        return self.module_dir(layer, list_method_name) / f"instance_name={instance_name}"

    def date_dir(
        self,
        layer: str,
        list_method_name: str,
        instance_name: str,
        run_date: date,
    ) -> Path:
        return (
            self.instance_dir(layer, list_method_name, instance_name)
            / f"year={run_date.year:04d}"
            / f"month={run_date.month:02d}"
            / f"day={run_date.day:02d}"
        )

    def project_data_path(
        self,
        layer: str,
        list_method_name: str,
        instance_name: str,
        run_date: date,
        project_key: str,
        extension: str,
    ) -> Path:
        return self.date_dir(layer, list_method_name, instance_name, run_date) / f"{project_key}.{extension}"


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def as_posix_relative(path: Path, *, base_dir: Path) -> str:
    """Return `path` relative to `base_dir` using POSIX separators.

    Dataiku managed folders expect POSIX-like paths regardless of platform.
    """

    rel = path.relative_to(base_dir)
    return str(PurePosixPath(rel))
