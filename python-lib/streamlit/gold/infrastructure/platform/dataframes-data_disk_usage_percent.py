META = {
    "id": "os.data_disk_usage_percent",
    "version": 1,
    "label": "Data Disk Usage (%)",
    "description": "Current disk usage percentage (latest timestamp)",
    "type": "dataframe",
    "order": 10,
    "value_column": "data_usage_percent",
    "groupby": ["instance_name"],
}

def query():
    return """
    WITH latest AS (
        SELECT
            instance_name,
            MAX(timestamp) AS max_ts
        FROM operating_system_filesystem_view
        GROUP BY instance_name
    )
    SELECT
        f.filesystem,
        f.size,
        f.used,
        f.available,
        f.used_pct,
        f.mounted_on,
        f.instance_name
    FROM operating_system_filesystem_view f
    JOIN latest l
    ON f.instance_name = l.instance_name
    AND f.timestamp = l.max_ts
    ORDER BY f.instance_name
    ;
    """