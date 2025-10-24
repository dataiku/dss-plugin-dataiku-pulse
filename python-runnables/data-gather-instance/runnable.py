try:
    from sage.base_data import client_handle as dss_objs
except:
    dss_objs = False
from sage.src import dss_funcs

import os
import pandas as pd
from datetime import datetime

from dataiku.runnables import Runnable, ResultTable
from dataiku.customrecipe import get_recipe_config


# Run Macro
class MyRunnable(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config
        self.plugin_config = plugin_config
        self.sage_project_key = plugin_config.get("sage_project_key", None)
        self.sage_project_url = plugin_config.get("sage_project_url", None)
        self.sage_project_api = plugin_config.get("sage_project_api", None)
        self.sage_worker_key  = plugin_config.get("sage_worker_key", None)
        self.ignore_certs     = plugin_config.get("ignore_certs", False)
        self.dt = datetime.utcnow()
        
        # Set environment variable
        self.sage_folder_connection = plugin_config.get("sage_folder_connection", "filesystem_folders")
        os.environ["SAGE_FOLDER_CONNECTION"] = self.sage_folder_connection
        
    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        # Set environment variable
        os.environ["SAGE_WORKER"] = self.sage_worker_key
        
        # Test if modules are found
        if not dss_objs:
            raise Exception("No categories or modules found")
        
        # Collect the modules && Run the modules
        local_client = dss_funcs.build_local_client()
        results = dss_funcs.run_modules(self, dss_objs, local_client)
        
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
