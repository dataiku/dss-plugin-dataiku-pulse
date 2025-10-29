from sage.src import dss_folder

import pandas as pd


def main(self, project_handle, folder, df):
    for module in df["module"][df["category"] == "dataiku_usage"].unique().tolist():
        # Get a partitions df for the module
        partitions_df = df[
            (df["category"] == "dataiku_usage")
            & (df["module"] == module)
        ]
        # try to pull last update time to keep only the newest partitions
        try:
            original_df = dss_folder.read_local_folder_input(self, project_handle, "base_data", f"/dataiku_usage/rolling_{module}.parquet")
            last_entry = pd.to_datetime(original_df["timestamp"].max())
        except:
            original_df = pd.DataFrame()
            last_entry = pd.to_datetime(1970)
        # Read in the new partitions and update the parquet
        partitions_df = partitions_df.loc[partitions_df["dt"] >= last_entry]
        partitions = partitions_df["partitions"].tolist()
        if not partitions:
            continue
        for partition in partitions:
            dfs = []
            for path in folder.get_partition_info(partition)["paths"]:
                ttdf = dss_folder.read_local_folder_input(self, project_handle, "partitioned_data", path)
                dfs.append(ttdf)
            tdf = pd.concat(dfs, ignore_index=True)
            original_df = pd.concat([original_df, tdf], ignore_index=True)
        # Write new output
        original_df["timestamp"] = pd.to_datetime(original_df["timestamp"])
        original_df = original_df.sort_values(by="timestamp", ascending=False)
        dss_folder.write_local_folder_output(self, project_handle, "base_data", f"/dataiku_usage/{module}.parquet", original_df)
    return