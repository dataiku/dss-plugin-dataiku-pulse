import pandas as pd
from dataikupulse.src import dss_funcs

FLAT_COLUMNS = {
    # Identity
    "nodeId",
    "nodeName",
    "nodeType",
    "rawNodeType",
    "hostname",

    # Instance / License
    "installId",
    "dipInstanceId",
    "licenseInstanceId",
    "licenseId",

    # Platform / Versioning
    "dssVersion",
    "os",
    "osVersion",
    "javaVendor",
    "javaVersion",

    # Time (convert upstream if possible)
    "dssStartupTimestamp",
}
RENAME_MAP = {

}

def main(self):
    df = pd.json_normalize(self.local_client.get_instance_info().raw)
    df["dssStartupTimestamp"] = pd.to_datetime(df["dssStartupTimestamp"], unit='ms')
    df = dss_funcs.normalize_dataframe("mazzei_pulse", df, FLAT_COLUMNS, RENAME_MAP)
    return df