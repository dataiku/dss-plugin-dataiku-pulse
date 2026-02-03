import concurrent.futures
from datetime import datetime, timezone
import logging
import os

import numpy as np
import pandas as pd
import dataiku

from dataiku.runnables import ResultTable, Runnable

from pulse_modules.helpers import dss_funcs



def build_rt(results):
    df = pd.DataFrame(results, columns=[
        "instance_level", "path", "module_name", "step", "result", "message"
    ])
    del df["instance_level"]
    df = df.astype(str)
    rt = ResultTable()
    n = 1
    for col in df.columns:
        rt.add_column(col, col, "STRING")
        n += 1
    for _, row in df.iterrows():
        rt.add_record(row.tolist())
    return rt


class MyRunnable(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config
        self.plugin_config = plugin_config
        self.params = plugin_config.get("pulse_primary", {})
        self.preset_pc = dss_funcs.get_preset_pc(self, "DATAIKU-PULSE")
        self.local_client = dss_funcs.build_local_client()
        self.remote_client = dss_funcs.build_remote_client(self)
        self.instance_name = dss_funcs.get_dss_name(self)
        self.dt = datetime.now(timezone.utc)
        
        logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.ERROR)
        self.logger = logging.getLogger(__name__)
        
    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        # Collect the modules && Run the modules
        results = dss_funcs.run_modules(self)
    
        # return results
        if results:
            df = pd.DataFrame(results, columns=["instance_level", "path", "module_name", "step", "result", "message"])
            df = df.astype(str)
            rt = ResultTable()
            n = 1
            for col in df.columns:
                rt.add_column(n, col, "STRING")
                n +=1
            for _, row in df.iterrows():
                rt.add_record(row.tolist())
            return rt
        raise Exception("Something went wrong")
# EOF