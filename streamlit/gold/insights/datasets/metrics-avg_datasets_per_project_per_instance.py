META = {
    "id": "datasets.avg_datasets_per_project_per_instance",
    "version": 1,
    "label": "Avg Datasets per Project",
    "description": "Average number of datasets per project per instance",
    "type": "metric",
    "order": 30,
    "value_column": "avg_datasets_per_project",
    "groupby": ["instance_name"],
}

def query():
    return """
        WITH per_project AS (
            SELECT
                instance_name,
                project_key,
                COUNT(*) AS dataset_count
            FROM datasets_metadata_base
            GROUP BY instance_name, project_key
        )
        SELECT
            instance_name,
            ROUND(AVG(dataset_count), 2) AS avg_datasets_per_project
        FROM per_project
        GROUP BY instance_name
        ORDER BY instance_name
    ;"""
