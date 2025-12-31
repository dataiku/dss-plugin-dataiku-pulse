FLAT_COLUMNS = [
    # Recipe identity
    "project_key",
    "recipes_name",
    "recipes_type",

    # Core behavior
    "recipes_neverRecomputeExistingPartitions",
    "recipes_redispatchPartitioning",
    "recipes_maxRunningActivities",
    "recipes_hashPropagationBehavior",

    # Engine classification
    "recipes_params_engineType",
    "recipes_params_engineLabel",
    "recipes_params_engineRecommended",

    # Versioning / lifecycle
    "recipes_versionTag_versionNumber",
    "recipes_versionTag_lastModifiedOn",
    "recipes_versionTag_lastModifiedBy_login",
    "recipes_creationTag_versionNumber",
    "recipes_creationTag_lastModifiedOn",
    "recipes_creationTag_lastModifiedBy_login",
]