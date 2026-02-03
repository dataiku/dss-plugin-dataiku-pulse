from backend.duck_db import create_conn
from backend.duck_db import expand_duckdb
from backend.duck_db import dataiku_sources
from backend.duck_db import raw_views
from backend.duck_db import dataiku_usage
from backend.duck_db import create_gold_tables
from backend.duck_db import query
from backend import settings


import logging
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.warning)
logger = logging.getLogger(__name__)

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
    
    # 4. Unload the gold tables
    base_path = f"{settings.blob_header}://{settings.blob_bket}/{settings.dss_gold_tables_folder_root}/gold"
    for table_name in base_tables:
        destination = f"{base_path}/{table_name}.parquet"
        logger.warning(f"Unloading {table_name} to {destination}...")
        query = (
            f"COPY {table_name} "
            f"TO '{destination}' "
            f"(FORMAT 'PARQUET', OVERWRITE TRUE);"
        )
        logger.debug(query)
        try:
            
            conn.execute(query)
        except Exception as e:
            logger.warning(f"Failed to unload {table_name}: {e}")
    
    # End
    logger.warning("Export process complete.")
    return

# -------------------------------------------------------------------------------------------
# build_gold_tables
# -------------------------------------------------------------------------------------------
build_gold_tables()
