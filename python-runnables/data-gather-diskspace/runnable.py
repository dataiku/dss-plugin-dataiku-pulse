from sage.src import dss_funcs, dss_folder

import os
import subprocess
import pandas as pd
from datetime import datetime, date, timedelta
import logging

from dataiku.runnables import Runnable, ResultTable

def get_size(d):
    try:
        result = subprocess.run(f"du -sc {d}", shell=True, capture_output=True, text=True, check=True)
        size = result.stdout.split("\t")[0]
        size = int(size)
    except:
        size = 0
    return size


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
        
        # change directory and get audit logs
        root_path = local_client.get_instance_info().raw["dataDirPath"]
        os.chdir(root_path)
        
        # Find directories maxdepth
        results = []
        cmd = "find . -maxdepth 3 -type d"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
        directories = result.stdout.split("\n")
        directories.remove(".")
        
        # turn into a df
        df = pd.DataFrame(directories, columns=["directory"])
        
        # remove jupyter-run / .git -- permission issues with sudo stuff
        df = df[~df["directory"].str.contains("jupyter-run")]
        df = df[~df["directory"].str.contains(".git")]

        # Explode directory
        cols = ["dot", "level_1", "level_2", "level_3"]
        df[cols] = df["directory"].str.split("/",  expand=True)

        # remove columns
        del df["dot"]
        del df["directory"]
        
        # Get details on sizes - level_1
        df["level_1_size"] = 0
        for i,g in df.groupby(by=["level_1"]):
            logging.error(f"value i: {i}")
            size = get_size(i)
            df.loc[df["level_1"] == i, "level_1_size"] = size
                        
        # Filter size on a base number (1gb / adjustable)
        gb = 1000000 * 1
        df = df[df["level_1_size"] >= gb]

        # Get details on sizes - level_2
        df["level_2_size"] = 0
        for i,g in df.groupby(by=["level_1", "level_2"]):
            d = "/".join(i)
            size = get_size(d)
            df.loc[
                (df["level_1"] == i[0])
                & (df["level_2"] == i[1]), "level_2_size"] = size

        # Get details on sizes - level_3
        df["level_3_size"] = 0
        for i,g in df.groupby(by=["level_1", "level_2", "level_3"]):
            d = "/".join(i)
            size = get_size(d)
            df.loc[
                (df["level_1"] == i[0])
                & (df["level_2"] == i[1])
                & (df["level_3"] == i[2]), "level_3_size"] = size
            
        results.append(["read/parse", True, None])
        
        # loop topics and save data
        remote_client = dss_funcs.build_remote_client(self.sage_project_url, self.sage_project_api, self.ignore_certs)
        dt_year  = str(self.dt.year)
        dt_month = str(f'{self.dt.month:02d}')
        dt_day   = str(f'{self.dt.day:02d}')
        df["instance_name"] = instance_name
        try:
            write_path = f"/{instance_name}/operating_system/diskspace/{dt_year}/{dt_month}/{dt_day}/data.parquet"
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
