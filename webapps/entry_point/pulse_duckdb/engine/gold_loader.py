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

import settings
from pulse_duckdb.engine.plugin_storage import gold_blob_url, gold_partitioned_glob




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
    """Return a DSSManagedFolder-like object.

    We resolve the folder id using `dataiku.Folder(...).get_id()` to avoid
    DSS-version differences (`id` vs `odbId`) in `list_managed_folders()`.
    """

    lookup = settings.PULSE_GOLD_TABLES_FOLDER_ID or settings.PULSE_GOLD_TABLES_FOLDER_NAME

    project_key = dataiku.default_project_key()

    try:
        folder = dataiku.Folder(
            lookup=lookup,
            project_key=project_key,
            ignore_flow=True,
        )
        folder_id = folder.get_id()
    except Exception as exc:
        raise ValueError(f"Managed folder {lookup!r} not found in default project {project_key}") from exc

    client = dataiku.api_client()
    project = client.get_project(project_key)
    return project.get_managed_folder(folder_id)



def list_gold_paths(*, suffixes: tuple[str, ...] = (".parquet", ".csv")) -> list[str]:
    client = dataiku.api_client()
    project = client.get_project(dataiku.default_project_key())
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

    Default behavior: materialize files as DuckDB tables (backward compatible).

    Special case:
    - `fact_dev_activity_events`: create a VIEW over partitioned parquet via
      `read_parquet(...)` instead of downloading into pandas.

    Returns basic stats for UI/debug.
    """

    client = dataiku.api_client()
    project = client.get_project(dataiku.default_project_key())
    folder = _resolve_gold_tables_folder(project)

    paths = list_gold_paths(suffixes=allowed_suffixes)

    # Special case: `fact_dev_activity_events` is written as partitioned parquet
    # under `gold/fact_dev_activity_events/**`. Those files are named `data_0.parquet`
    # so they won't be discovered as a top-level table name.
    def _safe_list_items(list_path: str | None = None) -> list[dict]:
        try:
            # Some DSS versions support `path=` to list a subtree.
            contents = folder.list_contents(path=list_path) if list_path else folder.list_contents()
        except TypeError:
            contents = folder.list_contents()
        return contents.get("items", []) or []

    # Try a shallow listing of `gold/` first (faster and more reliable on some backends).
    items = _safe_list_items("gold")
    if not items:
        items = _safe_list_items(None)

    # Discover partitioned parquet facts written as folder trees.
    # Layout: gold/<fact_name>/instance_name=*/year=*/month=*/day=*/*.parquet
    #
    # Dataiku managed-folder listings may include directory entries but not deep
    # children; we therefore infer fact names from any listed path under
    # `gold/fact_*/...`.
    partitioned_fact_tables: list[str] = sorted(
        {
            PurePosixPath(path if path.startswith("gold/") else f"gold/{path}").parts[1]
            for it in items
            for path in [str((it.get("path") or "")).lstrip("/")]
            if (path.startswith("gold/fact_") or path.startswith("fact_"))
            and len(PurePosixPath(path if path.startswith("gold/") else f"gold/{path}").parts) >= 2
        }
    )

    loaded: list[dict] = []
    skipped: list[dict] = []
    failed: list[dict] = []

    prefix_norm = prefix.lstrip("/")

    def _create_partitioned_parquet_view(*, table_name: str) -> None:
        # Partitioned `fact_*` datasets are safe to always expose as views because
        # we do not materialize them into DuckDB (no pandas download). They also
        # cannot be discovered via `list_gold_paths()` since they are directories.
        if (
            allowed_table_names is not None
            and table_name not in allowed_table_names
            and not table_name.startswith("fact_")
        ):
            skipped.append({"table": table_name, "path": f"gold/{table_name}/**", "reason": "not_allowed"})
            return

        try:
            if replace:
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
                    loaded.append({"table": table_name, "path": "(already exists)", "rows": 0})
                    return

            glob = gold_partitioned_glob(dataset=table_name)
            conn.execute(
                f"""
                CREATE OR REPLACE VIEW \"{table_name}\" AS
                SELECT *
                FROM read_parquet('{glob}', hive_partitioning = true);
                """.strip()
            )
            loaded.append({"table": table_name, "path": "(read_parquet partitions)", "rows": 0})
        except Exception as e:
            logger.exception("Failed creating partitioned view for %s", table_name)
            failed.append({"table": table_name, "path": f"gold/{table_name}/**", "error": str(e)})

    # Create partitioned views upfront when data is present.
    for table_name in partitioned_fact_tables:
        _create_partitioned_parquet_view(table_name=table_name)

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

            if table_name in {"fact_dev_activity_events", "fact_object_activity_events"}:
                # Skip: handled as partitioned view above.
                skipped.append({"table": table_name, "path": rel_path, "reason": "partitioned_view"})
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

            if table_name == "fact_dev_activity_events":
                loaded.append({"table": table_name, "path": "(read_parquet view)", "rows": int(rows)})
            else:
                loaded.append({"table": table_name, "path": rel_path, "rows": int(rows)})


        except Exception as e:
            logger.exception("Failed loading %s", rel_path)
            failed.append({"table": table_name, "path": rel_path, "error": str(e)})

    return {
        "ok": len(failed) == 0,
        "source_project": dataiku.default_project_key(),
        "folder_name": settings.PULSE_GOLD_TABLES_FOLDER_NAME,
        "prefix": prefix,
        "name_glob": name_glob,
        "paths_considered": len(paths),
        "loaded": loaded,
        "skipped": skipped,
        "failed": failed,
    }
