FLAT_COLUMNS = [
    # Knowledge bank identity
    "project_key",
    "knowledge_banks_id",
    "knowledge_banks_name",

    # Core classification
    "knowledge_banks_retrieverType",
    "knowledge_banks_vectorStoreType",
    "knowledge_banks_embeddingLLMId",

    # Execution / environment
    "knowledge_banks_rebuildBehavior",
    "knowledge_banks_envSelection_envMode",
    "knowledge_banks_envSelection_envName",
    "knowledge_banks_containerExecSelection_containerMode",

    # Versioning / lifecycle
    "knowledge_banks_versionTag_versionNumber",
    "knowledge_banks_versionTag_lastModifiedOn",
    "knowledge_banks_versionTag_lastModifiedBy.login",
    "knowledge_banks_creationTag_versionNumber",
    "knowledge_banks_creationTag_lastModifiedOn",
    "knowledge_banks_creationTag_lastModifiedBy_login",
]