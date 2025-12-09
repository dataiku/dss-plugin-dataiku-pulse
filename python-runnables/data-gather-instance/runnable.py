from dataiku.runnables import Runnable, ResultTable
from dataikupulse.src import dss_funcs
from datetime import datetime
import dataiku
import pandas as pd
import numpy as np
import os
import time
import logging


class MyRunnable(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config
        self.plugin_config = plugin_config
        self.params = plugin_config.get("pulse_primary", {})
        self.preset_pc = dss_funcs.get_preset_pc(self, "DATAIKU-PULSE")
        self.local_client = dss_funcs.build_local_client()
        self.remote_client = dss_funcs.build_remote_client(self)
        self.dt = datetime.utcnow()
        
        logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.ERROR)
        self.logger = logging.getLogger(__name__)
        
    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        # Collect the modules && Run the modules
        results = dss_funcs.run_modules(self, "client", self.local_client)
        
        # return results
        time.sleep(2)
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
