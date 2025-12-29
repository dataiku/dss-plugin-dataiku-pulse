FLAT_COLUMNS = {
    # Project identity
    "project_key",
    "project_name",
    "project_projectType",
    "project_projectAppType",

    # User / access context
    "login",

    # Governance / ownership
    "project_ownerDisplayName",

    # Core status / classification
    "project_permissionsVersion",
    "project_commitMode",
    "project_tutorialProject",

    # Versioning (promoted from nested for queryability)
    "project_versionTag_versionNumber",
    "project_versionTag_lastModifiedOn",
    "project_versionTag_lastModifiedBy_login",
    "project_creationTag_versionNumber",
    "project_creationTag_lastModifiedOn",
    "project_creationTag_lastModifiedBy_login",
}