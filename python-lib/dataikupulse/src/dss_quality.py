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


def data_quality(df: pd.DataFrame) -> dict:
    """
    Runs data quality checks on a SILVER dataframe.
    Returns a report dict with errors and warnings.
    """

    report = {
        "errors": [],
        "warnings": [],
        "stats": {}
    }

    # -------------------------
    # 1. Timestamp sanity
    # -------------------------
    TIMESTAMP_COLS = [
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
    ]

    for col in TIMESTAMP_COLS:
        if col in df.columns:
            if pd.api.types.is_float_dtype(df[col]):
                report["errors"].append(
                    f"{col} is float64 (expected datetime)"
                )

    # -------------------------
    # 2. Identifier columns must not be numeric
    # -------------------------
    ID_COLS = [
        "project_key",
        "dataset_projectKey",
        "scenarios_projectKey",
        "user",
        "email",
        "nodeId",
        "licenseId",
    ]

    for col in ID_COLS:
        if col in df.columns:
            if pd.api.types.is_numeric_dtype(df[col]):
                report["errors"].append(
                    f"{col} is numeric (expected string identifier)"
                )

    # -------------------------
    # 3. Boolean columns must be boolean dtype
    # -------------------------
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

    for col in BOOL_COLS:
        if col in df.columns:
            if not pd.api.types.is_bool_dtype(df[col]) \
               and not pd.api.types.is_boolean_dtype(df[col]):
                report["errors"].append(
                    f"{col} is not boolean dtype"
                )

    # -------------------------
    # 4. Extras column validation
    # -------------------------
    if "extras" in df.columns:
        invalid_extras = df["extras"].map(
            lambda x: not (x is None or isinstance(x, dict))
        )

        if invalid_extras.any():
            report["errors"].append(
                f"extras column contains invalid values (non-dict, non-null)"
            )

    # -------------------------
    # 5. Enum validation
    # -------------------------
    ENUM_RULES = {
        "severity": {"INFO", "WARN", "ERROR"},
        "msgtypebase": {"admin", "code", "generic", "automation"},
    }

    for col, allowed in ENUM_RULES.items():
        if col in df.columns:
            bad_vals = (
                set(df[col].dropna().unique()) - allowed
            )
            if bad_vals:
                report["warnings"].append(
                    f"{col} has unexpected values: {bad_vals}"
                )

    # -------------------------
    # 6. Numeric sanity checks
    # -------------------------
    if "used_pct" in df.columns:
        bad_pct = df[
            (df["used_pct"] < 0) | (df["used_pct"] > 100)
        ]
        if not bad_pct.empty:
            report["errors"].append(
                "used_pct outside range 0–100"
            )

    if {"used", "size"}.issubset(df.columns):
        bad_usage = df[df["used"] > df["size"]]
        if not bad_usage.empty:
            report["errors"].append(
                "used > size detected"
            )

    # -------------------------
    # Stats
    # -------------------------
    report["stats"] = {
        "row_count": len(df),
        "column_count": len(df.columns),
        "error_count": len(report["errors"]),
        "warning_count": len(report["warnings"]),
    }

    return report
