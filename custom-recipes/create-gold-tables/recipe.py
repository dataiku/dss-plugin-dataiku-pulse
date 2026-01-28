from dataiku.customrecipe import get_input_names_for_role
from dataiku.customrecipe import get_output_names_for_role
from dataiku.customrecipe import get_recipe_config
from dataiku.customrecipe import get_plugin_config
plugin_config = get_plugin_config()

####################################################################################################################
from backend.duck_db import create_conn
from backend.duck_db import expand_duckdb
from backend.duck_db import dataiku_sources
from backend.duck_db import raw_views
from backend.duck_db import dataiku_usage
from backend.duck_db import gold_tables
from backend.duck_db import query



import logging
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.DEBUG)


def build_gold_tables():
    # Delete anything existing
    create_conn.reset_duckdb(reset=True)
    
    # build and populate
    conn = create_conn.create_connection(read_only=False)
    expand_duckdb.configure_duckdb_runtime(conn)
    dataiku_sources.register_partition_df(conn)
    raw_views.register_raw_views(conn)
    dataiku_usage.register_dataiku_usage_views(conn)
    gold_tables.register_gold_tables(conn)
    return

build_gold_tables()
df = query.query_df("PRAGMA show_tables_expanded;", page="DEBUG")
print(df.to_json())