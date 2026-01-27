import io
import os
import pandas as pd

from dataikupulse.src import dss_folder, dss_funcs, dss_silver

def skip_file(self, path):
    new_path = path.replace("/raw/", "/silver/")
    exists = self.folder.get_path_details(new_path).get("exists", False)
    return exists


def check_save(self, df_clean, dq, path, category, module_name, results):
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


def rebuild_silver(self, paths):
    # Main loop
    results = []
    for path in paths:
        if skip_file(self, path):
            continue
        #
        with self.folder.get_download_stream(path) as stream:
            file_bytes = io.BytesIO(stream.read())
        df = pd.read_parquet(file_bytes)
        #
        category = path.split("/")[2].replace("category=", "")
        module_name = path.split("/")[3].replace("module=", "")
        self.instance_name = path.split("/")[4].replace("instance_name=", "")
        #
        mode="client"
        if "dataiku_usage" == category:
            mode="dataiku_usage"
        elif "operating_system" == category:
            mode="client"
        elif "user_login_acivity" == module_name:
            mode="SKIP"
        elif "_user_logins" in module_name:
            continue
        #
        df_clean, dq = dss_funcs._normalize_and_validate(self, df, category, module_name, mode)
        results = check_save(self, df_clean, dq, path, category, module_name, results)
    return results

#EOF





