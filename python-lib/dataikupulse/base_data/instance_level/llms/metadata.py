import pandas as pd
from dataikupulse.src import dss_funcs


def main(self):
    try:
        project_handle = self.local_client.get_project(self.params["pulse_worker_key"])
        if not project_handle.list_llms():
            return pd.DataFrame()
    except:
        return pd.DataFrame()
    prefix = "llms"
    df = pd.json_normalize(project_handle.list_llms()).add_prefix(f"{prefix}_")
    df = dss_funcs.normalize_dataframe(self, df, FLAT_COLUMNS)
    return df