import pandas as pd
from dataikupulse.src import dss_funcs

FLAT_COLUMNS = {
    "nodeId",
    "nodeName",
    "nodeType",
    "rawNodeType",
    "hostname",
    "installId",
    "dipInstanceId",
    "licenseInstanceId",
    "licenseId",
    "dssVersion",
    "os",
    "osVersion",
    "javaVendor",
    "javaVersion",
    "dssStartupTimestamp",
}

def main(self):
    df = pd.json_normalize(self.local_client.get_instance_info().raw)
    df["dssStartupTimestamp"] = pd.to_datetime(df["dssStartupTimestamp"], unit='ms')
    df = dss_funcs.normalize_dataframe("mazzei_pulse", df, FLAT_COLUMNS)
    return df