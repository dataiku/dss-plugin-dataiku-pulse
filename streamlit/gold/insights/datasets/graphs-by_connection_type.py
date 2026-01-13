META = {
    "id": "datasets.by_connection_type",
    "version": 1,
    "label": "Datasets by Connection Type",
    "description": "Distribution of datasets by connection type across instances",
    "type": "graph",
    "tab": "summary",
    "graph": {
        "kind": "bar",
        "x": "connection_type",
        "y": "dataset_count",
        "color": "instance_name",
        "barmode": "group",
        "x_title": "Connection Type",
        "y_title": "Number of Datasets",
        "legend_title": "Instance",
        "labels": {
            "connection_type": "Connection Type",
            "dataset_count": "Datasets",
            "instance_name": "Instance"
        }
    }
}

def query():
    return """
        WITH all_datasets AS (
            SELECT
                instance_name,
                dataset_type AS connection_type
            FROM datasets_metadata_base
        ),
        top_connections AS (
            SELECT
                connection_type
            FROM all_datasets
            GROUP BY connection_type
            ORDER BY COUNT(*) DESC
            LIMIT 10
        )
        SELECT
            d.instance_name,
            d.connection_type,
            COUNT(*) AS dataset_count
        FROM all_datasets d
        JOIN top_connections t
            ON d.connection_type = t.connection_type
        GROUP BY
            d.instance_name,
            d.connection_type
        ORDER BY
            dataset_count DESC,
            d.instance_name
    ;
    """
