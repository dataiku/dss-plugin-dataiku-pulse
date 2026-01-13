META = {
    "id": "datasets.inactive_dataset_ratio",
    "version": 1,
    "label": "Inactive Dataset Ratio",
    "description": "Percentage of datasets not updated in the last 30 days per instance",
    "type": "metric",
    "order": 40,
    "value_column": "inactive_ratio_pct",
    "groupby": ["instance_name"],
}

def query():
    return """
        WITH per_instance AS (
            SELECT
                instance_name,
                COUNT(*) AS total_datasets,
                COUNT(*) FILTER (
                    WHERE dataset_versionTag_lastModifiedOn < CURRENT_TIMESTAMP - INTERVAL 30 DAY
                ) AS inactive_datasets
            FROM datasets_metadata_base
            GROUP BY instance_name
        )
        SELECT
            instance_name,
            ROUND(
                inactive_datasets * 100.0 / NULLIF(total_datasets, 0),
                2
            ) AS inactive_ratio_pct
        FROM per_instance
        ORDER BY instance_name
    ;
    """
