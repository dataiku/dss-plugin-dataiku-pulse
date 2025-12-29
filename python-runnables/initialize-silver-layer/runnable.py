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
        self.local_client = dss_funcs.build_local_client()
        
        logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.ERROR)
        self.logger = logging.getLogger(__name__)
        
    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        raise Exception("unimplemented")
        