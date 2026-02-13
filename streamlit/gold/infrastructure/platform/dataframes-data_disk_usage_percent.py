META = {
    "id": "os.data_disk_usage_percent",
    "version": 1,
    "label": "Data Disk Usage (%)",
    "description": "Current disk usage percentage (latest timestamp)",
    "type": "dataframe",
    "order": 10,
    "value_column": "used_pct",
    "groupby": ["instance_name"],
}

def query():
    return """
    SELECT *
    FROM data_disk_usage_percent_base
    """
