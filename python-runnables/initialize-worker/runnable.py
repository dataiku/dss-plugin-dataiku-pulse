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
        # Connect to the plugin
        local_client = dss_funcs.build_local_client()
        plugin_handle = local_client.get_plugin(plugin_id="dataiku-pulse")
        plugin_settings = plugin_handle.get_settings()
            
        results = []
        for worker_host in self.params["worker_hosts"]:
            worker_url = worker_host["worker_url"]
            worker_api = worker_host["worker_api"]
            preset_name  = worker_host["preset_name"]
            
            # Get the respective param_set if available
            param_set = settings.get_parameter_set(parameter_set_name="params-worker-instances")
            preset = param_set.get_preset(preset_name=preset_name)
            preset.plugin_config["macro_configs"]
            
            
            # Create a remote client
            remote_client = dss_funcs.build_remote_client(worker_url, worker_api, self.params["worker_hosts"]ignore_certs)
            
