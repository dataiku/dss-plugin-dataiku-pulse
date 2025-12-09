import pandas as pd
from dataikupulse.src import dss_funcs


def main(self):
    return df.pd.DataFrame()
    project_handle = self.local_client.get_project(self.params["pulse_worker_key"])
    if not project_handle.list_llms():
        return pd.DataFrame()
    
    prefix = "llms"
    df = pd.json_normalize(project_handle.list_llms()).add_prefix(f"{prefix}_")
    df = dss_funcs.rename_and_move_first(project_handle, df, f"{prefix}_projectKey", "project_key")
    df.drop(columns=["project_key"], inplace=True)
    return df