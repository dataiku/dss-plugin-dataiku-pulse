from sage.src import dss_funcs, dss_folder

import os
import subprocess
import pandas as pd
from datetime import datetime, date, timedelta

from dataiku.runnables import Runnable, ResultTable

class MyRunnable(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config
        self.plugin_config = plugin_config
        self.sage_project_key = plugin_config.get("sage_project_key", None)
        self.sage_project_url = plugin_config.get("sage_project_url", None)
        self.sage_project_api = plugin_config.get("sage_project_api", None)
        self.ignore_certs     = plugin_config.get("ignore_certs", False)
        self.dt = datetime.utcnow()
        
    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        # Get local client and name
        local_client = dss_funcs.build_local_client()
        instance_name = dss_funcs.get_dss_name(local_client)
        
        # Get the output of the DF command
        results = []
        cmd = "df"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
        result = result.stdout.split("\n")
        result.pop(0)
        data = []
        for line in result:
            line = " ".join(line.split())
            line = line.split(" ")
            data.append(line)
        df = pd.DataFrame(data, columns=["filesystem", "size", "used", "available", "used_pct", "mounted_on"]).dropna()
        df['used_pct'] = df['used_pct'].str.replace(r'[^a-zA-Z0-9\s]', '', regex=True)
        df = df[~df["filesystem"].isin(["devtmpfs", "tmpfs"])]
        results.append(["read/parse", True, None])

        # loop topics and save data
        remote_client = dss_funcs.build_remote_client(self.sage_project_url, self.sage_project_api, self.ignore_certs)
        dt_year  = str(self.dt.year)
        dt_month = str(f'{self.dt.month:02d}')
        dt_day   = str(f'{self.dt.day:02d}')
        df["instance_name"] = instance_name
        try:
            write_path = f"/{instance_name}/operating_system/filesystem/{dt_year}/{dt_month}/{dt_day}/data.parquet"
            dss_folder.write_remote_folder_output(self, remote_client, write_path, df)
            results.append(["write/save", True, None])
        except Exception as e:
            results.append(["write/save", False, e])
        
        # return results
        if results:
            df = pd.DataFrame(results, columns=["step", "result", "message"])
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
