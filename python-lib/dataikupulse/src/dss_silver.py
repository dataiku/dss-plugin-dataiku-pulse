import pandas as pd
import numpy as np
import json

TIMESTAMP_COLS = [
    "timestamp",
    "run_timestamp",
    "dssStartupTimestamp",
    "creationDate",
    "last_session_activity",
    "first_commit_date",
    "last_commit_date",
    "creationTag_lastModifiedOn",
    "project_versionTag_lastModifiedOn",
    "project_creationTag_lastModifiedOn",
    "dataset_versionTag_lastModifiedOn",
    "dataset_creationTag_lastModifiedOn",
    "recipes_versionTag_lastModifiedOn",
    "recipes_creationTag_lastModifiedOn",
    "scenarios_start",
    "scenarios_nextRun",
    "scenarios_createdOn",
    "scenarios_lastModifiedOn",
]
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
ENUM_RULES = {
    "severity": {"INFO", "WARN", "ERROR"},
    "msgtypebase": {"admin", "code", "generic", "automation"},
}
NUMERIC_COLS = [
    "level_1_size",
    "level_2_size",
    "level_3_size",
    "size",
    "used",
    "available",
    "used_pct",
    "calltime",
    "dataset_versionTag_versionNumber",
    "dataset_creationTag_versionNumber",
]

def coerce_extras_to_json(series: pd.Series) -> pd.Series:
    def to_json_safe(val):
        if val is None or pd.isna(val):
            return None
        if isinstance(val, (dict, list)):
            return json.dumps(val, ensure_ascii=False)
        return str(val)

    return series.apply(to_json_safe)


def coerce_schema(df: pd.DataFrame) -> pd.DataFrame:
    """
    Applies semantic schema fixes to a normalized dataframe.
    This function MUTATES TYPES intentionally and deterministically.
    """
    # --------------------------------------------------
    # 1. Epoch millis → UTC timestamps
    # --------------------------------------------------
    for col in TIMESTAMP_COLS:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], unit="ms", utc=True, errors="coerce").dt.floor("s")
    for col in df.select_dtypes(include="datetimetz").columns:
        df[col] = df[col].astype("datetime64[s, UTC]")
    
    # --------------------------------------------------
    # 2. Identifier columns → nullable string
    # --------------------------------------------------
    for col in ID_COLS:
        if col in df.columns:
            df[col] = df[col].astype("string")

    # --------------------------------------------------
    # 3. Boolean coercion
    # --------------------------------------------------
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
    for col in NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    
    # --------------------------------------------------
    # 6. Extras column to JSON dump
    # --------------------------------------------------
    if "extras" in df.columns:
        df["extras"] = coerce_extras_to_json(df["extras"])

    return df


# ------------------------------------------------------------------------------------
# # Data Quaility Rules
# ------------------------------------------------------------------------------------
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
    for col in TIMESTAMP_COLS:
        if col in df.columns:
            if not pd.api.types.is_datetime64_any_dtype(df[col]):
                report["errors"].append(
                    f"{col} is {df[col].dtype} (expected datetime)"
                )
    for col in df.columns:
        if "lastModifiedOn" in col:
            if not pd.api.types.is_datetime64_any_dtype(df[col]):
                report["errors"].append(
                    f"{col} is {df[col].dtype} (expected datetime)"
                )
                
    # -------------------------
    # 2. Identifier columns must not be numeric
    # -------------------------
    for col in ID_COLS:
        if col in df.columns:
            if pd.api.types.is_numeric_dtype(df[col]):
                report["errors"].append(
                    f"{col} is numeric (expected string identifier)"
                )

    # -------------------------
    # 3. Boolean columns must be boolean dtype
    # -------------------------
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
    def is_valid_json_or_null(val):
        if val is None:
            return True
        if not isinstance(val, str):
            return False
        try:
            json.loads(val)
            return True
        except Exception:
            return False
    
    if "extras" in df.columns:
        invalid_extras = ~df["extras"].map(is_valid_json_or_null)

        if invalid_extras.any():
            report["errors"].append(
                "extras column contains invalid JSON values"
            )

    # -------------------------
    # 5. Enum validation
    # -------------------------
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
