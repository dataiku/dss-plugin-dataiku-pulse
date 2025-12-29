from datetime import datetime
import io
import os
import logging
import pandas as pd
from joblib import Parallel, delayed

from dataiku.runnables import Runnable, ResultTable
from dataikupulse.src import dss_funcs
from dataikupulse.src import dss_folder

def process_one_file(path):
    try:
        folder = dss_folder.get_local_folder(self, project_handle, folder_name)
        with folder.get_download_stream(path) as stream:
            file_bytes = io.BytesIO(stream.read())
        df = pd.read_parquet(file_bytes)
        df = dss_silver.coerce_schema(df)
        dq = dss_silver.data_quality(df)
        base_path = path.replace("/raw/", "")
        if dq["errors"]:
            write_path = f"/raw_errors/{base_path}"
        else:
            write_path = f"/silver/{base_path}"
        dss_folder.write_remote_folder_output(self, write_path, df)
        if dq["errors"]:
            filename = os.path.basename(write_path)
            report_path = write_path.replace(filename, f"dq_{filename}")
            df_report = pd.DataFrame([{
                "errors": dq["errors"],
                "warnings": dq["warnings"],
                **dq["stats"],
            }])
            dss_folder.write_remote_folder_output(self, report_path, df_report)
        return {"status": "ok", "errors": bool(dq["errors"])}
    except Exception as e:
        return {"status": "exception", "error": str(e)}

    
    
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
        self.dt = datetime.utcnow()
        
        logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.ERROR)
        self.logger = logging.getLogger(__name__)
        
    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        # variables
        project_handle = self.local_client.get_default_project()
        folder_name = "partitioned_data"
        
        # Get folder / raw paths
        folder = dss_folder.get_local_folder(self, project_handle, folder_name)
        partitions = folder.list_partitions()
        partitions_df = pd.DataFrame(partitions, columns=["partitions"])
        cols = ["layer", "category", "module", "instance_name", "date"]
        partitions_df[cols] = partitions_df["partitions"].str.split("|", expand=True)
        partitions_df = partitions_df.loc[partitions_df["layer"] == "raw"]
        
        # list of paths
        paths = []
        for row in partitions_df.itertuples():
            paths.extend(folder.get_partition_info(row.partitions)["paths"])
        
        # Re-Run Silver Quality Guard
        results = Parallel(
            n_jobs=4,          # start conservative; increase carefully
            backend="loky",    # multiprocessing, not threads
            verbose=10,
        )(
            delayed(process_one_file)(path)
            for path in paths
        )
        results_df = pd.DataFrame(results)
        
        # Return ResultsTable
        results_df = results_df.astype(str)
        rt = ResultTable()
        n = 1
        for col in results_df.columns:
            rt.add_column(n, col, "STRING")
            n +=1
        for index, row in results_df.iterrows():
            rt.add_record(row.tolist())
        return rt
        
        
        
        
        
        
        