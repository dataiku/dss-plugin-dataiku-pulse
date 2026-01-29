META = {
    "id": "datasets.active_datasets_last_30_days",
    "version": 1,
    "label": "Active Datasets (30 Days)",
    "description": "Number of datasets updated in the last 30 days per instance",
    "type": "metric",
    "order": 30,
    "value_column": "active_datasets_30d",
    "groupby": ["instance_name"],
}

def query():
    return """
        SELECT
            instance_name,
            COUNT(*) AS active_datasets_30d
        FROM datasets_metadata_base
        WHERE
            dataset_versionTag_lastModifiedOn >= CURRENT_TIMESTAMP - INTERVAL 30 DAY
        GROUP BY instance_name
        ORDER BY instance_name
    ;
    """
