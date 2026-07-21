from __future__ import annotations

import pandas as pd


def _first_present(columns: set[str], candidates: list[str]) -> str | None:
    for name in candidates:
        if name in columns:
            return name
    return None


def _is_truthy_ui_auth_source(df: pd.DataFrame) -> pd.Series:
    col = "message_authSource"
    if col not in df.columns:
        return pd.Series(True, index=df.index)
    return df[col].astype("string").fillna("") == "USER_FROM_UI"


def main(df: pd.DataFrame) -> pd.DataFrame:
    """Build hourly formal-MAU eligibility counts from audit logs.

    Output is designed to be written by the audit runnable under:
    - category=users_formal_mau (processor name)
    - module=formal_mau (via dataiku_category value)

    Columns produced:
    - timestamp: floored-to-hour UTC timestamp
    - login
    - application_open_count
    - dataiku_category (constant: formal_mau)
    """

    if df is None or not isinstance(df, pd.DataFrame) or df.shape[0] == 0:
        return pd.DataFrame()

    out = df.copy()

    if "topic" in out.columns:
        out = out[out["topic"] == "generic"]
        if out.shape[0] == 0:
            return pd.DataFrame()

    if "message_scenarioId" in out.columns:
        out = out[out["message_scenarioId"].isna()]
    if "message_jobId" in out.columns:
        out = out[out["message_jobId"].isna()]

    out = out[_is_truthy_ui_auth_source(out)]
    if out.shape[0] == 0:
        return pd.DataFrame()

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

    ts = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
    ts = pd.Series(ts, index=out.index).dt.floor("h")
    out = out[ts.notna()].copy()
    out["timestamp"] = ts[ts.notna()]
    if out.shape[0] == 0:
        return pd.DataFrame()

    msgtype_col = _first_present(
        set(out.columns),
        ["message_msgType", "message_msgtype", "msgType", "msgtype"],
    )
    if msgtype_col is None:
        return pd.DataFrame()

    msgtype = out[msgtype_col].astype("string").fillna("").str.strip().str.lower()
    out = out[msgtype == "application-open"].copy()
    login_values = login_values.loc[out.index]
    if out.shape[0] == 0:
        return pd.DataFrame()

    out["login"] = login_values
    out["application_open_count"] = 1

    group_cols = ["instance_name", "timestamp", "login"]
    for column in group_cols:
        if column not in out.columns:
            out[column] = pd.NA

    grouped = out.groupby(group_cols, dropna=False, as_index=False).agg(
        application_open_count=("application_open_count", "sum"),
    )
    grouped["application_open_count"] = pd.to_numeric(
        grouped["application_open_count"], errors="coerce"
    ).fillna(0).astype("Int64")
    grouped["dataiku_category"] = "formal_mau"

    return grouped[
        [
            "instance_name",
            "timestamp",
            "login",
            "application_open_count",
            "dataiku_category",
        ]
    ]
