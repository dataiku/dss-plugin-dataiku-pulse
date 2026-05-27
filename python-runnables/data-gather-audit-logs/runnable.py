from datetime import datetime, timedelta
import json
import logging
import os
import time
from pathlib import Path

import pandas as pd
import dataiku

from dataiku.runnables import ResultTable, Runnable

from pulse_modules.domain.audit_log import event_mapping, user_login
from pulse_modules.helpers import dss_funcs

MAX_CHUNK_BYTES = 20 * 1024 * 1024


def find_recent_files(file_list, hours=100):
    recent_files = []
    cutoff = time.time() - (hours * 3600)  # seconds
    for file in file_list:
        path = Path(file)
        if path.exists():
            last_modified = path.stat().st_mtime
            if last_modified >= cutoff:
                recent_files.append(path)
    return recent_files


def run_module(self, module, df):
    results = []
    r = module.main(self, df)
    if all(isinstance(x, list) for x in results):
        for l in r:
            results.append(l)
    else:
        results.append(r)
    return results


def _iter_jsonl_df_chunks(file_path: Path, *, max_bytes: int = MAX_CHUNK_BYTES):
    """Yield DataFrames from a JSONL file in ~max_bytes chunks."""
    chunk_lines = []
    chunk_bytes = 0
    with file_path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            chunk_lines.append(line)
            chunk_bytes += len(line.encode("utf-8"))
            if chunk_bytes >= max_bytes:
                yield _parse_jsonl_lines_to_df(chunk_lines)
                chunk_lines = []
                chunk_bytes = 0

    if chunk_lines:
        yield _parse_jsonl_lines_to_df(chunk_lines)


def _parse_jsonl_lines_to_df(lines: list[str]) -> pd.DataFrame:
    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except Exception:
            # Best-effort: skip malformed lines
            continue
    return pd.DataFrame.from_records(records)


class MyRunnable(Runnable):

    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config
        self.plugin_config = plugin_config
        self.params = plugin_config.get("pulse_primary", {})
        self.preset_pc = dss_funcs.get_preset_pc(self, "DATAIKU-PULSE")
        self.local_client = dss_funcs.build_local_client()
        self.remote_client = dss_funcs.build_remote_client(self)
        self.instance_name = dss_funcs.get_instance_id(self)
        self.dt = datetime.utcnow()
        
        logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.ERROR)
        self.logger = logging.getLogger(__name__)
        
    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        results = []

        # change directory and get audit logs
        root_path = self.local_client.get_instance_info().raw["dataDirPath"]
        audit_path = f"{root_path}/run/audit"
        os.chdir(audit_path)
        directory_path = "./"
        logs = [f for f in os.listdir(directory_path) if os.path.isfile(os.path.join(directory_path, f))]
        
        # get the cache timestamp and latest logs
        project_handle = self.local_client.get_default_project()
        variables = project_handle.get_variables()
        try:
            last_update = variables["local"]["audit_logs_cache"]
        except:
            last_update = str(datetime.now().astimezone() - timedelta(days=1))
        last_update = pd.to_datetime(last_update)
        time_diff = datetime.now().astimezone() - last_update
        hours = int(round((time_diff.total_seconds() / 3600) + 1, 0))
        logs = find_recent_files(logs, hours=hours)
        results.append(["SETUP", "Parse Latest Logs", True, None])

        # Open each remaining log for parsing (stream in chunks)
        max_ts_seen = pd.to_datetime(last_update, utc=True, errors="coerce")
        last_update_utc = pd.to_datetime(last_update, utc=True, errors="coerce")
        chunks_processed = 0
        rows_processed = 0
        logs_processed = 0

        for log in logs:
            logs_processed += 1
            for df in _iter_jsonl_df_chunks(Path(log)):
                if df is None or df.empty:
                    continue
                chunks_processed += 1

                # Column Cleanse (timestamp filter early)
                if "timestamp" not in df.columns:
                    continue
                df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
                df = df.loc[df["timestamp"] >= last_update_utc]
                if df.empty:
                    continue
                rows_processed += len(df)

                # Expand Messages and join
                if "message" in df.columns:
                    jdf = pd.json_normalize(df["message"]).add_prefix("message_").reset_index(drop=True)
                else:
                    jdf = pd.DataFrame()
                df = df.drop(columns=["message", "mdc"], errors="ignore").reset_index(drop=True)
                if not jdf.empty:
                    df = pd.concat([df, jdf], axis=1)

                df["date"] = df["timestamp"].dt.date
                df["instance_name"] = self.instance_name
                if "message_projectKey" in df.columns:
                    df = df.rename(columns={"message_projectKey": "message_project_key"})
                if "message_login" in df.columns:
                    df = df.rename(columns={"message_login": "message_logged_in"})
                if "message_authUser" in df.columns:
                    df = df.rename(columns={"message_authUser": "message_login"})

                results.append(["SETUP", f"Process Audit Log Chunk ({Path(log).name})", True, None])

                # Module Import
                results += run_module(self, user_login, df)
                results += run_module(self, event_mapping, df)

                ts_max = df["timestamp"].max()
                if pd.notna(ts_max) and (max_ts_seen is None or ts_max > max_ts_seen):
                    max_ts_seen = ts_max

        results.append(["SETUP", "Gather Audit Logs", True, None])
        results.append(["STATS", f"Logs processed: {logs_processed}", True, None])
        results.append(["STATS", f"Chunks processed: {chunks_processed}", True, None])
        results.append(["STATS", f"Rows processed: {rows_processed}", True, None])

        # Reset the audit_log_cache df
        if pd.notna(max_ts_seen) and pd.notna(last_update_utc) and max_ts_seen > last_update_utc:
            variables["local"]["audit_logs_cache"] = str(max_ts_seen)
            project_handle.set_variables(variables)
            results.append(["POST", "Set New Audit Log Cache timestamp", True, str(max_ts_seen)])
        else:
            results.append(["POST", "Set New Audit Log Cache timestamp", True, str(last_update)])
        
        # return results
        if results:
            df = pd.DataFrame(results, columns=["prcoess", "step", "result", "message"])
            df = df.astype(str)
            rt = ResultTable()
            n = 1
            for col in df.columns:
                rt.add_column(n, col, "STRING")
                n +=1
            for index, row in df.iterrows():
                rt.add_record(row.tolist())
            return rt
        else:
            raise Exception("Something went wrong")
