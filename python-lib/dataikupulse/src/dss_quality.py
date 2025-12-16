import pandas as pd
import numpy as np

def coerce_schema(df: pd.DataFrame) -> pd.DataFrame:
    """
    Applies semantic schema fixes to a normalized dataframe.
    This function MUTATES TYPES intentionally and deterministically.
    """

    df = df.copy()

    # --------------------------------------------------
    # 1. Epoch millis → UTC timestamps
    # --------------------------------------------------
    EPOCH_MS_COLS = [
        "timestamp",
        "dssStartupTimestamp",
        "creationDate",
        "last_session_activity",
        "first_commit_date",
        "last_commit_date",
        "scenarios_start",
        "scenarios_nextRun",
        "scenarios_createdOn",
        "scenarios_lastModifiedOn",
        "dataset_versionTag_lastModifiedOn",
        "dataset_creationTag_lastModifiedOn",
        "project_versionTag_lastModifiedOn",
        "project_creationTag_lastModifiedOn",
        "run_timestamp",
    ]

    for col in EPOCH_MS_COLS:
        if col in df.columns:
            if pd.api.types.is_numeric_dtype(df[col]):
                df[col] = pd.to_datetime(df[col], unit="ms", utc=True, errors="coerce")

    # --------------------------------------------------
    # 2. Identifier columns → nullable string
    # --------------------------------------------------
    ID_COLS = [
        "project_key",
        "dataset_projectKey",
        "scenarios_projectKey",
        "user",
        "email",
        "nodeId",
        "nodeName",
        "licenseId",
        "installId",
        "dipInstanceId",
        "licenseInstanceId",
        "commit",
    ]

    for col in ID_COLS:
        if col in df.columns:
            df[col] = df[col].astype("string")

    # --------------------------------------------------
    # 3. Boolean coercion
    # --------------------------------------------------
    BOOL_COLS = [
        "dataset_managed",
        "dataset_featureGroup",
        "project_tutorialProject",
        "scenarios_active",
        "scenarios_unavailable",
        "scenarios_markedAsTest",
        "scenarios_running",
        "enabled",
    ]

    TRUE_SET = {"true", "True", True, 1, "1"}
    FALSE_SET = {"false", "False", False, 0, "0"}

    def to_bool(x):
        if pd.isna(x):
            return None
        if x in TRUE_SET:
            return True
        if x in FALSE_SET:
            return False
        return None

    for col in BOOL_COLS:
        if col in df.columns:
            df[col] = df[col].map(to_bool).astype("boolean")

    # --------------------------------------------------
    # 4. Enum normalization (string + strip)
    # --------------------------------------------------
    ENUM_COLS = [
        "severity",
        "msgtypebase",
        "dataiku_category",
        "authsource",
        "sourceType",
        "dataset_type",
        "nodeType",
        "rawNodeType",
    ]

    for col in ENUM_COLS:
        if col in df.columns:
            df[col] = (
                df[col]
                .astype("string")
                .str.strip()
                .str.upper()
            )

    # --------------------------------------------------
    # 5. Numeric metric coercion
    # --------------------------------------------------
    NUMERIC_COLS = [
        "size",
        "used",
        "available",
        "used_pct",
        "calltime",
        "level_1_size",
        "level_2_size",
        "level_3_size",
    ]

    for col in NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df
