from __future__ import annotations

import logging
import secrets
from typing import Any

logger = logging.getLogger(__name__)

REPORTER_NAME = "pulse_failure_email"


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def notification_enabled(*, recipient: Any, channel_name: Any) -> bool:
    return bool(_normalize_text(recipient) and _normalize_text(channel_name))


def _find_matching_channel(client: Any, channel_name: str) -> tuple[str | None, str | None]:
    wanted = _normalize_text(channel_name)
    if not wanted:
        return None, "notification engine is blank"

    try:
        channels = client.list_messaging_channels() or []
    except Exception as exc:
        return None, f"unable to list messaging channels: {exc!r}"

    matches: list[Any] = []
    for channel in channels:
        cid = _normalize_text(getattr(channel, "id", None))
        cname = _normalize_text(getattr(channel, "name", None) or getattr(channel, "label", None))
        if cid == wanted or cname == wanted:
            matches.append(channel)

    if not matches:
        return None, f"notification engine not found: {wanted}"
    if len(matches) > 1:
        return None, f"multiple messaging channels matched notification engine: {wanted}"

    return _normalize_text(getattr(matches[0], "id", None)) or wanted, None


def _build_failure_reporter(*, recipient: str, channel_id: str) -> dict[str, Any]:
    return {
        "active": True,
        "messaging": {
            "type": "mail-scenario",
            "configuration": {
                "channelId": channel_id,
                "subject": "DSS scenario ${scenarioName}: ${outcome}",
                "recipient": recipient,
                "sendAsHTML": False,
                "attachments": [],
                "messageSource": "TEMPLATE_FILE",
                "templateFormat": "FREEMARKER",
                "templateName": "default.ftl",
            },
        },
        "id": secrets.token_urlsafe(6),
        "name": REPORTER_NAME,
        "runConditionEnabled": True,
        "runCondition": "outcome != 'SUCCESS'",
        "phase": "END",
    }


def ensure_failure_reporter_on_settings(
    *,
    client: Any,
    settings: Any,
    recipient: Any,
    channel_name: Any,
) -> tuple[str, str | None]:
    recipient_text = _normalize_text(recipient)
    channel_text = _normalize_text(channel_name)
    if not notification_enabled(recipient=recipient_text, channel_name=channel_text):
        return "skipped", "notification settings incomplete; skipping reporter setup"

    channel_id, channel_error = _find_matching_channel(client, channel_text)
    if channel_error:
        return "warning", channel_error

    reporters = getattr(settings, "raw_reporters", None)
    if reporters is None:
        raw = settings.get_raw()
        reporters = raw.setdefault("reporters", [])
        settings.raw_reporters = reporters

    desired = _build_failure_reporter(recipient=recipient_text, channel_id=str(channel_id))

    replaced = False
    for index, reporter in enumerate(list(settings.raw_reporters)):
        if str((reporter or {}).get("name") or "") == REPORTER_NAME:
            existing_id = (reporter or {}).get("id")
            if existing_id:
                desired["id"] = existing_id
            settings.raw_reporters[index] = desired
            replaced = True
            break

    if not replaced:
        settings.raw_reporters.append(desired)

    return ("updated" if replaced else "created"), None


def ensure_failure_reporter(*, client: Any, scenario: Any, recipient: Any, channel_name: Any) -> tuple[str, str | None]:
    settings = scenario.get_settings()
    status, reason = ensure_failure_reporter_on_settings(
        client=client,
        settings=settings,
        recipient=recipient,
        channel_name=channel_name,
    )
    if status in {"created", "updated"}:
        settings.save()
    return status, reason
