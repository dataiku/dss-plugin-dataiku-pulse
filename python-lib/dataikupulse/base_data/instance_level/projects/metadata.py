import pandas as pd
import numpy as np
from joblib import Parallel, delayed
from dataikupulse.src import dss_funcs


def main(self):
    # Get projects and expand
    df = pd.DataFrame(self.local_client.list_projects()).add_prefix("project_")
    jdf = pd.json_normalize(df["project_versionTag"]).add_prefix("project_versionTag_")
    df = pd.concat([df, jdf], axis=1)
    jdf = pd.json_normalize(df["project_creationTag"]).add_prefix("project_creationTag_")
    df = pd.concat([df, jdf], axis=1)
    
    # Imported projects missing creation values - temp fix for now
    df.loc[df["project_creationTag_versionNumber"].isna(), "project_creationTag_versionNumber"] = 0
    df.loc[df["project_creationTag_lastModifiedOn"].isna(), "project_creationTag_lastModifiedOn"] = df["project_versionTag_lastModifiedOn"]
    df.loc[df["project_creationTag_lastModifiedBy.login"].isna(), "project_creationTag_lastModifiedBy.login"] = df["project_versionTag_lastModifiedBy.login"]

    # Clean dates
    for c in ["project_versionTag_lastModifiedOn", "project_creationTag_lastModifiedOn"]:
        df[c] = pd.to_datetime(df[c], unit="ms", utc=True)
        df[c] = df[c].fillna(pd.to_datetime("1970-01-01", utc=True))
        
    # Rename a few colums
    df = df.rename(columns={"project_ownerLogin": "login"})
    # Project Key
    df = dss_funcs.rename_and_move_first(None, df, "project_projectKey", "project_key")
    return df