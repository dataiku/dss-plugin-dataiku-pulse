import pandas as pd
import os

from dataikupulse.src import dss_folder, dss_funcs, dss_silver
from dataikupulse.src.schemas import audit_dataiku_usage


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

        for category, raw_df in merged_df.groupby("dataiku_category"):
            # --------------------------------------------------
            # 0. Prepare RAW dataframe (as-is)
            # --------------------------------------------------
            raw_df = raw_df.dropna(axis=1, how="all").reset_index(drop=True)
            file_name = f"{category}-{dt_epoch}.parquet"
            raw_path = f"raw/dataiku_usage/{category}/{instance_name}/{dt_year}/{dt_month}/{dt_day}/{file_name}"

            # --------------------------------------------------
            # 1. Write RAW (Parquet-safe only)
            # --------------------------------------------------
            try:
                raw_df_safe = raw_df.map(dss_silver.sanitize_for_parquet)
                dss_folder.write_remote_folder_output(self, raw_path, raw_df_safe)
                results.append([
                    f"write/save - Dataiku Usage {category} -- RAW",
                    True,
                    file_name,
                ])
            except Exception as e:
                results.append([
                    f"write/save - Dataiku Usage {category} -- RAW",
                    False,
                    e,
                ])
                continue

            # --------------------------------------------------
            # 2. SILVER: normalize → coerce → quality
            # --------------------------------------------------
            try:
                silver_df = raw_df.copy()

                # 2.1 Normalize (schema-aware)
                FLAT_COLUMNS = audit_dataiku_usage.get_flat_cols(category)
                silver_df = dss_silver.normalize_dataframe(self, silver_df, FLAT_COLUMNS,)

                # 2.2 Order columns (deterministic)
                ordered = [c for c in FLAT_COLUMNS if c in silver_df.columns]
                rest = [c for c in silver_df.columns if c not in ordered]
                silver_df = silver_df[ordered + rest]

                # 2.3 Coerce schema (includes extras canonicalization)
                silver_df = dss_silver.coerce_schema(silver_df)

                # 2.4 Quality checks
                dq = dss_silver.data_quality(silver_df)

                # --------------------------------------------------
                # 3. Route based on quality
                # --------------------------------------------------
                if dq["errors"]:
                    layer = "raw_errors"
                    data_path = f"{layer}/dataiku_usage/{category}/{instance_name}/{dt_year}/{dt_month}/{dt_day}/{file_name}"
                    dq_path = f"{layer}/dataiku_usage/{category}/{instance_name}/{dt_year}/{dt_month}/{dt_day}/dq_{file_name}"
                    dss_folder.write_remote_folder_output(self, data_path, silver_df)
                    dss_folder.write_remote_folder_output(self, dq_path, pd.DataFrame([dq]),)
                    results.append([
                        f"write/save - Dataiku Usage {category} -- {layer}",
                        False,
                        "Quality errors detected",
                    ])
                else:
                    layer = "silver"
                    silver_path = f"{layer}/dataiku_usage/{category}/{instance_name}/{dt_year}/{dt_month}/{dt_day}/{file_name}"
                    dss_folder.write_remote_folder_output(self, silver_path, silver_df)
                    results.append([
                        f"write/save - Dataiku Usage {category} -- SILVER",
                        True,
                        None,
                    ])
            except Exception as e:
                results.append([
                    f"write/save - Dataiku Usage {category} -- SILVER/QUALITY",
                    False,
                    e,
                ])
    return results
