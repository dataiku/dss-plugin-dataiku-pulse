from __future__ import annotations

import duckdb
import pytest
import yaml
from pathlib import Path
import re


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
            (TIMESTAMP '2026-08-01 10:00:00', 'inst', 'alice', 'recipe-save', 'recipe', 'visual_recipes', 'P1', '/r', '{}'),
            (TIMESTAMP '2026-08-01 10:01:00', 'inst', 'alice', 'code-studio-protected-view', 'code_studio', 'coding', 'P1', '/c', '{}'),
            (TIMESTAMP '2026-08-01 10:02:00', 'inst', NULL, 'jupyter-create-kernel-context', 'jupyter', 'coding', 'P1', '/j', '{}'),
            (TIMESTAMP '2026-08-01 10:03:00', 'inst', 'bob', 'totally-unknown-event', 'unknown', 'coding', 'P1', '/u', '{}'),
            (TIMESTAMP '2026-08-01 10:04:00', 'inst', NULL, 'recipe-save', 'recipe', 'visual_recipes', 'P1', '/r2', '{}'),
            (TIMESTAMP '2026-08-01 10:05:00', 'inst', 'alice', 'RECIPE-SAVE', 'recipe', 'Visual Recipes', 'P1', '/r3', '{}'),
            (TIMESTAMP '2026-08-01 10:06:00', 'inst', 'alice', 'recipe save', 'recipe', 'visual-recipes', 'P1', '/r4', '{}'),
            (TIMESTAMP '2026-08-01 10:07:00', 'inst', 'alice', 'recipe_save', 'recipe', 'visual recipes', 'P1', '/r5', '{}'),
            (TIMESTAMP '2026-08-01 10:08:00', 'inst', 'alice', '  recipe---save  ', 'recipe', '  visual__recipes  ', 'P1', '/r6', '{}')
        ) AS t(timestamp, instance_name, login, msgtype, msgtypebase, dataiku_category, project_key, callpath, extras)
        """.strip()
    )
    before_count = conn.execute("SELECT COUNT(*) FROM fact_dev_activity_events").fetchone()[0]
    conn.execute(
        """
        CREATE OR REPLACE TABLE dim_category_to_capability AS
        SELECT * FROM (
          VALUES
            ('visual_recipes', 'data_engineering', 1, 1, 'Data Engineering', 'Visual Recipes', TRUE),
            ('coding', 'data_engineering', 1, 2, 'Data Engineering', 'Coding', TRUE)
        ) AS t(dataiku_category, capability, capability_order, category_order, capability_display_name, category_display_name, is_dev_activity)
        """.strip()
    )
    conn.execute(
        """
        CREATE OR REPLACE VIEW final_build_development_activity_events AS
        WITH normalized_events AS (
          SELECT
            e.*,
            """ + _sql_slug_expr("e.msgtype") + """ AS msgtype_norm,
            """ + _sql_slug_expr("e.dataiku_category") + """ AS dataiku_category_norm
          FROM fact_dev_activity_events e
        ),
        normalized_categories AS (
          SELECT
            m.*,
            """ + _sql_slug_expr("m.dataiku_category") + """ AS dataiku_category_norm
          FROM dim_category_to_capability m
        ),
        normalized_classification AS (
          SELECT
            c.*,
            """ + _sql_slug_expr("c.msgtype") + """ AS msgtype_norm
          FROM dim_dev_activity_event_classification c
        )
        SELECT
          e.timestamp,
          e.instance_name,
          e.login,
          e.project_key,
          e.msgtype,
          e.msgtypebase AS base_tag,
          COALESCE(m.category_display_name, e.dataiku_category) AS dataiku_category,
          COALESCE(m.capability_display_name, m.capability, 'Uncategorized') AS capability,
          COALESCE(c.activity_class, 'unclassified') AS activity_class,
          COALESCE(c.is_meaningful_activity, FALSE) AS is_meaningful_activity,
          CASE WHEN e.login IS NOT NULL AND length(trim(e.login)) > 0 THEN TRUE ELSE FALSE END AS is_user_attributed
        FROM normalized_events e
        LEFT JOIN normalized_categories m
          ON m.dataiku_category_norm = e.dataiku_category_norm
        LEFT JOIN normalized_classification c
          ON c.msgtype_norm = e.msgtype_norm
        """.strip()
    )
    conn.execute(
        """
        CREATE OR REPLACE VIEW dev_activity_top_users_30d AS
        WITH x AS (
          SELECT login, capability, CAST(date_trunc('day', timestamp) AS DATE) AS day, timestamp
          FROM final_build_development_activity_events
          WHERE timestamp >= now() - INTERVAL 30 DAY
            AND is_meaningful_activity IS TRUE
            AND login IS NOT NULL
            AND length(trim(login)) > 0
        )
        SELECT
          login,
          COUNT(*) AS event_count_30d,
          COUNT(DISTINCT day) AS active_days_30d,
          COUNT(DISTINCT capability) AS capabilities_touched_30d
        FROM x
        GROUP BY 1
        ORDER BY event_count_30d DESC
        """.strip()
    )
    conn.execute(
        """
        CREATE OR REPLACE VIEW dev_activity_capability_30d AS
        SELECT
          capability,
          COUNT(*) FILTER (WHERE timestamp >= now() - INTERVAL 30 DAY AND is_meaningful_activity IS TRUE) AS event_count_30d,
          COUNT(DISTINCT login) FILTER (
            WHERE timestamp >= now() - INTERVAL 30 DAY
              AND is_meaningful_activity IS TRUE
              AND login IS NOT NULL
              AND length(trim(login)) > 0
          ) AS active_users_30d
        FROM final_build_development_activity_events
        GROUP BY 1
        ORDER BY event_count_30d DESC
        """.strip()
    )
    conn.execute(
        """
        CREATE OR REPLACE VIEW dev_activity_category_30d AS
        SELECT
          capability,
          dataiku_category,
          COUNT(*) FILTER (WHERE timestamp >= now() - INTERVAL 30 DAY AND is_meaningful_activity IS TRUE) AS event_count_30d,
          COUNT(DISTINCT login) FILTER (
            WHERE timestamp >= now() - INTERVAL 30 DAY
              AND is_meaningful_activity IS TRUE
              AND login IS NOT NULL
              AND length(trim(login)) > 0
          ) AS active_users_30d
        FROM final_build_development_activity_events
        GROUP BY 1, 2
        ORDER BY event_count_30d DESC
        """.strip()
    )
    conn.execute(
        """
        CREATE OR REPLACE VIEW dev_activity_unclassified_events_30d AS
        SELECT
          msgtype,
          base_tag,
          dataiku_category,
          COUNT(*) AS event_count_30d,
          COUNT(DISTINCT login) AS distinct_users_30d,
          COUNT(*) FILTER (WHERE login IS NULL OR length(trim(login)) = 0) AS unattributed_events_30d
        FROM final_build_development_activity_events
        WHERE timestamp >= now() - INTERVAL 30 DAY
          AND activity_class = 'unclassified'
        GROUP BY 1, 2, 3
        ORDER BY event_count_30d DESC
        """.strip()
    )
    return conn, before_count


def test_classification_yaml_uses_allowed_classes(classification_rows):
    observed = {_slug(str(row["activity_class"])) for row in classification_rows}
    assert observed <= ALLOWED_CLASSES


def test_duplicate_normalized_msgtypes_fail_clearly(conn):
    rows = [
        {"msgtype": "Recipe Save", "activity_class": "meaningful_action", "is_meaningful_activity": True},
        {"msgtype": "recipe-save", "activity_class": "polling_status", "is_meaningful_activity": False},
    ]
    with pytest.raises(ValueError, match="recipe_save|recipe_save|recipe_save".replace("_", "[-_ ]?")):
        _build_dim_dev_activity_event_classification_for_test(conn, rows)



def test_raw_fact_row_count_unchanged_and_final_view_preserves_rows(classified_conn):
    conn, before_count = classified_conn
    after_count = conn.execute("SELECT COUNT(*) FROM fact_dev_activity_events").fetchone()[0]
    final_count = conn.execute("SELECT COUNT(*) FROM final_build_development_activity_events").fetchone()[0]
    assert after_count == before_count == 9
    assert final_count == before_count



def test_known_and_unknown_event_types_classify_conservatively(classified_conn):
    conn, _ = classified_conn
    rows = conn.execute(
        """
        SELECT msgtype, activity_class, is_meaningful_activity
        FROM final_build_development_activity_events
        ORDER BY timestamp
        """.strip()
    ).fetchall()
    assert rows == [
        ("recipe-save", "meaningful_action", True),
        ("code-studio-protected-view", "polling_status", False),
        ("jupyter-create-kernel-context", "system_unattributed", False),
        ("totally-unknown-event", "unclassified", False),
        ("recipe-save", "meaningful_action", True),
        ("RECIPE-SAVE", "meaningful_action", True),
        ("recipe save", "meaningful_action", True),
        ("recipe_save", "meaningful_action", True),
        ("  recipe---save  ", "meaningful_action", True),
    ]


def test_msgtype_variants_and_category_variants_join_via_canonical_normalization(classified_conn):
    conn, _ = classified_conn
    rows = conn.execute(
        """
        SELECT msgtype, activity_class, is_meaningful_activity, dataiku_category, capability
        FROM final_build_development_activity_events
        WHERE msgtype IN ('RECIPE-SAVE', 'recipe save', 'recipe_save', '  recipe---save  ')
        ORDER BY timestamp
        """.strip()
    ).fetchall()
    assert rows == [
        ('RECIPE-SAVE', 'meaningful_action', True, 'Visual Recipes', 'Data Engineering'),
        ('recipe save', 'meaningful_action', True, 'Visual Recipes', 'Data Engineering'),
        ('recipe_save', 'meaningful_action', True, 'Visual Recipes', 'Data Engineering'),
        ('  recipe---save  ', 'meaningful_action', True, 'Visual Recipes', 'Data Engineering'),
    ]



def test_polling_events_excluded_but_meaningful_events_included_in_rollups(classified_conn):
    conn, _ = classified_conn
    top_users = conn.execute("SELECT login, event_count_30d FROM dev_activity_top_users_30d ORDER BY login").fetchall()
    capability = conn.execute(
        "SELECT capability, event_count_30d, active_users_30d FROM dev_activity_capability_30d ORDER BY capability"
    ).fetchall()
    category = conn.execute(
        "SELECT capability, dataiku_category, event_count_30d, active_users_30d FROM dev_activity_category_30d ORDER BY capability, dataiku_category"
    ).fetchall()

    assert top_users == [("alice", 5)]
    assert capability == [("Data Engineering", 6, 1)]
    assert category == [("Data Engineering", "Coding", 0, 0), ("Data Engineering", "Visual Recipes", 6, 1)]



def test_unclassified_audit_view_and_null_login_user_counts(classified_conn):
    conn, _ = classified_conn
    rows = conn.execute(
        "SELECT msgtype, event_count_30d, distinct_users_30d, unattributed_events_30d FROM dev_activity_unclassified_events_30d"
    ).fetchall()
    assert rows == [("totally-unknown-event", 1, 1, 0)]
