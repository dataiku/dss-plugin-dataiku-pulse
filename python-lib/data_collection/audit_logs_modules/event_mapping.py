from __future__ import annotations

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
    return (project_key, webapp_id)


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

    path = Path(__file__).resolve().parent
    mapping_df = pd.read_csv(path / "mapping.csv")

    out = df.copy()

    if "topic" in out.columns:
        out = out[out["topic"] == "generic"].reset_index(drop=True)

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

    # AuthVia enrichment
    if "authvia" in merged.columns:
        merged["authvia"] = merged["authvia"].fillna("")
        merged["authvia"] = merged["authvia"].apply(normalize_authvia)
        merged[["project_key_temp", "webapp_id_temp"]] = pd.DataFrame(
            merged["authvia"].apply(parse_authvia).tolist(),
            index=merged.index,
        )

        if "project_key" not in merged.columns:
            merged["project_key"] = None
        merged["project_key"] = merged["project_key"].fillna(merged["project_key_temp"])

        if "webapp_id" not in merged.columns:
            merged["webapp_id"] = None
        merged["webapp_id"] = merged["webapp_id"].fillna(merged["webapp_id_temp"])

        merged = merged.drop(columns=["project_key_temp", "webapp_id_temp"], errors="ignore")

    return merged
