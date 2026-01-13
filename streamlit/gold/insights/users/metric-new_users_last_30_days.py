META = {
    "id": "users.number_new_users_last_30_days",
    "version": 1,
    "label": "New Users",
    "description": "Number of new users in the last 30 days",
    "type": "metric",
    "order": 30,
    "value_column": "current_30_days",
    "delta_column": "delta",
    "delta_pct_column": "delta_pct",
    "groupby": ["instance_name"],
}

def query():
    return """
        WITH base AS (
            SELECT
                instance_name,
                login,
                creationDate
            FROM users_metadata_base
        ),
        counts AS (
            SELECT
                instance_name,
                COUNT(DISTINCT login) FILTER (
                    WHERE creationDate >= CURRENT_TIMESTAMP - INTERVAL 30 DAY
                ) AS current_30_days,
                COUNT(DISTINCT login) FILTER (
                    WHERE creationDate >= CURRENT_TIMESTAMP - INTERVAL 60 DAY
                    AND creationDate <  CURRENT_TIMESTAMP - INTERVAL 30 DAY
                ) AS previous_30_days
            FROM base
            GROUP BY instance_name
        )
        SELECT
            instance_name,
            current_30_days,
            previous_30_days,
            current_30_days - previous_30_days AS delta,
            CASE
                WHEN previous_30_days = 0 THEN NULL
                ELSE ROUND(
                    (current_30_days - previous_30_days) * 100.0 / previous_30_days,
                    2
                )
            END AS delta_pct
        FROM counts
        ORDER BY instance_name
    ;"""
