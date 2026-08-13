from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pytest


def _slug(value: str) -> str:
    s = str(value or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def _build_dim_category_to_capability_for_test(
    conn: duckdb.DuckDBPyConnection,
    *,
    rows: list[dict],
) -> str:
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

    normalized_categories = [_slug(str(r.get("dataiku_category") or "")) for r in rows]
    duplicate_categories = sorted(
        {
            category
            for category in normalized_categories
            if category and normalized_categories.count(category) > 1
        }
    )
    if duplicate_categories:
        raise ValueError(
            "Duplicate normalized dataiku_category values in category_to_capability.yaml: "
            + ", ".join(duplicate_categories)
        )

    insert_rows = [
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
    ]

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
        insert_rows,
    )

    return "dim_category_to_capability"


@pytest.fixture()
def conn():
    connection = duckdb.connect(database=":memory:")
    try:
        yield connection
    finally:
        connection.close()


def test_build_dim_category_to_capability_includes_uncategorized(conn):
    rows = [
        {
            "dataiku_category": "administration",
            "capability": "uncategorized",
            "capability_display_name": "Uncategorized",
            "category_display_name": "Administration",
            "is_dev_activity": False,
        },
        {
            "dataiku_category": "coding",
            "capability": "data_engineering",
            "capability_display_name": "Data Engineering",
            "category_display_name": "Coding",
            "is_dev_activity": True,
        },
    ]

    table_name = _build_dim_category_to_capability_for_test(conn, rows=rows)

    assert table_name == "dim_category_to_capability"
    result = conn.execute(
        """
        SELECT dataiku_category, capability, capability_display_name, category_display_name, is_dev_activity
        FROM dim_category_to_capability
        ORDER BY dataiku_category
        """.strip()
    ).fetchall()
    assert result == [
        ("administration", "uncategorized", "Uncategorized", "Administration", False),
        ("coding", "data_engineering", "Data Engineering", "Coding", True),
    ]


@pytest.mark.parametrize(
    "duplicate_rows, expected_duplicates",
    [
        (
            [
                {"dataiku_category": "Admin", "capability": "uncategorized"},
                {"dataiku_category": " admin ", "capability": "other"},
            ],
            "admin",
        ),
        (
            [
                {"dataiku_category": "reading-listing", "capability": "uncategorized"},
                {"dataiku_category": "reading listing", "capability": "uncategorized"},
            ],
            "reading_listing",
        ),
    ],
)
def test_build_dim_category_to_capability_rejects_duplicate_normalized_categories(
    conn,
    duplicate_rows,
    expected_duplicates,
):
    with pytest.raises(ValueError, match=expected_duplicates):
        _build_dim_category_to_capability_for_test(conn, rows=duplicate_rows)



def test_build_dim_category_to_capability_normalizes_inserted_values(conn):
    rows = [
        {
            "dataiku_category": "  Reading Listing  ",
            "capability": "  UnCategorized ",
            "capability_display_name": "Reading & Listing",
            "category_display_name": "Reading Listing",
            "is_dev_activity": False,
        },
        {
            "dataiku_category": "Visual Recipes",
            "capability": "Data Engineering",
            "capability_display_name": "Data Engineering",
            "category_display_name": "Visual Recipes",
            "is_dev_activity": True,
        },
    ]

    _build_dim_category_to_capability_for_test(conn, rows=rows)

    result = conn.execute(
        "SELECT dataiku_category, capability FROM dim_category_to_capability ORDER BY dataiku_category"
    ).fetchall()
    assert result == [
        ("reading_listing", "uncategorized"),
        ("visual_recipes", "data_engineering"),
    ]



def test_build_dim_category_to_capability_keeps_one_row_per_normalized_category(conn):
    rows = [
        {
            "dataiku_category": "containers",
            "capability": "uncategorized",
            "capability_order": 99,
            "category_order": 2,
            "capability_display_name": "Uncategorized",
            "category_display_name": "Containers",
            "is_dev_activity": False,
        },
        {
            "dataiku_category": "coding",
            "capability": "data_engineering",
            "capability_order": 5,
            "category_order": 1,
            "capability_display_name": "Data Engineering",
            "category_display_name": "Coding",
            "is_dev_activity": True,
        },
    ]

    _build_dim_category_to_capability_for_test(conn, rows=rows)

    row_count = conn.execute("SELECT COUNT(*) FROM dim_category_to_capability").fetchone()[0]
    distinct_count = conn.execute(
        "SELECT COUNT(DISTINCT dataiku_category) FROM dim_category_to_capability"
    ).fetchone()[0]
    coding_row = conn.execute(
        """
        SELECT capability, capability_order, category_order, capability_display_name, category_display_name, is_dev_activity
        FROM dim_category_to_capability
        WHERE dataiku_category = 'coding'
        """.strip()
    ).fetchone()

    assert row_count == 2
    assert distinct_count == 2
    assert coding_row == ("data_engineering", 5, 1, "Data Engineering", "Coding", True)
