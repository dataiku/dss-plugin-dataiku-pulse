from __future__ import annotations

import ast
import json
import re
import time
from pathlib import Path
from typing import Any

from flask import request
import yaml

from pulse_dashboard import settings as pulse_settings


def _read_standard_project_variables() -> dict[str, Any]:
    """Best-effort read of DSS project `standard` variables.

    In local dev runs (outside DSS), this returns an empty dict.
    """

    try:
        import dataiku

        client = dataiku.api_client()
        project = client.get_project(dataiku.default_project_key())
        vars_ = project.get_variables() or {}
        standard = vars_.get("standard") or {}
        return standard if isinstance(standard, dict) else {}
    except Exception:
        return {}


def _read_license_groups() -> dict[str, list[str]]:
    """Read plugin-owned license grouping config from terminology.yaml.

    Expected keys:
    - license_creator
    - license_consumer
    - license_admin

    Any user profile not explicitly mapped into one of the above groups is
    treated as `license_other` by downstream consumers.
    """

    path = Path(__file__).resolve().parents[2] / "configs" / "terminology.yaml"
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        doc = {}

    groups = doc.get("license_groups") if isinstance(doc, dict) else None
    if not isinstance(groups, dict):
        groups = {}

    def _list(name: str) -> list[str]:
        value = groups.get(name, [])
        if not isinstance(value, list):
            return []
        return [str(item).strip().upper() for item in value if str(item).strip()]

    return {
        "license_creator": _list("license_creator"),
        "license_consumer": _list("license_consumer"),
        "license_admin": _list("license_admin"),
    }


def _read_user_profile_exclude_consumer(_standard_vars: dict[str, Any]) -> list[str]:
    """Profiles excluded by the `no_consumer` license filter.

    This is derived from the plugin-owned `license_consumer` group defined in
    `pulse_dashboard/configs/terminology.yaml`.
    """

    groups = _read_license_groups()
    return groups.get("license_consumer", []) or ["READER", "AI_CONSUMER"]


_WINDOW_TO_MONTHS = {
    "this_month": 1,
    "last_3_months": 3,
    "last_12_months": 12,
}


def _parse_window_months(value: str | None) -> int | None:
    if not value:
        return None
    value = str(value).strip().lower()
    months = _WINDOW_TO_MONTHS.get(value)
    return int(months) if months else None


def _parse_activity_filter(value: str | None) -> str:
    value = (value or "").strip().lower()
    allowed = {"license_creator", "license_consumer"}
    aliases = {
        "creator": "license_creator",
        "creators": "license_creator",
        "consumer": "license_consumer",
        "consumers": "license_consumer",
    }
    normalized = aliases.get(value, value)
    return normalized if normalized in allowed else "license_creator"


def _parse_instance_name(value: str | None) -> str | None:
    out = (value or "").strip()
    return out or None


def _sql_placeholders(n: int) -> str:
    return ",".join(["?"] * n)


def _parse_csv_list(value: str | None) -> list[str]:
    if not value:
        return []
    parts = [p.strip() for p in value.split(",")]
    return [p for p in parts if p]


def _hub_instances_sql_list() -> str:
    """Return a SQL-safe list like `'hub1','hub2'`.

    Used only to render plugin-controlled config into VIEW templates.
    """

    hub_instances = []
    if pulse_settings is not None:
        hub_instances = _parse_csv_list(getattr(pulse_settings, "PULSE_HUB_INSTANCE_NAMES", ""))

    if not hub_instances:
        return "'__none__'"

    escaped = ["'" + s.replace("'", "''") + "'" for s in hub_instances]
    return ",".join(escaped)


def _users_directory_cte_sql(*, cte_name: str = "directory") -> str:
    lines = [
        cte_name + " AS (",
        "  WITH src AS (",
        "    SELECT",
        "      instance_name,",
        "      users_login AS login,",
        "      lower(trim(users_login)) AS login_norm,",
        "      users_displayname AS display_name,",
        "      users_email AS email,",
        "      users_enabled = 'True' AS enabled,",
        "      users_userprofile AS user_profile,",
        "      users_groups AS group_names,",
        "      run_ts,",
        "      CASE WHEN instance_name IN (" + _hub_instances_sql_list() + ") THEN 1 ELSE 0 END AS is_hub",
        "    FROM base_users_instance_metadata",
        "    WHERE users_login IS NOT NULL",
        "      AND length(trim(users_login)) > 0",
        "  ),",
        "  ranked AS (",
        "    SELECT",
        "      *,",
        "      ROW_NUMBER() OVER (",
        "        PARTITION BY login_norm",
        "        ORDER BY run_ts DESC, is_hub DESC, instance_name ASC",
        "      ) AS rn",
        "    FROM src",
        "  )",
        "  SELECT",
        "    instance_name,",
        "    login,",
        "    login_norm,",
        "    display_name,",
        "    email,",
        "    enabled,",
        "    user_profile,",
        "    group_names,",
        "    run_ts",
        "  FROM ranked",
        "  WHERE rn = 1",
        ")",
    ]
    return "\n".join(lines) + "\n"


def _window_months_where_sql(*, months: int) -> str:
    months = max(1, min(24, int(months)))
    return f"last_activity_at >= date_trunc('month', current_date) - INTERVAL {months - 1} MONTH"


_MAX_LOOKBACK_DAYS = 365
_MAX_LOOKBACK_MONTHS = 24


class RequestValidationError(ValueError):
    """Raised when request inputs are present but invalid."""


def _normalize_optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
        return None

    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "1.0"}:
        return True
    if normalized in {"false", "0", "0.0"}:
        return False
    return None


def _parse_history_groups(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(v).strip() for v in parsed if str(v).strip()]
        except Exception:
            pass
        try:
            parsed = ast.literal_eval(raw)
            if isinstance(parsed, list):
                return [str(v).strip() for v in parsed if str(v).strip()]
        except Exception:
            pass
        return [raw]
    return [str(value).strip()] if str(value).strip() else []


def _normalize_user_directory_record(row: dict[str, Any]) -> dict[str, Any]:
    resulting_profile = str(row.get("users_resultinguserprofile") or "").strip()
    fallback_profile = str(row.get("users_userprofile") or "").strip()
    return {
        "instance_name": row.get("instance_name"),
        "login": row.get("users_login"),
        "display_name": row.get("users_displayname"),
        "email": row.get("users_email"),
        "user_profile": resulting_profile or fallback_profile or None,
        "enabled": _normalize_optional_bool(row.get("users_enabled")),
        "source_type": row.get("users_sourcetype"),
        "creation_date": row.get("users_creationdate"),
        "last_successful_login": row.get("users_lastsuccessfullogin"),
        "last_failed_login": row.get("users_lastfailedlogin"),
        "last_session_activity": row.get("users_lastsessionactivity"),
        "first_commit_date": row.get("users_first_commit_date"),
        "last_commit_date": row.get("users_last_commit_date"),
        "groups": _parse_history_groups(row.get("users_groups")),
        "run_ts": row.get("run_ts"),
        "partition_date": row.get("partition_date"),
    }


def _user_directory_record_rank_key(row: dict[str, Any]) -> tuple[int, int, int, int, str, str, str]:
    return (
        int(bool(str(row.get("display_name") or "").strip())),
        int(bool(str(row.get("email") or "").strip())),
        int(bool(str(row.get("user_profile") or "").strip())),
        int(row.get("enabled") is not None),
        str(row.get("run_ts") or ""),
        str(row.get("partition_date") or ""),
        str(row.get("instance_name") or ""),
    )


def _user_detail_instances_sql(*, include_instance_filter: bool) -> str:
    optional_instance_filter = "AND instance_name = ?" if include_instance_filter else ""
    sql = "\n".join(
        [
            "WITH ranked AS (",
            "    SELECT",
            "        instance_name,",
            "        users_login,",
            "        users_sourcetype,",
            "        users_displayname,",
            "        users_groups,",
            "        users_userprofile,",
            "        users_creationdate,",
            "        users_enabled,",
            "        users_resultinguserprofile,",
            "        users_email,",
            "        users_lastsuccessfullogin,",
            "        users_lastfailedlogin,",
            "        users_lastsessionactivity,",
            "        users_first_commit_date,",
            "        users_last_commit_date,",
            "        run_ts,",
            "        partition_date,",
            "        ROW_NUMBER() OVER (",
            "            PARTITION BY instance_name",
            "            ORDER BY",
            "                run_ts DESC NULLS LAST,",
            "                partition_date DESC NULLS LAST,",
            "                users_lastsessionactivity DESC NULLS LAST",
            "        ) AS rn",
            "    FROM base_users_instance_metadata",
            "    WHERE lower(trim(users_login)) = lower(trim(?))",
            f"    {optional_instance_filter}" if optional_instance_filter else "",
            ")",
            "SELECT",
            "    instance_name,",
            "    users_login,",
            "    users_sourcetype,",
            "    users_displayname,",
            "    users_groups,",
            "    users_userprofile,",
            "    users_creationdate,",
            "    users_enabled,",
            "    users_resultinguserprofile,",
            "    users_email,",
            "    users_lastsuccessfullogin,",
            "    users_lastfailedlogin,",
            "    users_lastsessionactivity,",
            "    users_first_commit_date,",
            "    users_last_commit_date,",
            "    run_ts,",
            "    partition_date",
            "FROM ranked",
            "WHERE rn = 1",
            "ORDER BY instance_name ASC;",
        ]
    )
    return "\n".join(line for line in sql.splitlines() if line.strip())


def _parse_login_norm(value: str) -> str:
    return value.strip().lower()


def _parse_int_arg(
    name: str,
    *,
    default: int | None = None,
    minimum: int | None = None,
    maximum: int | None = None,
    required: bool = False,
) -> int | None:
    raw = request.args.get(name)
    if raw is None or str(raw).strip() == "":
        if required:
            raise RequestValidationError(f"Missing query parameter '{name}'")
        return default

    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise RequestValidationError(f"Invalid integer for '{name}'") from exc

    if minimum is not None and value < minimum:
        raise RequestValidationError(f"'{name}' must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise RequestValidationError(f"'{name}' must be <= {maximum}")
    return value


def _parse_days_arg(*, default: int = 30, maximum: int = _MAX_LOOKBACK_DAYS) -> int:
    return int(_parse_int_arg("days", default=default, minimum=1, maximum=maximum) or default)


def _resolve_window_params(
    *,
    default_days: int = 30,
    max_days: int = _MAX_LOOKBACK_DAYS,
    max_months: int = _MAX_LOOKBACK_MONTHS,
) -> tuple[int | None, int | None]:
    window = request.args.get("window")
    months = _parse_window_months(window)
    if window and months is None:
        allowed = ", ".join(sorted(_WINDOW_TO_MONTHS))
        raise RequestValidationError(f"Invalid 'window'. Expected one of: {allowed}")

    if months is not None:
        return months, None

    explicit_months = _parse_int_arg("months", default=None, minimum=1, maximum=max_months)
    if explicit_months is not None:
        return explicit_months, None

    return None, _parse_days_arg(default=default_days, maximum=max_days)


def _parse_license_filter(value: str | None) -> str:
    value = (value or "").strip().lower()
    allowed = {
        "all_enabled",
        "no_consumer",
        "license_creator",
        "license_consumer",
        "license_admin",
        "license_other",
    }
    aliases = {
        "exclude_consumer": "no_consumer",
        "exclude-consumer": "no_consumer",
        "non_consumer": "no_consumer",
        "non-consumer": "no_consumer",
        "exclude_readers": "no_consumer",
    }
    normalized = aliases.get(value, value)
    return normalized if normalized in allowed else "all_enabled"


def _resolve_license_filter_clause(license_filter: str) -> tuple[str, list[str]]:
    groups = _read_license_groups()
    creator = groups.get("license_creator", [])
    consumer = groups.get("license_consumer", [])
    admin = groups.get("license_admin", [])
    known = sorted({*creator, *consumer, *admin})

    if license_filter == "no_consumer":
        if not consumer:
            return "", []
        placeholders = _sql_placeholders(len(consumer))
        return f" AND coalesce(upper(trim({{profile_expr}})), '') NOT IN ({placeholders})", list(consumer)

    if license_filter in {"license_creator", "license_consumer", "license_admin"}:
        target = groups.get(license_filter, [])
        if not target:
            return " AND 1 = 0", []
        placeholders = _sql_placeholders(len(target))
        return f" AND coalesce(upper(trim({{profile_expr}})), '') IN ({placeholders})", list(target)

    if license_filter == "license_other":
        if not known:
            return "", []
        placeholders = _sql_placeholders(len(known))
        return f" AND coalesce(upper(trim({{profile_expr}})), '') NOT IN ({placeholders})", list(known)

    return "", []


def _format_license_filter_clause(template: str, *, profile_expr: str) -> str:
    return template.format(profile_expr=profile_expr) if template else ""


def _sql_string_literals(values: list[str]) -> str:
    escaped = [str(value).replace("'", "''") for value in values]
    if not escaped:
        return "''"
    return ",".join(f"'{value}'" for value in escaped)


def _license_group_case_sql(profile_expr: str) -> str:
    groups = _read_license_groups()
    creator = groups.get("license_creator", [])
    consumer = groups.get("license_consumer", [])
    admin = groups.get("license_admin", [])

    clauses: list[str] = []
    if creator:
        clauses.append(
            f"WHEN coalesce(upper(trim({profile_expr})), '') IN ({_sql_string_literals(creator)}) THEN 'Creator Licenses'"
        )
    if consumer:
        clauses.append(
            f"WHEN coalesce(upper(trim({profile_expr})), '') IN ({_sql_string_literals(consumer)}) THEN 'Consumer Licenses'"
        )
    if admin:
        clauses.append(
            f"WHEN coalesce(upper(trim({profile_expr})), '') IN ({_sql_string_literals(admin)}) THEN 'Admin Licenses'"
        )

    if not clauses:
        return "'Other Licenses'"

    when_sql = "\n      ".join(clauses)
    return "CASE\n      " + when_sql + "\n      ELSE 'Other Licenses'\n    END"


def _license_profile_normalize_sql(profile_expr: str) -> str:
    return f"upper(regexp_replace(coalesce(trim({profile_expr}), 'UNKNOWN'), '[^A-Za-z0-9]+', '', 'g'))"


def _truthy_license_feature(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    normalized = str(value).strip().lower()
    return normalized in {"true", "t", "1", "yes", "y", "enabled"}


def _license_status_display_value(field_name: str, raw_value: Any) -> str | None:
    if raw_value is None:
        return None

    text = str(raw_value).strip()
    if not text:
        return None

    if field_name in {"valid", "expired", "has_license", "community"}:
        lowered = text.lower()
        if lowered in {"true", "t", "1", "yes", "y"}:
            return "True"
        if lowered in {"false", "f", "0", "no", "n"}:
            return "False"
        return text

    if field_name == "expires_on":
        if text.isdigit():
            try:
                ts = int(text)
                if ts > 10_000_000_000:
                    ts = ts // 1000
                return time.strftime("%Y-%m-%d", time.gmtime(ts))
            except Exception:
                return text
        return text

    if field_name == "emitted_on":
        if len(text) == 8 and text.isdigit():
            return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
        return text

    return text


def _addon_service_label(addon_key: Any) -> str:
    text = str(addon_key or "").strip()
    if not text:
        return "Unknown Add-on"

    custom_labels = {
        "advancedGovern": "Advanced Govern",
        "advancedLLMMesh": "Advanced LLM Mesh",
        "stories": "Stories",
    }
    if text in custom_labels:
        return custom_labels[text]

    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    spaced = spaced.replace("_", " ").replace("-", " ")
    return " ".join(part.capitalize() for part in spaced.split())
