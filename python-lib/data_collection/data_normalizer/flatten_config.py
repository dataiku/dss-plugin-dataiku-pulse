from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import List, Optional

import yaml

from .schema_config import flatten_columns_search_dirs, flatten_columns_dir as _flatten_columns_base_dir


@dataclass(frozen=True)
class FlattenConfig:
    required_columns: List[str]


def default_flatten_columns_dir() -> Path:
    return _flatten_columns_base_dir()


def normalize_required_columns(cols: list[str]) -> List[str]:
    # Backward-compatible wrapper; schema_config handles normalization.
    from .schema_config import normalize_column_names

    return normalize_column_names(cols)


def _todo_section_for_module(module: str) -> str:
    m = str(module).strip().lower()
    if "instance" in m:
        return "instance"
    if "audit" in m:
        return "audit"
    # Default: project (most list_* collectors)
    return "project"


def _slug(value: str) -> str:
    # Convert arbitrary strings into a stable snake-ish token.
    # - replace any non [A-Za-z0-9] with underscores
    # - collapse multiple underscores
    # - trim underscores
    s = re.sub(r"[^A-Za-z0-9]+", "_", str(value).strip())
    s = re.sub(r"_+", "_", s)
    return s.strip("_")


def _todo_flatten_filename(*, category: str, module: str, variant: str | None = None) -> str:
    # TODO naming is intentionally compatible with "activate by renaming":
    # removing the `TODO_` prefix yields a valid config file name.
    #
    # Example base:    TODO_jobs_project_metadata.yaml          -> jobs_project_metadata.yaml
    # Example variant: TODO_audit_dataiku_usage_genai_llm_audit_metadata.yaml
    #                 -> audit_dataiku_usage_genai_llm_audit_metadata.yaml
    safe_category = _slug(category)
    safe_module = _slug(module)
    if variant:
        safe_variant = _slug(variant)
        return f"TODO_{safe_category}_{safe_variant}_{safe_module}.yaml"
    return f"TODO_{safe_category}_{safe_module}.yaml"


def _write_todo_file(path: Path, *, category: str, module: str, section: str, variant: str | None = None) -> None:
    # Best-effort: this should never break normal runs.
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return
        variant_line = f"# Variant: {variant}\n" if variant else ""
        content = (
            f"# TODO: define required flat columns for {category} ({section}-level)\n"
            "# File naming convention: {category}_{module}.yaml\n"
            f"# Category: {category}\n"
            f"# Module: {module}\n"
            f"# Section: {section}\n"
            f"{variant_line}\n"
            "# Add column names here (lowercase, underscores)\n"
        )
        path.write_text(content, encoding="utf-8")
    except Exception:
        return


def load_flatten_config(
    *,
    category: str,
    module: str,
    flatten_columns_dir: Optional[Path] = None,
    todo_section: str | None = None,
    base: tuple[str, str] | None = None,
    variant: str | None = None,
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

    base_category, base_module = (base if base is not None else (category, module))
    base_filename = f"{base_category}_{base_module}.yaml"

    # Optional composition: merge base + variant.
    # Example:
    #   base file:    audit_dataiku_usage_audit_metadata.yaml
    #   variant file: audit_dataiku_usage_genai_llm_audit_metadata.yaml
    variant_filename = f"{base_category}_{variant}_{base_module}.yaml" if variant else None

    search_dirs: list[Path]
    if flatten_columns_dir is not None:
        search_dirs = [flatten_columns_dir]
    else:
        search_dirs = flatten_columns_search_dirs()
        if todo_section:
            preferred = _flatten_columns_base_dir() / _slug(todo_section)
            # Prefer the relevant macro section first (project/instance/audit)
            search_dirs = [preferred] + [d for d in search_dirs if d != preferred]

    def _find_path(name: str) -> Optional[Path]:
        for d in search_dirs:
            candidate = d / name
            if candidate.exists():
                return candidate
        return None

    section = _slug(todo_section) if todo_section else _todo_section_for_module(module)

    base_path = _find_path(base_filename)
    variant_path = _find_path(variant_filename) if variant_filename else None

    if base_path is None and variant_path is None:
        # Internal/dev convenience: create a TODO marker when configs are missing.
        # This is intended to help maintainers spot new DSS list_* endpoints
        # after upgrades without impacting end users.
        if os.environ.get("PULSE_AUTO_TODO_FLATTEN", "1") not in {"0", "false", "False"}:
            todo_name = _todo_flatten_filename(
                category=base_category,
                module=base_module,
                variant=variant,
            )
            todo_path = _flatten_columns_base_dir() / section / todo_name
            _write_todo_file(
                todo_path,
                category=base_category,
                module=base_module,
                section=section,
                variant=variant,
            )
        return None

    required: List[str] = []

    if base_path is not None:
        raw = yaml.safe_load(base_path.read_text(encoding="utf-8"))
        if raw is None:
            pass
        elif isinstance(raw, list):
            required.extend(normalize_required_columns(raw))
        else:
            raise ValueError(f"Expected YAML list in {base_path}, got {type(raw)!r}")

    if variant_path is not None:
        raw = yaml.safe_load(variant_path.read_text(encoding="utf-8"))
        if raw is None:
            pass
        elif isinstance(raw, list):
            required.extend(normalize_required_columns(raw))
        else:
            raise ValueError(f"Expected YAML list in {variant_path}, got {type(raw)!r}")

    # De-dupe while preserving order
    seen: set[str] = set()
    merged: List[str] = []
    for c in required:
        if c in seen:
            continue
        seen.add(c)
        merged.append(c)

    return FlattenConfig(required_columns=merged)
