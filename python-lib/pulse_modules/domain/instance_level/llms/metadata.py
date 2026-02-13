import pandas as pd
from pulse_modules.helpers import dss_funcs


def main(self):
    try:
        project_handle = self.local_client.get_project(self.params["pulse_worker_key"])
        if not project_handle.list_llms():
            return pd.DataFrame()
    except:
        return pd.DataFrame()
    prefix = "llms"
    df = pd.json_normalize(project_handle.list_llms()).add_prefix(f"{prefix}_")
    return df