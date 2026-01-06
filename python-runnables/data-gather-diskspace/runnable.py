from dataiku.runnables import Runnable, ResultTable
from dataikupulse.src import dss_funcs, dss_folder, dss_silver
import os
import subprocess
import pandas as pd
from datetime import datetime, date, timedelta
import logging


def get_size(d):
    result = subprocess.run(f"""du -sc "{d}" """, shell=True, capture_output=True, text=True, check=True)
    size = result.stdout.split("\t")[0]
    size = int(size)
    return size

        
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
        
        # change directory and get audit logs
        root_path = self.local_client.get_instance_info().raw["dataDirPath"]
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
        for i,g in df.groupby(by="level_1"):
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
        
        # Datetime stuff
        dt_year  = str(self.dt.year)
        dt_month = str(f'{self.dt.month:02d}')
        dt_day   = str(f'{self.dt.day:02d}')
        df["instance_name"] = instance_name
        df["timestamp"] = self.dt
        
        # RAW self, df, category, module_name, project_key, file_name, result
        try:
            long_results = dss_funcs._persist_raw(
                self=self,
                df=df,
                category="operating_system",
                module_name="diskspace",
                project_key=None,
                file_name=f"data.parquet", 
                result=[]
            )
            results.append(["User Login Classification", "write/save - RAW", True, None])
        except Exception as e:
            results.append(["User Login Classification", "write/save - RAW", False, e])
        # SILVER
        try:
            long_results = dss_funcs._process_quality_and_persist(self, df, "users", "user_login_activity", None, "SKIP", f"data-{dt_epoch}.parquet", [])
            results.append(["User Login Classification", "write/save - SILVER", True, None])
        except Exception as e:
            results.append(["User Login Classification", "write/save - SILVER", False, e])
        
        
        
        
        try:
            write_path = f"raw/operating_system/diskspace/{instance_name}/{dt_year}/{dt_month}/{dt_day}/data.parquet"
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
                write_path = f"{layer}/operating_system/diskspace/{self.instance_name}/{dt_year}/{dt_month}/{dt_day}/data.parquet"
                dss_folder.write_remote_folder_output(self, write_path, df)
                write_path = f"{layer}/operating_system/diskspace/{self.instance_name}/{dt_year}/{dt_month}/{dt_day}/dq_data.parquet"
                dss_folder.write_remote_folder_output(self, write_path, pd.DataFrame(dq))
            else:
                write_path = f"{layer}/operating_system/diskspace/{self.instance_name}/{dt_year}/{dt_month}/{dt_day}/data.parquet"
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
