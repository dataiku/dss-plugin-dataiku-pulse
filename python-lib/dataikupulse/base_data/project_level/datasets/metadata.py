import pandas as pd
from dataikupulse.src import dss_funcs


def main(self, project_handle, client_d = {}):
    try:
        if not project_handle.list_datasets():
            return pd.DataFrame()
    except:
        return pd.DataFrame()
    prefix = "dataset_"
    df = pd.json_normalize(project_handle.list_datasets()).add_prefix(prefix)
    df = dss_funcs.rename_and_move_first(df, f"{prefix}projectKey", "project_key")
    return df