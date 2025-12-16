import pandas as pd
import os

from dataikupulse.src import dss_folder, dss_funcs, dss_silver

FLAT_COLUMNS = {
    # Event identity / classification
    "severity",
    "logger",
    "topic",
    "audittopic",
    "msgtype",
    "msgtypebase",
    "dataiku_category",

    # Actor / auth context
    "login",
    "authsource",
    "authvia",
    "user",

    # Request / network
    "callpath",
    "clientip",
    "originalip",
    "xforwardedfor",

    # Time
    "timestamp",
    "date",

    # Instance
    "instance_name",
    "project_key",
}

def parse_authvia(s):
    project_key, webapp_id = None, None
    if "scenario=" in s:
        project_key, scenario_id = s[s.find("scenario="):].split(" ")[0].replace("scenario=", "").split(".", maxsplit=1)
    elif "ticket:python_trigger:" in s:
        project_key, scenario_id = s.replace("ticket:python_trigger:", "").split(".", maxsplit=1)
    elif "ticket:Standard webapp backend:" in s:
        project_key, d = s.replace("ticket:Standard webapp backend: ", "").split(".", maxsplit=1)
        if "," in d:
            webapp_id, login = d.split(",", maxsplit=1)
        elif isinstance(d, str):
            webapp_id = d
        else:
            print(s)
    elif "ticket:jupyter:" in s:
        project_key, jupyter_notebook = s.replace("ticket:jupyter:", "").split(".", maxsplit=1)
    elif "ticket:job:" in s:
        project_key, job_id = s.replace("ticket:job:", "").split(".", maxsplit=1)
    elif "ticket:plugin_ui_setup:" in s:
        pass
    return pd.Series([project_key, webapp_id],
                     index=["message_project_key_temp", "message_webapp_id"])



def main(self, df):
    results = []
    instance_name = df["instance_name"].iloc[0]
    path = os.path.dirname(os.path.realpath(__file__))
    mapping_df = pd.read_csv(f"{path}/mapping.csv")
    
    df = df[df["topic"] == "generic"].reset_index(drop=True)

    # Loop over any partitions of dates for data
    for i,grp in df.groupby("date"):
        # datetime for saving
        dt = grp["timestamp"].max()
        dt_year  = str(dt.year)
        dt_month = str(f'{dt.month:02d}')
        dt_day   = str(f'{dt.day:02d}')
        dt_epoch = dt.value
        
        # Merge the mapping tables
        merged_df = pd.merge(
            grp,
            mapping_df,
            on="message_msgType",
            how="left"
        )
        
        # Filter - remove dropped columns
        merged_df = merged_df[merged_df["dataiku_category"] != "DROP_DELETE"]
        merged_df.columns = merged_df.columns.str.lower()
        merged_df.columns = merged_df.columns.str.replace('message_', '', regex=False)
        merged_df["dataiku_category"] = merged_df["dataiku_category"].str.lower()
        
        # AuthVia
        merged_df["authvia"] = merged_df["authvia"].fillna("")
        merged_df["authvia"] = merged_df["authvia"].apply(lambda x: ', '.join(map(str, x)))
        merged_df[["project_key_temp", "webapp_id_temp"]] = merged_df["authvia"].apply(parse_authvia)
        
        # Update columns from AuthVia
        if "project_key" not in merged_df.columns:
            merged_df["project_key"] = None
        merged_df["project_key"] = merged_df["project_key"].fillna(merged_df["project_key_temp"])
        
        if "webapp_id" not in merged_df.columns:
            merged_df["webapp_id"] = None
        merged_df["webapp_id"] = merged_df["webapp_id"].fillna(merged_df["webapp_id_temp"])
        
        if "webappid" in merged_df.columns:
            dupes = merged_df.loc[:, merged_df.columns == "webappid"]
            merged_df["webappid"] = dupes.bfill(axis=1).iloc[:, 0]
            merged_df = merged_df.loc[:, ~merged_df.columns.duplicated()]

        # lets split the df by category and save
        for category, grp_df in merged_df.groupby("dataiku_category"):
            grp_df = grp_df.dropna(axis=1, how='all').reset_index(drop=True)
            grp_df = dss_funcs.normalize_dataframe(self, grp_df, FLAT_COLUMNS)
            try:
                file_name = f"data-{dt_epoch}.parquet" 
                write_path = f"raw/dataiku_usage/{category}/{instance_name}/{dt_year}/{dt_month}/{dt_day}/{file_name}"
                dss_folder.write_remote_folder_output(self, write_path, grp_df)
                results.append([f"write/save - Dataiku Usage {category}", True, f"data-{dt_epoch}.parquet"])
            except Exception as e:
                results.append([f"write/save - Dataiku Usage {category}", False, e])

            # Test quality control -- Save to Silver or Raw Error
            try:
                layer = "silver"
                grp_df = dss_silver.coerce_schema(grp_df)
                dq = dss_silver.data_quality(grp_df)
                if dq["errors"]:
                    layer = "raw_errors"
                    write_path = f"{layer}/dataiku_usage/{category}/{instance_name}/{dt_year}/{dt_month}/{dt_day}/{file_name}"
                    dss_folder.write_remote_folder_output(self, write_path, grp_df)
                    write_path = f"{layer}/dataiku_usage/{category}/{instance_name}/{dt_year}/{dt_month}/{dt_day}/dq_{file_name}"
                    dss_folder.write_remote_folder_output(self, write_path, pd.DataFrame(dq))
                else:
                    write_path = f"{layer}/dataiku_usage/{category}/{instance_name}/{dt_year}/{dt_month}/{dt_day}/{file_name}"
                    dss_folder.write_remote_folder_output(self, write_path, grp_df)
                results.append([f"write/save - Dataiku Usage {category} -- {layer}", True, None])
            except Exception as e:
                layer = "raw_errors"
                results.append([f"write/save - Dataiku Usage {category} -- QUALITY", False, e])
                
                
    return results
