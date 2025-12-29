import os
import re
import importlib
import pkgutil
import pandas as pd
import dataiku
import dataikuapi
from dataikupulse.src import dss_folder, dss_silver


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


def rename_and_move_first(df, old, new):
    if old in df.columns:
        df = df.rename(columns={old: new})
    if new in df.columns:
        cols = [new] + [c for c in df.columns if c != new]
        df = df[cols]
    return df

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
    # 0. NO PERIODS
    df.columns = df.columns.str.replace(".", "_", regex=False)
    
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
        flat["extras"] = extras if extras else None
        rows.append(flat)
    df = pd.DataFrame(rows)
    
    # 3. Add Additonal Information / output path
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


def load_flat_columns(package_name: str) -> dict:
    result = {}
    package = importlib.import_module(package_name)
    for module_info in pkgutil.iter_modules(package.__path__):
        module_name = f"{package_name}.{module_info.name}"
        module = importlib.import_module(module_name)
        if hasattr(module, "FLAT_COLUMNS"):
            result[module_info.name] = module.FLAT_COLUMNS
    return result


def _resolve_module_namespace(self, mode):
    if mode == "instance":
        from dataikupulse.base_data import instance_level as dss_objs
    elif mode == "projects":
        from dataikupulse.base_data import project_level as dss_objs
    else:
        raise ValueError(f"Unknown module mode: {mode}")
    return dss_objs


def _discover_modules(self, dss_objs):
    base_dir = dss_objs.__path__[0]
    for root, _, files in os.walk(base_dir):
        for f in files:
            if not f.endswith(".py") or f == "__init__.py":
                continue
            module_name = f.removesuffix(".py")
            category = root.replace(base_dir, "").lstrip(os.sep)
            module_path = os.path.join(root, f)
            yield category, module_name, module_path
    return


def _execute_module(self, module_name, module_path, project_handle, client_d, project_key, category, results):
    try:
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not hasattr(module, "main"):
            return pd.DataFrame()
        if project_handle:
            df = module.main(self, project_handle, client_d)
        else:
            df = module.main(self)
        results.append([project_key, category, module_name, "load/run", True, None])
        return df
    except Exception as e:
        results.append([project_key, category, module_name, "load/run", False, e])
        return pd.DataFrame()

    
def _is_valid_df(self, df):
    return isinstance(df, pd.DataFrame) and not df.empty


def _date_parts(self):
    return (str(self.dt.year), f"{self.dt.month:02d}", f"{self.dt.day:02d}",)


def _build_write_path(self, layer, category, module_name, file_name):
    year, month, day = _date_parts()
    return f"{layer}/{category}/{module_name}/{self.instance_name}/{year}/{month}/{day}/{file_name}"


def _persist_raw(self, df, category, module_name, project_key, results):
    file_name = "data.parquet"
    if project_key:
        file_name = f"{project_key}_data.parquet"
    path = _build_write_path("raw", category, module_name, file_name)
    try:
        dss_folder.write_remote_folder_output(self, path, df)
        results.append([project_key, category, module_name, "write/save -- RAW", True, None])
    except Exception as e:
        results.append([project_key, category, module_name, "write/save -- RAW", False, e])
    return

        
def _process_quality_and_persist(self, df, category, module_name, project_key, results):
    file_name = "data.parquet"
    if project_key:
        file_name = f"{project_key}_data.parquet"
    try:
        df = dss_silver.coerce_schema(df)
        dq = dss_silver.data_quality(df)
        df_report = pd.DataFrame([{
            "errors": dq["errors"],
            "warnings": dq["warnings"],
            **dq["stats"],
        }])
        if dq["errors"]:
            layer = "raw_errors"
            _write_quality_outputs(layer, category, module_name, file_name, df, df_report)
            results.append([project_key, category, module_name, f"write/save -- {layer}", False, "Check raw errors"])
        else:
            layer = "silver"
            path = _build_write_path(layer, category, module_name, file_name)
            dss_folder.write_remote_folder_output(self, path, df)
            results.append([project_key, category, module_name, f"write/save -- {layer}", True, None])
    except Exception as e:
        results.append([project_key, category, module_name, "write/save -- QUALITY", False, e])
    return

        
def _write_quality_outputs(self, layer, category, module_name, file_name, df, df_report):
    data_path = _build_write_path(layer, category, module_name, file_name)
    dq_path = _build_write_path(layer, category, module_name, f"dq_{file_name}")
    dss_folder.write_remote_folder_output(self, data_path, df)
    dss_folder.write_remote_folder_output(self, dq_path, df_report)

        

def run_modules(self, mode="instance", project_handle=None, client_d={}, project_key=None):
    dss_objs = _resolve_module_namespace(mode)
    results = []
    for category, module_name, module_path in _discover_modules(dss_objs):
        df = _execute_module(
            module_name,
            module_path,
            project_handle,
            client_d,
            project_key,
            category,
            results
        )
        if not _is_valid_df(df):
            continue
        _persist_raw(df, category, module_name, project_key, results)
        _process_quality_and_persist(df, category, module_name, project_key, results)
    return results









# EOF