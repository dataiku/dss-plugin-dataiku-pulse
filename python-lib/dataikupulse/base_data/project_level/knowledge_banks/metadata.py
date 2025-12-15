import pandas as pd
from dataikupulse.src import dss_funcs


def main(self, project_handle, client_d = {}):
    if not project_handle.list_knowledge_banks():
        return pd.DataFrame()
    
    prefix = "knowledge_banks"
    df = pd.json_normalize(project_handle.list_knowledge_banks()).add_prefix(f"{prefix}_")
    df = dss_funcs.rename_and_move_first(project_handle, df, f"{prefix}_projectKey", "project_key")
    return df