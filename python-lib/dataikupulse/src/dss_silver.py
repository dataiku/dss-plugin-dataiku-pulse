import json
import numpy as np
import pandas as pd
from datetime import date, datetime
from decimal import Decimal


UPPER_STR_COLS = [
    "severity",
    "msgtypebase",   
    "dataiku_category",
    "authsource",
    "sourceType",
    "dataset_type",
    "nodeType",
    "rawNodeType",
]
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
    "agents_versions_versionTag_lastModifiedOn",
    "agents_versions_creationTag_lastModifiedOn",
    "knowledge_banks_versionTag_lastModifiedOn",
    "knowledge_banks_creationTag_lastModifiedOn",
    "scenarios_start",
    "scenarios_nextRun",
    "scenarios_createdOn",
    "scenarios_lastModifiedOn",
]
NUMERIC_COLS = [
    "level_1_size",
    "level_2_size",
    "level_3_size",
    "size",
    "used",
    "available",
    "used_pct",
    "calltime",
    "osVersion",
    "project_versionTag_versionNumber",
    "project_creationTag_versionNumber",
    "dataset_versionTag_versionNumber",
    "dataset_creationTag_versionNumber",
    "recipes_maxRunningActivities",
    "recipes_versionTag_versionNumber",
    "recipes_creationTag_versionNumber",
    "knowledge_banks_versionTag_versionNumber",
    "knowledge_banks_creationTag_versionNumber",
    "agents_versions_versionTag_versionNumber",
    "agents_versions_creationTag_versionNumber",
]
BOOL_COLS = [
    "project_tutorialProject",
    "dataset_managed",
    "dataset_featureGroup",
    "recipes_neverRecomputeExistingPartitions",
    "recipes_redispatchPartitioning",
    "recipes_params_engineRecommended",
    "scenarios_active",
    "scenarios_unavailable",
    "scenarios_markedAsTest",
    "scenarios_running",
    "enabled",
]
TRUE_SET = {"true", "True", True, 1, "1"}
FALSE_SET = {"false", "False", False, 0, "0"}
NON_STRING_COLS = (
    set(TIMESTAMP_COLS)
    | set(NUMERIC_COLS)
    | set(BOOL_COLS)
)


# ------------------------------------------------
# Normalizer
# ------------------------------------------------
def sanitize_for_parquet(value):
    if isinstance(value, dict):
        if len(value) == 0:
            return None
        sanitized = {}
        for k, v in value.items():
            sv = sanitize_for_parquet(v)
            sanitized[k] = sv
        return sanitized
    if isinstance(value, list):
        return [sanitize_for_parquet(v) for v in value]
    return value


def normalize_dataframe(self, df: pd.DataFrame, FLAT_COLUMNS: {}) -> pd.DataFrame:
    # 0. NO PERIODS in column names
    df.columns = df.columns.str.replace(".", "_", regex=False)
    # 1. Ensure flat column(s) exist
    for col in FLAT_COLUMNS:
        if col not in df.columns:
            df[col] = None
    # 2. Split flat vs extras
    rows = []
    for _, row in df.iterrows():
        row_dict = row.to_dict()
        flat = {}
        extras = {}
        for col, value in row_dict.items():
            if col in FLAT_COLUMNS:
                flat[col] = value
            else:
                if value is None or (isinstance(value, float) and pd.isna(value)):
                    continue
                # Parquet-safe normalization
                extras[col] = sanitize_for_parquet(value)
        flat["extras"] = extras if extras else None
        rows.append(flat)
    df = pd.DataFrame(rows)
    # 3. Add Additonal Information / output path
    if "instance_name" not in df.columns:
        df.insert(
            loc=0,
            column="instance_name",
            value=self.instance_name
        )
    # 4. Add run_time
    df.insert(
        loc=df.columns.get_loc("extras"),
        column="run_timestamp",
        value=self.dt
    )
    return df


# ------------------------------------------------
# COERCE SCHEMA
# ------------------------------------------------
def get_string_cols(df: pd.DataFrame) -> list[str]:
    return [
        col for col in df.columns
        if col not in NON_STRING_COLS
    ]

def extras_to_json(series: pd.Series) -> pd.Series:
    """
    Canonicalize extras to a JSON string (or None).
    - dict/list -> JSON
    - np.ndarray -> list -> JSON
    - pd.NA/NaN/None -> None
    - strings -> if already JSON keep, else wrap as {"_value": "..."} to preserve info
    - unknown objects -> string via default handler
    """

    def default(o):
        # numpy
        if isinstance(o, np.ndarray):
            return o.tolist()
        if isinstance(o, (np.integer, np.floating)):
            return o.item()
        # pandas / datetime-ish
        if isinstance(o, (pd.Timestamp, datetime, date)):
            return o.isoformat()
        # decimals
        if isinstance(o, Decimal):
            return float(o)
        # last resort
        return str(o)

    def to_json(val):
        # true nulls
        if val is None or val is pd.NA:
            return None
        # pandas nan (float nan)
        try:
            if pd.isna(val):
                return None
        except Exception:
            pass

        # already JSON string? keep it
        if isinstance(val, str):
            s = val.strip()
            if s == "":
                return None
            try:
                json.loads(s)
                return s
            except Exception:
                # preserve non-JSON strings rather than dropping them
                return json.dumps({"_value": s}, ensure_ascii=False)

        # ndarray -> list
        if isinstance(val, np.ndarray):
            val = val.tolist()

        # dict/list -> dump
        if isinstance(val, (dict, list)):
            return json.dumps(val, ensure_ascii=False, default=default)

        # anything else: dump with default coercion
        return json.dumps(val, ensure_ascii=False, default=default)

    return series.map(to_json).astype("string")


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
    STRING_COLS = get_string_cols(df)

    for col in STRING_COLS + UPPER_STR_COLS:
        if col in df.columns:
            df[col] = (
                df[col]
                .astype("string")
                .str.strip()
            )
            
    # --------------------------------------------------
    # 3. Identifier columns → nullable string (UPPER)
    # --------------------------------------------------
    for col in UPPER_STR_COLS:
        if col in df.columns:
            df[col] = (
                df[col]
                .str.upper()
            )

    # --------------------------------------------------
    # 4. Numeric metric coercion
    # --------------------------------------------------
    for col in NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            
    # --------------------------------------------------
    # 5. Boolean coercion
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
    # 6. Extras column to JSON dump
    # --------------------------------------------------
    if "extras" in df.columns:
        df["extras"] = extras_to_json(df["extras"])

    return df


# ------------------------------------------------
# DATA QUALITY
# ------------------------------------------------
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
    STRING_COLS = get_string_cols(df)
    
    for col in STRING_COLS + UPPER_STR_COLS:
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
    # 5. Numeric sanity checks
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
