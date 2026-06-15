from __future__ import annotations

import logging

import pandas as pd
from datetime import datetime, timezone
from pathlib import Path

from dataiku.runnables import ResultTable, Runnable

from data_collection.data_collection.instance import get_instance_name
from data_collection.data_collection.introspection import get_noarg_list_methods
from data_collection.exclusion_config import load_exclusions, load_inclusions
from data_collection.helper import (
    OutputLayout,
    PulseMacroContext,
    build_context,
    ensure_output_folder,
    resolve_worker_project_key,
)
from data_collection.method_rules import MethodCallContext, MethodCollectResult, collect_method_output

from data_collection.data_normalizer import check_silver_dq, normalize_silver
from data_collection.helper import upload_json_gzip, upload_parquet

logger = logging.getLogger(__name__)


def get_custom_instance_methods(client) -> dict[str, object]:
    """Return curated non-`list_*` instance methods to collect."""

    methods: dict[str, object] = {}

    licensing_fn = getattr(client, "get_licensing_status", None)
    if callable(licensing_fn):
        methods["get_licensing_status"] = licensing_fn

    return methods


class LicenseOutputLayout(OutputLayout):
    @staticmethod
    def category_name(list_method_name: str) -> str:
        return "license"


def _license_status_row(payload):
    base = payload.get("base") or {}
    license_content = base.get("licenseContent") or {}
    licensee = license_content.get("licensee") or {}
    properties = license_content.get("properties") or {}
    row = {
        "instance_id": license_content.get("instanceId"),
        "license_kind": license_content.get("licenseKind"),
        "license_id": license_content.get("licenseId"),
        "has_license": base.get("hasLicense"),
        "valid": base.get("valid"),
        "expired": base.get("expired"),
        "community": base.get("community"),
        "fallback_profile": base.get("fallbackProfile"),
        "expires_on": base.get("expiresOn"),
        "licensee_company": licensee.get("company"),
        "licensee_name": licensee.get("name"),
        "standard_offer": properties.get("standardOffer"),
        "emitted_by": properties.get("emittedBy"),
        "emitted_on": properties.get("emittedOn"),
    }

    return [row]


def _to_bool(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return None


def _to_int(value):
    if value is None or value == "":
        return None
    if isinstance(value, str) and value.strip().lower() == "unlimited":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _camel_to_profile(key):
    body = key.removeprefix("max")
    parts = []
    current = []
    for char in body:
        if char.isupper() and current:
            parts.append("".join(current))
            current = [char]
        else:
            current.append(char)
    if current:
        parts.append("".join(current))

    mapping = {
        "Full Designers": "FULL_DESIGNER",
        "Advanced Analytics Designers": "ADVANCED_ANALYTICS_DESIGNER",
        "Data Designers": "DATA_DESIGNER",
        "Governance Managers": "GOVERNANCE_MANAGER",
        "Readers": "READER",
        "AI Consumers": "AI_CONSUMER",
        "AI Access Users": "AI_ACCESS_USER",
        "Technical Accounts": "TECHNICAL_ACCOUNT",
    }
    label = " ".join(parts)
    return mapping.get(label, label.upper().replace(" ", "_"))


def _max_license_rows(payload):
    base = payload.get("base") or {}
    profile_limits = base.get("profileLimits") or {}
    rows = []

    if isinstance(profile_limits, dict) and profile_limits:
        for profile_name, profile_payload in profile_limits.items():
            profile_payload = profile_payload or {}
            licensed = profile_payload.get("licensed") or {}
            resolved_profile = (
                licensed.get("profile")
                or profile_payload.get("profile")
                or str(profile_name).strip()
            )
            if not str(resolved_profile).strip():
                continue
            rows.append(
                {
                    "license_profile": str(resolved_profile).strip(),
                    "max_licenses": _to_int(licensed.get("licensedLimit")),
                }
            )
    else:
        properties = ((base.get("licenseContent") or {}).get("properties") or {})
        rows.extend(
            {
                "license_profile": _camel_to_profile(key),
                "max_licenses": _to_int(value),
            }
            for key, value in properties.items()
            if key.startswith("max")
        )

        profile_prefix = "users.profiles."
        profile_suffix = ".max"
        rows.extend(
            {
                "license_profile": str(key)[len(profile_prefix) : -len(profile_suffix)].strip(),
                "max_licenses": _to_int(value),
            }
            for key, value in properties.items()
            if key.startswith(profile_prefix) and key.endswith(profile_suffix)
        )

    sublicense = (base.get("sublicense") or {})
    sublicense_profile_limits = sublicense.get("profileLimits") or {}
    rows.extend(
        {
            "license_profile": f"SUBLICENSE_{str(profile_name).strip()}",
            "max_licenses": _to_int(limit),
        }
        for profile_name, limit in sublicense_profile_limits.items()
        if str(profile_name).strip()
    )

    deduped_rows: list[dict[str, object]] = []
    seen_profiles: set[str] = set()
    for row in rows:
        profile = str(row.get("license_profile") or "").strip()
        if not profile or profile in seen_profiles:
            continue
        seen_profiles.add(profile)
        deduped_rows.append(
            {
                "license_profile": profile,
                "max_licenses": row.get("max_licenses"),
            }
        )
    return deduped_rows


def _addon_license_rows(payload):
    properties = (((payload.get("base") or {}).get("licenseContent") or {}).get("properties") or {})
    return [
        {
            "addon_key": key.removeprefix("addons."),
            "addon_enabled": _to_bool(value),
        }
        for key, value in properties.items()
        if key.startswith("addons.")
    ]


def collect_licensing_output(*, fn, target, context, run_date):
    payload = fn()
    if payload is None or not isinstance(payload, dict) or not payload:
        return [MethodCollectResult("get_licensing_status", context.scope, "empty_payload", 0, 0, 0)]

    raw_layout = LicenseOutputLayout(base_dir=Path("partitioned_data"), module="license")
    raw_path = raw_layout.project_data_path("raw", "license", context.instance_name, run_date, "instance", "json.gz")
    upload_json_gzip(target=target, output_path=raw_path, output_base_dir=raw_layout.base_dir, payload=payload)

    module_rows = {
        "license_status": _license_status_row(payload),
        "max_licenses": _max_license_rows(payload),
        "addon_licenses": _addon_license_rows(payload),
    }

    results = []
    for module_name, rows in module_rows.items():
        layout = LicenseOutputLayout(base_dir=Path("partitioned_data"), module=module_name)
        raw_df = pd.DataFrame(rows)
        silver_df = normalize_silver(
            df=raw_df,
            instance_name=context.instance_name,
            run_ts=context.run_ts,
            category="license",
            module=module_name,
            todo_section="instance",
        )
        dq = check_silver_dq(silver_df)
        silver_path = layout.project_data_path("silver", "license", context.instance_name, run_date, "instance", "parquet")
        if dq.ok:
            upload_parquet(
                target=target,
                output_path=silver_path,
                output_base_dir=layout.base_dir,
                df=silver_df,
                write_empty=(module_name == "license_status"),
                compression="snappy",
            )
            results.append(MethodCollectResult(f"license::{module_name}", context.scope, "silver_written", len(rows), int(silver_df.shape[0]), 0))
        else:
            results.append(MethodCollectResult(f"license::{module_name}", context.scope, "silver_failed_dq", len(rows), int(silver_df.shape[0]), 0, message=str(dq.errors)))

    return results


class MyRunnable(Runnable):
    """Gather instance-level metadata from DSS methods with centralized rules."""

    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config or {}
        self.plugin_config = plugin_config or {}
        self.param_set = self.plugin_config.get("pulse_primary", {}) or {}

    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        ctx: PulseMacroContext = build_context(plugin_config=self.plugin_config)
        self.param_set = ctx.param_set

        instance_name = get_instance_name(ctx.local_client)
        if not instance_name:
            raise ValueError("Could not determine instance_name (nodeId/installId)")

        run_dt = datetime.now(timezone.utc)
        run_ts = run_dt.isoformat()
        run_date = run_dt.date()

        layout = OutputLayout(base_dir=Path("partitioned_data"), module="instance_metadata")
        methods = get_noarg_list_methods(ctx.local_client)
        custom_methods = get_custom_instance_methods(ctx.local_client)
        excluded = set(load_exclusions("instance_data").excluded_methods)
        project_inclusions = load_inclusions("instance_project_inclusion.yaml")
        worker_project_key = str(
            self.param_set.get("pulse_worker_key")
            or resolve_worker_project_key(ctx.local_client, fallback_project_key=self.project_key)
        )
        target = ensure_output_folder(param_set=self.param_set, remote_client=ctx.remote_client)

        if progress_callback is not None:
            progress_callback(0)

        method_results: list[MethodCollectResult] = []
        total_work = len(methods) + len(custom_methods) + len(project_inclusions)
        completed = 0

        for method_name, fn in sorted(methods.items()):
            if method_name in excluded:
                method_results.append(MethodCollectResult(method_name, "instance", "excluded", 0, 0, 0))
            else:
                method_results.append(
                    collect_method_output(
                        fn=fn,
                        method_name=method_name,
                        file_key="instance",
                        layout=layout,
                        target=target,
                        context=MethodCallContext(
                            scope="instance",
                            instance_name=instance_name,
                            run_ts=run_ts,
                            param_set=self.param_set,
                            worker_project_key=worker_project_key,
                        ),
                        run_date=run_date,
                        todo_section="instance",
                    )
                )
            completed += 1
            if progress_callback is not None and total_work > 0:
                progress_callback(completed / total_work)

        for method_name, fn in sorted(custom_methods.items()):
            if method_name in excluded:
                method_results.append(MethodCollectResult(method_name, "instance", "excluded", 0, 0, 0))
            elif method_name == "get_licensing_status":
                method_results.extend(
                    collect_licensing_output(
                        fn=fn,
                        target=target,
                        context=MethodCallContext(
                            scope="instance",
                            instance_name=instance_name,
                            run_ts=run_ts,
                            param_set=self.param_set,
                            worker_project_key=worker_project_key,
                        ),
                        run_date=run_date,
                    )
                )
            else:
                method_results.append(
                    collect_method_output(
                        fn=fn,
                        method_name=method_name,
                        file_key="instance",
                        layout=layout,
                        target=target,
                        context=MethodCallContext(
                            scope="instance",
                            instance_name=instance_name,
                            run_ts=run_ts,
                            param_set=self.param_set,
                            worker_project_key=worker_project_key,
                        ),
                        run_date=run_date,
                        todo_section="instance",
                    )
                )
            completed += 1
            if progress_callback is not None and total_work > 0:
                progress_callback(completed / total_work)

        project_methods = {}
        try:
            worker_project = ctx.local_client.get_project(worker_project_key)
            project_methods = get_noarg_list_methods(worker_project)
        except Exception as exc:
            logger.exception("Failed to resolve worker project %s", worker_project_key)
            method_results.append(MethodCollectResult("__worker_project__", "project_inclusion", "call_failed", 0, 0, 0, message=repr(exc)))

        for method_name in project_inclusions:
            fn = project_methods.get(method_name)
            if fn is None:
                method_results.append(MethodCollectResult(method_name, "project_inclusion", "method_not_found", 0, 0, 0))
            else:
                method_results.append(
                    collect_method_output(
                        fn=fn,
                        method_name=method_name,
                        file_key=worker_project_key,
                        layout=layout,
                        target=target,
                        context=MethodCallContext(
                            scope="instance",
                            instance_name=instance_name,
                            run_ts=run_ts,
                            param_set=self.param_set,
                            project_key=worker_project_key,
                            worker_project_key=worker_project_key,
                        ),
                        run_date=run_date,
                        todo_section="instance",
                    )
                )
            completed += 1
            if progress_callback is not None and total_work > 0:
                progress_callback(completed / total_work)

        status_counts: dict[str, int] = {}
        for item in method_results:
            status_counts[item.status] = status_counts.get(item.status, 0) + 1

        rt = ResultTable()
        rt.add_column(1, "metric", "STRING")
        rt.add_column(2, "value", "STRING")
        rt.add_column(3, "scope", "STRING")
        rt.add_column(4, "status", "STRING")
        rt.add_column(5, "details", "STRING")

        for row in [
            ("list_methods_total", str(len(methods)), "summary", "info", ""),
            ("custom_methods_total", str(len(custom_methods)), "summary", "info", ""),
            ("project_inclusions_total", str(len(project_inclusions)), "summary", "info", ""),
            ("excluded_total", str(status_counts.get("excluded", 0)), "summary", "info", ""),
            ("excluded_by_rule_total", str(status_counts.get("excluded_by_rule", 0)), "summary", "info", ""),
            ("silver_written_total", str(status_counts.get("silver_written", 0)), "summary", "info", ""),
            ("silver_failed_dq_total", str(status_counts.get("silver_failed_dq", 0)), "summary", "info", ""),
            ("empty_payload_total", str(status_counts.get("empty_payload", 0)), "summary", "info", ""),
            ("empty_dataframe_total", str(status_counts.get("empty_dataframe", 0)), "summary", "info", ""),
            ("call_failed_total", str(status_counts.get("call_failed", 0)), "summary", "info", ""),
            ("method_not_found_total", str(status_counts.get("method_not_found", 0)), "summary", "info", ""),
            ("needs_rule_total", str(status_counts.get("needs_rule", 0)), "summary", "info", ""),
        ]:
            rt.add_record(list(row))

        for item in method_results:
            rt.add_record([
                item.method_name,
                str(item.rows_silver if item.rows_silver else item.rows_raw),
                item.scope,
                item.status,
                f"rule={item.rule_mode}; duration_ms={item.duration_ms}; rows_raw={item.rows_raw}; rows_silver={item.rows_silver}; message={item.message or ''}",
            ])

        return rt
