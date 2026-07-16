from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from flask import Flask
from jinja2 import Environment, FileSystemLoader, select_autoescape

from pulse_dashboard.reporting.charts import build_users_report_charts
from pulse_dashboard.reporting.pdf_renderer import html_to_pdf_bytes
from pulse_dashboard.webapp_backend import full_backend as fb

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_ENV = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)
_APP = Flask("pulse_dashboard_report_builder")


def _window_label(months: int) -> str:
    if months == 1:
        return "This month"
    return f"Last {months} months"


def _query_string(params: dict[str, str | None]) -> str:
    return urlencode({key: value for key, value in params.items() if value})


def _json_from_route_result(result: Any) -> dict[str, Any]:
    response = result[0] if isinstance(result, tuple) else result
    data = response.get_json()
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected route payload type for report export")
    if data.get("ok") is False:
        raise RuntimeError(str(data.get("error") or "Report source route failed"))
    return data


def build_users_report_payload(
    *,
    instance_name: str | None,
    license_filter: str | None,
    activity_filter: str | None,
    months: int,
    sections: dict[str, bool] | None = None,
) -> dict[str, Any]:
    _query_df, _create_connection, _ensure_database_ready = fb._require_duckdb_engine()
    _ensure_database_ready()
    fb._ensure_ready_if_enabled()

    with _APP.test_request_context(
        "/api/build/users/kpis?" + _query_string(
            {
                "instance_name": instance_name,
                "licenseFilter": license_filter,
                "activityFilter": activity_filter,
            }
        )
    ):
        kpis_payload = _json_from_route_result(fb.build_users_kpis())

    with _APP.test_request_context(
        "/api/build/users/active-monthly?" + _query_string(
            {
                "instance_name": instance_name,
                "activityFilter": activity_filter,
                "months": str(months),
            }
        )
    ):
        monthly_payload = _json_from_route_result(fb.build_users_active_monthly())

    with _APP.test_request_context(
        "/api/build/users/segments?" + _query_string(
            {
                "instance_name": instance_name,
                "activityFilter": activity_filter,
                "months": str(months),
            }
        )
    ):
        segments_payload = _json_from_route_result(fb.build_users_segments())

    with _APP.test_request_context(
        "/api/build/users/leaderboard?" + _query_string(
            {
                "instance_name": instance_name,
                "activityFilter": activity_filter,
                "months": str(months),
            }
        )
    ):
        leaderboard_payload = _json_from_route_result(fb.build_users_leaderboard())

    payload = {
        "report": {
            "title": "Pulse Users Activity Report",
            "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "instanceName": instance_name,
            "licenseFilter": license_filter,
            "activityFilter": activity_filter,
            "windowLabel": _window_label(months),
        },
        "sections": {
            "includeSummary": True if sections is None else bool(sections.get("includeSummary", True)),
            "includeMonthly": True if sections is None else bool(sections.get("includeMonthly", True)),
            "includeSegments": True if sections is None else bool(sections.get("includeSegments", True)),
            "includeLeaderboard": True if sections is None else bool(sections.get("includeLeaderboard", True)),
            "includeLicenseSummary": True if sections is None else bool(sections.get("includeLicenseSummary", True)),
        },
        "kpis": kpis_payload.get("kpis") or {},
        "licenseSummary": {
            "licenseStatusSummary": kpis_payload.get("licenseStatusSummary") or {},
            "byProfile": kpis_payload.get("byProfile") or [],
            "byLicenseGroup": kpis_payload.get("byLicenseGroup") or [],
            "byLicenseGroupProfiles": kpis_payload.get("byLicenseGroupProfiles") or [],
        },
        "activeMonthly": {
            "months": monthly_payload.get("months") or months,
            "latest": monthly_payload.get("latest") or {},
            "series": monthly_payload.get("series") or [],
        },
        "segments": {
            "segments": segments_payload.get("segments") or [],
            "dominanceSegments": segments_payload.get("dominanceSegments") or [],
        },
        "leaderboard": {
            "rows": leaderboard_payload.get("rows") or [],
        },
    }
    payload["charts"] = build_users_report_charts(payload)
    return payload


def render_users_report_html(payload: dict[str, Any]) -> str:
    template = _ENV.get_template("users_report.html")
    css = (_TEMPLATE_DIR / "users_report.css").read_text(encoding="utf-8")
    return template.render(css=css, **payload)


def build_users_report_pdf_bytes(
    *,
    instance_name: str | None,
    license_filter: str | None,
    activity_filter: str | None,
    months: int,
    sections: dict[str, bool] | None = None,
) -> bytes:
    payload = build_users_report_payload(
        instance_name=instance_name,
        license_filter=license_filter,
        activity_filter=activity_filter,
        months=months,
        sections=sections,
    )
    html = render_users_report_html(payload)
    return html_to_pdf_bytes(html, base_url=str(_TEMPLATE_DIR))
