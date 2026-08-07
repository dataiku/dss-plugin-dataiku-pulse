from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import duckdb
import pandas as pd

import dataiku

from data_collection.helper.dss_folder_writer import DSSFolderTarget, upload_parquet
from data_collection.pulse_duckdb.destinations import gold_destination_for_table, gold_destination_path
from data_collection.pulse_duckdb.diagnostics import verify_event_fact_unload

FACT_TIME_COLUMNS: dict[str, str] = {
    "fact_user_activity_daily": "day",
    "fact_user_activity_project_daily": "day",
    "fact_formal_mau_daily": "day",
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
            _write_fact_table_partitions(
                conn,
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
            failed_tables.append(table_name)

    return unloaded_tables, failed_tables
