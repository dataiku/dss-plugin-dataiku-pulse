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
        
        logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.ERROR)
        self.logger = logging.getLogger(__name__)
        
    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        raise Exception(self.config)
        
        
        cont = True
        results = []
        for worker_host in self.params["worker_hosts"]:
            worker_url = worker_host.get("worker_url", None)
            worker_api = worker_host.get("worker_api", None)
            preset_name = worker_host.get("preset_name", None)
            self.preset_pc = dss_funcs.get_preset_pc(self, preset_name)
            self.preset_pc_name = "DATAIKU-PULSE"
            
            # Create a remote client
            try:
                remote_client = dss_funcs.build_remote_client(self, worker_url, worker_api)
            except Exception as e:
                results.append([worker_url, f"Failed to connect to host: {worker_url}  {worker_api}", False, e])
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
            
            # Create/Update the default call home config
            if cont:
                    try:
                        plugin_handle = remote_client.get_plugin(plugin_id="dataiku-pulse") 
                        dss_init.update_default_node(self, plugin_handle)
                        results.append([worker_url, "Default Preset Built", True, None])
                    except Exception as e:
                        results.append([worker_url, "Default Preset Built", False, e])
                        cont = False

            
            # Create the Worker Project
            if cont:
                try:
                    project_handle = dss_init.create_worker(remote_client, self.params["pulse_worker_key"])
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
                        
                        
                        
                        
                        