import io
import os
import pandas as pd

from dataikupulse.src.schemas import audit_dataiku_usage
from dataikupulse.src import dss_folder, dss_funcs, dss_silver

def check_save(self, df, path, results, df_clean, dq):
    if dq is None:
        results.append([category, module_name, "quality", False, "Unknown failure"])
        return results
    if df_clean is None:
        results.append([
            category,
            module_name,
            f"quality -- {dq['stage']}",
            False,
            dq["error"],
        ])
        return results
    df_report = pd.DataFrame([{
        "errors": dq["errors"],
        "warnings": dq["warnings"],
        **dq["stats"],
    }])
    base_path = path.replace("/raw/", "")
    if dq["errors"]:
        write_path = f"/raw_errors/{base_path}"
    else:
        write_path = f"/silver/{base_path}"
    dss_folder.write_remote_folder_output(self, write_path, df_clean)
    if dq["errors"]:
        filename = os.path.basename(write_path)
        report_path = write_path.replace(filename, f"dq_{filename}")
        df_report = pd.DataFrame([{
            "errors": dq["errors"],
            "warnings": dq["warnings"],
            **dq["stats"],
        }])
        dss_folder.write_remote_folder_output(self, report_path, df_report)
        results.append([category, module_name, f"write/save -- raw_errors", False, "Check raw errors"])
    else:
        results.append([category, module_name, f"write/save -- silver", True, None])
    return results
    

def silver_dataiku_usage(self, df, path, results):
    category = df["dataiku_category"].iloc[0]
    df_clean, dq = dss_funcs._normalize_and_validate(self, df, category, module_name=None, mode="audit_logs")
    


    
    return results






def silver_instance_projects(self, df, path, results):
    category = path.split("/")[2]
    module_name = path.split("/")[3]
    self.instance_name = path.split("/")[4]
    df_clean, dq = dss_funcs._normalize_and_validate(self, df, category, module_name)
    return results


def rebuild_silver(self, chunk_df):
    # Get all the partition paths
    paths = []
    for row in chunk_df.itertuples():
        paths.extend(self.folder.get_partition_info(row.partitions)["paths"])
    # Grab the parquet file and load as a df
    results = []
    for path in paths: # Loop paths
        # Grab the parquet file and load as a df
        with self.folder.get_download_stream(path) as stream:
            file_bytes = io.BytesIO(stream.read())
        df = pd.read_parquet(file_bytes)
        # Figure out which method to perform for NCQ
        if "/dataiku_usage/" in path:
            print(1)
        else:
            results = silver_instance_projects(self, df, path, results)
    return results