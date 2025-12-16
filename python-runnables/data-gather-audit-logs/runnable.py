from dataiku.runnables import Runnable, ResultTable
from dataikupulse.base_data.audit_log import user_login, event_mapping
from dataikupulse.src import dss_funcs
from datetime import timedelta, datetime
from pathlib import Path
import dataiku
import os
import pandas as pd
import time
import logging

def find_recent_files(file_list, hours=100):
    recent_files = []
    cutoff = time.time() - (hours * 3600)  # seconds
    for file in file_list:
        path = Path(file)
        if path.exists():
            last_modified = path.stat().st_mtime
            if last_modified >= cutoff:
                recent_files.append(path)
    return recent_files


def run_module(self, module, df):
    results = []
    r = module.main(self, df)
    if all(isinstance(x, list) for x in results):
        for l in r:
            results.append(l)
    else:
        results.append(r)
    return results


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
        results = []
        # Get local client and name
        instance_name = dss_funcs.get_dss_name(self)
        
        # change directory and get audit logs
        root_path = self.local_client.get_instance_info().raw["dataDirPath"]
        audit_path = f"{root_path}/run/audit"
        os.chdir(audit_path)
        directory_path = "./"
        logs = [f for f in os.listdir(directory_path) if os.path.isfile(os.path.join(directory_path, f))]
        
        # get the cache timestamp and latest logs
        project_handle = self.local_client.get_default_project()
        variables = project_handle.get_variables()
        try:
            last_update = variables["local"]["audit_logs_cachea"]
        except:
            last_update = str(datetime.now().astimezone() - timedelta(days=1))
        last_update = pd.to_datetime(last_update)
        time_diff = datetime.now().astimezone() - last_update
        hours = round((time_diff.total_seconds() / 3600) + 1, 0)
        logs = find_recent_files(logs, hours=hours)
        results.append(["Parse Latest Logs", True, None])

        # Open each remaining log for parsing
        dfs = []
        for log in logs:
            df = pd.read_json(log, lines=True)
            dfs.append(df)
        df = pd.concat(dfs, ignore_index=True)
        df = df[df["timestamp"] >= last_update]
        results.append(["Gather Audit Logs", True, None])
        
        # Expand Messages and join
        jdf = pd.json_normalize(df["message"]).add_prefix("message_").reset_index(drop=True)
        df = df.drop(columns=["message", "mdc"]).reset_index(drop=True)
        df = pd.concat([df, jdf], axis=1)
        
        # Column Cleanse
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["date"] = df["timestamp"].dt.date
        df["instance_name"] = instance_name
        if "message_projectKey" in df.columns:
            df = df.rename(columns={"message_projectKey": "message_project_key"})
        if "message_login" in df.columns:
            df = df.rename(columns={"message_login": "message_logged_in"})
        if "message_authUser" in df.columns:
            df = df.rename(columns={"message_authUser": "message_login"})
            
        # Module Import
        results += run_module(self, user_login, df)
        results += run_module(self, event_mapping, df)
        
        # Reset the audit_log_cache df
        variables["local"]["audit_logs_cache"] = str(df["timestamp"].max())
        project_handle.set_variables(variables)
        results.append(["Set New Audit Log Cache timestamp", True, str(df["timestamp"].max())])
        
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
