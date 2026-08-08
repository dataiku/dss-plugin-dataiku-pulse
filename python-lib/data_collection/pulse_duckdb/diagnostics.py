from __future__ import annotations

import logging

import duckdb
import dataiku

from data_collection.pulse_duckdb.destinations import gold_destination_path

logger = logging.getLogger(__name__)


def gold_path_exists(folder_lookup: str, rel_path: str) -> bool:
    folder = dataiku.Folder(folder_lookup)
    normalized = str(rel_path or "").lstrip("/")
    try:
        paths = folder.list_paths_in_partition()
    except TypeError:
        paths = folder.list_paths_in_partition("NP")
    normalized_dir = normalized.rstrip("/") + "/"
    return any(
        str(path).lstrip("/") == normalized or str(path).lstrip("/").startswith(normalized_dir)
        for path in paths
    )


def gold_paths_under(folder_lookup: str, rel_path: str, *, limit: int = 20) -> list[str]:
    folder = dataiku.Folder(folder_lookup)
    normalized = str(rel_path or "").lstrip("/").rstrip("/") + "/"
    try:
        paths = folder.list_paths_in_partition()
    except TypeError:
        paths = folder.list_paths_in_partition("NP")
    matched = [str(path) for path in paths if str(path).lstrip("/").startswith(normalized)]
    return matched[:limit]


def gold_path_exists_safe(folder_lookup: str, rel_path: str) -> bool | None:
    try:
        return gold_path_exists(folder_lookup, rel_path)
    except Exception:
        logger.warning(
            "GOLD path existence check failed for folder=%s path=%s; continuing without managed-folder verification",
            folder_lookup,
            rel_path,
            exc_info=True,
        )
        return None


def gold_paths_under_safe(folder_lookup: str, rel_path: str, *, limit: int = 20) -> list[str] | None:
    try:
        return gold_paths_under(folder_lookup, rel_path, limit=limit)
    except Exception:
        logger.warning(
            "GOLD path visibility check failed for folder=%s path=%s; continuing without managed-folder verification",
            folder_lookup,
            rel_path,
            exc_info=True,
        )
        return None


def duckdb_parquet_glob_for_gold(gold_ctx, relative_path: str) -> str:
    rel = str(relative_path or "").strip("/")
    if rel.endswith(".parquet"):
        return gold_destination_path(gold_ctx, rel)
    return gold_destination_path(gold_ctx, f"{rel}/**/*.parquet")


def log_duckdb_gold_readback(
    conn: duckdb.DuckDBPyConnection,
    *,
    gold_ctx,
    relative_path: str,
    table_name: str,
) -> None:
    parquet_glob = duckdb_parquet_glob_for_gold(gold_ctx, relative_path)
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS rows_count
            FROM read_parquet(?, hive_partitioning = true, union_by_name = true)
            """.strip(),
            [parquet_glob],
        ).fetchone()
        logger.info(
            "DuckDB GOLD readback succeeded: table=%s parquet_glob=%s rows=%s",
            table_name,
            parquet_glob,
            int(row[0] or 0) if row else 0,
        )
    except Exception:
        logger.warning(
            "DuckDB GOLD readback failed: table=%s parquet_glob=%s",
            table_name,
            parquet_glob,
            exc_info=True,
        )


def verify_event_fact_unload(
    conn: duckdb.DuckDBPyConnection,
    *,
    gold_ctx,
    gold_folder_lookup: str,
    relative_path: str,
    table_name: str,
) -> None:
    log_duckdb_gold_readback(
        conn,
        gold_ctx=gold_ctx,
        relative_path=relative_path,
        table_name=table_name,
    )
    exists = gold_path_exists_safe(gold_folder_lookup, relative_path)
    existing_paths = gold_paths_under_safe(gold_folder_lookup, relative_path)
    logger.info(
        "Event-fact standard unload verification: table=%s exists=%s sample_paths=%s verification_skipped=%s",
        table_name,
        exists,
        existing_paths,
        exists is None or existing_paths is None,
    )


def table_debug_snapshot(conn: duckdb.DuckDBPyConnection, table_name: str) -> dict[str, object]:
    snapshot: dict[str, object] = {"table_name": table_name, "exists": False}
    try:
        row = conn.execute(
            """
            SELECT table_type
            FROM information_schema.tables
            WHERE table_schema='main' AND table_name=?
            """,
            [table_name],
        ).fetchone()
        if not row:
            return snapshot

        snapshot["exists"] = True
        snapshot["table_type"] = str(row[0])
        snapshot["rows"] = int(conn.execute(f'SELECT COUNT(*) FROM "{table_name}";').fetchone()[0])  # nosec B608 (table_name is taken from a fixed internal diagnostic list, not user input)
        columns = conn.execute(f'DESCRIBE "{table_name}";').fetchall()
        snapshot["columns"] = [str(col[0]) for col in columns]
        sample = conn.execute(f'SELECT * FROM "{table_name}" LIMIT 3;').fetchall()  # nosec B608 (table_name is taken from a fixed internal diagnostic list, not user input)
        snapshot["sample_rows"] = [list(row) for row in sample]
    except Exception as exc:
        snapshot["error"] = str(exc)
    return snapshot


def log_pre_unload_debug(conn: duckdb.DuckDBPyConnection, *, gold_ctx, table_names: list[str]) -> None:
    logger.info(
        "Pre-unload debug: gold_folder_lookup=%s folder_root=%s bucket_or_container=%s blob_header=%s",
        gold_ctx.folder_lookup,
        gold_ctx.folder_root,
        gold_ctx.bucket_or_container,
        gold_ctx.blob_header,
    )
    for table_name in table_names:
        logger.info("Pre-unload table snapshot: %s", table_debug_snapshot(conn, table_name))
