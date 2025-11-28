from dataiku.runnables import Runnable, ResultTable
from dataikupulse.src import dss_funcs
from datetime import datetime
from joblib import Parallel, delayed
import dataiku
import numpy as np
import pandas as pd
import os
import logging



class MyRunnable(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config
        self.plugin_config = plugin_config
        self.params = plugin_config.get("pulse_primary", {})
        self.preset_pc = dss_funcs.get_preset_pc("DATAIKU-PULSE")
        self.local_client = dss_funcs.build_local_client()
        self.remote_client = dss_funcs.build_remote_client(self)
        self.dt = datetime.utcnow()
        
        logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.ERROR)
        self.logger = logging.getLogger(__name__)


    def get_progress_target(self):
        return None

    
    def data_gather(self, project_keys):
        results = []
        for key in project_keys:
            project_handle = self.local_client.get_project(project_key=key)
            results += dss_funcs.run_modules(self, "projects", project_handle, self.client_d, key)
        cols = ["project_key", "path", "module_name", "step", "result", "message"]
        if results:
            df = pd.DataFrame(results, columns=cols)
        else:
            df = pd.DataFrame(columns=cols)
        return df
    
    
    def run(self, progress_callback):
        # Grab some exra details
        client_d = {}
        try:
            client_d["python_env_name"] = self.local_client.get_general_settings().settings["codeEnvs"]["defaultPythonEnv"]
            if not client_d["python_env_name"]:
                client_d["python_env_name"] = "USE_BUILTIN_MODE"
        except:
            client_d["python_env_name"] = "USE_BUILTIN_MODE"
        try:
            client_d["r_env_name"] = self.local_client.get_general_settings().settings["codeEnvs"]["defaultREnv"]
            if not client_d["r_env_name"]:
                client_d["r_env_name"] = "USE_BUILTIN_MODE"
        except:
            client_d["r_env_name"] = "USE_BUILTIN_MODE"
        try:
            client["container_env_name"] = self.local_client.get_general_settings().settings["containerSettings"]["defaultExecutionConfig"]
            if not client_d["container_env_name"]:
                client_d["container_env_name"] = "DSS_LOCAL"
        except:
            client_d["container_env_name"] = "DSS_LOCAL"
        self.client_d = client_d
        
        # Collect the modules && Run the modules
        project_keys = self.local_client.list_project_keys()
        if self.preset_pc["do_parallel"]:
            pk_arrays = np.array_split(project_keys, self.preset_pc["cores"])
            dfs = Parallel(n_jobs=self.cores, backend="threading")(delayed(self.data_gather)(project_keys)
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