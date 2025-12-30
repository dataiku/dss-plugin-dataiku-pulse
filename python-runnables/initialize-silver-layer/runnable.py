from datetime import datetime
import io
import os
import logging
import pandas as pd
import numpy as np
from joblib import Parallel, delayed

from dataiku.runnables import Runnable, ResultTable
from dataikupulse.src import dss_funcs
from dataikupulse.src import dss_folder


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

    def silver_instance_projects(self, df, path, results):
        category = path.split("/")[2]
        module_name = path.split("/")[3]
        self.instance_name = path.split("/")[4]
        df_clean, dq = dss_funcs._normalize_and_validate(self, df, category, module_name)
        if dq is None:
            results.append([category, module_name, "quality", False, "Unknown failure"])
            return results
        if df_clean is None:
            results.append([
                category,
                module_name,
                f"quality -- {dq['stage']}",
                False,
                dq["error"],
            ])
            return results
        df_report = pd.DataFrame([{
            "errors": dq["errors"],
            "warnings": dq["warnings"],
            **dq["stats"],
        }])
        base_path = path.replace("/raw/", "")
        if dq["errors"]:
            write_path = f"/raw_errors/{base_path}"
        else:
            write_path = f"/silver/{base_path}"
        dss_folder.write_remote_folder_output(self, write_path, df_clean)
        if dq["errors"]:
            filename = os.path.basename(write_path)
            report_path = write_path.replace(filename, f"dq_{filename}")
            df_report = pd.DataFrame([{
                "errors": dq["errors"],
                "warnings": dq["warnings"],
                **dq["stats"],
            }])
            dss_folder.write_remote_folder_output(self, report_path, df_report)
            results.append([category, module_name, f"write/save -- raw_errors", False, "Check raw errors"])
        else:
            results.append([category, module_name, f"write/save -- silver", True, None])
        return results
        
    def rebuild_silver(self, chunk_df):
        # Get all the partition paths
        paths = []
        for row in chunk_df.itertuples():
            paths.extend(self.folder.get_partition_info(row.partitions)["paths"])
        # Grab the parquet file and load as a df
        results = []
        for path in paths: # Loop paths
            # Grab the parquet file and load as a df
            with self.folder.get_download_stream(path) as stream:
                file_bytes = io.BytesIO(stream.read())
            df = pd.read_parquet(file_bytes)
            # Figure out which method to perform for NCQ
            if "/dataiku_usage/" in path:
                print(1)
            else:
                results = self.silver_instance_projects(df, path, results)
        return results
        
    def run(self, progress_callback):
        # variables
        self.project_handle = self.local_client.get_default_project()
        self.folder_name = "partitioned_data"
        
        # Get folder / raw paths
        self.folder = dss_folder.get_local_folder(self, self.project_handle, self.folder_name)
        partitions = self.folder.list_partitions()
        partitions_df = pd.DataFrame(partitions, columns=["partitions"])
        cols = ["layer", "category", "module", "instance_name", "date"]
        partitions_df[cols] = partitions_df["partitions"].str.split("|", expand=True)
        partitions_df = partitions_df.loc[partitions_df["layer"] == "raw"]
        
        # added filtering
        partitions_df = partitions_df.loc[partitions_df["category"] == "datasets"]
        
        # Re-Run Silver Quality Guard
        if self.preset_pc["do_parallel"]:
            chunks = np.array_split(partitions_df, self.preset_pc["cores"])
            results = Parallel(
                n_jobs=self.preset_pc["cores"],
                backend="threading",
                batch_size=1,
                verbose=10,
            )(
                delayed(self.rebuild_silver)(chunk)
                for chunk in chunks
                if not chunk.empty
            )
            dfs = []
            for r in results:
                dfs.append(pd.DataFrame(r).astype(str))
            results_df = pd.concat(dfs, ignore_index=True)
        else:
            results = self.rebuild_silver(partition_df)
            results_df =pd.DataFrame(results).astype(str)
            
        # Return ResultsTable
        
        rt = ResultTable()
        n = 1
        for col in results_df.columns:
            rt.add_column(n, col, "STRING")
            n +=1
        for index, row in results_df.iterrows():
            rt.add_record(row.tolist())
        return rt

# EOF

