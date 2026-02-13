import pandas as pd
from pulse_modules.helpers import dss_funcs


def main(self):
    df = pd.json_normalize(self.local_client.get_instance_info().raw)
    return df