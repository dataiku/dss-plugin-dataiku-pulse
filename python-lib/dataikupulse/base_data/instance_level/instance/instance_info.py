import pandas as pd
from dataikupulse.src import dss_funcs


def main(self):
    df = pd.json_normalize(self.local_client.get_instance_info().raw)
    df = dss_funcs.normalize_dataframe(self, df, FLAT_COLUMNS)
    return df