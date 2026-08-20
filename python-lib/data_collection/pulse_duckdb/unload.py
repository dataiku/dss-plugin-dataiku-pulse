from __future__ import annotations

import logging
import tempfile
from collections.abc import Iterable
from pathlib import Path

import duckdb
import pandas as pd

import dataiku

from data_collection.helper.dss_folder_writer import DSSFolderTarget, upload_parquet
from data_collection.pulse_duckdb.destinations import gold_destination_for_table, gold_destination_path
from data_collection.pulse_duckdb.diagnostics import verify_event_fact_unload

logger = logging.getLogger(__name__)

FACT_TIME_COLUMNS: dict[str, str] = {
    "fact_user_activity_daily": "day",
    "fact_user_activity_project_daily": "day",
    "fact_formal_mau_daily": "day",
    "fact_license_utilization_daily": "snapshot_date",
    "fact_dev_activity_events": "timestamp",
    "fact_object_activity_events": "timestamp",
}


def _single_file_copy_sql(table_name: str, destination_path: str) -> str:
    return f"COPY {table_name} TO '{destination_path}' (FORMAT 'PARQUET', OVERWRITE TRUE);"  # nosec B608


def _table_df(conn: duckdb.DuckDBPyConnection, table_name: str) -> pd.DataFrame:
    return conn.execute(f"SELECT * FROM {table_name};").df()  # nosec B608


def _fact_partition_output_path(destination: str, instance_name: str, year: int, month: int, day: int) -> Path:
    return (
        Path(destination)
        / f"instance_name={instance_name}"
        / f"year={year:04d}"
        / f"month={month:02d}"
        / f"day={day:02d}"
        / "data.parquet"
    )


def _clear_gold_partition(folder_lookup: str, partition_root: Path) -> None:
    folder = dataiku.Folder(folder_lookup)
    rel_path = str(partition_root).strip("/") + "/"
    folder.clear_path(rel_path)


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _fact_partition_key_rows(
    conn: duckdb.DuckDBPyConnection,
    *,
    table_name: str,
    time_column: str,
) -> list[tuple[str, int, int, int]]:
    query = "\n".join(
        [
            "SELECT DISTINCT",
            "  CAST(instance_name AS VARCHAR) AS instance_name,",
            f"  EXTRACT(YEAR FROM CAST({time_column} AS TIMESTAMP))::INTEGER AS year,",
            f"  EXTRACT(MONTH FROM CAST({time_column} AS TIMESTAMP))::INTEGER AS month,",
            f"  EXTRACT(DAY FROM CAST({time_column} AS TIMESTAMP))::INTEGER AS day",
            f"FROM {table_name}",
            "WHERE instance_name IS NOT NULL",
            f"  AND CAST({time_column} AS TIMESTAMP) IS NOT NULL",
            "ORDER BY 1, 2, 3, 4;",
        ]
    )  # nosec B608 (table_name/time_column come from internal fact contract)
    return [
        (str(instance_name), int(year), int(month), int(day))
        for instance_name, year, month, day in conn.execute(query).fetchall()
    ]


def _fact_calendar_days(
    conn: duckdb.DuckDBPyConnection,
    *,
    table_name: str,
    time_column: str,
) -> list[tuple[int, int, int]]:
    query = "\n".join(
        [
            "SELECT DISTINCT",
            f"  EXTRACT(YEAR FROM CAST({time_column} AS TIMESTAMP))::INTEGER AS year,",
            f"  EXTRACT(MONTH FROM CAST({time_column} AS TIMESTAMP))::INTEGER AS month,",
            f"  EXTRACT(DAY FROM CAST({time_column} AS TIMESTAMP))::INTEGER AS day",
            f"FROM {table_name}",
            "WHERE instance_name IS NOT NULL",
            f"  AND CAST({time_column} AS TIMESTAMP) IS NOT NULL",
            "ORDER BY 1, 2, 3;",
        ]
    )  # nosec B608 (table_name/time_column come from internal fact contract)
    return [
        (int(year), int(month), int(day))
        for year, month, day in conn.execute(query).fetchall()
    ]


def _duckdb_fact_partition_copy_sql(
    *,
    table_name: str,
    time_column: str,
    destination_path: str,
    instance_name: str,
    year: int,
    month: int,
    day: int,
) -> str:
    instance_literal = _sql_string(instance_name)
    return "\n".join(
        [
            "COPY (",
            f"  SELECT * FROM {table_name}",  # nosec B608 (table_name comes from the internal fact contract)
            f"  WHERE instance_name = {instance_literal}",
            f"    AND EXTRACT(YEAR FROM CAST({time_column} AS TIMESTAMP)) = {int(year)}",
            f"    AND EXTRACT(MONTH FROM CAST({time_column} AS TIMESTAMP)) = {int(month)}",
            f"    AND EXTRACT(DAY FROM CAST({time_column} AS TIMESTAMP)) = {int(day)}",
            f") TO '{destination_path}' (FORMAT 'PARQUET', OVERWRITE TRUE);",
        ]
    )  # nosec B608 (all identifiers come from internal fact contract; destination_path is managed-folder derived)


def _process_rss_mb() -> float:
    status_path = Path("/proc/self/status")
    try:
        for line in status_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                parts = line.split()
                if len(parts) >= 2:
                    return float(parts[1]) / 1024.0
    except OSError:
        pass
    return float("nan")


def _duckdb_memory_snapshot(conn: duckdb.DuckDBPyConnection) -> dict[str, str | None]:
    snapshot: dict[str, str | None] = {}
    for setting in ["memory_limit", "threads", "temp_directory", "max_temp_directory_size"]:
        try:
            snapshot[setting] = str(conn.execute(f"SELECT current_setting('{setting}')").fetchone()[0])
        except Exception:
            snapshot[setting] = None
    return snapshot


def _table_columns(conn: duckdb.DuckDBPyConnection, table_name: str) -> set[str]:
    rows = conn.execute(
        (
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'main' AND table_name = ?"
        ),
        [table_name],
    ).fetchall()
    return {str(row[0]).lower() for row in rows}


def _duckdb_native_partition_select_sql(
    *,
    table_name: str,
    time_column: str,
) -> str:
    return "\n".join(  # nosec B608 (table_name/time_column come from the internal fact contract)
        [
            "SELECT",
            "  *,",
            f"  EXTRACT(YEAR FROM CAST({time_column} AS TIMESTAMP))::INTEGER AS __pulse_partition_year,",
            f"  EXTRACT(MONTH FROM CAST({time_column} AS TIMESTAMP))::INTEGER AS __pulse_partition_month,",
            f"  EXTRACT(DAY FROM CAST({time_column} AS TIMESTAMP))::INTEGER AS __pulse_partition_day",
            f"FROM {table_name}",
        ]
    )


def _duckdb_native_partition_copy_sql(
    *,
    table_name: str,
    time_column: str,
    destination_path: str,
) -> str:
    select_sql = _duckdb_native_partition_select_sql(table_name=table_name, time_column=time_column)
    return "\n".join(
        [
            "COPY (",
            *[f"  {line}" for line in select_sql.splitlines()],
            f") TO '{destination_path}' (",
            "  FORMAT 'PARQUET',",
            "  OVERWRITE TRUE,",
            "  PARTITION_BY (instance_name, __pulse_partition_year, __pulse_partition_month, __pulse_partition_day)",
            ");",
        ]
    )  # nosec B608 (table_name/time_column come from the internal fact contract; destination_path is managed-folder derived)


def _duckdb_native_day_partition_copy_sql(
    *,
    table_name: str,
    time_column: str,
    destination_path: str,
    year: int,
    month: int,
    day: int,
) -> str:
    return "\n".join(
        [
            "COPY (",
            f"  SELECT * FROM {table_name}",  # nosec B608 (table_name comes from the internal fact contract)
            "  WHERE instance_name IS NOT NULL",
            f"    AND EXTRACT(YEAR FROM CAST({time_column} AS TIMESTAMP)) = {int(year)}",
            f"    AND EXTRACT(MONTH FROM CAST({time_column} AS TIMESTAMP)) = {int(month)}",
            f"    AND EXTRACT(DAY FROM CAST({time_column} AS TIMESTAMP)) = {int(day)}",
            f") TO '{destination_path}' (",
            "  FORMAT 'PARQUET',",
            "  OVERWRITE TRUE,",
            "  PARTITION_BY (instance_name)",
            ");",
        ]
    )  # nosec B608 (table_name/time_column come from internal fact contract; destination_path is a local managed stage path)


def _upload_staged_fact_partitions(
    *,
    gold_folder_lookup: str,
    stage_table_root: Path,
    destination: str,
    table_name: str,
) -> None:
    partition_files = sorted(
        stage_table_root.glob(
            "instance_name=*/__pulse_partition_year=*/__pulse_partition_month=*/__pulse_partition_day=*/*.parquet"
        )
    )
    folder = dataiku.Folder(gold_folder_lookup)
    total = len(partition_files)
    if total == 0:
        raise RuntimeError(f"DuckDB staged no fact partitions for {table_name} under {stage_table_root}")
    logger.info(
        "DuckDB fact stage upload: table=%s partitions=%s rss_mb=%.1f",
        table_name,
        total,
        _process_rss_mb(),
    )
    for index, staged_file in enumerate(partition_files, start=1):
        partition_suffix = staged_file.relative_to(stage_table_root)
        parts = partition_suffix.parts
        if len(parts) != 5:
            raise RuntimeError(f"Unexpected staged fact partition layout for {table_name}: {partition_suffix}")

        instance_dir, year_dir, month_dir, day_dir, _duckdb_leaf = parts
        if not instance_dir.startswith("instance_name="):
            raise RuntimeError(f"Unexpected staged instance partition for {table_name}: {partition_suffix}")
        if not year_dir.startswith("__pulse_partition_year="):
            raise RuntimeError(f"Unexpected staged year partition for {table_name}: {partition_suffix}")
        if not month_dir.startswith("__pulse_partition_month="):
            raise RuntimeError(f"Unexpected staged month partition for {table_name}: {partition_suffix}")
        if not day_dir.startswith("__pulse_partition_day="):
            raise RuntimeError(f"Unexpected staged day partition for {table_name}: {partition_suffix}")

        instance_name = instance_dir.split("=", 1)[1]
        year_value = int(year_dir.split("=", 1)[1])
        month_value = int(month_dir.split("=", 1)[1])
        day_value = int(day_dir.split("=", 1)[1])
        relative_output_path = _fact_partition_output_path(
            destination,
            instance_name,
            year_value,
            month_value,
            day_value,
        )
        partition_root = relative_output_path.parent
        logger.info(
            "DuckDB fact partition upload: table=%s partition=%s index=%s/%s rss_mb=%.1f",
            table_name,
            str(partition_root),
            index,
            total,
            _process_rss_mb(),
        )
        _clear_gold_partition(gold_folder_lookup, partition_root)
        with staged_file.open("rb") as handle:
            folder.upload_stream(str(relative_output_path), handle)


def _upload_staged_fact_day_partitions(
    *,
    gold_folder_lookup: str,
    stage_day_root: Path,
    destination: str,
    table_name: str,
    year: int,
    month: int,
    day: int,
) -> int:
    partition_files = sorted(stage_day_root.glob("instance_name=*/*.parquet"))
    folder = dataiku.Folder(gold_folder_lookup)
    total = len(partition_files)
    if total == 0:
        raise RuntimeError(
            f"DuckDB staged no fact day partitions for {table_name} day={year:04d}-{month:02d}-{day:02d} under {stage_day_root}"
        )

    for staged_file in partition_files:
        partition_suffix = staged_file.relative_to(stage_day_root)
        parts = partition_suffix.parts
        if len(parts) != 2:
            raise RuntimeError(f"Unexpected staged fact day partition layout for {table_name}: {partition_suffix}")

        instance_dir, _duckdb_leaf = parts
        if not instance_dir.startswith("instance_name="):
            raise RuntimeError(f"Unexpected staged instance partition for {table_name}: {partition_suffix}")

        instance_name = instance_dir.split("=", 1)[1]
        relative_output_path = _fact_partition_output_path(destination, instance_name, year, month, day)
        partition_root = relative_output_path.parent
        _clear_gold_partition(gold_folder_lookup, partition_root)
        with staged_file.open("rb") as handle:
            folder.upload_stream(str(relative_output_path), handle)

    return total


def _write_fact_table_partitions_duckdb(
    conn: duckdb.DuckDBPyConnection,
    *,
    gold_ctx,
    gold_folder_lookup: str,
    table_name: str,
    destination: str,
    time_column: str,
) -> None:
    day_rows = _fact_calendar_days(conn, table_name=table_name, time_column=time_column)
    memory_snapshot = _duckdb_memory_snapshot(conn)
    logger.info(
        "DuckDB fact unload memory baseline: table=%s days=%s rss_mb=%.1f memory_limit=%s threads=%s temp_directory=%s max_temp_directory_size=%s",
        table_name,
        len(day_rows),
        _process_rss_mb(),
        memory_snapshot["memory_limit"],
        memory_snapshot["threads"],
        memory_snapshot["temp_directory"],
        memory_snapshot["max_temp_directory_size"],
    )

    with tempfile.TemporaryDirectory(prefix="pulse-gold-stage-") as tmpdir:
        staging_root = Path(tmpdir)
        stage_table_root = staging_root / Path(destination)

        for day_index, (year, month, day) in enumerate(day_rows, start=1):
            day_label = f"{year:04d}-{month:02d}-{day:02d}"
            stage_day_root = stage_table_root / f"day={day_label}"
            stage_day_root.mkdir(parents=True, exist_ok=True)
            logger.info(
                "DuckDB fact day unload: table=%s day=%s index=%s/%s rss_before_copy_mb=%.1f",
                table_name,
                day_label,
                day_index,
                len(day_rows),
                _process_rss_mb(),
            )
            conn.execute(
                _duckdb_native_day_partition_copy_sql(
                    table_name=table_name,
                    time_column=time_column,
                    destination_path=str(stage_day_root),
                    year=year,
                    month=month,
                    day=day,
                )
            )
            logger.info(
                "DuckDB fact day copied: table=%s day=%s index=%s/%s rss_after_copy_mb=%.1f",
                table_name,
                day_label,
                day_index,
                len(day_rows),
                _process_rss_mb(),
            )
            instance_partitions = _upload_staged_fact_day_partitions(
                gold_folder_lookup=gold_folder_lookup,
                stage_day_root=stage_day_root,
                destination=destination,
                table_name=table_name,
                year=year,
                month=month,
                day=day,
            )
            logger.info(
                "DuckDB fact day upload: table=%s day=%s index=%s/%s instance_partitions=%s rss_after_upload_mb=%.1f",
                table_name,
                day_label,
                day_index,
                len(day_rows),
                instance_partitions,
                _process_rss_mb(),
            )
            for staged_file in stage_day_root.glob("**/*"):
                if staged_file.is_file():
                    staged_file.unlink()
            for staged_dir in sorted(stage_day_root.glob("**/*"), reverse=True):
                if staged_dir.is_dir():
                    staged_dir.rmdir()
            stage_day_root.rmdir()
            logger.info(
                "DuckDB fact day cleanup: table=%s day=%s index=%s/%s rss_after_cleanup_mb=%.1f",
                table_name,
                day_label,
                day_index,
                len(day_rows),
                _process_rss_mb(),
            )


def _write_fact_table_partitions(
    conn: duckdb.DuckDBPyConnection,
    *,
    gold_folder_lookup: str,
    table_name: str,
    destination: str,
    time_column: str,
) -> None:
    df = _table_df(conn, table_name)
    if df.shape[0] == 0:
        return

    if "instance_name" not in df.columns:
        raise ValueError(f"Fact table {table_name} is missing required column instance_name")
    if time_column not in df.columns:
        raise ValueError(f"Fact table {table_name} is missing canonical time column {time_column}")

    working = df.copy()
    working = working[working["instance_name"].notna()].copy()
    working[time_column] = pd.to_datetime(working[time_column], utc=True, errors="coerce")
    working = working[working[time_column].notna()].copy()
    if working.shape[0] == 0:
        return

    working["year"] = working[time_column].dt.year.astype(int)
    working["month"] = working[time_column].dt.month.astype(int)
    working["day"] = working[time_column].dt.day.astype(int)

    target = DSSFolderTarget(project_key=dataiku.default_project_key(), folder_lookup=gold_folder_lookup)
    for (instance_name, year, month, day), partition_df in working.groupby(["instance_name", "year", "month", "day"], dropna=False):
        output_path = _fact_partition_output_path(destination, str(instance_name), int(year), int(month), int(day))
        _clear_gold_partition(gold_folder_lookup, output_path.parent)
        upload_parquet(
            target=target,
            output_path=output_path,
            output_base_dir=Path('.'),
            df=partition_df,
            compression='gzip',
            write_empty=True,
        )


def _write_single_table_via_dataiku(
    conn: duckdb.DuckDBPyConnection,
    *,
    gold_folder_lookup: str,
    table_name: str,
    destination: str,
) -> None:
    upload_parquet(
        target=DSSFolderTarget(project_key=dataiku.default_project_key(), folder_lookup=gold_folder_lookup),
        output_path=Path(destination),
        output_base_dir=Path('.'),
        df=_table_df(conn, table_name),
        compression='gzip',
        write_empty=True,
    )


def unload_gold_table(
    conn: duckdb.DuckDBPyConnection,
    *,
    gold_ctx,
    gold_folder_lookup: str,
    table_name: str,
    unload_behavior: str,
) -> str:
    destination = gold_destination_for_table(table_name)
    table_type = table_name.split("_", 1)[0] if "_" in table_name else table_name
    logger.info(
        "Starting GOLD unload: table=%s type=%s destination=%s",
        table_name,
        table_type,
        destination,
    )

    if unload_behavior == "duckdb":
        if not gold_ctx.bucket_or_container:
            raise ValueError("Could not resolve GOLD bucket/container")
        if not gold_ctx.blob_header:
            raise ValueError("Could not resolve GOLD blob header")

        destination_path = gold_destination_path(gold_ctx, destination)
        if table_name.startswith("fact_"):
            time_column = FACT_TIME_COLUMNS.get(table_name)
            if not time_column:
                raise ValueError(f"No canonical fact time column configured for {table_name}")
            _write_fact_table_partitions_duckdb(
                conn,
                gold_ctx=gold_ctx,
                gold_folder_lookup=gold_folder_lookup,
                table_name=table_name,
                destination=destination,
                time_column=time_column,
            )
        else:
            sql = _single_file_copy_sql(table_name, destination_path)
            conn.execute(sql)
        if table_name in {"fact_dev_activity_events", "fact_object_activity_events"}:
            verify_event_fact_unload(
                conn,
                gold_ctx=gold_ctx,
                gold_folder_lookup=gold_folder_lookup,
                relative_path=destination,
                table_name=table_name,
            )
        logger.info("Completed GOLD unload: table=%s", table_name)
        return destination

    if unload_behavior == "dataiku":
        if table_name.startswith("fact_"):
            time_column = FACT_TIME_COLUMNS.get(table_name)
            if not time_column:
                raise ValueError(f"No canonical fact time column configured for {table_name}")
            _write_fact_table_partitions(
                conn,
                gold_folder_lookup=gold_folder_lookup,
                table_name=table_name,
                destination=destination,
                time_column=time_column,
            )
        else:
            _write_single_table_via_dataiku(
                conn,
                gold_folder_lookup=gold_folder_lookup,
                table_name=table_name,
                destination=destination,
            )
        logger.info("Completed GOLD unload: table=%s", table_name)
        return destination

    raise ValueError(f"Unknown unload behavior: {unload_behavior!r}")


def unload_gold_tables(
    conn: duckdb.DuckDBPyConnection,
    *,
    gold_ctx,
    gold_folder_lookup: str,
    table_names: Iterable[str],
    unload_behavior: str,
) -> tuple[list[str], list[str]]:
    unloaded_tables: list[str] = []
    failed_tables: list[str] = []

    for table_name in table_names:
        try:
            unload_gold_table(
                conn,
                gold_ctx=gold_ctx,
                gold_folder_lookup=gold_folder_lookup,
                table_name=table_name,
                unload_behavior=unload_behavior,
            )
            unloaded_tables.append(table_name)
        except Exception:
            logger.exception("Failed GOLD unload: table=%s", table_name)
            failed_tables.append(table_name)

    return unloaded_tables, failed_tables
