from dataikupulse.src import dss_funcs

import dataiku
import pandas as pd
import numpy as np
import os
from joblib import Parallel, delayed
from datetime import datetime

from dataiku.runnables import Runnable, ResultTable


class MyRunnable(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config
        self.plugin_config = plugin_config
        self.pulse_project_key = plugin_config.get("pulse_project_key", None)
        self.pulse_project_url = plugin_config.get("pulse_project_url", None)
        self.pulse_project_api = plugin_config.get("pulse_project_api", None)
        self.pulse_worker_key  = plugin_config.get("pulse_worker_key", None)
        self.ignore_certs     = plugin_config.get("ignore_certs", False)
        self.dt = datetime.utcnow()
        
        # Set environment variable
        self.pulse_folder_connection = plugin_config.get("pulse_folder_connection", "filesystem_folders")
        os.environ["pulse_FOLDER_CONNECTION"] = self.pulse_folder_connection
        
    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        
        
        rt = ResultTable()
        rt.add_column(1, "Column1", "STRING")
        rt.add_record(["hi"])
        return "hi"
    
    
    
    
    
    
        # Collect the modules && Run the modules
        local_client = dss_funcs.build_local_client()
        results = dss_funcs.run_modules(self, "client", local_client)
        
        # return results
        if results:
            df = pd.DataFrame(results, columns=["instance_level", "path", "module_name", "step", "result", "message"])
            del df["instance_level"]
            df = df.astype(str)
            rt = ResultTable()
            n = 1
            for col in df.columns:
                rt.add_column(n, col, "STRING")
                n +=1
            for index, row in df.iterrows():
                rt.add_record(row.tolist())
            return rt
        else:
            raise Exception("Something went wrong")
