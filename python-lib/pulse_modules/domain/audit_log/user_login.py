import pandas as pd
import yaml

from pulse_modules.helpers import dss_funcs


REQUIRED_COLUMNS = [
    "timestamp",
    "date",
    "message_callPath",
    "message_msgType",
    "message_login",
    "message_project_key",
    "instance_name",
]

ACTION_WORDS = [
    "save",
    "create",
    "analysis",
    "clear",
    "run",
]

REMOVE_WORDS = [
    "list",
    "dataset-clear-samples",
    "dataset-save-schema",
    "project-save-variables",
]

ACTION_PATTERN = "|".join(ACTION_WORDS)
REMOVE_PATTERN = "|".join(REMOVE_WORDS)

with open("./mau_definition.yaml") as f:
    mau_config = yaml.safe_load(f)

version = mau_config["current_version"]
rules = mau_config["definitions"][version]["rules"]

# ------------------------------------------------
# Clean the Audit Log DF
# ------------------------------------------------
def clean_audit_log_base(df, results,):
    # 1. Remove scenarios & jobs (if columns exist)
    if "message_scenarioId" in df.columns:
        df = df[df["message_scenarioId"].isna()]
    if "message_jobId" in df.columns:
        df = df[df["message_jobId"].isna()]
    # 2. Keep only user-generated UI actions
    df = df[df["message_authSource"] == "USER_FROM_UI"]
    # 3. Drop invalid rows / empty columns
    df = (
        df
        .dropna(subset=["message_login"])
        .dropna(axis=1, how="all")
        .reset_index(drop=True)
    )
    # 4. Validate & project required columns
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        results.append([
            "Loading Audit Logs",
            False,
            f"Missing required columns: {missing}"
        ])
        return None, results
    df = df[REQUIRED_COLUMNS]
    return df, results


# ------------------------------------------------
# Calculate who is who / Actions
# ------------------------------------------------
def get_view_users(df):
    return set(df["message_login"].dropna().unique())

def get_developing_users(df):
    action_df = df[
        df["message_msgType"].str.contains(ACTION_PATTERN, na=False)
    ]
    action_df = action_df[
        ~action_df["message_msgType"].str.contains(REMOVE_PATTERN, na=False)
    ]
    return set(action_df["message_login"].dropna().unique())

def classify_users_by_activity(df):
    view_users = get_view_users(df)
    developing_users = get_developing_users(df)
    # DEVELOPING overrides VIEW
    developing_users = developing_users & view_users
    view_only_users = view_users - developing_users
    return {
        "developing_users": developing_users,
        "view_users": view_only_users,
    }

def classification_to_df(classification, instance_name=None, timestamp=None):
    rows = []
    for user in classification.get("developing_users", []):
        rows.append({
            "instance_name": instance_name,
            "login": user,
            "activity_type": "DEVELOPER",
            "timestamp": timestamp,
        })
    for user in classification.get("view_users", []):
        rows.append({
            "instance_name": instance_name,
            "login": user,
            "activity_type": "VIEWER",
            "timestamp": timestamp,
        })
    return pd.DataFrame(rows)


# ------------------------------------------------
#
# ------------------------------------------------
def build_dss_users(self):
    data = self.local_client.list_users(
        as_objects=False,
        include_settings=False
    )
    dss_users_df = pd.DataFrame(data)
    
    # Normalize trial status
    if "trialStatus" in dss_users_df.columns:
        jdf = (
            pd.json_normalize(dss_users_df["trialStatus"])
            .add_prefix("trialStatus_")
        )
        dss_users_df = pd.concat([dss_users_df, jdf], axis=1)
        # Ensure booleans exist safely
        for col in [
            "trialStatus_exists",
            "trialStatus_valid",
            "trialStatus_expired",
            "trialStatus_illegal",
        ]:
            if col not in dss_users_df.columns:
                dss_users_df[col] = False
            dss_users_df[col] = (
                dss_users_df[col]
                .fillna(False)
                .astype(bool)
            )
        # Canonical trial flag
        dss_users_df["is_trial"] = (
            dss_users_df["trialStatus_exists"]
            & dss_users_df["trialStatus_valid"]
            & ~dss_users_df["trialStatus_expired"]
            & ~dss_users_df["trialStatus_illegal"]
        )
    else:
        dss_users_df["is_trial"] = False
        
    # Normalize enabled
    if "enabled" in dss_users_df.columns:
        dss_users_df["enabled"] = (
            dss_users_df["enabled"]
            .fillna(False)
            .astype(bool)
        )
    else:
        dss_users_df["enabled"] = False
    return dss_users_df

# ------------------------------------------------
# MAIN
# ------------------------------------------------
def main(self, df):
    results = []
    
    # Get cleaned DF
    df, results = clean_audit_log_base(df, results)
    if df is None or not isinstance(df, pd.DataFrame):
        return results

    # Loop over any partitions of dates for data
    instance_name = df["instance_name"].iloc[0]
    for date,grp in df.groupby("date"):
        # datetime for saving
        self.dt = grp["timestamp"].max()
        dt_epoch = self.dt.value
        
        # Classify, dataframe
        classification = classify_users_by_activity(grp)
        users_login_df = classification_to_df(classification, instance_name, self.dt)

        # RAW 
        try:
            long_results = dss_funcs._persist_raw(self, users_login_df, "users", "user_login_activity", None, f"data-{dt_epoch}.parquet", [])
            results.append(["User Login Classification", "write/save - RAW", True, None])
        except Exception as e:
            results.append(["User Login Classification", "write/save - RAW", False, e])
        # SILVER
        try:
            long_results = dss_funcs._process_quality_and_persist(self, users_login_df, "users", "user_login_activity", None, "SKIP", f"data-{dt_epoch}.parquet", [])
            results.append(["User Login Classification", "write/save - SILVER", True, None])
        except Exception as e:
            results.append(["User Login Classification", "write/save - SILVER", False, e])

    return results
        
        