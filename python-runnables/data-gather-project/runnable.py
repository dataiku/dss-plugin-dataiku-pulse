from dataikupulse.src import dss_funcs

import dataiku
import pandas as pd
import numpy as np
import os
from joblib import Parallel, delayed
from datetime import datetime

from dataiku.runnables import Runnable, ResultTable


def data_gather(project_keys):
    results = [[1,1,1,1,1,1]]
    #for key in project_keys:
    #    project_handle = self.local_client.get_project(project_key=key)
    #    results += dss_funcs.run_modules(self, "projects", project_handle, self.client_d, key)
    #    break
    cols = ["project_key", "path", "module_name", "step", "result", "message"]
    if results:
        df = pd.DataFrame(results, columns=cols)
    else:
        df = pd.DataFrame(columns=cols)
    return df


class MyRunnable(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config
        self.plugin_config = plugin_config
        self.pulse_project_key = plugin_config.get("pulse_project_key", None)
        self.pulse_project_url = plugin_config.get("pulse_project_url", None)
        self.pulse_project_api = plugin_config.get("pulse_project_api", None)
        self.ignore_certs      = plugin_config.get("ignore_certs", False)
        self.do_parallel       = plugin_config.get("do_parallel", False)
        self.cores             = plugin_config.get("cores", 2)
        self.dt                = datetime.utcnow()
        self.pulse_folder_connection = plugin_config.get("pulse_folder_connection", "filesystem_folders")


    def get_progress_target(self):
        return None
    
    
    def run(self, progress_callback):
        # Set environment variable
        os.environ["pulse_FOLDER_CONNECTION"] = self.pulse_folder_connection
        
        # Grab some exra details
        local_client = dss_funcs.build_local_client()
        client_d = {}
        try:
            client_d["python_env_name"] = local_client.get_general_settings().settings["codeEnvs"]["defaultPythonEnv"]
            if not client_d["python_env_name"]:
                client_d["python_env_name"] = "USE_BUILTIN_MODE"
        except:
            client_d["python_env_name"] = "USE_BUILTIN_MODE"
        try:
            client_d["r_env_name"] = local_client.get_general_settings().settings["codeEnvs"]["defaultREnv"]
            if not client_d["r_env_name"]:
                client_d["r_env_name"] = "USE_BUILTIN_MODE"
        except:
            client_d["r_env_name"] = "USE_BUILTIN_MODE"
        try:
            client["container_env_name"] = local_client.get_general_settings().settings["containerSettings"]["defaultExecutionConfig"]
            if not client_d["container_env_name"]:
                client_d["container_env_name"] = "DSS_LOCAL"
        except:
            client_d["container_env_name"] = "DSS_LOCAL"
        self.client_d = client_d
        
        # Collect the modules && Run the modules
        project_keys = local_client.list_project_keys()
        if self.do_parallel:
            pk_arrays = np.array_split(project_keys, self.cores)
            dfs = Parallel(n_jobs=self.cores)(delayed(self.data_gather)(project_keys)
                                              for project_keys in pk_arrays)
            df = pd.concat(dfs, ignore_index=True)
        else:
            df = self.data_gather(project_keys)            
            
        # return results
        if not df.empty:
            df = df.astype(str)
            rt = ResultTable()
            n = 1
            for col in df.columns:
                rt.add_column(n, col, "STRING")
                n +=1
            for index, row in df.iterrows():
                rt.add_record(row.tolist())
            return rt
        raise Exception("Something went wrong")

        

# EOF