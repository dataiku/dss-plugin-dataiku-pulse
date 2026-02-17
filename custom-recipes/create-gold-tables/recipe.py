import io
import logging

import dataiku
from dataiku.customrecipe import get_recipe_config

from pulse_duckdb import settings
from pulse_duckdb.engine import (
    create_conn,
    create_gold_tables,
    dataiku_sources,
    dataiku_usage,
    expand_duckdb,
    query,
    raw_views,
)

LOG_LEVEL = logging.WARNING
logging.getLogger("pulse_duckdb").setLevel(LOG_LEVEL)

def build_gold_tables():
    # 1. Delete anything existing
    create_conn.reset_duckdb()
    
    # 2. build and populate
    conn = create_conn.create_connection(read_only=False)
    expand_duckdb.configure_duckdb_runtime(conn)
    dataiku_sources.reg_dss_source_folder_df(conn, data_src="partitioned_data", show_ui=True)
    raw_views.register_raw_views(conn)
    dataiku_usage.register_dataiku_usage_views(conn)
    create_gold_tables.register_gold_tables(conn)
    
    # 3. Get table listing
    query = """
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_name LIKE '%_base'
    """
    df = conn.execute(query).df()
    base_tables = df['table_name'].tolist()
    
    # 4. Custom edits
    recipe_config = get_recipe_config()
    unload_behavior = recipe_config.get('unload_behavior', "duckdb")
    
    # 5. Unload the gold tables
    for table_name in base_tables:
        destination = "gold/{table_name}.parquet"
        logger.warning(f"Unloading {table_name} to {destination}...")
        
        if unload_behavior == "duckdb":
            path = (
                f"{settings.blob_header}://"
                f"{settings.blob_bket}/"
                f"{settings.dss_gold_tables_folder_root}/"
                f"{destination}"
            )
            query = (
                f"COPY {table_name} "
                f"TO '{path}' "
                f"(FORMAT 'PARQUET', OVERWRITE TRUE);"
            )
            logger.debug(query)
            try:
                conn.execute(query)
            except Exception as e:
                logger.warning(f"Failed to unload {table_name}: {e}")
                
        elif unload_behavior == "dataiku":
            unload_df = conn.execute(f"SELECT * FROM {table_name};")

            f = io.BytesIO()
            unload_df.to_parquet(f, compression="gzip", engine='pyarrow', index=False)
            f.seek(0)
            content = f.read()
            settings.dss_gold_tables_folder.upload_stream(path, content)
        else:
            logger.error("Unknown unload behavior")
            raise
    
    # End
    logger.warning("Export process complete.")
    return

# -------------------------------------------------------------------------------------------
# build_gold_tables
# -------------------------------------------------------------------------------------------
build_gold_tables()
