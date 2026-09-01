from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd


def normalize_authvia(x: Any) -> str:
    if isinstance(x, list):
        return ", ".join(map(str, x))
    if isinstance(x, str):
        return x
    return ""


def parse_authvia(s: Any) -> tuple[str | None, str | None]:
    project_key = None
    webapp_id = None
    try:
        if not isinstance(s, str) or not s.strip():
            return (None, None)
        if "scenario=" in s:
            part = s.split("scenario=", 1)[1].split(" ")[0]
            project_key = part.split(".", 1)[0]
        elif "ticket:python_trigger:" in s:
            part = s.replace("ticket:python_trigger:", "")
            project_key = part.split(".", 1)[0]
        elif "ticket:Standard webapp backend:" in s:
            part = s.replace("ticket:Standard webapp backend: ", "")
            project_key, remainder = part.split(".", 1)
            webapp_id = remainder.split(",", 1)[0]
        elif "ticket:jupyter:" in s:
            part = s.replace("ticket:jupyter:", "")
            project_key = part.split(".", 1)[0]
        elif "ticket:job:" in s:
            part = s.replace("ticket:job:", "")
            project_key = part.split(".", 1)[0]
    except Exception:
        return (None, None)
    return (_clean_identifier(project_key), _clean_identifier(webapp_id))


_PLACEHOLDER_PATTERN = re.compile(r"^\{[^{}]+\}$")


def _clean_identifier(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    if _PLACEHOLDER_PATTERN.match(cleaned):
        return None
    return cleaned


def parse_webapps_extras(value: Any) -> tuple[str | None, str | None]:
    try:
        if isinstance(value, str):
            if not value.strip():
                return (None, None)
            payload = json.loads(value)
        elif isinstance(value, dict):
            payload = value
        else:
            return (None, None)

        if not isinstance(payload, dict):
            return (None, None)

        nested_extras = payload.get("extras")
        if isinstance(nested_extras, str) and nested_extras.strip():
            try:
                nested_payload = json.loads(nested_extras)
            except Exception:
                nested_payload = None
            if isinstance(nested_payload, dict):
                payload = {**nested_payload, **payload}

        project_key = _clean_identifier(payload.get("projectkey"))
        webapp_id = _clean_identifier(payload.get("webappid"))
        return (project_key, webapp_id)
    except Exception:
        return (None, None)


@lru_cache(maxsize=1)
def _load_mapping_df() -> pd.DataFrame:
    path = Path(__file__).resolve().parent
    return pd.read_csv(path / "mapping.csv")


def main(df: pd.DataFrame) -> pd.DataFrame:
    """Map audit log msg types to Dataiku categories.

    Expects a dataframe that includes:
    - `message_msgType`
    - `topic`
    - `timestamp` (datetime-like)

    Produces:
    - adds `dataiku_category`
    - attempts to infer `project_key` and `webapp_id` from `authvia`
    """

    mapping_df = _load_mapping_df()

    out = df.copy()

    if "topic" in out.columns:
        out = out[out["topic"] == "generic"].copy()
        if out.shape[0] == 0:
            return out

    if "message_msgType" not in out.columns:
        return pd.DataFrame()

    msgtype = out["message_msgType"]
    msgtype_mask = msgtype.notna()
    if msgtype.dtype == "object":
        msgtype_str = msgtype.astype("string")
        msgtype_mask = msgtype_mask & msgtype_str.fillna("").str.strip().ne("")
    out = out[msgtype_mask].copy()
    if out.shape[0] == 0:
        return out

    merged = pd.merge(
        out,
        mapping_df,
        on="message_msgType",
        how="left",
    )

    if "dataiku_category" in merged.columns:
        merged = merged[merged["dataiku_category"] != "DROP_DELETE"]

    if merged.shape[0] == 0:
        return merged

    # Minor cleanse
    merged.columns = [c.lower() for c in merged.columns]
    merged.columns = merged.columns.str.replace("message_", "", regex=False)

    if "dataiku_category" in merged.columns:
        merged["dataiku_category"] = merged["dataiku_category"].astype("string").str.lower()
        merged["dataiku_category"] = merged["dataiku_category"].fillna("unclassified")

    # AuthVia enrichment
    if "authvia" in merged.columns:
        merged["authvia"] = merged["authvia"].fillna("")
        merged["authvia"] = merged["authvia"].apply(normalize_authvia)
        merged[["project_key_source_call_temp", "webappid_source_call_temp"]] = pd.DataFrame(
            merged["authvia"].apply(parse_authvia).tolist(),
            index=merged.index,
        )

        if "project_key_source_call" not in merged.columns:
            merged["project_key_source_call"] = None
        merged["project_key_source_call"] = merged["project_key_source_call"].fillna(
            merged["project_key_source_call_temp"]
        )

        if "webappid_source_call" not in merged.columns:
            merged["webappid_source_call"] = None
        merged["webappid_source_call"] = merged["webappid_source_call"].fillna(merged["webappid_source_call_temp"])

        merged = merged.drop(columns=["project_key_source_call_temp", "webappid_source_call_temp"], errors="ignore")

    return merged
