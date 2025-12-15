import os
import re
import importlib
import pandas as pd
import dataiku
import dataikuapi
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

def sanitize_for_parquet(value):
    # Empty dict → None
    if isinstance(value, dict):
        if len(value) == 0:
            return None

        sanitized = {}
        for k, v in value.items():
            sv = sanitize_for_parquet(v)
            sanitized[k] = sv

        return sanitized

    # Lists: sanitize elements
    if isinstance(value, list):
        return [sanitize_for_parquet(v) for v in value]

    # Everything else
    return value

def normalize_dataframe(self, df: pd.DataFrame, FLAT_COLUMNS: {}) -> pd.DataFrame:
    # 1. Ensure flat column exist
    for col in FLAT_COLUMNS:
        if col not in df.columns:
            df[col] = None
    # 2. Split flat vs extras
    rows = []
    for _, row in df.iterrows():
        row_dict = row.to_dict()
        flat = {}
        extras = {}
        for col, value in row_dict.items():
            if col in FLAT_COLUMNS:
                flat[col] = value
            else:
                if value is None or (isinstance(value, float) and pd.isna(value)):
                    continue
                # Parquet-safe normalization
                extras[col] = sanitize_for_parquet(value)
        flat["extras"] = extras
        rows.append(flat)
    df = pd.DataFrame(rows)
    
    # 3. Add Additonal Information / output path
    df.columns = df.columns.str.lower()
    df.columns = df.columns.str.replace(".", "_", regex=False)
    if "instance_name" not in df.columns:
        df.insert(
            loc=0,
            column="instance_name",
            value=self.instance_name
        )
    
    # 4. Add run_time
    df.insert(
        loc=df.columns.get_loc("extras"),
        column="run_timestamp",
        value=self.dt
    )
    
    return df


def run_modules(self, mode = "instance", project_handle = None, client_d = {}, project_key = None):
    if mode == "instance":
        from dataikupulse.base_data import instance_level as dss_objs
    elif mode == "projects":
        from dataikupulse.base_data import project_level as dss_objs
    else:
        raise Exception("Unknown Module Mode")
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
                write_path = f"{self.instance_name}/{path}/{module_name}/{dt_year}/{dt_month}/{dt_day}/data.parquet"
                if project_key:
                    write_path = f"{self.instance_name}/{path}/{module_name}/{dt_year}/{dt_month}/{dt_day}/{project_key}_data.parquet"
                dss_folder.write_remote_folder_output(self, write_path, df)
                results.append([project_key, path, module_name, "write/save", True, None])
            except Exception as e:
                results.append([project_key, path, module_name, "write/save", False, e])
    return results
# EOF