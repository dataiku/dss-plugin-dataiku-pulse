from datetime import datetime
import io
import logging
import os

import numpy as np
import pandas as pd
from joblib import Parallel, delayed

from dataiku.runnables import ResultTable, Runnable

from pulse_modules.helpers import dss_folder, dss_funcs, dss_rebuild_silver


class MyRunnable(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config
        self.plugin_config = plugin_config
        self.params = plugin_config.get("pulse_primary", {})
        self.preset_pc = dss_funcs.get_preset_pc(self, "DATAIKU-PULSE")
        self.local_client = dss_funcs.build_local_client()
        self.remote_client = dss_funcs.build_remote_client(self)
        self.instance_name = dss_funcs.get_instance_id(self)
        self.dt = datetime.utcnow()
        
        logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.ERROR)
        self.logger = logging.getLogger(__name__)
        
    def get_progress_target(self):
        return None
        
    def run(self, progress_callback):
        # variables
        self.project_handle = self.local_client.get_default_project()
        self.folder_name = "partitioned_data"
        self.folder = dss_folder.get_local_folder(self, self.project_handle, self.folder_name)
        
        # Gather Paths
        all_paths = self.folder.list_paths_in_partition()
        limit = self.config.get("pulse_limit_category", "all")
        paths = [
            path for path in all_paths
            if "/raw/category=" in path
        ]
        if limit != "all":
            paths = [
                path for path in paths
                if f"/raw/category={limit}/" in path
            ]
        if not paths:
            raise Exception(f"No Hive-partitioned paths found for category '{limit}'")

        # Drop SILVER for selected category (or all)
        silver_paths = [
            path for path in all_paths
            if "/silver/category=" in path
        ]
        if limit != "all":
            silver_paths = [
                path for path in silver_paths
                if f"/silver/category={limit}/" in path
            ]
        for path in silver_paths:
            try:
                self.folder.delete_path(path=path)
            except Exception as e:
                # best-effort deletion; rebuild will overwrite many paths
                self.logger.error(f"Failed to delete silver path: {path} - {e}")

        # Re-Run Silver Quality Guard
        if self.preset_pc["do_parallel"]:
            chunks = np.array_split(paths, self.preset_pc["cores"])
            results = Parallel(
                n_jobs=self.preset_pc["cores"],
                backend="threading",
                batch_size=1,
                verbose=10,
            )(
                delayed(dss_rebuild_silver.rebuild_silver)(self, chunk)
                for chunk in chunks
                if chunk.size > 0
            )
            dfs = []
            for r in results:
                dfs.append(pd.DataFrame(r).astype(str))
            results_df = pd.concat(dfs, ignore_index=True)
        else:
            results = dss_rebuild_silver.rebuild_silver(self, paths)
            results_df =pd.DataFrame(results).astype(str)
            
        # Return ResultsTable
        results_df.columns = ["category", "module", "step", "results", "errors"]
        rt = ResultTable()
        n = 1
        for col in results_df.columns:
            rt.add_column(n, col, "STRING")
            n +=1
        for index, row in results_df.iterrows():
            rt.add_record(row.tolist())
        return rt

# EOF

