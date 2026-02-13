import pandas as pd
from pulse_modules.helpers import dss_funcs


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
    # Rename a few colums
    df = df.rename(columns={"project_ownerLogin": "login"})
    # Project Key
    df = df.rename(columns={"project_projectKey": "project_key"})
    return df