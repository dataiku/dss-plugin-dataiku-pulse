from dataiku.runnables import Runnable, ResultTable
from dataikupulse.src import dss_funcs
from dataikupulse.src import dss_init
import pandas as pd
import os
import logging


class MyRunnable(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config
        self.plugin_config = plugin_config
        self.params = plugin_config.get("pulse_primary", {})
        self.preset_pc = dss_funcs.get_preset_pc(self, "DATAIKU-PULSE")
        self.local_client = dss_funcs.build_local_client()
        self.remote_client = dss_funcs.build_remote_client(self)
        self.instance_name = dss_funcs.get_dss_name(self)
        self.dt = datetime.now(timezone.utc)
        
        logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.ERROR)
        self.logger = logging.getLogger(__name__)
        
    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        # variables
        project_handle =
        folder_name = 
        
        # Get folder / raw paths
        folder = get_local_folder(self, project_handle, folder_name)
        partitions_df = pd.DataFrame(partitions, columns=["partitions"])
        cols = ["layer", "category", "module", "instance_name", "date"]
        partitions_df[cols] = partitions_df["partitions"].str.split("|", expand=True)
        partitions_df = partitions_df.loc[partitions_df["layer"] == "raw"]
        
        # Re-Run Silver Quality Guard
        for row in partitions_df.itertuples(): # get partition
            for path in folder.get_partition_info(row.partitions)["paths"]: # get path(s)
                with folder.get_download_stream(path) as stream: # read in stream/df
                    file_bytes = io.BytesIO(stream.read())
                df = pd.read_parquet(file_bytes)
                # Fix Quality
                layer = "/silver/"
                try:
                    df = dss_silver.coerce_schema(df)
                    dq = dss_silver.data_quality(df)
                    df_report = pd.DataFrame([{
                        "errors": dq["errors"],
                        "warnings": dq["warnings"],
                        **dq["stats"],
                    }])
                    if dq["errors"]:
                        layer = "/raw_errors/"
                        write_path = path.replace("/raw/", layer)
                        dss_folder.write_remote_folder_output(self, write_path, df)
                        write_path = path.replace("/raw/", layer)
                        dss_folder.write_remote_folder_output(self, write_path, df_report)
                except:
                    hi
                
                
                
        raise Exception("unimplemented")
        
        
        
        
        
        
        