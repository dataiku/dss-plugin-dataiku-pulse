import pandas as pd
from dataikupulse.src import dss_funcs

FLAT_COLUMNS = {
    # Agent identity
    "agents_id",
    "agents_name",
    "agents_type",
    "agents_projectKey",

    # Versioning / lifecycle
    "agents_activeVersion",
    "agents_versions_versionId",

    # Version metadata (promoted for queryability)
    "agents_versions_versionTag.versionNumber",
    "agents_versions_versionTag.lastModifiedOn",
    "agents_versions_versionTag.lastModifiedBy.login",
    "agents_versions_creationTag.versionNumber",
    "agents_versions_creationTag.lastModifiedOn",
    "agents_versions_creationTag.lastModifiedBy.login",
}

def main(self, project_handle, client_d = {}):
    try:
        if not project_handle.list_agents():
            return pd.DataFrame()
    except:
        return pd.DataFrame()
    prefix = "agents"
    df = pd.json_normalize(project_handle.list_agents()).add_prefix(f"{prefix}_")
    df = df.explode(f"{prefix}_versions").reset_index(drop=True)
    df = pd.concat([
        df.drop(columns=[f"{prefix}_versions"]),
        pd.json_normalize(df[f"{prefix}_versions"]).add_prefix(f"{prefix}_versions_")
    ], axis=1)
    df = dss_funcs.rename_and_move_first(df, f"{prefix}_projectKey", "project_key")
    df = dss_funcs.normalize_dataframe(self, df, FLAT_COLUMNS)
    return df