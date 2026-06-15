from __future__ import annotations

import re
from dataclasses import dataclass
from importlib.resources import as_file, files
from pathlib import Path
import pandas as pd
import yaml


@dataclass(frozen=True)
class UsersActivityVocab:
    action_words: tuple[str, ...]
    remove_words: tuple[str, ...]

    @property
    def action_pattern(self) -> re.Pattern[str]:
        if not self.action_words:
            return re.compile(r"$^")
        return re.compile("|".join(re.escape(w) for w in self.action_words), flags=re.IGNORECASE)

    @property
    def remove_pattern(self) -> re.Pattern[str]:
        if not self.remove_words:
            return re.compile(r"$^")
        return re.compile("|".join(re.escape(w) for w in self.remove_words), flags=re.IGNORECASE)


def _load_vocab() -> UsersActivityVocab:
    res = files("data_collection.audit_logs_modules").joinpath("users_activity_vocab.yaml")
    with as_file(res) as p:
        payload = yaml.safe_load(Path(p).read_text(encoding="utf-8"))

    if not isinstance(payload, dict):
        payload = {}

    def _list(key: str) -> tuple[str, ...]:
        raw = payload.get(key, [])
        if raw is None:
            return ()
        if not isinstance(raw, list):
            return ()
        out: list[str] = []
        for item in raw:
            s = str(item).strip()
            if s:
                out.append(s)
        return tuple(out)

    return UsersActivityVocab(
        action_words=_list("action_words"),
        remove_words=_list("remove_words"),
    )


def _first_present(columns: set[str], candidates: list[str]) -> str | None:
    for name in candidates:
        if name in columns:
            return name
    return None


def _is_truthy_ui_auth_source(df: pd.DataFrame) -> pd.Series:
    # Legacy behavior: retain only USER_FROM_UI actions.
    col = "message_authSource"
    if col not in df.columns:
        return pd.Series(True, index=df.index)
    return df[col].astype("string").fillna("") == "USER_FROM_UI"


def main(df: pd.DataFrame) -> pd.DataFrame:
    """Build hourly user activity counts from audit logs.

    Output is designed to be written by the audit runnable under:
    - category=users (processor name)
    - module=user_activity (via dataiku_category value)

    Columns produced:
    - timestamp: floored-to-hour UTC timestamp
    - login
    - project_key (nullable)
    - viewing_actions_count
    - developing_actions_count
    - dataiku_category (constant: user_activity)
    """

    if df is None or not isinstance(df, pd.DataFrame) or df.shape[0] == 0:
        return pd.DataFrame()

    out = df.copy()

    if "topic" in out.columns:
        out = out[out["topic"] == "generic"]
        if out.shape[0] == 0:
            return pd.DataFrame()

    # Best-effort base filters (match legacy intent)
    if "message_scenarioId" in out.columns:
        out = out[out["message_scenarioId"].isna()]
    if "message_jobId" in out.columns:
        out = out[out["message_jobId"].isna()]

    out = out[_is_truthy_ui_auth_source(out)]

    login_col = _first_present(
        set(out.columns),
        [
            "message_login",
            "message_user",
            "message_authUser",
            "mdc_user",
            "login",
        ],
    )
    if login_col is None:
        return pd.DataFrame()

    login_values = out[login_col].astype("string")
    out = out[login_values.notna() & (login_values.str.len() > 0)].copy()
    login_values = login_values.loc[out.index]

    if out.shape[0] == 0:
        return pd.DataFrame()

    if "timestamp" not in out.columns:
        return pd.DataFrame()

    # Ensure UTC + floor to hour.
    ts = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
    ts = pd.Series(ts, index=out.index).dt.floor("h")
    out = out[ts.notna()].copy()
    out["timestamp"] = ts[ts.notna()]

    if out.shape[0] == 0:
        return pd.DataFrame()

    project_key_col = _first_present(
        set(out.columns),
        [
            "message_project_key",
            "message_projectKey",
            "project_key",
            "projectKey",
        ],
    )

    if project_key_col is None:
        out["project_key"] = pd.NA
    else:
        out["project_key"] = out[project_key_col]

    msgtype_col = _first_present(set(out.columns), ["message_msgType", "message_msgtype", "msgType", "msgtype"])
    if msgtype_col is None:
        return pd.DataFrame()

    msgtype = out[msgtype_col].astype("string").fillna("")
    msgtype_base_col = _first_present(set(out.columns), ["message_msgTypeBase", "message_msgtypebase", "msgTypeBase", "msgtypebase"])
    if msgtype_base_col is not None:
        msgtype_base = out[msgtype_base_col].astype("string").fillna("").str.lower()
    else:
        msgtype_base = pd.Series("", index=out.index, dtype="string")

    vocab = _load_vocab()
    mutating_bases = {
        "action",
        "add",
        "admin",
        "create",
        "delete",
        "edit",
        "import",
        "run",
        "save",
        "update",
        "upload",
    }
    base_is_developing = msgtype_base.isin(mutating_bases)
    text_is_developing = msgtype.str.contains(vocab.action_pattern) & ~msgtype.str.contains(vocab.remove_pattern)
    is_developing = base_is_developing | text_is_developing

    # Option 1: viewing count is all retained UI actions.
    out["viewing_actions_count"] = 1
    out["developing_actions_count"] = is_developing.astype("int64")

    # Normalize output columns before grouping.
    out["login"] = login_values

    group_cols = ["instance_name", "timestamp", "login", "project_key"]
    for c in group_cols:
        if c not in out.columns:
            # Should not happen for instance_name, but be safe.
            out[c] = pd.NA

    grouped = out.groupby(group_cols, dropna=False, as_index=False).agg(
        viewing_actions_count=("viewing_actions_count", "sum"),
        developing_actions_count=("developing_actions_count", "sum"),
    )

    grouped["dataiku_category"] = "user_activity"

    # Stable column ordering.
    return grouped[
        [
            "instance_name",
            "timestamp",
            "login",
            "project_key",
            "viewing_actions_count",
            "developing_actions_count",
            "dataiku_category",
        ]
    ]
