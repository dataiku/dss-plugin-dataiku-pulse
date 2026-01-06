from dataikupulse.src import dss_funcs, dss_folder, dss_silver

import os
import subprocess
import pandas as pd
from datetime import datetime, date, timedelta
import logging

from dataiku.runnables import Runnable, ResultTable

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
        self.dt = datetime.utcnow()
        
        logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.ERROR)
        self.logger = logging.getLogger(__name__)
        
    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        # Get local client and name
        instance_name = dss_funcs.get_dss_name(self)
        
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

        # datetime stuff
        remote_client = dss_funcs.build_remote_client(self)
        dt_year  = str(self.dt.year)
        dt_month = str(f'{self.dt.month:02d}')
        dt_day   = str(f'{self.dt.day:02d}')
        df["instance_name"] = instance_name
        df["timestamp"] = self.dt
        
        # RAW
        try:
            long_results = dss_funcs._persist_raw(
                self=self,
                df=df,
                category="operating_system",
                module_name="diskspace",
                project_key=None,
                file_name=f"data.parquet", 
                results=[]
            )
            results.append(["write/save - RAW", True, None])
        except Exception as e:
            results.append(["write/save - RAW", False, e])
            
        # SILVER
        try:
            long_results = dss_funcs._process_quality_and_persist(
                self=self,
                df=df,
                category="operating_system",
                module_name="diskspace",
                project_key=None,
                mode="client",
                file_name=f"data.parquet",
                results=[]
            )
            results.append(["write/save - SILVER", True, None])
        except Exception as e:
            results.append(["write/save - SILVER", False, e])
        
        
        
        
        
        try:
            write_path = f"raw/operating_system/filesystem/{instance_name}/{dt_year}/{dt_month}/{dt_day}/data.parquet"
            dss_folder.write_remote_folder_output(self, write_path, df)
            results.append(["write/save", True, None])
        except Exception as e:
            results.append(["write/save", False, e])

        # Test quality control -- Save to Silver or Raw Error
        try:
            layer = "silver"
            df = dss_silver.coerce_schema(df)
            dq = dss_silver.data_quality(df)
            if dq["errors"]:
                layer = "raw_errors"
                write_path = f"{layer}/operating_system/filesystem/{instance_name}/{dt_year}/{dt_month}/{dt_day}/data.parquet"
                dss_folder.write_remote_folder_output(self, write_path, df)
                write_path = f"{layer}//operating_system/filesystem/{instance_name}/{dt_year}/{dt_month}/{dt_day}/dq_data.parquet"
                dss_folder.write_remote_folder_output(self, write_path, pd.DataFrame(dq))
            else:
                write_path = f"{layer}/operating_system/filesystem/{instance_name}/{dt_year}/{dt_month}/{dt_day}/data.parquet"
                dss_folder.write_remote_folder_output(self, write_path, df)
            results.append([f"write/save -- {layer}", True, None])
        except Exception as e:
            layer = "raw_errors"
            results.append([f"write/save -- QUALITY", False, e])

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
