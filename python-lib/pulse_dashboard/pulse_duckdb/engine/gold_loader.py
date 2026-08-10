"""Load curated files from Dataiku managed folders into DuckDB."""

from __future__ import annotations

import fnmatch
import io
import logging
import os
import time
import uuid
from collections import defaultdict
from pathlib import Path, PurePosixPath

import dataiku
import duckdb
import pandas as pd
from data_collection.pulse_duckdb.destinations import gold_destination_path
from shared_duckdb.sql_utils import quote_identifier
from shared_duckdb.context import build_storage_context

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


def _build_gold_blob_paths(paths: list[str]) -> tuple[object, dict[str, list[str]]]:
    settings.PULSE_SOURCE_PROJECT_KEY = settings.resolve_source_project_key()
    lookup = _resolve_gold_folder_lookup()
    storage_ctx = build_storage_context(
        project_key=settings.PULSE_SOURCE_PROJECT_KEY,
        folder_lookup=lookup,
    )
    logger.info(
        "DuckDB gold_loader: storage context resolved project=%s folder_lookup=%s provider=%s bucket_or_container=%s root=%s",
        settings.PULSE_SOURCE_PROJECT_KEY,
        lookup,
        storage_ctx.connection_type,
        storage_ctx.bucket_or_container,
        storage_ctx.folder_root,
    )
    grouped: dict[str, list[str]] = defaultdict(list)
    for path in sorted(str(path).lstrip("/") for path in paths if str(path)):
        table_name = infer_table_name(path)
        grouped[table_name].append(gold_destination_path(storage_ctx, path))
    for table_name, blob_paths in grouped.items():
        logger.info(
            "DuckDB gold_loader: grouped table=%s parquet_files=%s sample_paths=%s",
            table_name,
            len(blob_paths),
            blob_paths[:3],
        )
    return storage_ctx, dict(grouped)


def _load_remote_parquet_table(
    conn: duckdb.DuckDBPyConnection,
    *,
    table_name: str,
    blob_paths: list[str],
) -> int:
    if not blob_paths:
        raise ValueError(f"No blob paths provided for {table_name}")

    started = time.time()
    sql_preview_paths = ", ".join(repr(path) for path in blob_paths[:5])
    if len(blob_paths) > 5:
        sql_preview_paths = f"{sql_preview_paths}, ..."
    params: list[object] = []
    if len(blob_paths) == 1:
        path_expr = "?"
        params.append(blob_paths[0])
        sql_preview = (
            f'CREATE OR REPLACE TABLE {quote_identifier(table_name)} '
            f"AS SELECT * FROM read_parquet({repr(blob_paths[0])});"
        )
    else:
        path_expr = "[" + ", ".join("?" for _ in blob_paths) + "]"
        params.extend(blob_paths)
        sql_preview = (
            f'CREATE OR REPLACE TABLE {quote_identifier(table_name)} '
            f"AS SELECT * FROM read_parquet([{sql_preview_paths}]);"
        )

    sql = (
        f'CREATE OR REPLACE TABLE {quote_identifier(table_name)} AS '
        f'SELECT * FROM read_parquet({path_expr});'
    )  # nosec B608 (table_name is derived from curated GOLD naming and blob paths are parameterized)
    logger.info(
        "DuckDB gold_loader: starting remote parquet load table=%s parquet_files=%s first_path=%s",
        table_name,
        len(blob_paths),
        blob_paths[0],
    )
    logger.info("DuckDB gold_loader: remote parquet SQL table=%s sql=%s", table_name, sql_preview)
    conn.execute(sql, params)
    row = conn.execute(f'SELECT COUNT(*) FROM {quote_identifier(table_name)};').fetchone()  # nosec B608
    rows = int(row[0]) if row else 0
    logger.info(
        "DuckDB gold_loader: finished remote parquet load table=%s parquet_files=%s rows=%s elapsed_sec=%.3f",
        table_name,
        len(blob_paths),
        rows,
        time.time() - started,
    )
    return rows


def list_gold_paths(*, suffixes: tuple[str, ...] = (".parquet", ".csv")) -> list[str]:
    """List gold table object paths.

    We intentionally use the high-level `dataiku.Folder(...).list_paths_in_partition("NP")`
    which returns paths recursively. The lower-level managed-folder handle
    `list_contents()` is not reliably recursive across DSS versions.
    """

    started = time.time()
    settings.PULSE_SOURCE_PROJECT_KEY = settings.resolve_source_project_key()
    lookup = _resolve_gold_folder_lookup()
    logger.info(
        "DuckDB gold_loader.list_gold_paths: start project=%s lookup=%s suffixes=%s",
        settings.PULSE_SOURCE_PROJECT_KEY,
        lookup,
        suffixes,
    )

    folder = dataiku.Folder(
        lookup=lookup,
        project_key=settings.PULSE_SOURCE_PROJECT_KEY,
        ignore_flow=True,
    )

    paths: list[str] = []
    for path in folder.list_paths_in_partition("NP"):
        p = str(path)
        if any(p.lower().endswith(s) for s in suffixes):
            paths.append(p)

    sorted_paths = sorted(paths)
    logger.info(
        "DuckDB gold_loader.list_gold_paths: found %s matching paths in %.3fs",
        len(sorted_paths),
        time.time() - started,
    )
    return sorted_paths


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

    started = time.time()
    logger.info("DuckDB gold_loader: loading parquet path=%s table=%s append=%s", path, table_name, append)
    ingest_dir = Path(settings.DUCKDB_DIR) / "_ingest"
    ingest_dir.mkdir(parents=True, exist_ok=True)

    tmp_path = ingest_dir / f"{table_name}.{uuid.uuid4().hex}.parquet"
    try:
        with folder.get_download_stream(path) as stream:
            tmp_path.write_bytes(stream.read())

        partitions = _parse_hive_partitions(path)

        try:
            file_cols = {
                str(r[0]) for r in conn.execute("DESCRIBE SELECT * FROM read_parquet(?);", [str(tmp_path)]).fetchall()
            }
        except Exception:
            file_cols = set()

        available_exprs: dict[str, tuple[str, object]] = {col: (quote_identifier(col), None) for col in file_cols}

        if partitions.get("instance_name") and "instance_name" not in available_exprs:
            available_exprs["instance_name"] = ("CAST(? AS VARCHAR)", partitions["instance_name"])
        if partitions.get("project_key") and "project_key" not in available_exprs:
            available_exprs["project_key"] = ("CAST(? AS VARCHAR)", partitions["project_key"])
        for key in ["year", "month", "day"]:
            if partitions.get(key) and key not in available_exprs:
                available_exprs[key] = ("CAST(? AS INTEGER)", int(partitions[key]))

        if append:
            target_rows = conn.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema='main' AND table_name=?
                ORDER BY ordinal_position
                """,
                [table_name],
            ).fetchall()
            target_cols = [str(row[0]) for row in target_rows]
            select_cols: list[str] = []
            params: list[object] = []
            insert_cols = ", ".join(quote_identifier(col) for col in target_cols)
            for col in target_cols:
                expr, value = available_exprs.get(col, ("NULL", None))
                select_cols.append(f"{expr} AS {quote_identifier(col)}")
                if value is not None:
                    params.append(value)
            params.append(str(tmp_path))
            sql_select = f"SELECT {', '.join(select_cols)} FROM read_parquet(?)"  # nosec B608 (select cols derived from validated schema)
            conn.execute(
                f'INSERT INTO {quote_identifier(table_name)} ({insert_cols}) {sql_select};',
                params,
            )  # nosec B608 (table_name/columns validated from information_schema)
        else:
            select_cols = ["*"]
            params: list[object] = []
            if partitions.get("instance_name") and "instance_name" not in file_cols:
                select_cols.append("CAST(? AS VARCHAR) AS instance_name")
                params.append(partitions["instance_name"])
            if partitions.get("project_key") and "project_key" not in file_cols:
                select_cols.append("CAST(? AS VARCHAR) AS project_key")
                params.append(partitions["project_key"])
            for key in ["year", "month", "day"]:
                if partitions.get(key) and key not in file_cols:
                    select_cols.append(f"CAST(? AS INTEGER) AS {key}")
                    params.append(int(partitions[key]))
            params.append(str(tmp_path))
            sql_select = f"SELECT {', '.join(select_cols)} FROM read_parquet(?)"  # nosec B608 (select_cols are fixed/derived)
            conn.execute(f'CREATE TABLE {quote_identifier(table_name)} AS {sql_select};', params)  # nosec B608 (table_name validated)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        logger.info(
            "DuckDB gold_loader: finished parquet path=%s table=%s in %.3fs",
            path,
            table_name,
            time.time() - started,
        )


def _load_csv_to_table(
    conn: duckdb.DuckDBPyConnection, folder: dataiku.Folder, *, path: str, table_name: str
) -> None:
    """Load a CSV from a managed folder into DuckDB.

    DuckDB's best CSV type inference is available via `read_csv_auto`, but it
    requires a file path. So we download the managed-folder object to a temp
    file under `settings.DUCKDB_DIR` and load from there.
    """

    started = time.time()
    logger.info("DuckDB gold_loader: loading csv path=%s table=%s", path, table_name)
    ingest_dir = Path(settings.DUCKDB_DIR) / "_ingest"
    ingest_dir.mkdir(parents=True, exist_ok=True)

    tmp_path = ingest_dir / f"{table_name}.{uuid.uuid4().hex}.csv"
    try:
        with folder.get_download_stream(path) as stream:
            tmp_path.write_bytes(stream.read())

        # `read_csv_auto` infers TIMESTAMP/BOOLEAN/etc better than pandas.
        conn.execute(
            f'CREATE TABLE {quote_identifier(table_name)} AS SELECT * FROM read_csv_auto(?, header=true);',  # nosec B608 (table_name validated)
            [str(tmp_path)],
        )
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        logger.info(
            "DuckDB gold_loader: finished csv path=%s table=%s in %.3fs",
            path,
            table_name,
            time.time() - started,
        )


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
    paths: list[str] | None = None,
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

    started = time.time()
    settings.PULSE_SOURCE_PROJECT_KEY = settings.resolve_source_project_key()
    lookup = _resolve_gold_folder_lookup()
    logger.info(
        "DuckDB gold_loader.load_gold_tables: start replace=%s prefix=%s glob=%s project=%s lookup=%s",
        replace,
        prefix,
        name_glob,
        settings.PULSE_SOURCE_PROJECT_KEY,
        lookup,
    )

    if paths is None:
        paths = list_gold_paths(suffixes=allowed_suffixes)
    else:
        paths = sorted(str(path) for path in paths if str(path))

    filtered_paths: list[str] = []
    prefix_norm = prefix.lstrip("/")

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
            continue
        if suffix not in allowed_suffixes:
            continue
        filtered_paths.append(rel_path)

    storage_ctx, grouped_blob_paths = _build_gold_blob_paths(filtered_paths)
    logger.info(
        "DuckDB gold_loader.load_gold_tables: resolved provider=%s bucket_or_container=%s grouped_tables=%s",
        storage_ctx.connection_type,
        storage_ctx.bucket_or_container,
        len(grouped_blob_paths),
    )
    logger.info(
        "DuckDB gold_loader.load_gold_tables: physical GOLD grouped tables=%s names=%s",
        len(grouped_blob_paths),
        sorted(grouped_blob_paths.keys()),
    )

    loaded: list[dict] = []
    skipped: list[dict] = []
    failed: list[dict] = []

    for table_name, blob_paths in grouped_blob_paths.items():
        try:
            table_started = time.time()
            logger.info(
                "DuckDB gold_loader.load_gold_tables: loading table=%s parquet_files=%s replace=%s",
                table_name,
                len(blob_paths),
                replace,
            )
            if not replace:
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
                    skipped.append({"table": table_name, "reason": "already_exists"})
                    continue

            rows = _load_remote_parquet_table(conn, table_name=table_name, blob_paths=blob_paths)
            loaded.append({"table": table_name, "path": blob_paths[0], "rows": rows, "files": len(blob_paths)})
            logger.info(
                "DuckDB gold_loader.load_gold_tables: loaded table=%s parquet_files=%s rows=%s elapsed_sec=%.3f",
                table_name,
                len(blob_paths),
                rows,
                time.time() - table_started,
            )
        except Exception as e:
            logger.exception("Failed loading %s", table_name)
            failed.append({"table": table_name, "path": blob_paths[0] if blob_paths else "", "error": str(e)})

    logger.info(
        "DuckDB gold_loader.load_gold_tables: finished in %.3fs loaded=%s skipped=%s failed=%s",
        time.time() - started,
        len(loaded),
        len(skipped),
        len(failed),
    )
    return {
        "ok": len(failed) == 0,
        "source_project": settings.PULSE_SOURCE_PROJECT_KEY,
        "folder_name": settings.PULSE_GOLD_TABLES_FOLDER_NAME,
        "prefix": prefix,
        "name_glob": name_glob,
        "paths_considered": len(filtered_paths),
        "loaded": loaded,
        "skipped": skipped,
        "failed": failed,
    }
