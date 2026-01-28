from dataiku.customrecipe import get_input_names_for_role
from dataiku.customrecipe import get_output_names_for_role
from dataiku.customrecipe import get_recipe_config
from dataiku.customrecipe import get_plugin_config
plugin_config = get_plugin_config()

####################################################################################################################
from backend.duckdb import init_duckdb
init_duckdb._run_gold_pipeline