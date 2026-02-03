# DuckDB

## Custom Recipe - Create Gold Tables

1. init_duckdb.py - Starting point for creating/setting up the DuckDB
    1. create_conn.py - Creates the "conn" or the connection to DuckDB
        1. storage_config.py - Figures out "partitioned_data" blob storage and setups permissions
    1. dataiku_sources.py - Registers the "partioned_data" blob storage dataframe
    1. raw_views.py - Creates the raw views to all the partitions
    1. gold_tables.py - Creates the phsyical cache gold tables from the views
    
## Additional Files

1. query.py - Connection used for "Read Only" mode for metrics/graphs/dataframes
1. sql_logger.py - Used during streamlit display process. Logs the query to DuckDB to help calculate future gold/cache tables.