META = {
    "id": "datasets.created_per_month",
    "version": 1,
    "label": "Datasets Created Per Month",
    "description": "Number of datasets created per month by instance",
    "type": "graph",
    "tab": "trends",
    "graph": {
        "kind": "line",
        "x": "month",
        "y": "dataset_count",
        "color": "instance_name",
        "x_title": "Month",
        "y_title": "Datasets Created",
        "legend_title": "Instance",
        "labels": {
            "month": "Month",
            "dataset_count": "Datasets Created",
            "instance_name": "Instance"
        }
    }
}

def query():
    return """
        SELECT
            DATE_TRUNC('month', dataset_creationTag_lastModifiedOn) AS month,
            instance_name,
            COUNT(*) AS dataset_count
        FROM datasets_metadata_base
        GROUP BY
            month,
            instance_name
        ORDER BY
            month,
            instance_name
    ;
    """
