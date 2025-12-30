import pandas as pd
from dataikupulse.src import dss_folder, dss_silver



from typing import List, Tuple
import pandas as pd

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
    """
    Classifies users as VIEW or DEVELOPING for a single partition.
    """

    view_users = get_view_users(df)
    developing_users = get_developing_users(df)

    # DEVELOPING overrides VIEW
    developing_users = developing_users & view_users
    view_only_users = view_users - developing_users

    return {
        "developing_users": developing_users,
        "view_users": view_only_users,
    }


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
    for i,grp in df.groupby("date"):
        # datetime for saving
        dt = grp["timestamp"].max()
        dt_year  = str(dt.year)
        dt_month = str(f'{dt.month:02d}')
        dt_day   = str(f'{dt.day:02d}')
        dt_epoch = dt.value

        # Login Users
        login_users = grp[grp["message_msgType"] == "application-open"]["message_login"].unique()
        login_users_df = pd.DataFrame(login_users, columns=["viewing_user_logins"])
        login_users_df["timestamp"] = pd.to_datetime(i)
        login_users_df["instance_name"] = instance_name
        try:
            login_users_df.columns = login_users_df.columns.str.replace('message_', '', regex=False)
            login_users_df.columns = login_users_df.columns.str.lower()
            file_name = f"data-{dt_epoch}.parquet" 
            write_path = f"raw/users/viewing_user_logins/{instance_name}/{dt_year}/{dt_month}/{dt_day}/{file_name}"
            dss_folder.write_remote_folder_output(self, write_path, login_users_df)
            results.append(["write/save", True, f"login users data-{dt_epoch}.parquet"])
        except Exception as e:
            results.append(["write/save - All", False, e])

       # Test quality control -- Save to Silver or Raw Error
        try:
            layer = "silver"
            login_users_df = dss_silver.coerce_schema(login_users_df)
            dq = dss_silver.data_quality(login_users_df)
            if dq["errors"]:
                layer = "raw_errors"
                write_path = f"{layer}/users/viewing_user_logins/{instance_name}/{dt_year}/{dt_month}/{dt_day}/{file_name}"
                dss_folder.write_remote_folder_output(self, write_path, login_users_df)
                write_path = f"{layer}/users/viewing_user_logins/{instance_name}/{dt_year}/{dt_month}/{dt_day}/dq_{file_name}"
                dss_folder.write_remote_folder_output(self, write_path, pd.DataFrame(dq))
            else:
                write_path = f"{layer}/users/viewing_user_logins/{instance_name}/{dt_year}/{dt_month}/{dt_day}/{file_name}"
                dss_folder.write_remote_folder_output(self, write_path, login_users_df)
                results.append([f"write/save -- {layer}", True, None])
        except Exception as e:
            layer = "raw_errors"
            results.append([f"write/save -- QUALITY", False, e])
            
            
        # Developer Users
        tdf = grp[grp["message_login"].isin(login_users)]
        ## Action Items
        action_words = ["save", "create", "analysis", "clear", "run"] # Action Words -- Focus on
        pattern = "|".join(action_words)
        tdf = tdf[tdf["message_msgType"].str.contains(pattern, na=False)]
        ## Bad items
        remove_strings = ["list", "dataset-clear-samples", "dataset-save-schema", "project-save-variables"] # Vague Words -- Remove
        pattern = "|".join(remove_strings)
        tdf = tdf[~tdf["message_msgType"].str.contains(pattern, na=False)]
        ## Unique it
        developer_users = tdf["message_login"].unique()
        developer_users_df = pd.DataFrame(developer_users, columns=["developer_user_logins"])
        developer_users_df["timestamp"] = pd.to_datetime(i)
        developer_users_df["instance_name"] = instance_name
        try:
            developer_users_df.columns = developer_users_df.columns.str.replace('message_', '', regex=False)
            developer_users_df.columns = developer_users_df.columns.str.lower()
            file_name = f"data-{dt_epoch}.parquet" 
            write_path = f"raw/users/developer_user_logins/{instance_name}/{dt_year}/{dt_month}/{dt_day}/{file_name}"
            dss_folder.write_remote_folder_output(self, write_path, developer_users_df)
            results.append(["write/save", True, f"developing users data-{dt_epoch}.parquet"])
        except Exception as e:
            results.append(["write/save - All", False, e])

       # Test quality control -- Save to Silver or Raw Error
        try:
            layer = "silver"
            login_users_df = dss_silver.coerce_schema(login_users_df)
            dq = dss_silver.data_quality(login_users_df)
            if dq["errors"]:
                layer = "raw_errors"
                write_path = f"{layer}/users/developer_user_logins/{instance_name}/{dt_year}/{dt_month}/{dt_day}/{file_name}"
                dss_folder.write_remote_folder_output(self, write_path, login_users_df)
                write_path = f"{layer}/users/developer_user_logins/{instance_name}/{dt_year}/{dt_month}/{dt_day}/dq_{file_name}"
                dss_folder.write_remote_folder_output(self, write_path, pd.DataFrame(dq))
            else:
                write_path = f"{layer}/users/developer_user_logins/{instance_name}/{dt_year}/{dt_month}/{dt_day}/{file_name}"
                dss_folder.write_remote_folder_output(self, write_path, login_users_df)
                results.append([f"write/save -- {layer}", True, None])
        except Exception as e:
            layer = "raw_errors"
            results.append([f"write/save -- QUALITY", False, e])

    return results