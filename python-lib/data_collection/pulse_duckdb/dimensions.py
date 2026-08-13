from __future__ import annotations

import duckdb


def build_dim_addon_feature_flags(conn: duckdb.DuckDBPyConnection) -> str:
    conn.execute(
        """
        CREATE OR REPLACE TABLE dim_addon_feature_flags AS
        SELECT
          addon_key,
          BOOL_OR(CASE WHEN try_cast(addon_enabled AS BOOLEAN) IS TRUE THEN TRUE ELSE FALSE END) AS enabled_any_instance
        FROM base_license_addon_licenses_latest
        GROUP BY addon_key
        ORDER BY addon_key;
        """.strip()
    )
    return "dim_addon_feature_flags"
