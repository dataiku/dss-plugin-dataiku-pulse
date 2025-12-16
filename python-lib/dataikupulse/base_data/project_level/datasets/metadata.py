import pandas as pd
from dataikupulse.src import dss_funcs

FLAT_COLUMNS = {
    # Dataset identity
    "dataset_projectKey",
    "dataset_name",
    "dataset_smartName",

    # Core classification
    "dataset_type",
    "dataset_managed",
    "dataset_featureGroup",
    "dataset_typeSystemVersion",

    # Versioning / lifecycle
    "dataset_versionTag.versionNumber",
    "dataset_versionTag.lastModifiedOn",
    "dataset_versionTag.lastModifiedBy.login",
    "dataset_creationTag.versionNumber",
    "dataset_creationTag.lastModifiedOn",
    "dataset_creationTag.lastModifiedBy.login",
}

def main(self, project_handle, client_d = {}):
    try:
        if not project_handle.list_datasets():
            return pd.DataFrame()
    except:
        return pd.DataFrame()
    prefix = "dataset_"
    df = pd.json_normalize(project_handle.list_datasets()).add_prefix(prefix)
    df = dss_funcs.rename_and_move_first(project_handle, df, f"{prefix}projectKey", "project_key")
    df = dss_funcs.normalize_dataframe(self, df, FLAT_COLUMNS)
    return df