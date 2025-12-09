import dataiku
import pandas as pd
import numpy as np
from joblib import Parallel, delayed
from datetime import datetime, date, timedelta

today = date.today()

def main(self):
    project_keys = self.local_client.list_project_keys()
    dfs = []
    for project_key in project_keys:
        project_handle = self.local_client.get_project(project_key=project_key)
        git_log = project_handle.get_project_git().log()
        df = pd.DataFrame(git_log["entries"])
        if df.empty:
            continue
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df[(df["timestamp"].dt.date >= today)]
        df["project_key"] = project_key
        dfs.append(df)
    df = pd.concat(dfs, ignore_index=True)
    return df