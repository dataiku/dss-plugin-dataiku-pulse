import dataiku
import pandas as pd
import numpy as np
from joblib import Parallel, delayed
from datetime import date

FLAT_COLUMNS = {
    "project_key",
    "commit",
    "author",
    "timestamp",
    "message",
}
today = date.today()

def split_work(client, project_keys):
    dfs = []
    for project_key in project_keys:
        project_handle = client.get_project(project_key=project_key)
        git_log = project_handle.get_project_git().log()
        df = pd.DataFrame(git_log["entries"])
        if df.empty:
            continue
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df[(df["timestamp"].dt.date >= today)]
        df["project_key"] = project_key
        dfs.append(df)
    return pd.concat(dfs, ignore_index=True)


def main(self):
    project_keys = self.local_client.list_project_keys()
    pkey_array = np.array_split(project_keys, 2)
    results = Parallel(n_jobs=2, prefer="threads")(
        delayed(split_work)(client=self.local_client, project_keys=i) for i in pkey_array
    )
    df = pd.concat(results, ignore_index=True)
    return df