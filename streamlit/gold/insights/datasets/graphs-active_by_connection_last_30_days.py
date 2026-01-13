META = {
    "id": "datasets.active_by_connection_last_30_days",
    "version": 1,
    "label": "Active Datasets by Connection (30 Days)",
    "description": "Datasets updated in the last 30 days by connection type",
    "type": "graph",
    "tab": "activity",
    "graph": {
        "kind": "bar",
        "x": "connection_type",
        "y": "active_dataset_count",
        "color": "instance_name",
        "barmode": "group",
        "x_title": "Connection Type",
        "y_title": "Active Datasets (30 Days)",
        "legend_title": "Instance",
        "labels": {
            "connection_type": "Connection Type",
            "active_dataset_count": "Active Datasets",
            "instance_name": "Instance"
        }
    }
}

def query():
    return """
        WITH active AS (
            SELECT
                instance_name,
                dataset_type AS connection_type
            FROM datasets_metadata_base
            WHERE
                dataset_versionTag_lastModifiedOn >= CURRENT_TIMESTAMP - INTERVAL 30 DAY
        ),
        top_connections AS (
            SELECT
                connection_type
            FROM active
            GROUP BY connection_type
            ORDER BY COUNT(*) DESC
            LIMIT 10
        )
        SELECT
            a.instance_name,
            a.connection_type,
            COUNT(*) AS active_dataset_count
        FROM active a
        JOIN top_connections t
            ON a.connection_type = t.connection_type
        GROUP BY
            a.instance_name,
            a.connection_type
        ORDER BY
            active_dataset_count DESC,
            a.instance_name
    ;
    """

