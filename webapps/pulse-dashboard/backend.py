from __future__ import annotations

import logging
from urllib.parse import splitport, urlparse
from pathlib import Path
from typing import Any, cast

import dataiku
from dataiku.customwebapp import get_webapp_config
from flask import Flask, jsonify, request, send_from_directory

from pulse_dashboard.webapp_backend import register_local_routes, register_routes

logger = logging.getLogger(__name__)

webapp_config = get_webapp_config() or {}
pulse_primary = webapp_config.get("pulse_primary")
if not isinstance(pulse_primary, dict):
    raise RuntimeError("Pulse primary configuration is missing or invalid")

INTERNAL_PREVIEW_DEBUG_VERSION = "preview-debug-2026-08-03-v2"

_SENSITIVE_HEADER_NAMES = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
}

_SENSITIVE_AUTH_KEYS = {
    "groups",
    "secrets",
    "sessionToken",
    "sessiontoken",
    "apiKey",
    "apikey",
    "cookie",
    "cookies",
}

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BUILD_DIR = _REPO_ROOT / "resource" / "pulse-dashboard" / "build"

app = cast(Flask | None, globals().get("app"))
_HAS_INJECTED_DSS_APP = app is not None

if app is None:  # pragma: no cover
    static_dir = _BUILD_DIR / "static"
    if static_dir.is_dir():
        app = Flask(__name__, static_folder=str(static_dir), static_url_path="/static")
    else:
        app = Flask(__name__)

app = cast(Flask, app)

if not logger.handlers:
    gunicorn_error_logger = logging.getLogger("gunicorn.error")
    if gunicorn_error_logger.handlers:
        logger.handlers = gunicorn_error_logger.handlers
        logger.setLevel(gunicorn_error_logger.level)
        logger.propagate = False

logger.info("Pulse DSS wrapper backend loaded from %s", __file__)
logger.info("Internal Preview backend version=%s", INTERNAL_PREVIEW_DEBUG_VERSION)


@app.route("/")
@app.route("/<path:_path>")
def index(_path: str | None = None) -> Any:
    return send_from_directory(app.root_path, "body.html")


def _normalize_optional_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    normalized = str(value or "").strip().lower()
    return normalized in {"true", "1", "yes", "on"}


def _normalize_hostname(value: str) -> str:
    host = str(value or "").strip().lower().rstrip(".")
    normalized_host, _port = splitport(host)
    return str(normalized_host or "").strip().lower().rstrip(".")


def _resolve_request_host_context() -> dict[str, str | None]:
    forwarded_host = str(request.headers.get("X-Forwarded-Host") or "").strip()
    referer = str(request.headers.get("Referer") or "").strip()
    host_header = str(request.headers.get("Host") or "").strip()
    request_host = str(getattr(request, "host", None) or "").strip()
    forwarded_proto = str(request.headers.get("X-Forwarded-Proto") or "").strip()

    referer_host: str | None = None
    if referer:
        try:
            parsed_referer_host = urlparse(referer).hostname
            if parsed_referer_host:
                referer_host = str(parsed_referer_host).strip() or None
        except Exception:
            logger.debug("Unable to parse Referer hostname for Internal Preview", exc_info=True)

    effective_host = ""
    effective_host_source = "request_host"
    if forwarded_host:
        effective_host = forwarded_host.split(",", 1)[0].strip()
        effective_host_source = "x_forwarded_host"
    elif referer_host:
        effective_host = referer_host
        effective_host_source = "referer"
    elif host_header:
        effective_host = host_header
        effective_host_source = "host"
    else:
        effective_host = request_host

    normalized_effective_host = _normalize_hostname(effective_host)
    normalized_request_host = _normalize_hostname(request_host)

    return {
        "requestHost": request_host or None,
        "hostHeader": host_header or None,
        "forwardedHost": forwarded_host or None,
        "forwardedProto": forwarded_proto or None,
        "refererHost": referer_host,
        "effectiveHost": effective_host or None,
        "effectiveHostSource": effective_host_source,
        "normalizedEffectiveHost": normalized_effective_host or None,
        "normalizedRequestHost": normalized_request_host or None,
    }


def _read_internal_preview_config() -> dict[str, object]:
    configured_values = _read_standard_project_variables(
        [
            "enable_internal_preview",
            "internal_preview_host",
            "internal_preview_login",
            "internal_preview_access_tier",
        ]
    )
    return {
        "enabled": _normalize_optional_bool((configured_values.get("enable_internal_preview") or {}).get("value")),
        "host": _normalize_hostname(str((configured_values.get("internal_preview_host") or {}).get("value") or "")),
        "login": str((configured_values.get("internal_preview_login") or {}).get("value") or "").strip(),
        "tier": str((configured_values.get("internal_preview_access_tier") or {}).get("value") or "organization").strip().lower(),
    }


def _project_key_from_handle(project: Any) -> str | None:
    if project is None:
        return None
    for attr_name in ("project_key", "projectKey"):
        value = getattr(project, attr_name, None)
        if value:
            return str(value)
    get_project_key = getattr(project, "get_project_key", None)
    if callable(get_project_key):
        try:
            value = get_project_key()
        except Exception:
            value = None
        if value:
            return str(value)
    return None


def _summarize_preview_failure(preview_config: dict[str, object], host_context: dict[str, str | None]) -> str:
    preview_enabled = preview_config.get("enabled") is True
    preview_host = str(preview_config.get("host") or "")
    preview_login = str(preview_config.get("login") or "").strip()
    normalized_request_host = str(host_context.get("normalizedEffectiveHost") or "")

    if not preview_enabled:
        return "preview_disabled"
    if not preview_host:
        return "configured_host_missing"
    if not preview_login:
        return "login_missing"
    if not normalized_request_host:
        return "request_host_missing"
    if normalized_request_host != preview_host:
        return "host_mismatch"
    return "unknown"


def _resolve_preview_permissions(access_tier: object) -> dict[str, object]:
    normalized_tier = str(access_tier or "").strip().lower()
    if normalized_tier == "administration":
        return {"self": True, "organization": True, "administration": True, "highestTier": "administration"}
    if normalized_tier == "organization":
        return {"self": True, "organization": True, "administration": False, "highestTier": "organization"}
    return {"self": True, "organization": False, "administration": False, "highestTier": "self"}


def _resolve_effective_request_context() -> dict[str, object]:
    configured_values = _read_standard_project_variables(["organization_owner", "administration_owner"])
    configured_groups = {
        "organization": str((configured_values.get("organization_owner") or {}).get("value") or "").strip() or None,
        "administration": str((configured_values.get("administration_owner") or {}).get("value") or "").strip() or None,
    }

    request_headers = dict(request.headers)
    safe_header_names = sorted(
        header_name
        for header_name in request_headers.keys()
        if str(header_name).lower() not in _SENSITIVE_HEADER_NAMES
    )
    logger.info("/api/me auth resolution started; version=%s safe_header_names=%s", INTERNAL_PREVIEW_DEBUG_VERSION, safe_header_names)

    auth_info: dict[str, Any] | None = None
    try:
        raw_auth_info = dataiku.api_client().get_auth_info_from_browser_headers(request_headers, with_secrets=False)
        if isinstance(raw_auth_info, dict):
            auth_info = raw_auth_info
        else:
            logger.info("/api/me auth resolution returned non-dict payload: %s", type(raw_auth_info).__name__)
    except Exception as exc:
        exception_text = str(exc)
        if "NotAuthenticatedException" in exception_text:
            logger.info("No authenticated DSS browser session; evaluating Internal Preview")
        else:
            logger.exception("Unexpected error resolving authenticated DSS user")

    if isinstance(auth_info, dict):
        associated_user_raw = auth_info.get("associatedDSSUser")
        associated_user = associated_user_raw if isinstance(associated_user_raw, dict) else {}

        username = str(auth_info.get("authIdentifier") or "").strip()
        if username:
            groups_raw = auth_info.get("groups")
            groups = [str(group).strip() for group in groups_raw if str(group).strip()] if isinstance(groups_raw, list) else []

            user_info: dict[str, object] = {
                "login": username,
                "groups": groups,
            }

            display_name = str(
                associated_user.get("displayName")
                or auth_info.get("displayName")
                or auth_info.get("display_name")
                or ""
            ).strip()
            if display_name:
                user_info["displayName"] = display_name

            email = str(associated_user.get("email") or auth_info.get("email") or "").strip()
            if email:
                user_info["email"] = email

            auth_debug_summary = {
                "authIdentifier": username,
                "authMethod": str(auth_info.get("authMethod") or "").strip(),
                "authSource": str(auth_info.get("authSource") or "").strip(),
                "via": str(auth_info.get("via") or "").strip(),
                "groups": groups,
                "associatedDSSUserKeys": sorted(
                    key for key in associated_user.keys() if str(key) not in _SENSITIVE_AUTH_KEYS
                ),
            }
            logger.info("/api/me auth resolution summary=%s", auth_debug_summary)
            return {
                "ok": True,
                "authenticated": True,
                "previewMode": False,
                "authSource": "dss",
                "user": user_info,
                "permissions": _resolve_permissions(user_info),
                "configuredGroups": configured_groups,
                "realIdentity": user_info,
                "authFailed": False,
            }

        logger.info("/api/me auth resolution did not provide authIdentifier")

    logger.info("Evaluating Internal Preview after DSS authentication result")
    preview_config = _read_internal_preview_config()
    host_context = _resolve_request_host_context()
    normalized_request_host = str(host_context.get("normalizedEffectiveHost") or "") or None
    preview_enabled = preview_config.get("enabled") is True
    preview_host = str(preview_config.get("host") or "")
    preview_login = str(preview_config.get("login") or "").strip()
    preview_tier = str(preview_config.get("tier") or "self")

    project_handle = _get_default_project_handle()
    project_key = _project_key_from_handle(project_handle)
    preview_failure = _summarize_preview_failure(preview_config, host_context)

    logger.info(
        "Internal Preview evaluation enabled=%s configured_host_present=%s login_configured=%s tier=%s effective_host_source=%s normalized_request_host=%r normalized_configured_host=%r host_matched=%s",
        preview_enabled,
        bool(preview_host),
        bool(preview_login),
        preview_tier,
        host_context.get("effectiveHostSource"),
        normalized_request_host,
        preview_host or None,
        bool(preview_host and normalized_request_host and normalized_request_host == preview_host),
    )
    logger.info(
        "/api/me preview config enabled=%s configured_host=%s request_host=%s host_header=%s forwarded_host=%s referer_present=%s referer_host=%s forwarded_proto=%s normalized_configured_host=%s normalized_effective_host=%s login_configured=%s access_tier=%s project_key=%s preview_failure=%s effective_host_source=%s host_matched=%s",
        preview_enabled,
        preview_host or None,
        host_context.get("requestHost"),
        host_context.get("hostHeader"),
        host_context.get("forwardedHost"),
        bool(request.headers.get("Referer")),
        host_context.get("refererHost"),
        host_context.get("forwardedProto"),
        preview_host or None,
        host_context.get("normalizedEffectiveHost"),
        bool(preview_login),
        preview_tier,
        project_key,
        preview_failure,
        host_context.get("effectiveHostSource"),
        bool(preview_host and normalized_request_host and normalized_request_host == preview_host),
    )

    if preview_enabled and normalized_request_host and preview_host and preview_login and normalized_request_host == preview_host:
        logger.info(
            "Internal preview mode activated; hostname=%s effective_login=%s access_tier=%s",
            normalized_request_host,
            preview_login,
            preview_tier,
        )
        preview_user = {
            "login": preview_login,
            "displayName": preview_login,
            "email": None,
            "groups": [],
            "isPreview": True,
        }
        return {
            "ok": True,
            "authenticated": True,
            "previewMode": True,
            "authSource": "internal_preview",
            "user": preview_user,
            "permissions": _resolve_preview_permissions(preview_tier),
            "configuredGroups": configured_groups,
            "realIdentity": None,
            "authFailed": False,
        }

    return {
        "ok": False,
        "authenticated": False,
        "previewMode": False,
        "authSource": None,
        "user": None,
        "permissions": _resolve_permissions(None),
        "configuredGroups": configured_groups,
        "realIdentity": None,
        "authFailed": False,
        "previewDiagnostics": {
            "enabled": preview_enabled,
            "configuredHostPresent": bool(preview_host),
            "loginConfigured": bool(preview_login),
            "tier": preview_tier,
            "requestHost": host_context.get("requestHost"),
            "forwardedHost": host_context.get("forwardedHost"),
            "refererHost": host_context.get("refererHost"),
            "normalizedConfiguredHost": preview_host or None,
            "normalizedRequestHost": host_context.get("normalizedEffectiveHost"),
            "hostMatched": bool(preview_host and normalized_request_host and normalized_request_host == preview_host),
            "effectiveHostSource": host_context.get("effectiveHostSource"),
            "projectKey": project_key,
            "failedCondition": preview_failure,
        },
    }


def _get_default_project_handle() -> Any:
    try:
        get_default_project = getattr(dataiku, "get_default_project", None)
        if callable(get_default_project):
            project = get_default_project()
            if hasattr(project, "get_variables"):
                return project

        default_project_key = getattr(dataiku, "default_project_key", None)
        if callable(default_project_key):
            project_key = default_project_key()
            if project_key:
                return dataiku.api_client().get_project(str(project_key))

        client = dataiku.api_client()
        return getattr(client, "get_default_project")()
    except Exception:
        logger.exception("Unable to resolve default DSS project handle")
        return None


def _read_standard_project_variables(keys: list[str]) -> dict[str, dict[str, object]]:
    project = _get_default_project_handle()
    if project is None:
        return {key: {"found": False, "value": None, "reason": "project_unavailable"} for key in keys}

    try:
        vars_ = project.get_variables() or {}
        standard = vars_.get("standard") or {}
    except Exception:
        logger.exception("Unable to read DSS project variables")
        return {key: {"found": False, "value": None, "reason": "variables_unavailable"} for key in keys}

    results: dict[str, dict[str, object]] = {}
    for key in keys:
        raw_value = standard.get(key)
        value = str(raw_value).strip() if raw_value is not None else ""
        found = bool(value)
        results[key] = {
            "found": found,
            "value": value if found else None,
            "reason": None if found else "missing",
        }
    return results


def _resolve_permissions(user_info: dict[str, object] | None) -> dict[str, object]:
    if not isinstance(user_info, dict):
        return {
            "self": False,
            "organization": False,
            "administration": False,
            "highestTier": None,
        }

    groups = user_info.get("groups")
    user_groups = []
    if isinstance(groups, list):
        seen = set()
        for group in groups:
            normalized = str(group).strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                user_groups.append(normalized)

    configured_values = _read_standard_project_variables(["organization_owner", "administration_owner"])
    organization_group = str((configured_values.get("organization_owner") or {}).get("value") or "").strip()
    administration_group = str((configured_values.get("administration_owner") or {}).get("value") or "").strip()

    administration = bool(administration_group and administration_group in user_groups)
    organization = bool((organization_group and organization_group in user_groups) or administration)
    self_access = True

    highest_tier: str | None
    if administration:
        highest_tier = "administration"
    elif organization:
        highest_tier = "organization"
    elif self_access:
        highest_tier = "self"
    else:
        highest_tier = None

    return {
        "self": self_access,
        "organization": organization,
        "administration": administration,
        "highestTier": highest_tier,
    }


@app.route("/api/me")
def current_user() -> Any:
    logger.info("/api/me called version=%s remote_addr=%s", INTERNAL_PREVIEW_DEBUG_VERSION, request.remote_addr)
    response_payload = _resolve_effective_request_context()
    return jsonify(response_payload)


if not _HAS_INJECTED_DSS_APP:
    register_local_routes(app)
else:
    register_routes(app)
