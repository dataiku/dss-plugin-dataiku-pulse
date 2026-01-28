from dataiku.customrecipe import get_input_names_for_role
from dataiku.customrecipe import get_output_names_for_role
from dataiku.customrecipe import get_recipe_config
from dataiku.customrecipe import get_plugin_config
plugin_config = get_plugin_config()

####################################################################################################################
from backend.duck_db import create_conn


def build_gold_tables():
    try:
        create_conn.reset_duckdb(reset=True)
        expand_duckdb.configure_duckdb_runtime(),
        dataiku_sources.register_partition_df(),
        raw_views.register_raw_views(),
        dataiku_usage.register_dataiku_usage_views(),
        gold_tables.register_gold_tables(),
    except Exception as e:
        raise Exception(e)
    return