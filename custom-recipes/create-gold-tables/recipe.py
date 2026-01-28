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
    except Exception as e:
        raise Exception(e)
    return