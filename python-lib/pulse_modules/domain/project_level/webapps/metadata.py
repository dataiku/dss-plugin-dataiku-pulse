import pandas as pd
from pulse_modules.helpers import dss_funcs


def main(self, project_handle, client_d = {}):
    try:
        if not project_handle.list_dashboards():
            return pd.DataFrame()
    except:
        return pd.DataFrame()
    prefix = "dashboards"
    df = pd.json_normalize(project_handle.list_dashboards()).add_prefix(f"{prefix}_")
    df = dss_funcs.rename_and_move_first(df, f"{prefix}_projectKey", "project_key")
    return df