import pandas as pd
from dataikupulse.src import dss_funcs

FLAT_COLUMNS = {
    # Scenario identity
    "scenarios_projectKey",
    "scenarios_id",
    "scenarios_name",

    # Classification / state
    "scenarios_type",
    "scenarios_active",
    "scenarios_unavailable",
    "scenarios_running",
    "scenarios_markedAsTest",

    # Lifecycle / scheduling
    "scenarios_createdOn",
    "scenarios_lastModifiedOn",
    "scenarios_start",
    "scenarios_nextRun",

    # Execution context
    "scenarios_runAsUser",
}

def main(self, project_handle, client_d = {}):
    try:
        if not project_handle.list_scenarios():
            return pd.DataFrame()
    except:
        return pd.DataFrame()
    prefix = "scenarios_"
    df = pd.json_normalize(project_handle.list_scenarios()).add_prefix(prefix)
    df = dss_funcs.rename_and_move_first(project_handle, df, f"{prefix}projectKey", "project_key")
    df = dss_funcs.normalize_dataframe(self, df, FLAT_COLUMNS)
    return df