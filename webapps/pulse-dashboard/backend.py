from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

import dataiku
from flask import Flask, jsonify, request, send_from_directory

from pulse_dashboard.webapp_backend import register_local_routes, register_routes

logger = logging.getLogger(__name__)

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


@app.route("/")
@app.route("/<path:_path>")
def index(_path: str | None = None) -> Any:
    return send_from_directory(app.root_path, "body.html")


def _resolve_authenticated_user() -> dict[str, object] | None:
    request_headers = dict(request.headers)
    safe_header_names = sorted(
        header_name
        for header_name in request_headers.keys()
        if str(header_name).lower() not in _SENSITIVE_HEADER_NAMES
    )
    logger.info("/api/me auth resolution started; safe_header_names=%s", safe_header_names)

    try:
        auth_info = dataiku.api_client().get_auth_info_from_browser_headers(request_headers, with_secrets=False)
    except Exception:
        logger.exception("Unable to resolve authenticated Dataiku user")
        return None

    if not isinstance(auth_info, dict):
        logger.info("/api/me auth resolution returned non-dict payload: %s", type(auth_info).__name__)
        return None

    associated_user_raw = auth_info.get("associatedDSSUser")
    associated_user = associated_user_raw if isinstance(associated_user_raw, dict) else {}

    username = str(auth_info.get("authIdentifier") or "").strip()
    if not username:
        logger.info("/api/me auth resolution did not provide authIdentifier")
        return None

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
    return user_info


def _get_default_project_handle() -> Any:
    try:
        get_default_project = getattr(dataiku, "get_default_project", None)
        if callable(get_default_project):
            project = get_default_project()
            if hasattr(project, "get_variables"):
                return project

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
    logger.info("/api/me called from remote_addr=%s", request.remote_addr)
    user_info = _resolve_authenticated_user()
    permissions = _resolve_permissions(user_info)
    configured_values = _read_standard_project_variables(["organization_owner", "administration_owner"])
    configured_groups = {
        "organization": str((configured_values.get("organization_owner") or {}).get("value") or "").strip() or None,
        "administration": str((configured_values.get("administration_owner") or {}).get("value") or "").strip() or None,
    }

    if user_info is None:
        response_payload = {
            "ok": False,
            "authenticated": False,
            "user": None,
            "permissions": permissions,
        }
        return jsonify(response_payload)

    response_payload = {
        "ok": True,
        "authenticated": True,
        "user": user_info,
        "configuredGroups": configured_groups,
        "permissions": permissions,
    }
    return jsonify(response_payload)


if not _HAS_INJECTED_DSS_APP:
    register_local_routes(app)
else:
    register_routes(app)
