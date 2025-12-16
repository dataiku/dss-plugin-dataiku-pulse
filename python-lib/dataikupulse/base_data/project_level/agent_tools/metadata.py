import pandas as pd
from dataikupulse.src import dss_funcs

FLAT_COLUMNS = {
    # Agent Tools identity
    "project_key",
    "agent_tools_id",
    "agent_tools_type",
    "agent_tools_name",
}

def main(self, project_handle, client_d = {}):
    try:
        if not project_handle.list_agent_tools():
            return pd.DataFrame()
    except:
        return pd.DataFrame()
    prefix = "agent_tools"
    df = pd.json_normalize(project_handle.list_agent_tools()).add_prefix(f"{prefix}_")
    df = dss_funcs.rename_and_move_first(df, f"{prefix}_projectKey", "project_key")
    df = dss_funcs.normalize_dataframe(self, df, FLAT_COLUMNS)
    return df