import pandas as pd
from dataikupulse.src import dss_folder


def main(self, df):
    results = []
    
    # Remove scenarios, job and NaN's
    if "message_scenarioId" in df.columns:
        df = df[df["message_scenarioId"].isna()]
    if "message_jobId" in df.columns:
        df = df[df["message_jobId"].isna()]
    df = df[df["message_authSource"] == "USER_FROM_UI"]
    df = df.dropna(subset=["message_login"])
    df = df.dropna(axis=1, how='all').reset_index(drop=True)

    # Select the columns needed
    try:
        df = df[["timestamp", "date", "message_callPath", "message_msgType", "message_login", "message_project_key", "instance_name"]]
    except:
        cols = df.columns.tolist()
        results.append(["Loading Audit Logs", False, f"Invalid or missing column names: {cols}"])
        return results

    instance_name = df["instance_name"].iloc[0]
    # Loop over any partitions of dates for data
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
                results.append([project_key, category, module_name, f"write/save -- {layer}", True, None])
        except Exception as e:
            layer = "raw_errors"
            results.append([project_key, category, module_name, f"write/save -- QUALITY", False, e])
            
            
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
           
    return results