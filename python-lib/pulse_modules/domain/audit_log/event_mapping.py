import pandas as pd
import os

from pulse_modules.helpers import dss_folder, dss_funcs, dss_silver
from pulse_modules.flat_columns import audit_dataiku_usage


def normalize_authvia(x):
    if isinstance(x, list):
        return ", ".join(map(str, x))
    if isinstance(x, str):
        return x
    return ""


def parse_authvia(s):
    project_key = None
    webapp_id = None
    try:
        if not isinstance(s, str) or not s.strip():
            return (None, None)
        if "scenario=" in s:
            part = s.split("scenario=", 1)[1].split(" ")[0]
            project_key = part.split(".", 1)[0]
        elif "ticket:python_trigger:" in s:
            part = s.replace("ticket:python_trigger:", "")
            project_key = part.split(".", 1)[0]
        elif "ticket:Standard webapp backend:" in s:
            part = s.replace("ticket:Standard webapp backend: ", "")
            project_key, remainder = part.split(".", 1)
            webapp_id = remainder.split(",", 1)[0]
        elif "ticket:jupyter:" in s:
            part = s.replace("ticket:jupyter:", "")
            project_key = part.split(".", 1)[0]
        elif "ticket:job:" in s:
            part = s.replace("ticket:job:", "")
            project_key = part.split(".", 1)[0]
    except Exception:
        return (None, None)
    return (project_key, webapp_id)


def main(self, df):
    results = []
    instance_name = df["instance_name"].iloc[0]
    path = os.path.dirname(os.path.realpath(__file__))
    mapping_df = pd.read_csv(f"{path}/mapping.csv")
    
    df = df[df["topic"] == "generic"].reset_index(drop=True)

    # Loop over any partitions of dates for data
    for i,grp in df.groupby("date"):
        # Merge the mapping tables
        merged_df = pd.merge(
            grp,
            mapping_df,
            on="message_msgType",
            how="left"
        )
        
        # Filter - remove dropped columns
        merged_df = merged_df[merged_df["dataiku_category"] != "DROP_DELETE"]

        if merged_df.empty:
            continue

        # Minor cleanse
        merged_df.columns = merged_df.columns.str.lower()
        merged_df.columns = merged_df.columns.str.replace('message_', '', regex=False)
        merged_df["dataiku_category"] = merged_df["dataiku_category"].str.lower()

        # AuthVia
        # With chunked JSONL parsing, a given chunk may not contain any event
        # with authVia, so the column may be missing entirely.
        if "authvia" not in merged_df.columns:
            merged_df["authvia"] = ""
        merged_df["authvia"] = merged_df["authvia"].fillna("")
        merged_df["authvia"] = merged_df["authvia"].apply(normalize_authvia)
        merged_df[["project_key_temp", "webapp_id_temp"]] = pd.DataFrame(
                        merged_df["authvia"].apply(parse_authvia).tolist(),
                        index=merged_df.index
                    )
        
        # Update columns from AuthVia
        if "project_key" not in merged_df.columns:
            merged_df["project_key"] = None
        merged_df["project_key"] = merged_df["project_key"].fillna(merged_df["project_key_temp"])
        
        if "webapp_id" not in merged_df.columns:
            merged_df["webapp_id"] = None
        merged_df["webapp_id"] = merged_df["webapp_id"].fillna(merged_df["webapp_id_temp"])
        
        if "webappid" in merged_df.columns:
            dupes = merged_df.loc[:, merged_df.columns == "webappid"]
            merged_df["webappid"] = (
                dupes.bfill(axis=1)
                     .iloc[:, 0]
                     .infer_objects(copy=False)
            )
            merged_df = merged_df.loc[:, ~merged_df.columns.duplicated()]

        # Save outputs
        for module_name, mn_df in merged_df.groupby("dataiku_category"):
            category = "dataiku_usage"
            # Safe epoch for filename only
            ts = (
                mn_df
                .filter(regex="^timestamp$")
                .bfill(axis=1)
                .infer_objects(copy=False)
                .iloc[:, 0]
            )
            self.dt = pd.to_datetime(ts, errors="coerce").max()
            dt_epoch = (
                int(self.dt.timestamp() * 1_000_000_000)
                if pd.notna(self.dt)
                else "unknown"
            )
            # Final cleanse
            mn_df = mn_df.dropna(axis=1, how="all").reset_index(drop=True)
            file_name = f"{module_name}-{dt_epoch}.parquet"
            # RAW 
            try:
                long_results = dss_funcs._persist_raw(self, mn_df, category, module_name, None, file_name, [])
                results.append([category, f"write/save - {module_name} -- raw", True, None])
            except Exception as e:
                results.append([category, f"write/save - {module_name} -- raw", False, e])
                continue
            # SILVER
            try:
                long_results = dss_funcs._process_quality_and_persist(self, mn_df, category, module_name, None, category, file_name, [])
                results.append([category, f"write/save - {module_name} -- silver", True, None])
            except Exception as e:
                results.append([category, f"write/save - {module_name} -- silver", False, e])
    return results


#EOF


