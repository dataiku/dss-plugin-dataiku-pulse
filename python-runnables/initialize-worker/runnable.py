from dataikupulse.src import dss_funcs
from dataikupulse.src import dss_init

import os
import pandas as pd

from dataiku.runnables import Runnable, ResultTable

class MyRunnable(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config
        self.plugin_config = plugin_config
        self.params = plugin_config["pulse_primary"]

        
    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        results = []
        for api_config in self.params["worker_hosts"]:
            # Create a remote client
            worker_url = api_config["worker_url"]
            worker_api = api_config["worker_api"]
            remote_client = dss_funcs.build_remote_client(worker_url, worker_api, self.ignore_certs)
            
            # Install/Update Plugin if not found
            cont = True
            if self.pulse_project_url != worker_url:
                try:
                    dss_init.install_plugin(self, remote_client)
                    results.append([worker_url, "Plugin Configured", True, None])
                except Exception as e:
                    results.append([worker_url, "Plugin Configured", False, e])
                    cont = False

            # Create the Worker Project
            if cont:
                try:
                    project_handle = dss_init.create_worker(remote_client, self.pulse_worker_key)
                    results.append([worker_url, "Worker Created", True, None])
                except Exception as e:
                    results.append([worker_url, "Worker Created", False, e])
                    cont = False

            # Create the DSS Commit Table
            if cont:
                try:
                    dss_init.get_dss_commits(project_handle)
                    results.append([worker_url, "Load DSS Commits Table", True, None])
                except Exception as e:
                    cont = False
                    results.append([worker_url, "Load DSS Commits Table", False, e])
            
            # Create the Phone Home Scenarios
            if cont:
                try:
                    dss_init.create_scenarios(self, project_handle)
                    results.append([worker_url, "Create/Update Scenarios", True, None])
                except Exception as e:
                    cont = False
                    results.append([worker_url, "Create/Update Scenarios", False, e])
        
        # return results
        if results:
            df = pd.DataFrame(results, columns=["worker_url", "step", "results", "message"])
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
