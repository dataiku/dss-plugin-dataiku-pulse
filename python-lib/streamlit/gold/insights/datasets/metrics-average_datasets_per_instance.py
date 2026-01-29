META = {
    "id": "datasets.avg_datasets_per_instance",
    "version": 1,
    "label": "Avg Datasets per Instance",
    "description": "Average number of datasets per instance",
    "type": "metric",
    "order": 20,
    "value_column": "avg_datasets",
    "groupby": ["instance_name"],
}
def query():
    return """
        SELECT
            instance_name,
            COUNT(*) AS avg_datasets
        FROM datasets_metadata_base
        GROUP BY instance_name
        ORDER BY instance_name
    ;
    """
