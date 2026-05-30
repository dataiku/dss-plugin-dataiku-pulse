from __future__ import annotations

import pandas as pd

from .casting import (
    cast_boolean_columns,
    cast_datetime_columns,
    cast_numeric_columns,
    cast_string_columns,
)
from .column_sanitize import sanitize_columns
from .extras import pack_extras
from .flatten_config import load_flatten_config
from .schema_config import load_casting_columns


def _ensure_required_columns(df: pd.DataFrame, required: list[str]) -> pd.DataFrame:
    out = df
    for col in required:
        if col not in out.columns:
            out[col] = pd.NA
    return out


def normalize_silver(
    *,
    df: pd.DataFrame,
    instance_name: str,
    run_ts: str | None = None,
    category: str | None = None,
    module: str = "metadata",
    todo_section: str | None = None,
    flatten_base: tuple[str, str] | None = None,
    flatten_variant: str | None = None,
    include_instance_column: bool = True,
) -> pd.DataFrame:
    """Apply silver-layer normalization rules.

    Current rules:
    1) Sanitize column names (special chars -> `_`) and force lower-case.
    2) Ensure `instance_name` is the first column.
    3) If a flatten YAML exists for (category, module):
       - ensure required columns exist (null-fill)
       - roll all non-required columns into `extras` (JSON string)
    """

    out = df.copy()

    # Rule 1: sanitize + lower-case column names
    out.columns = [c.lower() for c in sanitize_columns(out.columns)]

    # Canonicalize project key: drop any prefix for `*_projectkey`.
    # Example: dataset_projectKey -> dataset_projectkey -> project_key
    if "project_key" not in out.columns and "projectkey" in out.columns:
        out = out.rename(columns={"projectkey": "project_key"})

    if "project_key" not in out.columns:
        project_key_cols = [c for c in out.columns if c.endswith("_projectkey")]
        if len(project_key_cols) == 1:
            out = out.rename(columns={project_key_cols[0]: "project_key"})
        elif len(project_key_cols) > 1:
            # Prefer the one derived from `project_*` payloads if present.
            preferred = "project_projectkey"
            if preferred in project_key_cols:
                out = out.rename(columns={preferred: "project_key"})
            else:
                out = out.rename(columns={project_key_cols[0]: "project_key"})

    # Rule 2: ensure instance_name first
    if include_instance_column:
        if "instance_name" not in out.columns:
            out.insert(0, "instance_name", instance_name)
        else:
            out["instance_name"] = instance_name
            cols = ["instance_name"] + [c for c in out.columns if c != "instance_name"]
            out = out[cols]

    # Global required: run_ts (always present as a column for SILVER)
    if run_ts is not None:
        if "run_ts" not in out.columns:
            out.insert(out.shape[1], "run_ts", run_ts)
        else:
            out["run_ts"] = run_ts

    # instance_name should always be a stripped string (no case normalization)
    out = cast_string_columns(out, ["instance_name"], uppercase=False)

    if not category:
        # No flattening config available, so only apply minimal global rules.
        # (Casting rules are applied after flattening.)
        return out

    cfg = load_flatten_config(
        category=category,
        module=module,
        todo_section=todo_section,
        base=flatten_base,
        variant=flatten_variant,
    )
    if cfg is None or not cfg.required_columns:
        # No flattening config, but still apply casting + string stripping.
        datetime_cols = load_casting_columns(name="datetime").columns
        numeric_cols = load_casting_columns(name="numeric").columns
        boolean_cols = load_casting_columns(name="boolean").columns
        upper_str_cols = load_casting_columns(name="upper_str").columns

        # Remove duplicate column names to avoid pandas datetime assembly errors.
        if out.columns.duplicated().any():
            out = out.loc[:, ~out.columns.duplicated()]

        out = cast_datetime_columns(out, ["run_ts"])
        out = cast_datetime_columns(out, [c for c in datetime_cols if c in out.columns])
        out = cast_numeric_columns(out, [c for c in numeric_cols if c in out.columns])
        out = cast_boolean_columns(out, [c for c in boolean_cols if c in out.columns])
        out = cast_string_columns(out, [c for c in upper_str_cols if c in out.columns], uppercase=True)

        already_cast = (
            set(datetime_cols)
            | set(numeric_cols)
            | set(boolean_cols)
            | set(upper_str_cols)
            | {"run_ts"}
        )
        remaining = [c for c in out.columns if c not in already_cast]
        out = cast_string_columns(out, remaining)

        return out

    required = [c for c in cfg.required_columns if c not in {"instance_name", "run_ts"}]
    required_with_globals = ["instance_name", "run_ts"] + required

    out = _ensure_required_columns(out, required_with_globals)

    if out.columns.duplicated().any():
        out = out.loc[:, ~out.columns.duplicated()]

    # Flatten + pack extras (must happen before casting)
    rows = [
        pack_extras(row=row, required_columns=required_with_globals)
        for row in out.to_dict(orient="records")
    ]
    packed = pd.DataFrame(rows)
    packed = _ensure_required_columns(packed, required_with_globals)

    # Enforce stable order before casting
    cols = ["instance_name"] + required + ["run_ts"]
    if "extras" in packed.columns:
        cols.append("extras")
    packed = packed[cols]

    # Casting happens after flattening so `extras` is stable.
    datetime_cols = load_casting_columns(name="datetime").columns
    numeric_cols = load_casting_columns(name="numeric").columns
    boolean_cols = load_casting_columns(name="boolean").columns
    upper_str_cols = load_casting_columns(name="upper_str").columns

    # Global cast: run_ts should always be a datetime
    packed = cast_datetime_columns(packed, ["run_ts"])

    # Only cast non-extras columns. Once a value is in `extras`, it is a JSON
    # string and further casting is intentionally not applied.
    non_extras_cols = [c for c in packed.columns if c != "extras"]

    packed = cast_datetime_columns(packed, [c for c in datetime_cols if c in non_extras_cols])
    packed = cast_numeric_columns(packed, [c for c in numeric_cols if c in non_extras_cols])
    packed = cast_boolean_columns(packed, [c for c in boolean_cols if c in non_extras_cols])
    packed = cast_string_columns(packed, [c for c in upper_str_cols if c in non_extras_cols], uppercase=True)

    # For all remaining flat columns (including instance_name), enforce string + strip.
    already_cast = (
        set(datetime_cols)
        | set(numeric_cols)
        | set(boolean_cols)
        | set(upper_str_cols)
        | {"run_ts"}
    )
    remaining = [c for c in non_extras_cols if c not in already_cast]
    packed = cast_string_columns(packed, remaining)

    return packed
