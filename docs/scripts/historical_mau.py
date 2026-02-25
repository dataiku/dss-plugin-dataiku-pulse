def load_nearest_metadata(folder, base_dt, max_lookahead_days=30):
    for offset in range(max_lookahead_days + 1):
        try_dt = base_dt + timedelta(days=offset)
        path = (
            f"/raw/category=users/module=metadata/instance_name=tam_global/"
            f"year={try_dt.year}"
            f"/month={try_dt.month:02d}"
            f"/day={try_dt.day:02d}"
            f"/data.parquet"
        )
        try:
            with folder.get_download_stream(path) as stream:
                file_bytes = io.BytesIO(stream.read())
            return pd.read_parquet(file_bytes)
        except Exception:
            continue
    raise FileNotFoundError(
        f"No metadata file found within {max_lookahead_days} days of {base_dt}"
    )
    return


def normalize_user_meta_df(user_meta_df):
    if "trialStatus" in user_meta_df.columns:
        # Ensure booleans exist safely
        for col in [
            "trialStatus.exists",
            "trialStatus.valid",
            "trialStatus.expired",
            "trialStatus.illegal",
        ]:
            if col not in user_meta_df.columns:
                user_meta_df[col] = False
            user_meta_df[col] = (
                user_meta_df[col]
                .fillna(False)
                .astype(bool)
            )
        # Canonical trial flag
        user_meta_df["is_trial"] = (
            user_meta_df["trialStatus.exists"]
            & user_meta_df["trialStatus.valid"]
            & ~user_meta_df["trialStatus.expired"]
            & ~user_meta_df["trialStatus.illegal"]
        )
    else:
        user_meta_df["is_trial"] = False
        # Normalize enabled
    if "enabled" in user_meta_df.columns:
        user_meta_df["enabled"] = (
            user_meta_df["enabled"]
            .fillna(False)
            .astype(bool)
        )
    else:
        user_meta_df["enabled"] = False
    return user_meta_df

    
def apply_mau_rules(users_login_df, enabled_map, trial_map, profile_map, version):
    eligible = pd.Series(True, index=users_login_df.index)
    enabled = users_login_df["login"].map(enabled_map).fillna(False)
    is_trial = users_login_df["login"].map(trial_map).fillna(False)
    profile = users_login_df["login"].map(profile_map)
    eligible &= enabled
    eligible &= ~is_trial
    eligible &= ~profile.isin(["READER", "AI_CONSUMER"])
    users_login_df["is_mau_eligible"] = eligible
    users_login_df["mau_definition_version"] = version
    return users_login_df


def write_local_folder_output(folder, path, df):
    f = io.BytesIO()
    df.to_parquet(f, compression="gzip", engine='pyarrow', index=False)
    f.seek(0)
    content = f.read()
    folder.upload_stream(path, content)
    return


# ------------------------------------------------------------------------------------
from datetime import datetime, timedelta
import io
import pandas as pd

import dataiku
from dataiku import pandasutils as pdu

client = dataiku.api_client()
project_handle = client.get_default_project()

folder = dataiku.Folder(
    lookup="partitioned_data",
    project_key=dataiku.default_project_key(),
    ignore_flow=True
)

# ------------------------------------------------------------------------------------
paths = folder.list_paths_in_partition()
df = pd.DataFrame(paths, columns=["paths"])
cols = ["dot", "layer", "category", "module", "instance_name", "year", "month", "day", "data"]
df[cols] = df["paths"].str.split("/", expand=True)
for c in cols:
    df[c] = df[c].str.replace(f"{c}=", "", regex=False)
df["year"] = df["year"].astype(int)
df["month"] = df["month"].astype(int)
df["day"] = df["day"].astype(int)
df["date"] = pd.to_datetime(df[["year", "month", "day"]])
filtered_df = df.loc[
    (df["layer"] == "raw")
    &(df["category"] == "users")
    &(df["module"] == "user_login_activity")
]

# ------------------------------------------------------------------------------------
for _,grp in filtered_df.groupby("date"):
    dt = grp.iloc[0]["date"]
    
    user_meta_df = load_nearest_metadata(folder, dt)
    user_meta_df = normalize_user_meta_df(user_meta_df)
    enabled_map = dict(zip(user_meta_df["login"], user_meta_df["enabled"]))
    trial_map = dict(zip(user_meta_df["login"], user_meta_df["is_trial"]))
    profile_map = dict(zip(user_meta_df["login"], user_meta_df["userProfile"]))
    
    for p in grp["paths"]:
        with folder.get_download_stream(p) as stream:
            file_bytes = io.BytesIO(stream.read())
        users_login_df = pd.read_parquet(file_bytes)
        users_login_df = apply_mau_rules(
            users_login_df,
            enabled_map,
            trial_map,
            profile_map,
            "v1_2026"
        )
        write_local_folder_output(folder, p, users_login_df)