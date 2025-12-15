import pandas as pd

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
    for c in ["dssStartupTimestamp"]:
        df[c] = pd.to_datetime(df[c], unit="ms", utc=True)
        df[c] = pd.to_datetime(df[c], utc=True)
        df[c] = df[c].fillna(pd.to_datetime("1970-01-01", utc=True))
        df[c] = df[c].dt.strftime("%Y-%m-%d %H:%M:%S.%f")

    return df