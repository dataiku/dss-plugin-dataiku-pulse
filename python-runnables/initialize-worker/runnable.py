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
        self.params = plugin_config.get("pulse_primary", {})
        self.preset_pc = {
            "pulse_dataiku_user": "admin",
            "ignore_certs": False,
            "do_parallel": False,
            "cores": 2,
            "macro_configs": [],
        }
        
    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        cont = True
        results = []

        # Connect to the plugin
        local_client = dss_funcs.build_local_client()
        plugin_handle = local_client.get_plugin(plugin_id="dataiku-pulse")
        plugin_settings = plugin_handle.get_settings()
        for worker_host in self.params["worker_hosts"]:
            worker_url = worker_host.get("worker_url", None)
            worker_api = worker_host.get("worker_api", None)
            preset_name  = worker_host.get("preset_name", None)
            
            # Get the respective param_set if available
            if preset_name:
                param_set = plugin_settings.get_parameter_set(parameter_set_name="params-worker-instances")
                preset = param_set.get_preset(preset_name=preset_name)
                try:
                    self.preset_pc = preset.plugin_config
                except:
                    pass
            
            # Create a remote client
            try:
                remote_client = dss_funcs.build_remote_client(worker_url, worker_api, self.preset_pc["ignore_certs"])
            except Exception as e:
                results.append([worker_url, "Failed to connect to host", False, e])
                cont = False
            
            # Install/Update Plugin if not found
            if cont:
                if self.params["pulse_project_url"] != worker_url:
                    try:
                        dss_init.install_plugin(self, remote_client)
                        results.append([worker_url, "Plugin Configured", True, None])
                    except Exception as e:
                        results.append([worker_url, "Plugin Configured", False, e])
                        cont = False
            
            # Create the Worker Project
            if cont:
                try:
                    project_handle = dss_init.create_worker(remote_client, self.preset_pc["pulse_worker_key"])
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
                        
                        
                        
                        
                        