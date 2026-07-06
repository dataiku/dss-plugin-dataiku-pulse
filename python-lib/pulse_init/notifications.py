"""Failure-notification reporters for Pulse-created scenarios.

Every scenario Pulse creates gets an email reporter that fires at scenario END
when the outcome is FAILED or ABORTED. The recipient and messaging channel come
from plugin settings (`notification_email`, `notification_channel_id`); when no
channel is specified and the target instance has exactly one mail channel, that
channel is auto-selected.

The reporter raw shape below was captured live from DSS 14.7
(`scripts/verify_api_shapes.py`, 2026-07-02) — re-run that script against the
oldest DSS in the fleet before changing the template. Verified findings baked
in here:

- the channel reference lives at `messaging.configuration.channelId`
  (a top-level `messaging.channelId` is silently dropped on save);
- run conditions use `||`, not `or` (`or` silently never activates);
- `${scenarioRunURL}` is NOT an available Freemarker variable — referencing it
  makes the whole email fail with an InvalidReferenceException. Only
  `${scenarioName}`, `${scenarioProjectKey}` and `${outcome}` are used; the
  scenario link is rendered statically at build time instead;
- a custom `name` key persists on reporter dicts, so idempotent upsert keys
  on `name == PULSE_REPORTER_NAME` and never touches user-added reporters.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

logger = logging.getLogger(__name__)

PULSE_REPORTER_NAME = "pulse_failure_email"

# Channel family/type values treated as email-capable.
_MAIL_KINDS = {"mail", "smtp", "aws-ses-mail", "microsoft-graph-mail"}

# Captured from a server-normalized reporter on DSS 14.7 (see module docstring).
_MAIL_REPORTER_TEMPLATE: dict[str, Any] = {
    "name": PULSE_REPORTER_NAME,
    "active": True,
    "phase": "END",
    "runConditionEnabled": True,
    "runCondition": "outcome == 'FAILED' || outcome == 'ABORTED'",
    "messaging": {
        "type": "mail-scenario",
        "configuration": {
            "channelId": "",
            "recipient": "",
            "subject": "",
            "message": "",
            "sendAsHTML": False,
            "attachments": [],
            "messageSource": "INLINE",
            "templateFormat": "FREEMARKER",
        },
    },
}


def resolve_email_channel_id(
    client: Any,
    *,
    preferred_channel_id: str | None,
) -> tuple[str | None, str | None, str | None]:
    """Resolve the messaging channel to use on one target instance.

    Returns (channel_id, channel_type, skip_reason). Exactly one of
    channel_id / skip_reason is set.

    - With `preferred_channel_id`: the channel must exist by id; it is used
      whatever its family (email is only the default behavior).
    - Without: exactly one mail-family channel must exist; 0 or >1 yields an
      actionable skip reason listing the candidates.
    """

    try:
        channels = client.list_messaging_channels() or []
    except AttributeError:
        return (
            None,
            None,
            "this DSS version does not support listing messaging channels "
            "(client.list_messaging_channels missing); set up the reporter manually",
        )
    except Exception as exc:  # noqa: BLE001 - per-instance resolution must not raise
        return None, None, f"could not list messaging channels: {exc!r}"

    def _kind(ch: Any) -> str:
        # Family ("mail") is what mail-detection needs; fall back to the
        # concrete type ("smtp") on clients that don't expose family.
        return str(getattr(ch, "family", "") or getattr(ch, "type", "") or "")

    by_id = {str(getattr(ch, "id", "")): ch for ch in channels}

    if preferred_channel_id:
        ch = by_id.get(str(preferred_channel_id))
        if ch is None:
            available = ", ".join(sorted(k for k in by_id if k)) or "<none>"
            return (
                None,
                None,
                f"channel {preferred_channel_id!r} not found on this instance "
                f"(available: {available})",
            )
        return str(getattr(ch, "id")), _kind(ch), None

    mail_channels = [ch for ch in channels if _kind(ch).lower() in _MAIL_KINDS]
    if len(mail_channels) == 1:
        ch = mail_channels[0]
        return str(getattr(ch, "id")), _kind(ch), None
    if not mail_channels:
        return (
            None,
            None,
            "no mail messaging channel exists on this instance; create one or set "
            "'Notification messaging channel id' in the Pulse settings",
        )
    candidates = ", ".join(sorted(str(getattr(ch, "id", "")) for ch in mail_channels))
    return (
        None,
        None,
        f"multiple mail channels exist on this instance ({candidates}); set "
        "'Notification messaging channel id' in the Pulse settings to pick one",
    )


def build_failure_reporter(
    *,
    channel_id: str,
    channel_type: str,
    recipient: str,
    scenario_name: str,
    project_key: str,
    instance_label: str,
    instance_url: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Build the reporter dict for one scenario. Returns (reporter, skip_reason).

    Only the mail-family shape has been captured/verified; a non-mail channel
    (possible when the user explicitly specified one) yields a skip reason
    rather than an unverified payload.
    """

    is_mail = str(channel_type or "").lower() in _MAIL_KINDS
    if not is_mail:
        return None, (
            f"channel {channel_id!r} has unsupported type {channel_type!r}: only "
            "mail-family reporter payloads are supported by Pulse so far"
        )

    scenario_link = ""
    if instance_url:
        scenario_link = (
            f"\n\nScenario: {instance_url.rstrip('/')}/projects/{project_key}"
            f"/scenarios/{scenario_name}"
        )

    reporter = copy.deepcopy(_MAIL_REPORTER_TEMPLATE)
    config = reporter["messaging"]["configuration"]
    config["channelId"] = channel_id
    config["recipient"] = recipient
    config["subject"] = f"Pulse scenario FAILED: {instance_label}/{project_key}/{scenario_name}"
    # Only Freemarker variables verified to exist may appear here (see module docstring).
    config["message"] = (
        "Pulse scenario ${scenarioName} (project ${scenarioProjectKey}) on instance "
        f"{instance_label} finished with outcome ${{outcome}}.{scenario_link}\n\n"
        "This reporter is managed by the Dataiku Pulse plugin "
        f"({PULSE_REPORTER_NAME}); it is re-created on every Pulse init run."
    )
    return reporter, None


def ensure_failure_reporter(settings: Any, *, reporter: dict[str, Any] | None) -> str:
    """Idempotently upsert (or remove) the Pulse reporter on scenario settings.

    Only the reporter whose `name` is PULSE_REPORTER_NAME is ever touched;
    user-added reporters are preserved as-is. `reporter=None` removes a stale
    Pulse reporter (used when notifications are disabled).

    Returns "created" | "updated" | "unchanged" | "removed". The caller is
    responsible for `settings.save()`.
    """

    reporters = settings.raw_reporters
    existing_idx = next(
        (
            i
            for i, r in enumerate(reporters)
            if isinstance(r, dict) and r.get("name") == PULSE_REPORTER_NAME
        ),
        None,
    )

    if reporter is None:
        if existing_idx is None:
            return "unchanged"
        del reporters[existing_idx]
        return "removed"

    if reporter.get("name") != PULSE_REPORTER_NAME:
        raise ValueError("reporter must be built by build_failure_reporter")

    if existing_idx is None:
        reporters.append(copy.deepcopy(reporter))
        return "created"

    existing = reporters[existing_idx]
    # Compare only the keys we manage: the server adds fields (e.g. `id`,
    # normalized defaults) that must be preserved and not treated as drift.
    merged = copy.deepcopy(existing)
    for key, value in reporter.items():
        if key == "messaging":
            merged_messaging = dict(merged.get("messaging") or {})
            merged_messaging["type"] = value["type"]
            merged_config = dict(merged_messaging.get("configuration") or {})
            merged_config.update(value["configuration"])
            merged_messaging["configuration"] = merged_config
            merged["messaging"] = merged_messaging
        else:
            merged[key] = value

    if merged == existing:
        return "unchanged"
    reporters[existing_idx] = merged
    return "updated"
