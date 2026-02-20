import pandas as pd
from pulse_modules.helpers import dss_funcs


def main(self):
    data =  client.get_licensing_status()
    df = pd.DataFrame(data["limits"]["licensedProfiles"])
    df = df.T[["licensedLimit"]].reset_index(drop=False, names="profile")
    return df