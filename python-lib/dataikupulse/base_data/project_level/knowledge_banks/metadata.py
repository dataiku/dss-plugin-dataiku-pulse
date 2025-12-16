import pandas as pd
from dataikupulse.src import dss_funcs

FLAT_COLUMNS = {
    # Knowledge bank identity
    "knowledge_banks_projectKey",
    "knowledge_banks_id",
    "knowledge_banks_name",

    # Core classification
    "knowledge_banks_retrieverType",
    "knowledge_banks_vectorStoreType",
    "knowledge_banks_embeddingLLMId",

    # Execution / environment
    "knowledge_banks_rebuildBehavior",
    "knowledge_banks_envSelection.envMode",
    "knowledge_banks_envSelection.envName",
    "knowledge_banks_containerExecSelection.containerMode",

    # Versioning / lifecycle
    "knowledge_banks_versionTag.versionNumber",
    "knowledge_banks_versionTag.lastModifiedOn",
    "knowledge_banks_versionTag.lastModifiedBy.login",
    "knowledge_banks_creationTag.versionNumber",
    "knowledge_banks_creationTag.lastModifiedOn",
    "knowledge_banks_creationTag.lastModifiedBy.login",
}


def main(self, project_handle, client_d = {}):
    try:
        if not project_handle.list_knowledge_banks():
            return pd.DataFrame()
    except:
        return pd.DataFrame()
    prefix = "knowledge_banks"
    df = pd.json_normalize(project_handle.list_knowledge_banks()).add_prefix(f"{prefix}_")
    df = dss_funcs.rename_and_move_first(project_handle, df, f"{prefix}_projectKey", "project_key")
    df = dss_funcs.normalize_dataframe(self, df, FLAT_COLUMNS)
    return df