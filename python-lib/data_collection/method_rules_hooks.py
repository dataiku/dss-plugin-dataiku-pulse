from __future__ import annotations

from typing import Any

import pandas as pd

from data_collection.method_rules import MethodCallContext


def build_call_kwargs_hook(method_name: str, context: MethodCallContext) -> dict[str, Any]:
    if method_name == "list_connections":
        return {}
    raise ValueError(f"No call kwargs hook defined for {method_name}")


def cleanup_payload_hook(method_name: str, payload: Any, context: MethodCallContext) -> Any:
    return payload


def cleanup_dataframe_hook(
    method_name: str,
    df: pd.DataFrame,
    context: MethodCallContext,
) -> pd.DataFrame:
    if method_name == "list_connections":
        out = df.copy()
        secret_like = [
            col
            for col in out.columns
            if any(token in col.lower() for token in ["secret", "credential", "password", "ticket"])
        ]
        if secret_like:
            out = out.drop(columns=secret_like, errors="ignore")
        return out
    return df
