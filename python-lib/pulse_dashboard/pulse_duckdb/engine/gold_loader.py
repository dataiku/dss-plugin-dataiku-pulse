"""Load curated files from Dataiku managed folders into DuckDB."""

from __future__ import annotations

import fnmatch
import io
import logging
import os
import uuid
from pathlib import Path, PurePosixPath

import dataiku
import duckdb
import pandas as pd
import pyarrow.parquet as pq

from ... import settings




logger = logging.getLogger(__name__)


def _extract_table_name(path: str) -> str | None:
    """Infer DuckDB table name from managed folder path.

    Expected example:
    - /gold/actor_capability_subcategory_usage_last_30_days_base.parquet

    Returns:
    - actor_capability_subcategory_usage_last_30_days_base
    """
    p = PurePosixPath(path)
    if p.suffix.lower() != ".parquet":
        return None
    return p.stem


def _resolve_gold_tables_folder(project) -> object:
    """Return a DSSManagedFolder-like object."""
    if settings.PULSE_GOLD_TABLES_FOLDER_ID:
        return project.get_managed_folder(settings.PULSE_GOLD_TABLES_FOLDER_ID)

    # Fall back to lookup by name
    folders = project.list_managed_folders()
    for folder in folders:
        if folder.get("name") == settings.PULSE_GOLD_TABLES_FOLDER_NAME:
            return project.get_managed_folder(folder["id"])

    raise ValueError(
        f"Managed folder '{settings.PULSE_GOLD_TABLES_FOLDER_NAME}' not found in project {settings.PULSE_SOURCE_PROJECT_KEY}"
    )


def list_gold_paths(*, suffixes: tuple[str, ...] = (".parquet", ".csv")) -> list[str]:
    client = dataiku.api_client()
    project = client.get_project(settings.PULSE_SOURCE_PROJECT_KEY)
    folder = _resolve_gold_tables_folder(project)

    contents = folder.list_contents()
    items = contents.get("items", [])
    paths: list[str] = []
    for item in items:
        path = item.get("path")
        if not path:
            continue
        p = str(path)
        if any(p.lower().endswith(s) for s in suffixes):
            paths.append(p)

    # Make stable for debugging
    return sorted(paths)


def _load_df_from_parquet(folder, path: str) -> pd.DataFrame:
    resp = folder.get_file(path)
    resp.raise_for_status()
    table = pq.read_table(io.BytesIO(resp.content))
    return table.to_pandas()


def _load_csv_to_table(conn: duckdb.DuckDBPyConnection, folder, *, path: str, table_name: str) -> None:
    """Load a CSV from a managed folder into DuckDB.

    DuckDB's best CSV type inference is available via `read_csv_auto`, but it
    requires a file path. So we download the managed-folder object to a temp
    file under `settings.DUCKDB_DIR` and load from there.
    """

    resp = folder.get_file(path)
    resp.raise_for_status()

    ingest_dir = Path(settings.DUCKDB_DIR) / "_ingest"
    ingest_dir.mkdir(parents=True, exist_ok=True)

    tmp_path = ingest_dir / f"{table_name}.{uuid.uuid4().hex}.csv"
    try:
        tmp_path.write_bytes(resp.content)

        # `read_csv_auto` infers TIMESTAMP/BOOLEAN/etc better than pandas.
        conn.execute(
            f'CREATE TABLE "{table_name}" AS SELECT * FROM read_csv_auto(?, header=true);',
            [str(tmp_path)],
        )
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def load_gold_tables(
    conn: duckdb.DuckDBPyConnection,
    *,
    replace: bool = True,
    prefix: str = "",
    name_glob: str = "*",
    allowed_suffixes: tuple[str, ...] = (".parquet", ".csv"),
    allowed_table_names: set[str] | None = None,
) -> dict:
    """Load curated files from the gold_tables managed folder into DuckDB.

    This function supports both:
    - parquet (preferred for real pipeline)
    - csv (used for demo workflows)

    Filtering controls:
    - `prefix`: only load objects under this managed-folder path prefix
    - `name_glob`: fnmatch-style filter applied to the basename
    - `allowed_suffixes`: file suffixes to consider

    Returns basic stats for UI/debug.
    """

    client = dataiku.api_client()
    project = client.get_project(settings.PULSE_SOURCE_PROJECT_KEY)
    folder = _resolve_gold_tables_folder(project)

    paths = list_gold_paths(suffixes=allowed_suffixes)

    loaded: list[dict] = []
    skipped: list[dict] = []
    failed: list[dict] = []

    prefix_norm = prefix.lstrip("/")

    for path in paths:
        rel_path = str(path).lstrip("/")
        base_name = PurePosixPath(rel_path).name

        if prefix_norm and not rel_path.startswith(prefix_norm):
            continue
        if name_glob and not fnmatch.fnmatch(base_name, name_glob):
            continue

        suffix = PurePosixPath(base_name).suffix.lower()
        table_name = PurePosixPath(base_name).stem

        if allowed_table_names is not None and table_name not in allowed_table_names:
            skipped.append({"table": table_name, "path": rel_path, "reason": "not_allowed"})
            continue

        try:
            if replace:
                # The existing object might be a VIEW or a TABLE (from previous runs).
                # DuckDB errors if you drop the wrong type, so we ignore failures.
                try:
                    conn.execute(f'DROP VIEW "{table_name}";')
                except Exception:
                    pass
                try:
                    conn.execute(f'DROP TABLE "{table_name}";')
                except Exception:
                    pass
            else:
                exists = (
                    conn.execute(
                        "SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_schema = 'main' AND table_name = ?;",
                        [table_name],
                    ).fetchone()[0]
                    > 0
                )
                if exists:
                    skipped.append({"table": table_name, "path": rel_path, "reason": "already_exists"})
                    continue

            if suffix == ".parquet":
                df = _load_df_from_parquet(folder, rel_path)
                conn.register("_tmp_df", df)
                conn.execute(f'CREATE TABLE "{table_name}" AS SELECT * FROM _tmp_df;')
                conn.unregister("_tmp_df")
                rows = len(df)

            elif suffix == ".csv" and settings.PULSE_GOLD_LOAD_USE_DUCKDB_CSV_AUTO:
                _load_csv_to_table(conn, folder, path=rel_path, table_name=table_name)
                rows = conn.execute(f'SELECT COUNT(*) FROM "{table_name}";').fetchone()[0]

            elif suffix == ".csv":
                # Fallback: pandas-based loader
                df = pd.read_csv(io.BytesIO(folder.get_file(rel_path).content))
                conn.register("_tmp_df", df)
                conn.execute(f'CREATE TABLE "{table_name}" AS SELECT * FROM _tmp_df;')
                conn.unregister("_tmp_df")
                rows = len(df)

            else:
                skipped.append({"path": rel_path, "reason": "unsupported_suffix"})
                continue

            loaded.append({"table": table_name, "path": rel_path, "rows": int(rows)})

        except Exception as e:
            logger.exception("Failed loading %s", rel_path)
            failed.append({"table": table_name, "path": rel_path, "error": str(e)})

    return {
        "ok": len(failed) == 0,
        "source_project": settings.PULSE_SOURCE_PROJECT_KEY,
        "folder_name": settings.PULSE_GOLD_TABLES_FOLDER_NAME,
        "prefix": prefix,
        "name_glob": name_glob,
        "paths_considered": len(paths),
        "loaded": loaded,
        "skipped": skipped,
        "failed": failed,
    }
