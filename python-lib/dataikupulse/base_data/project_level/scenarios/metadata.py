import pandas as pd
from dataikupulse.src import dss_funcs


def main(self, project_handle, client_d = {}):
    try:
        if not project_handle.list_scenarios():
            return pd.DataFrame()
    except:
        return pd.DataFrame()
    prefix = "scenarios_"
    df = pd.json_normalize(project_handle.list_scenarios()).add_prefix(prefix)
    df = dss_funcs.rename_and_move_first(project_handle, df, f"{prefix}projectKey", "project_key")
    return df