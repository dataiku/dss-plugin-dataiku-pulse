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




def _resolve_gold_folder_lookup() -> str:
    """Return the managed-folder lookup used by `dataiku.Folder`.

    Prefer an explicit folder id when provided; otherwise fall back to name.
    """

    return settings.PULSE_GOLD_TABLES_FOLDER_ID or settings.PULSE_GOLD_TABLES_FOLDER_NAME


def list_gold_paths(*, suffixes: tuple[str, ...] = (".parquet", ".csv")) -> list[str]:
    """List gold table object paths.

    We intentionally use the high-level `dataiku.Folder(...).list_paths_in_partition("NP")`
    which returns paths recursively. The lower-level managed-folder handle
    `list_contents()` is not reliably recursive across DSS versions.
    """

    folder = dataiku.Folder(
        lookup=_resolve_gold_folder_lookup(),
        project_key=settings.PULSE_SOURCE_PROJECT_KEY,
        ignore_flow=True,
    )

    paths: list[str] = []
    for path in folder.list_paths_in_partition("NP"):
        p = str(path)
        if any(p.lower().endswith(s) for s in suffixes):
            paths.append(p)

    return sorted(paths)


def _parse_hive_partitions(rel_path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in PurePosixPath(rel_path).parts:
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        if k in {"instance_name", "year", "month", "day", "project_key"} and v:
            out[k] = v
    return out


def _load_parquet_to_table(
    conn: duckdb.DuckDBPyConnection,
    folder: dataiku.Folder,
    *,
    path: str,
    table_name: str,
    append: bool,
) -> None:
    """Download a parquet file and load into DuckDB.

    We download to a temporary file and let DuckDB read it from disk.
    This is more memory-friendly than loading the whole parquet into pandas.
    """

    ingest_dir = Path(settings.DUCKDB_DIR) / "_ingest"
    ingest_dir.mkdir(parents=True, exist_ok=True)

    tmp_path = ingest_dir / f"{table_name}.{uuid.uuid4().hex}.parquet"
    try:
        with folder.get_download_stream(path) as stream:
            tmp_path.write_bytes(stream.read())

        partitions = _parse_hive_partitions(path)

        file_cols: set[str] = set()
        if partitions and not append:
            try:
                file_cols = {str(r[0]) for r in conn.execute("DESCRIBE SELECT * FROM read_parquet(?);", [str(tmp_path)]).fetchall()}
            except Exception:
                file_cols = set()

        select_cols = ["*"]
        params: list[object] = []

        # Re-inject hive partition keys as columns when parquet files omit them.
        if partitions.get("instance_name") and "instance_name" not in file_cols:
            select_cols.append("CAST(? AS VARCHAR) AS instance_name")
            params.append(partitions["instance_name"])
        if partitions.get("project_key") and "project_key" not in file_cols:
            select_cols.append("CAST(? AS VARCHAR) AS project_key")
            params.append(partitions["project_key"])
        for k in ["year", "month", "day"]:
            if partitions.get(k) and k not in file_cols:
                select_cols.append(f"CAST(? AS INTEGER) AS {k}")
                params.append(int(partitions[k]))

        sql_select = f"SELECT {', '.join(select_cols)} FROM read_parquet(?)"
        params.append(str(tmp_path))

        if append:
            conn.execute(f'INSERT INTO "{table_name}" {sql_select};', params)
        else:
            conn.execute(f'CREATE TABLE "{table_name}" AS {sql_select};', params)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def _load_csv_to_table(
    conn: duckdb.DuckDBPyConnection, folder: dataiku.Folder, *, path: str, table_name: str
) -> None:
    """Load a CSV from a managed folder into DuckDB.

    DuckDB's best CSV type inference is available via `read_csv_auto`, but it
    requires a file path. So we download the managed-folder object to a temp
    file under `settings.DUCKDB_DIR` and load from there.
    """

    ingest_dir = Path(settings.DUCKDB_DIR) / "_ingest"
    ingest_dir.mkdir(parents=True, exist_ok=True)

    tmp_path = ingest_dir / f"{table_name}.{uuid.uuid4().hex}.csv"
    try:
        with folder.get_download_stream(path) as stream:
            tmp_path.write_bytes(stream.read())

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


def infer_table_name(rel_path: str) -> str:
    """Infer target table name from a managed-folder path.

    Supports both:
    - single-file tables: `gold/base_foo.parquet`
    - partitioned tables: `gold/fact_bar/instance_name=.../year=.../data_0.parquet`

    In the partitioned case, the table name is the first segment after `gold/`.
    """

    parts = [p for p in PurePosixPath(rel_path).parts if p]
    if parts and parts[0] == "/":
        parts = parts[1:]

    # Drop leading 'gold' prefix if present.
    if parts and parts[0] == "gold":
        parts = parts[1:]

    if not parts:
        return PurePosixPath(rel_path).stem

    first = parts[0]
    if first.startswith(("base_", "dim_", "fact_", "reg_")):
        return PurePosixPath(first).stem

    return PurePosixPath(rel_path).stem


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

    folder = dataiku.Folder(
        lookup=_resolve_gold_folder_lookup(),
        project_key=settings.PULSE_SOURCE_PROJECT_KEY,
        ignore_flow=True,
    )

    paths = list_gold_paths(suffixes=allowed_suffixes)

    loaded: list[dict] = []
    skipped: list[dict] = []
    failed: list[dict] = []

    prefix_norm = prefix.lstrip("/")

    created_tables: set[str] = set()

    for path in paths:
        rel_path = str(path).lstrip("/")
        base_name = PurePosixPath(rel_path).name

        if prefix_norm and not rel_path.startswith(prefix_norm):
            continue
        if name_glob and not fnmatch.fnmatch(base_name, name_glob):
            continue

        suffix = PurePosixPath(base_name).suffix.lower()
        table_name = infer_table_name(rel_path)

        if allowed_table_names is not None and table_name not in allowed_table_names:
            skipped.append({"table": table_name, "path": rel_path, "reason": "not_allowed"})
            continue

        try:
            if replace and table_name not in created_tables:
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

            if not replace and table_name in created_tables:
                # We are appending multiple files into one table.
                pass
            elif not replace:
                exists = (
                    (lambda row: int(row[0]) if row else 0)(
                        conn.execute(
                            "SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_schema = 'main' AND table_name = ?;",
                            [table_name],
                        ).fetchone()
                    )
                    > 0
                )
                if exists:
                    skipped.append({"table": table_name, "path": rel_path, "reason": "already_exists"})
                    continue

            if suffix == ".parquet":
                append = table_name in created_tables
                _load_parquet_to_table(conn, folder, path=rel_path, table_name=table_name, append=append)
                created_tables.add(table_name)
                rows = -1

            elif suffix == ".csv" and settings.PULSE_GOLD_LOAD_USE_DUCKDB_CSV_AUTO:
                _load_csv_to_table(conn, folder, path=rel_path, table_name=table_name)
                created_tables.add(table_name)
                row = conn.execute(f'SELECT COUNT(*) FROM "{table_name}";').fetchone()
                rows = int(row[0]) if row else 0

            elif suffix == ".csv":
                # Fallback: pandas-based loader
                with folder.get_download_stream(rel_path) as stream:
                    df = pd.read_csv(io.BytesIO(stream.read()))
                conn.register("_tmp_df", df)
                if table_name in created_tables:
                    conn.execute(f'INSERT INTO "{table_name}" SELECT * FROM _tmp_df;')
                else:
                    conn.execute(f'CREATE TABLE "{table_name}" AS SELECT * FROM _tmp_df;')
                    created_tables.add(table_name)
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
