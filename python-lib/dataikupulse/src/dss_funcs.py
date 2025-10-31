# dataikupulse/src/dss_folder.py
## Last Modified: 2025-10-25
# -----------------------------------------------------------------------------------------
import dataiku
import dataikuapi
import os
import re
import importlib
import pandas as pd
from dataikupulse.src import dss_folder


# ---------- DATAIKU CLIENT HANDLES -----------------------------
def build_local_client():
    client = dataiku.api_client()
    return client


def build_remote_client(host, api_key, ignore_certs=False):
    if ignore_certs:
        client = dataikuapi.DSSClient(host, api_key, insecure_tls=True)
    else:
        client = dataikuapi.DSSClient(host, api_key)
    return client


def get_dss_name(client):
    instance_info = client.get_instance_info()
    try:
        instance_name = instance_info.node_name.lower()
    except:
        instance_name = instance_info.node_id.lower()
    instance_name = re.sub(r'[^a-zA-Z0-9]', ' ', instance_name)
    instance_name = re.sub(r'\s+', '_', instance_name)
    return instance_name


def get_dss_name_id_mapping(client):
    instance_info = client.get_instance_info()
    instance_name = get_dss_name(client)
    try:
        instance_name_base = instance_info.node_name
    except:
        instance_name_base = instance_info.node_id
    instance_id_base = instance_info.node_id
    mapping = [instance_name, instance_name_base, instance_id_base]
    return mapping


# ---------- DATA GATHER MODULES -----------------------------
def run_modules(self, dss_objs, handle, client_d = {}, project_key = None):
    results = []
    directory = dss_objs.__path__[0]
    for root, _, files in os.walk(directory):
        for f in files:
            if not f.endswith(".py") or f == "__init__.py":
                continue
            module_name = f.removesuffix(".py")
            path = root.replace(directory, "")
            fp = os.path.join(root, f)
            try:
                spec = importlib.util.spec_from_file_location(module_name, fp)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                if hasattr(module, 'main'):
                    df = module.main(self, handle, client_d)
                    results.append([project_key, path, module_name, "load/run", True, None])
            except Exception as e:
                df = pd.DataFrame()
                results.append([project_key, path, module_name, "load/run", False, e])
            if not isinstance(df, pd.DataFrame) or df.empty:
                #results.append([project_key, path, module_name, "load/run", False, "DF CAME BACK EMPTY"])
                continue # nothing to write, skip
            try:
                # Remote client and DT parsing
                remote_client = build_remote_client(self.pulse_project_url, self.pulse_project_api, self.ignore_certs)
                dt_year  = str(self.dt.year)
                dt_month = str(f'{self.dt.month:02d}')
                dt_day   = str(f'{self.dt.day:02d}')
                # Add Additonal Information / output path
                df.columns = df.columns.str.lower()
                df.columns = df.columns.str.replace(".", "_", regex=False)
                instance_name = get_dss_name(build_local_client())
                if "instance_name" not in df.columns:
                    df["instance_name"] = instance_name
                write_path = f"/{instance_name}/{path}/{module_name}/{dt_year}/{dt_month}/{dt_day}/data.parquet"
                if project_key:
                    write_path = f"/{instance_name}/{path}/{module_name}/{dt_year}/{dt_month}/{dt_day}/{project_key}_data.parquet"
                # Final cleanse of DF for dictionary/lists to strings
                for col in df.columns:
                    types = df[col].dropna().map(type).unique()
                    if any(t in (dict, list) for t in types):
                        df[col] = df[col].astype(str)
                df = df.reset_index(drop=True)
                # Write the output finally
                if "timestamp" not in df.columns:
                    df["timestamp"] = self.dt
                dss_folder.write_remote_folder_output(self, remote_client, write_path, df)
                results.append([project_key, path, module_name, "write/save", True, None])
            except Exception as e:
                results.append([project_key, path, module_name, "write/save", False, e])
    return results


def get_nested_value(data, keys, dt=False):
    current = data
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            if dt:
                return pd.to_datetime(0, unit="ms")
            else:
                return False
    return current


def rename_and_move_first(project_handle, df, old, new):
    if old in df.columns:
        df = df.rename(columns={old: new})
    else:
        if project_handle:
            df[new] = project_handle.project_key
    if new in df.columns:
        cols = [new] + [c for c in df.columns if c != new]
        df = df[cols]
    return df


def normalize_column_type(df: pd.DataFrame, col: str, default_if_str="None", default_if_bool=False):
    if col not in df.columns:
        df[col] = default_if_str
        return df

    # Look at non-null values
    df[col] = df[col].apply(lambda x: str(x) if not pd.isna(x) else "None")
    non_null_vals = df[col].dropna()

    if non_null_vals.empty:
        df[col] = default_if_str
        return df

    # Count types
    type_counts = non_null_vals.map(type).value_counts()

    # Pick the most common type
    main_type = type_counts.index[0]
    
    if main_type is bool:
        def to_bool(x):
            if isinstance(x, str):
                if x.lower() == "true":
                    return True
                elif x.lower() == "false":
                    return False
            return x
        df[col] = df[col].map(to_bool).fillna(default_if_bool).astype(bool)
    else:  # everything else → string
        df[col] = df[col].fillna(default_if_str).astype(str)

    return df



# EOF