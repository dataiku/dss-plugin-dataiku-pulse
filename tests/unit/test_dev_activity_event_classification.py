from __future__ import annotations

from pathlib import Path
import re

import duckdb
import pytest
import yaml


ALLOWED_CLASSES = {
    "meaningful_action",
    "supporting_request",
    "polling_status",
    "system_unattributed",
    "unclassified",
}


def _slug(value: str) -> str:
    s = str(value or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def _sql_slug_expr(column: str) -> str:
    return (
        "regexp_replace("
        f"replace(replace(lower(trim({column})), ' ', '_'), '-', '_'),"
        " '_+', '_', 'g'"
        ")"
    )


def _build_dim_dev_activity_event_classification_for_test(conn: duckdb.DuckDBPyConnection, rows: list[dict]) -> str:
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

    normalized_msgtypes = [_slug(str(r.get("msgtype") or "")) for r in rows]
    duplicate_msgtypes = sorted({m for m in normalized_msgtypes if m and normalized_msgtypes.count(m) > 1})
    if duplicate_msgtypes:
        raise ValueError(
            "Duplicate normalized msgtype values in event_classification.yaml: " + ", ".join(duplicate_msgtypes)
        )

    insert_rows = [
        (
            _slug(str(r.get("msgtype") or "")),
            _slug(str(r.get("activity_class") or "")),
            bool(r.get("is_meaningful_activity")),
            r.get("description"),
        )
        for r in rows
    ]
    conn.executemany(
        """
        INSERT INTO dim_dev_activity_event_classification (msgtype, activity_class, is_meaningful_activity, description)
        VALUES (?, ?, ?, ?)
        """.strip(),
        insert_rows,
    )
    return "dim_dev_activity_event_classification"


@pytest.fixture()
def conn():
    connection = duckdb.connect(database=":memory:")
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture()
def classification_rows():
    path = Path("python-lib/data_collection/pulse_duckdb/gold_specs/dataiku_dev_tools/event_classification.yaml")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture()
def classified_conn(conn, classification_rows):
    _build_dim_dev_activity_event_classification_for_test(conn, classification_rows)
    conn.execute(
        """
        CREATE OR REPLACE TABLE fact_dev_activity_events AS
        SELECT * FROM (
          VALUES
            ('inst1', TIMESTAMP '2026-01-01 10:00:00', 'alice', ' recipe-save ', 'Visual Recipes', 'data_engineering', TIMESTAMP '2026-01-02 00:00:00'),
            ('inst1', TIMESTAMP '2026-01-01 11:00:00', 'bob', 'SQL_QUERY_START', 'SQL', 'data_engineering', TIMESTAMP '2026-01-02 00:00:00'),
            ('inst1', TIMESTAMP '2026-01-01 12:00:00', 'carol', ' code-studio-current-users ', 'Code Studio', 'data_science', TIMESTAMP '2026-01-02 00:00:00'),
            ('inst1', TIMESTAMP '2026-01-01 13:00:00', NULL, 'jupyter-delete-kernel-context', 'Notebooks', 'data_science', TIMESTAMP '2026-01-02 00:00:00'),
            ('inst1', TIMESTAMP '2026-01-01 14:00:00', 'dave', 'mystery-event', 'Other', 'other', TIMESTAMP '2026-01-02 00:00:00'),
            ('inst1', TIMESTAMP '2026-01-01 15:00:00', 'erin', 'RECIPE SAVE', 'Visual Recipes', 'data_engineering', TIMESTAMP '2026-01-02 00:00:00'),
            ('inst1', TIMESTAMP '2026-01-01 16:00:00', '', 'code-studio-object-state', 'Code Studio', 'data_science', TIMESTAMP '2026-01-02 00:00:00')
        ) AS t(instance_name, timestamp, login, msgtype, dataiku_category, capability, run_timestamp)
        """.strip()
    )
    conn.execute(
        """
        CREATE OR REPLACE TABLE dim_category_to_capability AS
        SELECT * FROM (
          VALUES
            ('visual_recipes', 'data_engineering', 1, 1, 'Data Engineering', 'Visual Recipes', TRUE),
            ('sql', 'data_engineering', 1, 2, 'Data Engineering', 'SQL', TRUE),
            ('code_studio', 'data_science', 2, 1, 'Data Science', 'Code Studio', TRUE),
            ('notebooks', 'data_science', 2, 2, 'Data Science', 'Notebooks', TRUE),
            ('other', 'uncategorized', 9, 9, 'Uncategorized', 'Other', TRUE)
        ) AS t(dataiku_category, capability, capability_order, category_order, capability_display_name, category_display_name, is_dev_activity)
        """.strip()
    )

    msgtype_norm_sql = _sql_slug_expr("e.msgtype")
    category_norm_sql = _sql_slug_expr("e.dataiku_category")
    mapping_category_norm_sql = _sql_slug_expr("m.dataiku_category")
    classification_msgtype_norm_sql = _sql_slug_expr("c.msgtype")
    sql = f"""
    CREATE OR REPLACE VIEW final_build_development_activity_events AS
    SELECT
      e.instance_name,
      e.timestamp,
      e.login,
      e.msgtype,
      e.dataiku_category,
      COALESCE(m.capability, e.capability) AS capability,
      COALESCE(c.activity_class, 'unclassified') AS activity_class,
      COALESCE(c.is_meaningful_activity, FALSE) AS is_meaningful_activity,
      CASE
        WHEN e.login IS NOT NULL AND length(trim(e.login)) > 0 THEN TRUE
        ELSE FALSE
      END AS is_user_attributed,
      e.run_timestamp
    FROM fact_dev_activity_events e
    LEFT JOIN dim_category_to_capability m
      ON {mapping_category_norm_sql} = {category_norm_sql}
    LEFT JOIN dim_dev_activity_event_classification c
      ON {classification_msgtype_norm_sql} = {msgtype_norm_sql}
    """.strip()  # nosec B608 -- test-only SQL built from fixed column expressions.
    conn.execute(sql)
    return conn


def test_classification_yaml_has_unique_normalized_msgtypes(classification_rows):
    normalized = [_slug(str(r.get("msgtype") or "")) for r in classification_rows]
    duplicates = sorted({m for m in normalized if m and normalized.count(m) > 1})
    assert duplicates == []


@pytest.mark.parametrize(
    ("msgtype", "expected_class", "expected_flag"),
    [
        ("recipe-save", "meaningful_action", True),
        ("sql-query-start", "meaningful_action", True),
        ("code-studio-current-users", "polling_status", False),
        ("jupyter-delete-kernel-context", "system_unattributed", False),
        ("unknown-new-event", "unclassified", False),
    ],
)
def test_known_and_unknown_msgtypes_classify_as_expected(classification_rows, msgtype, expected_class, expected_flag):
    lookup = {_slug(str(row["msgtype"])): row for row in classification_rows}
    row = lookup.get(_slug(msgtype))
    if row is None:
        actual_class = "unclassified"
        actual_flag = False
    else:
        actual_class = _slug(str(row["activity_class"]))
        actual_flag = bool(row["is_meaningful_activity"])
    assert actual_class == expected_class
    assert actual_flag is expected_flag


def test_duplicate_normalized_msgtypes_raise_clear_error(conn):
    rows = [
        {
            "msgtype": "Recipe Save",
            "activity_class": "meaningful_action",
            "is_meaningful_activity": True,
            "description": "a",
        },
        {
            "msgtype": "recipe-save",
            "activity_class": "meaningful_action",
            "is_meaningful_activity": True,
            "description": "b",
        },
    ]
    with pytest.raises(ValueError, match="Duplicate normalized msgtype values.*recipe_save"):
        _build_dim_dev_activity_event_classification_for_test(conn, rows)


def test_final_view_preserves_all_raw_fact_rows(classified_conn):
    raw_count = classified_conn.execute("SELECT COUNT(*) FROM fact_dev_activity_events").fetchone()[0]
    final_count = classified_conn.execute("SELECT COUNT(*) FROM final_build_development_activity_events").fetchone()[0]
    assert final_count == raw_count


def test_known_variants_join_to_classification(classified_conn):
    rows = classified_conn.execute(
        """
        SELECT msgtype, activity_class, is_meaningful_activity
        FROM final_build_development_activity_events
        WHERE msgtype IN (' recipe-save ', 'SQL_QUERY_START', 'RECIPE SAVE')
        ORDER BY timestamp
        """.strip()
    ).fetchall()
    assert rows == [
        (' recipe-save ', 'meaningful_action', True),
        ('SQL_QUERY_START', 'meaningful_action', True),
        ('RECIPE SAVE', 'meaningful_action', True),
    ]


def test_unknown_msgtypes_remain_unclassified_and_not_meaningful(classified_conn):
    row = classified_conn.execute(
        """
        SELECT activity_class, is_meaningful_activity
        FROM final_build_development_activity_events
        WHERE msgtype = 'mystery-event'
        """.strip()
    ).fetchone()
    assert row == ('unclassified', False)


def test_polling_events_are_excluded_from_user_facing_counts(classified_conn):
    count = classified_conn.execute(
        """
        SELECT COUNT(*)
        FROM final_build_development_activity_events
        WHERE is_meaningful_activity IS TRUE
        """.strip()
    ).fetchone()[0]
    assert count == 3


def test_null_or_blank_login_does_not_count_as_active_user(classified_conn):
    count = classified_conn.execute(
        """
        SELECT COUNT(DISTINCT login)
        FROM final_build_development_activity_events
        WHERE is_meaningful_activity IS TRUE
          AND login IS NOT NULL
          AND length(trim(login)) > 0
        """.strip()
    ).fetchone()[0]
    assert count == 3


def test_activity_classes_are_from_supported_set(classification_rows):
    actual = {_slug(str(row.get("activity_class") or "")) for row in classification_rows}
    assert actual <= ALLOWED_CLASSES
