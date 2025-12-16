import pandas as pd
from dataikupulse.src import dss_funcs


def main(self, project_handle, client_d = {}):
    try:
        if not project_handle.list_agents():
            return pd.DataFrame()
    except:
        return pd.DataFrame()
    prefix = "agents"
    df = pd.json_normalize(project_handle.list_agents()).add_prefix(f"{prefix}_")
    df = df.explode(f"{prefix}_versions").reset_index(drop=True)
    df = pd.concat([
        df.drop(columns=[f"{prefix}_versions"]),
        pd.json_normalize(df[f"{prefix}_versions"]).add_prefix(f"{prefix}_versions_")
    ], axis=1)
    df.columns = df.columns.str.replace('.', '_', regex=False)
    df = dss_funcs.rename_and_move_first(project_handle, df, f"{prefix}_projectKey", "project_key")
    return df