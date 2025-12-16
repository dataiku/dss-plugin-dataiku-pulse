import os
import re
import importlib
import pandas as pd
import dataiku
import dataikuapi
from dataikupulse.src import dss_folder, dss_quality


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


import pandas as pd
import numpy as np

def data_quality(df: pd.DataFrame) -> dict:
    """
    Runs data quality checks on a SILVER dataframe.
    Returns a report dict with errors and warnings.
    """

    report = {
        "errors": [],
        "warnings": [],
        "stats": {}
    }

    # -------------------------
    # 1. Timestamp sanity
    # -------------------------
    TIMESTAMP_COLS = [
        "timestamp",
        "dssStartupTimestamp",
        "creationDate",
        "last_session_activity",
        "first_commit_date",
        "last_commit_date",
        "scenarios_start",
        "scenarios_nextRun",
        "scenarios_createdOn",
        "scenarios_lastModifiedOn",
    ]

    for col in TIMESTAMP_COLS:
        if col in df.columns:
            if pd.api.types.is_float_dtype(df[col]):
                report["errors"].append(
                    f"{col} is float64 (expected datetime)"
                )

    # -------------------------
    # 2. Identifier columns must not be numeric
    # -------------------------
    ID_COLS = [
        "project_key",
        "dataset_projectKey",
        "scenarios_projectKey",
        "user",
        "email",
        "nodeId",
        "licenseId",
    ]

    for col in ID_COLS:
        if col in df.columns:
            if pd.api.types.is_numeric_dtype(df[col]):
                report["errors"].append(
                    f"{col} is numeric (expected string identifier)"
                )

    # -------------------------
    # 3. Boolean columns must be boolean dtype
    # -------------------------
    BOOL_COLS = [
        "dataset_managed",
        "dataset_featureGroup",
        "project_tutorialProject",
        "scenarios_active",
        "scenarios_unavailable",
        "scenarios_markedAsTest",
        "scenarios_running",
        "enabled",
    ]

    for col in BOOL_COLS:
        if col in df.columns:
            if not pd.api.types.is_bool_dtype(df[col]) \
               and not pd.api.types.is_boolean_dtype(df[col]):
                report["errors"].append(
                    f"{col} is not boolean dtype"
                )

    # -------------------------
    # 4. Extras column validation
    # -------------------------
    if "extras" in df.columns:
        invalid_extras = df["extras"].map(
            lambda x: not (x is None or isinstance(x, dict))
        )

        if invalid_extras.any():
            report["errors"].append(
                f"extras column contains invalid values (non-dict, non-null)"
            )

    # -------------------------
    # 5. Enum validation
    # -------------------------
    ENUM_RULES = {
        "severity": {"INFO", "WARN", "ERROR"},
        "msgtypebase": {"admin", "code", "generic", "automation"},
    }

    for col, allowed in ENUM_RULES.items():
        if col in df.columns:
            bad_vals = (
                set(df[col].dropna().unique()) - allowed
            )
            if bad_vals:
                report["warnings"].append(
                    f"{col} has unexpected values: {bad_vals}"
                )

    # -------------------------
    # 6. Numeric sanity checks
    # -------------------------
    if "used_pct" in df.columns:
        bad_pct = df[
            (df["used_pct"] < 0) | (df["used_pct"] > 100)
        ]
        if not bad_pct.empty:
            report["errors"].append(
                "used_pct outside range 0–100"
            )

    if {"used", "size"}.issubset(df.columns):
        bad_usage = df[df["used"] > df["size"]]
        if not bad_usage.empty:
            report["errors"].append(
                "used > size detected"
            )

    # -------------------------
    # Stats
    # -------------------------
    report["stats"] = {
        "row_count": len(df),
        "column_count": len(df.columns),
        "error_count": len(report["errors"]),
        "warning_count": len(report["warnings"]),
    }

    return report


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
            category = path[1:]
            try:
                spec = importlib.util.spec_from_file_location(module_name, fp)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                if hasattr(module, 'main'):
                    if project_handle: # project level stuff
                        df = module.main(self, project_handle, client_d)
                    else:
                        df = module.main(self) # Instance level
                    results.append([project_key, category, module_name, "load/run", True, None])
            except Exception as e:
                df = pd.DataFrame()
                results.append([project_key, category, module_name, "load/run", False, e])
            if not isinstance(df, pd.DataFrame) or df.empty:
                continue # nothing to write, skip
            try:
                dt_year  = str(self.dt.year)
                dt_month = str(f'{self.dt.month:02d}')
                dt_day   = str(f'{self.dt.day:02d}')
                file_name = "data.parquet" 
                if project_key:
                    file_name = f"{project_key}_data.parquet"
                write_path = f"raw/{category}/{module_name}/{self.instance_name}/{dt_year}/{dt_month}/{dt_day}/{file_name}"
                dss_folder.write_remote_folder_output(self, write_path, df)
                results.append([project_key, category, module_name, "write/save", True, None])
            except Exception as e:
                results.append([project_key, category, module_name, "write/save", False, e])
    return results





# EOF