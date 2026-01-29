from pathlib import Path
import dataiku

# Base app directory (useful if the app is executed from various working dirs)
BASE_DIR = Path(__file__).resolve().parent.parent

# DuckDB file location
DB_PATH = Path("/tmp/duckdb/pulse.duckdb")

# Dataiku Folder
dss_partitioned_folder = dataiku.Folder(
    lookup="partitioned_data",
    project_key=dataiku.default_project_key(),
    ignore_flow=True
)

gold_tables_folder = dataiku.Folder(
    lookup="gold_tables",
    project_key=dataiku.default_project_key(),
    ignore_flow=True
)

# Dataiku Handlers
client = dataiku.api_client()

# Plugin Configuration
plugin_handle = client.get_plugin(plugin_id="dataiku-pulse")
plugin_settings = plugin_handle.get_settings()
param_set = plugin_settings.get_parameter_set(parameter_set_name="params-dashboard-instance")
preset = param_set.get_preset(preset_name=param_set.list_preset_names()[0])
monitor_os = preset.get_raw()["pluginConfig"]["monitor_os"]

# Project Data
project_handle = client.get_default_project()
folder_handle = project_handle.get_managed_folder(odb_id=dss_partitioned_folder.get_id())

# BLOB Headers
connection_name = folder_handle.get_settings().settings["params"]["connection"]
connection_handle = client.get_connection(name=connection_name)
connection_type = connection_handle.get_info()["type"]

if connection_type == "EC2":
    blob_header = "s3"
    blob_bket = dss_partitioned_folder.get_info()["accessInfo"]["bucket"]    
elif connection_type == "Azure":
    blob_header = "az"
    blob_bket = dss_partitioned_folder.get_info()["accessInfo"]["container"]
elif connection_type == "GCS":
    blob_header = "gs"
    blob_bket = dss_partitioned_folder.get_info()["accessInfo"]["bucket"]

dss_partitioned_folder_root = dss_partitioned_folder.get_info()["accessInfo"]["root"][1:]
gold_tables_folder_root = gold_tables_folder.get_info()["accessInfo"]["root"][1:]
