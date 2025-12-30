import pandas as pd
from dataikupulse.src import dss_folder, dss_silver


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
    return set(
        df.loc[
            df["message_msgType"] == "application-open",
            "message_login"
        ]
        .dropna()
        .unique()
    )

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

def classification_to_df(classification, date=None):
    rows = []
    for user in classification.get("developing_users", []):
        rows.append({
            "user": user,
            "user_type": "DEVELOPING",
            "date": date,
        })
    for user in classification.get("view_users", []):
        rows.append({
            "user": user,
            "user_type": "VIEW",
            "date": date,
        })
    return pd.DataFrame(rows)


# ------------------------------------------------
# Calculate who is who / Actions
# ------------------------------------------------
def main(self, df):
    results = []
    # Get cleaned DF
    df, results = clean_audit_log_base(df, results)
    if df == None:
        return results

    # Loop over any partitions of dates for data
    instance_name = df["instance_name"].iloc[0]
    for date,grp in df.groupby("date"):
        # datetime for saving
        dt = grp["timestamp"].max()
        dt_year  = str(dt.year)
        dt_month = str(f'{dt.month:02d}')
        dt_day   = str(f'{dt.day:02d}')
        dt_epoch = dt.value
        
        classification = classify_users_by_activity(grp)
        users_login_df = classification_to_df(classification, date)
        results = _persist_raw(self, df, category, module_name, project_key, results)
        results = _process_quality_and_persist(self, df, category, module_name, project_key, results)
    return results
        
        