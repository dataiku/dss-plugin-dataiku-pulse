from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from data_collection.method_rules import MethodCallContext


logger = logging.getLogger(__name__)


def _activity_rows(client: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for user in client.list_users_activity():
        if hasattr(user, "get_raw"):
            rows.append(dict(user.get_raw()))
        elif isinstance(user, dict):
            rows.append(dict(user))
        else:
            raw = getattr(user, "raw", None)
            if isinstance(raw, dict):
                rows.append(dict(raw))
            else:
                rows.append(dict(vars(user)))
    return rows


def _get_column_names_from_schema(schema: list[dict[str, Any]]) -> list[str]:
    return [str(col["name"]) for col in schema if "name" in col]


def _commit_dates_df(client: Any, worker_project_key: str | None) -> pd.DataFrame:
    if not worker_project_key:
        return pd.DataFrame(columns=["login", "first_commit_date", "last_commit_date"])

    try:
        project_handle = client.get_project(project_key=worker_project_key)
        dataset_handle = project_handle.get_dataset("dss_commits")
        schema = dataset_handle.get_schema().get("columns", [])
        columns = _get_column_names_from_schema(schema)
        raw_data = dataset_handle.iter_rows()
        commits_df = pd.DataFrame(raw_data, columns=columns)
    except Exception:
        logger.exception("Failed to read dss_commits from worker project %s", worker_project_key)
        return pd.DataFrame(columns=["login", "first_commit_date", "last_commit_date"])

    if commits_df.empty or "author" not in commits_df.columns or "timestamp" not in commits_df.columns:
        return pd.DataFrame(columns=["login", "first_commit_date", "last_commit_date"])

    grouped = commits_df.groupby("author", dropna=True)["timestamp"].agg(["min", "max"]).reset_index()
    if grouped.empty:
        return pd.DataFrame(columns=["login", "first_commit_date", "last_commit_date"])

    grouped = grouped.rename(columns={"author": "login", "min": "first_commit_date", "max": "last_commit_date"})
    grouped["first_commit_date"] = pd.to_datetime(grouped["first_commit_date"], unit="ms", utc=True, errors="coerce")
    grouped["last_commit_date"] = pd.to_datetime(grouped["last_commit_date"], unit="ms", utc=True, errors="coerce")
    return grouped


def cleanup_list_users_payload(payload: Any, context: MethodCallContext) -> Any:
    """Enrich instance-level `list_users` payload with activity and commit dates.

    Preserves all columns returned by `list_users()` and left-joins all columns
    returned by `list_users_activity()` on `login`. When available, also adds
    `first_commit_date` / `last_commit_date` from the worker project's
    `dss_commits` dataset.
    """

    if not isinstance(payload, list):
        return payload

    if context.client is None:
        return payload

    users_df = pd.DataFrame(payload)
    if users_df.empty or "login" not in users_df.columns:
        return payload

    merged_df = users_df

    activity_rows = _activity_rows(context.client)
    if activity_rows:
        activity_df = pd.DataFrame(activity_rows)
        if not activity_df.empty and "login" in activity_df.columns:
            merged_df = merged_df.merge(activity_df, on="login", how="left")

    commit_dates_df = _commit_dates_df(context.client, context.worker_project_key)
    if not commit_dates_df.empty:
        merged_df = merged_df.merge(commit_dates_df, on="login", how="left")

    return merged_df.to_dict(orient="records")



def cleanup_list_users_dataframe(df: pd.DataFrame, context: MethodCallContext) -> pd.DataFrame:
    """Return normalized instance-level `list_users` dataframe unchanged."""

    return df
