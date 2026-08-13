from __future__ import annotations

from pathlib import Path

import duckdb
import yaml

from data_collection.data_normalizer.flatten_config import _slug
from data_collection.pulse_duckdb.duckdb_manager import prepare_duckdb
from data_collection.pulse_duckdb.object_activity import _create_event_mapping_module_view
from data_collection.pulse_duckdb.sql_utils import log_phase_snapshot, log_table_stats, log_timed_phase


def load_dev_toolbox_modules(base_dir: Path) -> list[str]:
    """Load development-activity modules from YAML.

    Expected file: gold_specs/dataiku_dev_tools/toolbox.yaml
    """

    path = base_dir / "dataiku_dev_tools" / "toolbox.yaml"
    if not path.exists():
        return []

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"Invalid dataiku_dev_tools toolbox.yaml (expected YAML list): {path}")

    out: list[str] = []
    seen: set[str] = set()
    for value in raw:
        if value is None:
            continue
        module_name = _slug(str(value))
        if not module_name or module_name in seen:
            continue
        seen.add(module_name)
        out.append(module_name)
    return out


def _load_category_to_capability(base_dir: Path) -> list[dict]:
    path = base_dir / "dataiku_dev_tools" / "category_to_capability.yaml"
    if not path.exists():
        return []

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"Invalid category_to_capability.yaml (expected YAML list): {path}")

    rows: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if not item.get("dataiku_category") or not item.get("capability"):
            continue
        row = dict(item)
        row["dataiku_category"] = _slug(str(row["dataiku_category"]))
        row["capability"] = _slug(str(row["capability"]))
        row.setdefault("capability_order", 1)
        row.setdefault("category_order", 1)
        row.setdefault("capability_display_name", str(item.get("capability_display_name") or row["capability"]))
        row.setdefault("category_display_name", str(item.get("category_display_name") or row["dataiku_category"]))
        row.setdefault("is_dev_activity", True)
        rows.append(row)
    return rows


def build_dim_category_to_capability(conn: duckdb.DuckDBPyConnection, *, base_dir: Path) -> str:
    rows = _load_category_to_capability(base_dir)

    conn.execute(
        """
        CREATE OR REPLACE TABLE dim_category_to_capability AS
        SELECT
          CAST(NULL AS VARCHAR) AS dataiku_category,
          CAST(NULL AS VARCHAR) AS capability,
          CAST(NULL AS INTEGER) AS capability_order,
          CAST(NULL AS INTEGER) AS category_order,
          CAST(NULL AS VARCHAR) AS capability_display_name,
          CAST(NULL AS VARCHAR) AS category_display_name,
          CAST(NULL AS BOOLEAN) AS is_dev_activity
        WHERE 1=0;
        """.strip()
    )

    if not rows:
        return "dim_category_to_capability"

    normalized = [_slug(str(r.get("dataiku_category") or "")) for r in rows]
    duplicates = sorted({value for value in normalized if value and normalized.count(value) > 1})
    if duplicates:
        raise ValueError(
            "Duplicate normalized dataiku_category values in category_to_capability.yaml: " + ", ".join(duplicates)
        )

    conn.executemany(
        """
        INSERT INTO dim_category_to_capability (
          dataiku_category,
          capability,
          capability_order,
          category_order,
          capability_display_name,
          category_display_name,
          is_dev_activity
        ) VALUES (?, ?, ?, ?, ?, ?, ?);
        """.strip(),
        [
            (
                _slug(str(r.get("dataiku_category") or "")),
                _slug(str(r.get("capability") or "")),
                int(r.get("capability_order") or 1),
                int(r.get("category_order") or 1),
                r.get("capability_display_name"),
                r.get("category_display_name"),
                bool(r.get("is_dev_activity")),
            )
            for r in rows
        ],
    )
    return "dim_category_to_capability"


def _load_dev_event_classification(base_dir: Path) -> list[dict]:
    path = base_dir / "dataiku_dev_tools" / "event_classification.yaml"
    if not path.exists():
        return []

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"Invalid event_classification.yaml (expected YAML list): {path}")

    rows: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if not item.get("msgtype") or not item.get("activity_class"):
            continue
        row = dict(item)
        row["msgtype"] = _slug(str(row["msgtype"]))
        row["activity_class"] = _slug(str(row["activity_class"]))
        row.setdefault("is_meaningful_activity", False)
        row.setdefault("description", None)
        rows.append(row)
    return rows


def build_dim_dev_activity_event_classification(
    conn: duckdb.DuckDBPyConnection,
    *,
    base_dir: Path,
) -> str:
    rows = _load_dev_event_classification(base_dir)

    conn.execute(
        """
        CREATE OR REPLACE TABLE dim_dev_activity_event_classification AS
        SELECT
          CAST(NULL AS VARCHAR) AS msgtype,
          CAST(NULL AS VARCHAR) AS activity_class,
          CAST(NULL AS BOOLEAN) AS is_meaningful_activity,
          CAST(NULL AS VARCHAR) AS description
        WHERE 1=0;
        """.strip()
    )

    if not rows:
        return "dim_dev_activity_event_classification"

    normalized = [_slug(str(r.get("msgtype") or "")) for r in rows]
    duplicates = sorted({value for value in normalized if value and normalized.count(value) > 1})
    if duplicates:
        raise ValueError(
            "Duplicate normalized msgtype values in event_classification.yaml: " + ", ".join(duplicates)
        )

    conn.executemany(
        """
        INSERT INTO dim_dev_activity_event_classification (
          msgtype,
          activity_class,
          is_meaningful_activity,
          description
        ) VALUES (?, ?, ?, ?);
        """.strip(),
        [
            (
                _slug(str(r.get("msgtype") or "")),
                _slug(str(r.get("activity_class") or "")),
                bool(r.get("is_meaningful_activity")),
                r.get("description"),
            )
            for r in rows
        ],
    )
    return "dim_dev_activity_event_classification"


def build_fact_dev_activity_events(
    conn: duckdb.DuckDBPyConnection,
    *,
    ctx,
    base_dir: Path,
) -> str:
    with log_timed_phase(conn, label="build_fact_dev_activity_events"):
        modules = load_dev_toolbox_modules(base_dir)
        if not modules:
            return ""

        conn.execute(
            """
            CREATE OR REPLACE TABLE fact_dev_activity_events AS
            SELECT
              CAST(NULL AS TIMESTAMP) AS timestamp,
              CAST(NULL AS VARCHAR) AS instance_name,
              CAST(NULL AS VARCHAR) AS login,
              CAST(NULL AS VARCHAR) AS msgtype,
              CAST(NULL AS VARCHAR) AS msgtypebase,
              CAST(NULL AS VARCHAR) AS dataiku_category,
              CAST(NULL AS VARCHAR) AS project_key,
              CAST(NULL AS VARCHAR) AS callpath,
              CAST(NULL AS VARCHAR) AS extras,
              CAST(NULL AS TIMESTAMP) AS run_timestamp,
              CAST(NULL AS INTEGER) AS year,
              CAST(NULL AS INTEGER) AS month,
              CAST(NULL AS INTEGER) AS day
            WHERE 1=0;
            """.strip()
        )

        db_path = Path(conn.sql("PRAGMA database_list").fetchone()[2])
        inserted_any = False

        for module_name in modules:
            view_name = f"v_event_mapping__{_slug(module_name)}"
            module_label = f"fact_dev_activity_events.view.{view_name}"
            insert_label = f"fact_dev_activity_events.insert.{_slug(module_name)}"
            log_phase_snapshot(conn, label=f"{insert_label}.before_open", phase="start")

            module_setup = prepare_duckdb(ctx=ctx, read_only=False, reset=False, db_path=db_path)
            try:
                log_phase_snapshot(module_setup.conn, label=module_label, phase="start")
                created = _create_event_mapping_module_view(
                    module_setup.conn,
                    ctx=ctx,
                    module=module_name,
                    view_name=view_name,
                )
                log_phase_snapshot(module_setup.conn, label=module_label, phase="end")
                if not created:
                    continue

                inserted_any = True
                insert_sql = f"""
                    INSERT INTO fact_dev_activity_events
                    SELECT
                      try_cast(COALESCE(timestamp, date) AS TIMESTAMP) AS timestamp,
                      instance_name,
                      authuser AS login,
                      msgtype,
                      msgtypebase,
                      dataiku_category,
                      project_key,
                      callpath,
                      extras,
                      try_cast(run_ts AS TIMESTAMP) AS run_timestamp,
                      CAST(year AS INTEGER) AS year,
                      CAST(month AS INTEGER) AS month,
                      CAST(day AS INTEGER) AS day
                    FROM {view_name}
                """.strip()  # nosec B608 (view_name is generated from curated toolbox modules and internal slugging; it is not user-controlled)

                with log_timed_phase(module_setup.conn, label=insert_label):
                    module_setup.conn.execute(insert_sql)
                log_phase_snapshot(module_setup.conn, label=f"{insert_label}.after_insert", phase="end")
            finally:
                module_setup.conn.close()
                log_phase_snapshot(conn, label=f"{insert_label}.after_close", phase="end")

        if not inserted_any:
            return ""

        log_table_stats(conn, "fact_dev_activity_events")
        log_phase_snapshot(conn, label="fact_dev_activity_events.log_table_stats", phase="end")
        return "fact_dev_activity_events"
