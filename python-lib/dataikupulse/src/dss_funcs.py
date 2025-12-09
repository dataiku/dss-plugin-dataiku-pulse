import dataiku
import dataikuapi
import pandas as pd
import os
import re
import importlib
from dataikupulse.src import dss_folder


# ---------- DATAIKU CLIENT HANDLES -----------------------------
def build_local_client():
    client = dataiku.api_client()
    return client


def build_remote_client(self, remote_url=False, api_key=False):
    try:
        ignore_certs = self.preset_pc["ignore_certs"]
    except:
        ignore_certs = False
    if remote_url:
        host = remote_url
        api_key = api_key
    else:
        host = self.params["pulse_project_url"]
        api_key = self.params["pulse_project_api"]
    if ignore_certs:
        # no_check_certificate v14?
        client = dataikuapi.DSSClient(host, api_key, insecure_tls=True)
    else:
        client = dataikuapi.DSSClient(host, api_key)
    return client


def get_dss_name(self):
    instance_info = self.local_client.get_instance_info()
    try:
        instance_name = instance_info.node_name.lower()
    except:
        instance_name = instance_info.node_id.lower()
    instance_name = re.sub(r'[^a-zA-Z0-9]', ' ', instance_name)
    instance_name = re.sub(r'\s+', '_', instance_name)
    return instance_name


def get_dss_name_id_mapping(self):
    instance_info = self.local_client.get_instance_info()
    instance_name = get_dss_name(self)
    try:
        instance_name_base = instance_info.node_name
    except:
        instance_name_base = instance_info.node_id
    instance_id_base = instance_info.node_id
    mapping = [instance_name, instance_name_base, instance_id_base]
    return mapping

def get_preset_pc(self, preset_name):
    # Connect to the plugin
    local_client = build_local_client()
    plugin_handle = local_client.get_plugin(plugin_id="dataiku-pulse")
    plugin_settings = plugin_handle.get_settings()
    preset_pc = {
        "pulse_dataiku_user": self.params["pulse_dataiku_user"],
        "ignore_certs": self.params["ignore_certs"],
        "do_parallel": self.params["do_parallel"],
        "cores": self.params["cores"],
        "macro_configs": [],
    }
    # Get the respective param_set if available
    if preset_name:
        param_set = plugin_settings.get_parameter_set(parameter_set_name="params-worker-instances")
        preset = param_set.get_preset(preset_name=preset_name)
        try:
            preset_pc = preset.plugin_config
        except:
            pass
    return preset_pc


# ---------- DATA GATHER MODULES -----------------------------
def run_modules(self, mode, project_handle = None, client_d = {}, project_key = None):
    if mode == "projects":
        from dataikupulse.base_data import project_level as dss_objs
    else:
        from dataikupulse.base_data import instance_level as dss_objs
    results = []
    directory = dss_objs.__path__[0]
    for root, _, files in os.walk(directory):
        for f in files:
            if not f.endswith(".py") or f == "__init__.py":
                continue
            module_name = f.removesuffix(".py")
            path = root.replace(directory, "")
            fp = os.path.join(root, f)
            path = path[1:]
            try:
                spec = importlib.util.spec_from_file_location(module_name, fp)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                if hasattr(module, 'main'):
                    if project_handle: # project level stuff
                        df = module.main(self, project_handle, client_d)
                    else:
                        df = module.main(self) # Instance level
                    results.append([project_key, path, module_name, "load/run", True, None])
            except Exception as e:
                df = pd.DataFrame()
                results.append([project_key, path, module_name, "load/run", False, e])
            if not isinstance(df, pd.DataFrame) or df.empty:
                continue # nothing to write, skip
            try:
                # Remote client and DT parsing
                dt_year  = str(self.dt.year)
                dt_month = str(f'{self.dt.month:02d}')
                dt_day   = str(f'{self.dt.day:02d}')
                # Add Additonal Information / output path
                df.columns = df.columns.str.lower()
                df.columns = df.columns.str.replace(".", "_", regex=False)
                instance_name = self.instance_name
                if "instance_name" not in df.columns:
                    df["instance_name"] = instance_name
                apple
                write_path = f"{instance_name}/{path}/{module_name}/{dt_year}/{dt_month}/{dt_day}/data.parquet"
                if project_key:
                    write_path = f"{instance_name}/{path}/{module_name}/{dt_year}/{dt_month}/{dt_day}/{project_key}_data.parquet"
                # Final cleanse of DF for dictionary/lists to strings
                for col in df.columns:
                    types = df[col].dropna().map(type).unique()
                    if any(t in (dict, list) for t in types):
                        df[col] = df[col].astype(str)
                df = df.reset_index(drop=True)
                # Write the output finally
                if "timestamp" not in df.columns:
                    df["timestamp"] = self.dt
                dss_folder.write_remote_folder_output(self, write_path, df)
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