from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, date
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import yaml

from data_collection.data_normalizer import check_silver_dq, normalize_silver
from data_collection.helper import (
    DSSFolderTarget,
    OutputLayout,
    build_error_row,
    filter_payload_by_delta,
    raw_to_dataframe,
    upload_json,
    upload_json_gzip,
    upload_parquet,
)


@dataclass(frozen=True)
class MethodRule:
    method_name: str
    enabled: bool = True
    call_mode: str = "auto"
    kwargs: dict[str, Any] = field(default_factory=dict)
    cleanup_mode: str = "none"
    delta_mode: str = "auto"
    payload_drop_keys: list[str] = field(default_factory=list)
    df_drop_columns: list[str] = field(default_factory=list)
    df_rename_columns: dict[str, str] = field(default_factory=dict)
    df_fillna: dict[str, Any] = field(default_factory=dict)
    df_cast_columns: dict[str, str] = field(default_factory=dict)
    artifact_key: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class MethodCallContext:
    scope: str
    instance_name: str
    run_ts: str
    param_set: dict[str, Any]
    project_key: str | None = None
    worker_project_key: str | None = None
    since: datetime | None = None


@dataclass(frozen=True)
class MethodCollectResult:
    method_name: str
    scope: str
    status: str
    rows_raw: int
    rows_silver: int
    duration_ms: int
    message: str | None = None
    rule_mode: str = "auto"
    flatten_config_missing: bool = False


RuleHook = Callable[[str, Any, MethodCallContext], Any]


def _rules_path(scope: str) -> Path:
    return Path(__file__).resolve().parent / "collection_exclusions" / f"{scope}_method_rules.yaml"


def load_method_rules(scope: str) -> dict[str, MethodRule]:
    path = _rules_path(scope)
    if not path.exists():
        return {}

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    methods = raw.get("methods") or {}
    if not isinstance(methods, dict):
        raise ValueError(f"Expected 'methods' mapping in {path}")

    rules: dict[str, MethodRule] = {}
    for method_name, spec in methods.items():
        if not isinstance(spec, dict):
            raise ValueError(f"Expected mapping for method {method_name!r} in {path}")
        rules[str(method_name)] = MethodRule(
            method_name=str(method_name),
            enabled=bool(spec.get("enabled", True)),
            call_mode=str(spec.get("call_mode", "auto") or "auto"),
            kwargs=dict(spec.get("kwargs") or {}),
            cleanup_mode=str(spec.get("cleanup_mode", "none") or "none"),
            delta_mode=str(spec.get("delta_mode", "auto") or "auto"),
            payload_drop_keys=[str(v) for v in (spec.get("payload_drop_keys") or [])],
            df_drop_columns=[str(v) for v in (spec.get("df_drop_columns") or [])],
            df_rename_columns={str(k): str(v) for k, v in dict(spec.get("df_rename_columns") or {}).items()},
            df_fillna=dict(spec.get("df_fillna") or {}),
            df_cast_columns={str(k): str(v) for k, v in dict(spec.get("df_cast_columns") or {}).items()},
            artifact_key=(str(spec.get("artifact_key")) if spec.get("artifact_key") else None),
            notes=(str(spec.get("notes")) if spec.get("notes") else None),
        )
    return rules


def resolve_method_rule(method_name: str, rules: dict[str, MethodRule]) -> MethodRule:
    return rules.get(method_name, MethodRule(method_name=method_name))


def build_call_kwargs(rule: MethodRule, context: MethodCallContext) -> dict[str, Any]:
    if rule.call_mode in {"auto", "no_args"}:
        return {}
    if rule.call_mode == "fixed_kwargs":
        return dict(rule.kwargs)
    if rule.call_mode == "python_hook":
        from data_collection.method_rules_hooks import build_call_kwargs_hook

        return dict(build_call_kwargs_hook(rule.method_name, context))
    raise ValueError(f"Unsupported call_mode for {rule.method_name}: {rule.call_mode}")


def cleanup_payload(rule: MethodRule, payload: Any, context: MethodCallContext) -> Any:
    if rule.cleanup_mode == "none":
        return payload
    if rule.cleanup_mode == "rename_drop_cast":
        if isinstance(payload, dict) and rule.payload_drop_keys:
            return {k: v for k, v in payload.items() if k not in set(rule.payload_drop_keys)}
        return payload
    if rule.cleanup_mode == "python_hook":
        from data_collection.method_rules_hooks import cleanup_payload_hook

        return cleanup_payload_hook(rule.method_name, payload, context)
    raise ValueError(f"Unsupported cleanup_mode for {rule.method_name}: {rule.cleanup_mode}")


def cleanup_dataframe(rule: MethodRule, df: pd.DataFrame, context: MethodCallContext) -> pd.DataFrame:
    if df.empty:
        return df

    out = df.copy()
    if rule.cleanup_mode == "python_hook":
        from data_collection.method_rules_hooks import cleanup_dataframe_hook

        return cleanup_dataframe_hook(rule.method_name, out, context)

    if rule.cleanup_mode in {"none", "rename_drop_cast"}:
        if rule.df_drop_columns:
            out = out.drop(columns=rule.df_drop_columns, errors="ignore")
        if rule.df_rename_columns:
            out = out.rename(columns=rule.df_rename_columns)
        for col, value in rule.df_fillna.items():
            if col in out.columns:
                out[col] = out[col].fillna(value)
        for col, dtype_name in rule.df_cast_columns.items():
            if col not in out.columns:
                continue
            if dtype_name == "string":
                out[col] = out[col].astype("string")
            elif dtype_name == "boolean":
                out[col] = out[col].astype("boolean")
            elif dtype_name == "Int64":
                out[col] = pd.to_numeric(out[col], errors="coerce").astype("Int64")
            elif dtype_name == "float64":
                out[col] = pd.to_numeric(out[col], errors="coerce")
            else:
                out[col] = out[col].astype(dtype_name)
        return out

    raise ValueError(f"Unsupported cleanup_mode for {rule.method_name}: {rule.cleanup_mode}")


def build_artifact_paths(
    *,
    layout: OutputLayout,
    method_name: str,
    instance_name: str,
    run_date: date,
    file_key: str,
) -> dict[str, Path]:
    return {
        "raw": layout.project_data_path("raw", method_name, instance_name, run_date, file_key, "json.gz"),
        "raw_error": layout.project_data_path("raw_errors", method_name, instance_name, run_date, file_key, "json"),
        "silver": layout.project_data_path("silver", method_name, instance_name, run_date, file_key, "parquet"),
        "silver_fail": layout.project_data_path("silver_fail", method_name, instance_name, run_date, file_key, "parquet"),
        "silver_fail_reason": layout.project_data_path("silver_fail", method_name, instance_name, run_date, file_key, "dq.json"),
    }


def collect_method_output(
    *,
    fn: Any,
    method_name: str,
    file_key: str,
    layout: OutputLayout,
    target: DSSFolderTarget,
    context: MethodCallContext,
    run_date: date,
    todo_section: str,
    debug_dir: Path | None = None,
) -> MethodCollectResult:
    import json
    import time

    started = time.time()
    rule = resolve_method_rule(method_name, load_method_rules(context.scope))
    artifact_name = rule.artifact_key or method_name
    paths = build_artifact_paths(
        layout=layout,
        method_name=artifact_name,
        instance_name=context.instance_name,
        run_date=run_date,
        file_key=file_key,
    )

    if not rule.enabled:
        return MethodCollectResult(method_name, context.scope, "excluded_by_rule", 0, 0, 0, rule_mode=rule.call_mode)

    try:
        kwargs = build_call_kwargs(rule, context)
    except Exception as exc:
        return MethodCollectResult(
            method_name,
            context.scope,
            "needs_rule",
            0,
            0,
            int((time.time() - started) * 1000),
            message=repr(exc),
            rule_mode=rule.call_mode,
        )

    try:
        payload = fn(**kwargs)
        payload = cleanup_payload(rule, payload, context)

        if payload is None:
            return MethodCollectResult(method_name, context.scope, "empty_payload", 0, 0, int((time.time() - started) * 1000), rule_mode=rule.call_mode)
        if isinstance(payload, (list, tuple, set)) and len(payload) == 0:
            return MethodCollectResult(method_name, context.scope, "empty_payload", 0, 0, int((time.time() - started) * 1000), rule_mode=rule.call_mode)
        if isinstance(payload, dict) and len(payload) == 0:
            return MethodCollectResult(method_name, context.scope, "empty_payload", 0, 0, int((time.time() - started) * 1000), rule_mode=rule.call_mode)

        prefix = f"{layout.prefix_base(artifact_name)}_"
        raw_df = raw_to_dataframe(payload, prefix=prefix)
        raw_df.attrs["pulse_raw_payload"] = payload
        raw_df = cleanup_dataframe(rule, raw_df, context)
        if raw_df.shape[0] == 0:
            return MethodCollectResult(method_name, context.scope, "empty_dataframe", 0, 0, int((time.time() - started) * 1000), rule_mode=rule.call_mode)

        filtered_payload = payload
        if context.scope == "project" and context.since is not None and rule.delta_mode != "disabled":
            maybe_filtered = filter_payload_by_delta(payload=payload, raw_df=raw_df, since=context.since)
            if maybe_filtered is not None:
                if isinstance(maybe_filtered, list) and len(maybe_filtered) == 0:
                    return MethodCollectResult(method_name, context.scope, "filtered_by_delta", int(raw_df.shape[0]), 0, int((time.time() - started) * 1000), rule_mode=rule.call_mode)
                filtered_payload = maybe_filtered
                raw_df = cleanup_dataframe(rule, raw_to_dataframe(filtered_payload, prefix=prefix), context)
            elif rule.delta_mode == "required":
                return MethodCollectResult(method_name, context.scope, "needs_rule", int(raw_df.shape[0]), 0, int((time.time() - started) * 1000), message="delta_required_but_no_timestamp", rule_mode=rule.call_mode)
            elif debug_dir is not None:
                debug_dir.mkdir(parents=True, exist_ok=True)
                out_path = debug_dir / f"{file_key}__{method_name}__missing_timestamps.json"
                out_path.write_text(
                    json.dumps(
                        {
                            "file_key": file_key,
                            "method_name": method_name,
                            "columns": [str(c) for c in raw_df.columns],
                            "rows": int(raw_df.shape[0]),
                            "sample": raw_df.head(50).to_dict("records"),
                        },
                        ensure_ascii=False,
                        indent=2,
                        default=str,
                    ),
                    encoding="utf-8",
                )

        upload_json_gzip(target=target, output_path=paths["raw"], output_base_dir=layout.base_dir, payload=filtered_payload)

        category = layout.category_name(artifact_name)
        silver_stats: dict = {}
        silver_df = normalize_silver(
            df=raw_df,
            instance_name=context.instance_name,
            run_ts=context.run_ts,
            category=category,
            module=layout.module,
            todo_section=todo_section,
            stats_out=silver_stats,
        )
        flatten_missing = bool(silver_stats.get("flatten_config_missing"))
        dq = check_silver_dq(
            silver_df,
            flatten_required=silver_stats.get("required_columns") or None,
        )
        if dq.ok:
            upload_parquet(
                target=target,
                output_path=paths["silver"],
                output_base_dir=layout.base_dir,
                df=silver_df,
                compression="snappy",
            )
            return MethodCollectResult(
                method_name,
                context.scope,
                "silver_written",
                int(raw_df.shape[0]),
                int(silver_df.shape[0]),
                int((time.time() - started) * 1000),
                message=("flatten_config_missing" if flatten_missing else None),
                rule_mode=rule.call_mode,
                flatten_config_missing=flatten_missing,
            )

        upload_parquet(
            target=target,
            output_path=paths["silver_fail"],
            output_base_dir=layout.base_dir,
            df=silver_df,
            compression="snappy",
        )
        upload_json(
            target=target,
            output_path=paths["silver_fail_reason"],
            output_base_dir=layout.base_dir,
            payload={
                "instance_name": context.instance_name,
                "project_key": context.project_key,
                "run_ts": context.run_ts,
                "method_name": method_name,
                "scope": context.scope,
                "rows": int(silver_df.shape[0]),
                "cols": int(silver_df.shape[1]),
                "dq_errors": dq.errors,
            },
        )
        return MethodCollectResult(
            method_name,
            context.scope,
            "silver_failed_dq",
            int(raw_df.shape[0]),
            int(silver_df.shape[0]),
            int((time.time() - started) * 1000),
            message=str(dq.errors),
            rule_mode=rule.call_mode,
            flatten_config_missing=flatten_missing,
        )
    except Exception as exc:
        err_df = build_error_row(
            error=exc,
            instance_name=context.instance_name,
            project_key=context.project_key or file_key,
            run_ts=context.run_ts,
        )
        upload_json(
            target=target,
            output_path=paths["raw_error"],
            output_base_dir=layout.base_dir,
            payload={
                "instance_name": context.instance_name,
                "project_key": context.project_key,
                "run_ts": context.run_ts,
                "method_name": method_name,
                "scope": context.scope,
                "error": repr(exc),
            },
        )
        upload_parquet(
            target=target,
            output_path=paths["silver"],
            output_base_dir=layout.base_dir,
            df=err_df,
            write_empty=True,
            compression="snappy",
        )
        return MethodCollectResult(
            method_name,
            context.scope,
            "call_failed",
            0,
            0,
            int((time.time() - started) * 1000),
            message=repr(exc),
            rule_mode=rule.call_mode,
        )
