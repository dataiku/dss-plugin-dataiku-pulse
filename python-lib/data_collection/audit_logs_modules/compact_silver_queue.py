from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb

from shared_duckdb.create_conn import (
    _duckdb_memory_limit_bytes,
    _duckdb_memory_limit_setting,
    _effective_memory_limit_bytes,
)
from shared_storage_discovery import (
    SelectedPartitionPaths,
    SelectedPathRecord,
    is_compact_output_record,
    iter_managed_folder_paths,
    partition_date_from_record,
    selected_path_record_from_relative_path,
)

logger = logging.getLogger(__name__)

QUEUE_STATUSES = {"pending", "succeeded", "failed", "retained"}
MODULE_STATUSES = {
    "pending",
    "listing",
    "processing",
    "succeeded",
    "failed",
    "retained",
}


@dataclass(frozen=True)
class QueueRuntime:
    temp_dir: Path
    db_path: Path
    disk_total_bytes: int
    disk_used_bytes: int
    disk_free_bytes: int
    memory_limit_setting: str | None


@dataclass(frozen=True)
class QueueSummary:
    total_matched_paths: int
    filtered_matching_paths: int
    skipped_compact_outputs: int
    excluded_recent_paths: int
    eligible_paths: int
    eligible_partition_count: int
    cutoff_date: date
    minimum_age_days: int


@dataclass(frozen=True)
class QueueBatch:
    selected_partitions: list[SelectedPartitionPaths]


@dataclass(frozen=True)
class ModuleManifestEntry:
    module: str
    relative_prefix: str
    status: str


class CompactSilverQueue:
    def __init__(self, *, runtime: QueueRuntime, conn: duckdb.DuckDBPyConnection):
        self.runtime = runtime
        self.conn = conn
        self._closed = False

    @classmethod
    def create(cls) -> "CompactSilverQueue":
        temp_dir = Path(tempfile.mkdtemp(prefix="compact-silver-queue-"))
        usage = shutil.disk_usage(temp_dir)
        if usage.total <= 0:
            raise RuntimeError("Compact SILVER queue filesystem is not usable")

        db_path = temp_dir / "compact_silver_queue.duckdb"
        effective_memory_bytes, _memory_source = _effective_memory_limit_bytes()
        memory_limit_bytes = _duckdb_memory_limit_bytes(effective_memory_bytes)
        memory_limit_setting = _duckdb_memory_limit_setting(memory_limit_bytes)
        config = {
            "temp_directory": str(temp_dir),
            "preserve_insertion_order": "false",
        }
        if memory_limit_setting:
            config["memory_limit"] = memory_limit_setting

        conn = duckdb.connect(str(db_path), config=config)
        conn.execute("""
            CREATE TABLE queue (
                category VARCHAR NOT NULL,
                module VARCHAR NOT NULL,
                instance_name VARCHAR NOT NULL,
                year VARCHAR NOT NULL,
                month VARCHAR NOT NULL,
                day VARCHAR NOT NULL,
                full_path VARCHAR NOT NULL,
                relative_path VARCHAR NOT NULL,
                status VARCHAR NOT NULL DEFAULT 'pending'
            )
            """)
        conn.execute("""
            CREATE TABLE counters (
                total_matched_paths BIGINT NOT NULL,
                filtered_matching_paths BIGINT NOT NULL,
                skipped_compact_outputs BIGINT NOT NULL,
                excluded_recent_paths BIGINT NOT NULL,
                eligible_paths BIGINT NOT NULL,
                cutoff_date VARCHAR NOT NULL,
                minimum_age_days INTEGER NOT NULL
            )
            """)
        conn.execute("""
            CREATE TABLE modules (
                module VARCHAR NOT NULL PRIMARY KEY,
                relative_prefix VARCHAR NOT NULL,
                status VARCHAR NOT NULL DEFAULT 'pending'
            )
            """)

        runtime = QueueRuntime(
            temp_dir=temp_dir,
            db_path=db_path,
            disk_total_bytes=usage.total,
            disk_used_bytes=usage.used,
            disk_free_bytes=usage.free,
            memory_limit_setting=memory_limit_setting,
        )
        logger.info(
            "Compact SILVER queue created disk_total_bytes=%s disk_free_bytes=%s memory_limit=%s",
            runtime.disk_total_bytes,
            runtime.disk_free_bytes,
            runtime.memory_limit_setting,
        )
        return cls(runtime=runtime, conn=conn)

    def close(self) -> None:
        if self._closed:
            return
        self.conn.close()
        shutil.rmtree(self.runtime.temp_dir, ignore_errors=True)
        self._closed = True

    def populate_from_discovery(
        self,
        *,
        storage_ctx: Any,
        relative_prefix: str,
        suffix: str,
        partition_filters: dict[str, str],
        minimum_age_days: int,
        utc_today: date | None = None,
        insert_batch_size: int = 10_000,
        raise_on_empty: bool = True,
    ) -> QueueSummary:
        if minimum_age_days < 0:
            raise ValueError(
                f"minimum_age_days must be non-negative, got {minimum_age_days}"
            )
        if insert_batch_size <= 0:
            raise ValueError(
                f"insert_batch_size must be positive, got {insert_batch_size}"
            )

        today_utc = utc_today or datetime.now(timezone.utc).date()
        cutoff_date = today_utc - timedelta(days=minimum_age_days)

        total_matched_paths = 0
        filtered_matching_paths = 0
        skipped_compact_outputs = 0
        excluded_recent_paths = 0
        eligible_paths = 0
        pending_rows: list[tuple[str, str, str, str, str, str, str, str, str]] = []

        for relative_path in iter_managed_folder_paths(
            storage_ctx, relative_prefix=relative_prefix, suffix=suffix
        ):
            total_matched_paths += 1
            record = selected_path_record_from_relative_path(storage_ctx, relative_path)
            if any(
                getattr(record, column_name) != expected_value
                for column_name, expected_value in partition_filters.items()
            ):
                continue
            if is_compact_output_record(record):
                skipped_compact_outputs += 1
                continue

            filtered_matching_paths += 1
            if partition_date_from_record(record) >= cutoff_date:
                excluded_recent_paths += 1
                continue

            eligible_paths += 1
            pending_rows.append(
                (
                    record.category,
                    record.module,
                    record.instance_name,
                    record.year,
                    record.month,
                    record.day,
                    record.full_path,
                    record.relative_path,
                    "pending",
                )
            )
            if len(pending_rows) >= insert_batch_size:
                self.conn.executemany(
                    "INSERT INTO queue VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", pending_rows
                )
                pending_rows = []

        if pending_rows:
            self.conn.executemany(
                "INSERT INTO queue VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", pending_rows
            )

        if raise_on_empty and filtered_matching_paths > 0 and eligible_paths <= 0:
            raise ValueError(
                f"All exact-filter matches are excluded by minimum_age_days={minimum_age_days}; cutoff_date={cutoff_date.isoformat()}"
            )
        if raise_on_empty and eligible_paths <= 0:
            raise ValueError(
                "No managed-folder paths matched the requested exact partition filters"
            )

        self.conn.execute("DELETE FROM counters")
        self.conn.execute(
            "INSERT INTO counters VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                total_matched_paths,
                filtered_matching_paths,
                skipped_compact_outputs,
                excluded_recent_paths,
                eligible_paths,
                cutoff_date.isoformat(),
                minimum_age_days,
            ],
        )
        eligible_partition_count = int(self.conn.execute("""
                SELECT COUNT(*)
                FROM (
                    SELECT DISTINCT category, module, instance_name, year, month, day
                    FROM queue
                    WHERE status = 'pending'
                )
                """).fetchone()[0])
        return QueueSummary(
            total_matched_paths=total_matched_paths,
            filtered_matching_paths=filtered_matching_paths,
            skipped_compact_outputs=skipped_compact_outputs,
            excluded_recent_paths=excluded_recent_paths,
            eligible_paths=eligible_paths,
            eligible_partition_count=eligible_partition_count,
            cutoff_date=cutoff_date,
            minimum_age_days=minimum_age_days,
        )

    @staticmethod
    def module_name_from_prefix(relative_prefix: str) -> str:
        segment = str(relative_prefix or "").strip().strip("/").split("/")[-1]
        key, separator, value = segment.partition("=")
        if separator != "=" or key != "module" or not value:
            raise ValueError(
                "Unexpected module manifest prefix: expected direct module=... child"
            )
        return value

    def replace_module_manifest(
        self, *, module_prefixes: list[str]
    ) -> list[ModuleManifestEntry]:
        rows = []
        seen_modules: set[str] = set()
        for prefix in module_prefixes:
            module = self.module_name_from_prefix(prefix)
            if module in seen_modules:
                raise ValueError(
                    f"Duplicate module manifest entry for module={module!r}"
                )
            seen_modules.add(module)
            rows.append((module, prefix, "pending"))
        self.conn.execute("DELETE FROM modules")
        if rows:
            self.conn.executemany("INSERT INTO modules VALUES (?, ?, ?)", rows)
        return self.iter_module_manifest()

    def iter_module_manifest(self) -> list[ModuleManifestEntry]:
        rows = self.conn.execute("""
            SELECT module, relative_prefix, status
            FROM modules
            ORDER BY module ASC
            """).fetchall()
        return [
            ModuleManifestEntry(
                module=str(module), relative_prefix=str(prefix), status=str(status)
            )
            for module, prefix, status in rows
        ]

    def mark_module_status(self, *, module: str, status: str) -> None:
        if status not in MODULE_STATUSES:
            raise ValueError(f"Unsupported module status: {status}")
        self.conn.execute(
            "UPDATE modules SET status = ? WHERE module = ?", [status, module]
        )

    def release_module_paths(self, *, module: str) -> None:
        self.conn.execute("DELETE FROM queue WHERE module = ?", [module])

    def queued_path_count(self, *, module: str | None = None) -> int:
        if module is None:
            return int(self.conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0])
        return int(
            self.conn.execute(
                "SELECT COUNT(*) FROM queue WHERE module = ?", [module]
            ).fetchone()[0]
        )

    def next_partition_batch(self, *, batch_size: int) -> QueueBatch:
        if batch_size <= 0:
            raise ValueError(f"batch_size must be positive, got {batch_size}")

        partition_rows = self.conn.execute(
            """
            SELECT category, module, instance_name, year, month, day
            FROM (
                SELECT DISTINCT category, module, instance_name, year, month, day
                FROM queue
                WHERE status = 'pending'
            )
            ORDER BY CAST(year AS INTEGER) DESC,
                     CAST(month AS INTEGER) DESC,
                     CAST(day AS INTEGER) DESC,
                     category ASC,
                     module ASC,
                     instance_name ASC
            LIMIT ?
            """,
            [batch_size],
        ).fetchall()

        selected_partitions: list[SelectedPartitionPaths] = []
        for category, module, instance_name, year, month, day in partition_rows:
            path_rows = self.conn.execute(
                """
                SELECT full_path, relative_path
                FROM queue
                WHERE status = 'pending'
                  AND category = ?
                  AND module = ?
                  AND instance_name = ?
                  AND year = ?
                  AND month = ?
                  AND day = ?
                ORDER BY relative_path ASC
                """,
                [category, module, instance_name, year, month, day],
            ).fetchall()
            selected_partitions.append(
                SelectedPartitionPaths(
                    category=str(category),
                    module=str(module),
                    instance_name=str(instance_name),
                    year=str(year),
                    month=str(month),
                    day=str(day),
                    selected_records=[
                        SelectedPathRecord(
                            relative_path=str(relative_path),
                            full_path=str(full_path),
                            base_name=Path(str(relative_path)).name,
                            layer="silver",
                            category=str(category),
                            module=str(module),
                            instance_name=str(instance_name),
                            year=str(year),
                            month=str(month),
                            day=str(day),
                        )
                        for full_path, relative_path in path_rows
                    ],
                )
            )
        return QueueBatch(selected_partitions=selected_partitions)

    def mark_partition_status(
        self, *, partition: SelectedPartitionPaths, status: str
    ) -> None:
        if status not in QUEUE_STATUSES:
            raise ValueError(f"Unsupported queue status: {status}")
        self.conn.execute(
            """
            UPDATE queue
            SET status = ?
            WHERE category = ?
              AND module = ?
              AND instance_name = ?
              AND year = ?
              AND month = ?
              AND day = ?
              AND status = 'pending'
            """,
            [
                status,
                partition.category,
                partition.module,
                partition.instance_name,
                partition.year,
                partition.month,
                partition.day,
            ],
        )

    def remaining_pending_partition_count(self) -> int:
        return int(self.conn.execute("""
                SELECT COUNT(*)
                FROM (
                    SELECT DISTINCT category, module, instance_name, year, month, day
                    FROM queue
                    WHERE status = 'pending'
                )
                """).fetchone()[0])


__all__ = [
    "CompactSilverQueue",
    "QueueBatch",
    "QueueRuntime",
    "QueueSummary",
]
