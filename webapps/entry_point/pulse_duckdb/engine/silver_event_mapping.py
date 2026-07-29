from __future__ import annotations

import logging
import os
import re
from pathlib import Path, PurePosixPath
from typing import Iterable

import dataiku
import duckdb

import settings

logger = logging.getLogger(__name__)


def _sql_string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _build_read_parquet_view_sql(*, view_name: str, parquet_glob: str) -> str:
    lines = [
        f"CREATE OR REPLACE VIEW {view_name} AS",
        "SELECT",
        "  *,",
        "  make_date(CAST(year AS INTEGER), CAST(month AS INTEGER), CAST(day AS INTEGER)) AS partition_date",
        f"FROM read_parquet({_sql_string_literal(parquet_glob)}, hive_partitioning = true);",
    ]
    return "\n".join(lines)


def _resolve_silver_folder() -> object:
    lookup = settings.PULSE_SILVER_FOLDER_ID or settings.PULSE_SILVER_FOLDER_NAME

    try:
        folder = dataiku.Folder(
            lookup=lookup,
            project_key=dataiku.default_project_key(),
            ignore_flow=True,
        )
        folder_id = folder.get_id()
    except Exception as exc:
        raise ValueError(f"Managed folder {lookup!r} not found in default project") from exc

    client = dataiku.api_client()
    project = client.get_project(dataiku.default_project_key())
    return project.get_managed_folder(folder_id)


def _list_parquet_paths(folder, *, prefix: str) -> list[str]:
    # Dataiku managed folder list_contents returns paths with leading '/'
    contents = folder.list_contents()
    items = contents.get("items", []) or []
    out: list[str] = []
    prefix_norm = prefix.lstrip("/")

    for item in items:
        p = item.get("path")
        if not p:
            continue
        rel = str(p).lstrip("/")
        if prefix_norm and not rel.startswith(prefix_norm):
            continue
        if not rel.lower().endswith(".parquet"):
            continue
        out.append(rel)

    return sorted(out)


def _safe_suffix(value: str) -> str:
    # Turn module names like "Coding" or "Generative AI & LLM" into identifiers.
    s = value.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "unknown"


def _ensure_local_copy(folder, *, rel_path: str, dest_root: Path) -> Path:
    # Mirror the remote path under dest_root to preserve hive partitions.
    dest_path = dest_root / rel_path
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    if dest_path.exists() and dest_path.stat().st_size > 0:
        return dest_path

    resp = folder.get_file(rel_path)
    resp.raise_for_status()
    dest_path.write_bytes(resp.content)
    return dest_path


def _extract_module_name(rel_path: str) -> str | None:
    # Expect .../silver/category=event_mapping/module=<NAME>/...
    p = PurePosixPath(rel_path)
    for part in p.parts:
        if part.startswith("module="):
            return part.split("=", 1)[1]
    return None


def cache_event_mapping_parquet(*, dest_root: Path | None = None) -> dict:
    """Download SILVER event_mapping parquet files to local disk.

    Returns a small report dict (paths, modules).
    """

    if dest_root is None:
        dest_root = Path(settings.DUCKDB_DIR) / "_silver_cache"

    folder = _resolve_silver_folder()
    prefix = "silver/category=event_mapping/"
    paths = _list_parquet_paths(folder, prefix=prefix)

    modules: set[str] = set()
    downloaded = 0

    for rel in paths:
        mod = _extract_module_name(rel)
        if mod:
            modules.add(mod)

        before = (dest_root / rel).exists()
        _ensure_local_copy(folder, rel_path=rel, dest_root=dest_root)
        if not before:
            downloaded += 1

    return {
        "ok": True,
        "prefix": prefix,
        "paths": len(paths),
        "downloaded": downloaded,
        "dest_root": str(dest_root),
        "modules": sorted(modules),
    }


def create_event_mapping_views(conn: duckdb.DuckDBPyConnection, *, cache_root: Path | None = None) -> dict:
    """Create temporary DuckDB views over cached SILVER event_mapping parquet."""

    if cache_root is None:
        cache_root = Path(settings.DUCKDB_DIR) / "_silver_cache"

    glob_all = (
        cache_root
        / "silver"
        / "category=event_mapping"
        / "module=*"
        / "instance_name=*"
        / "year=*"
        / "month=*"
        / "day=*"
        / "*.parquet"
    )

    # Always create an "all categories" view.
    sql_all = _build_read_parquet_view_sql(
        view_name="v_event_mapping__all",
        parquet_glob=glob_all.as_posix(),
    )

    created: list[str] = []
    errors: list[dict] = []

    try:
        conn.execute(sql_all)
        created.append("v_event_mapping__all")
    except Exception as exc:
        errors.append({"view": "v_event_mapping__all", "error": str(exc)})

    # Create per-module views for convenience.
    # We infer module list from the cached file tree.
    module_dirs = sorted((cache_root / "silver" / "category=event_mapping").glob("module=*"))
    for d in module_dirs:
        mod = d.name.split("=", 1)[1] if "=" in d.name else d.name
        suffix = _safe_suffix(mod)
        view_name = f"v_event_mapping__{suffix}"

        glob_mod = (
            cache_root
            / "silver"
            / "category=event_mapping"
            / f"module={mod}"
            / "instance_name=*"
            / "year=*"
            / "month=*"
            / "day=*"
            / "*.parquet"
        )

        sql = _build_read_parquet_view_sql(
            view_name=view_name,
            parquet_glob=glob_mod.as_posix(),
        )

        try:
            conn.execute(sql)
            created.append(view_name)
        except Exception as exc:
            errors.append({"view": view_name, "module": mod, "error": str(exc)})

    return {"ok": len(errors) == 0, "created": created, "errors": errors}
