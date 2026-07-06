from __future__ import annotations

import types

from pulse_init.notifications import (
    PULSE_REPORTER_NAME,
    build_failure_reporter,
    ensure_failure_reporter,
    resolve_email_channel_id,
)


def _channel(id_, family=None, type_=None):
    return types.SimpleNamespace(id=id_, family=family, type=type_)


class FakeMessagingClient:
    def __init__(self, channels):
        self._channels = channels

    def list_messaging_channels(self):
        return self._channels


class RaisesAttributeErrorClient:
    def list_messaging_channels(self):
        raise AttributeError("list_messaging_channels")


# ---------------------------------------------------------------------------
# resolve_email_channel_id
# ---------------------------------------------------------------------------


def test_resolve_email_channel_id_single_mail_channel_no_preference():
    client = FakeMessagingClient([_channel("c1", family="mail")])

    channel_id, channel_type, reason = resolve_email_channel_id(
        client, preferred_channel_id=None
    )

    assert channel_id == "c1"
    assert channel_type == "mail"
    assert reason is None


def test_resolve_email_channel_id_zero_mail_channels():
    client = FakeMessagingClient([])

    channel_id, channel_type, reason = resolve_email_channel_id(
        client, preferred_channel_id=None
    )

    assert channel_id is None
    assert channel_type is None
    assert reason
    assert "no mail messaging channel" in reason


def test_resolve_email_channel_id_multiple_mail_channels():
    client = FakeMessagingClient(
        [_channel("c1", family="mail"), _channel("c2", family="mail")]
    )

    channel_id, channel_type, reason = resolve_email_channel_id(
        client, preferred_channel_id=None
    )

    assert channel_id is None
    assert channel_type is None
    assert "c1" in reason
    assert "c2" in reason


def test_resolve_email_channel_id_preferred_hit_even_non_mail_family():
    client = FakeMessagingClient(
        [_channel("c1", family="mail"), _channel("s1", family="slack")]
    )

    channel_id, channel_type, reason = resolve_email_channel_id(
        client, preferred_channel_id="s1"
    )

    assert channel_id == "s1"
    assert channel_type == "slack"
    assert reason is None


def test_resolve_email_channel_id_preferred_miss_lists_available():
    client = FakeMessagingClient(
        [_channel("c1", family="mail"), _channel("c2", family="mail")]
    )

    channel_id, channel_type, reason = resolve_email_channel_id(
        client, preferred_channel_id="missing"
    )

    assert channel_id is None
    assert channel_type is None
    assert "c1" in reason
    assert "c2" in reason


def test_resolve_email_channel_id_attribute_error_skip_reason_mentions_dss_version():
    client = RaisesAttributeErrorClient()

    channel_id, channel_type, reason = resolve_email_channel_id(
        client, preferred_channel_id=None
    )

    assert channel_id is None
    assert channel_type is None
    assert "DSS version" in reason or "dss version" in reason.lower()


# ---------------------------------------------------------------------------
# build_failure_reporter
# ---------------------------------------------------------------------------


def test_build_failure_reporter_mail_channel():
    reporter, reason = build_failure_reporter(
        channel_id="c1",
        channel_type="mail",
        recipient="alerts@example.com",
        scenario_name="my_scenario",
        project_key="PROJ",
        instance_label="prod",
    )

    assert reason is None
    assert reporter is not None
    assert reporter["name"] == PULSE_REPORTER_NAME == "pulse_failure_email"
    assert "||" in reporter["runCondition"]
    assert " or " not in reporter["runCondition"]

    config = reporter["messaging"]["configuration"]
    assert config["channelId"] == "c1"
    assert config["recipient"] == "alerts@example.com"
    assert config["subject"]
    assert "${scenarioRunURL}" not in config["message"]


def test_build_failure_reporter_non_mail_channel_returns_reason():
    reporter, reason = build_failure_reporter(
        channel_id="s1",
        channel_type="slack",
        recipient="alerts@example.com",
        scenario_name="my_scenario",
        project_key="PROJ",
        instance_label="prod",
    )

    assert reporter is None
    assert reason


# ---------------------------------------------------------------------------
# ensure_failure_reporter
# ---------------------------------------------------------------------------


class FakeSettings:
    def __init__(self, reporters):
        self.raw_reporters = reporters


def _build_reporter(recipient="alerts@example.com"):
    reporter, reason = build_failure_reporter(
        channel_id="c1",
        channel_type="mail",
        recipient=recipient,
        scenario_name="my_scenario",
        project_key="PROJ",
        instance_label="prod",
    )
    assert reason is None
    return reporter


def test_ensure_failure_reporter_idempotency_and_user_reporters_untouched():
    user_reporter = {"name": "user_x", "foo": "bar"}
    settings = FakeSettings([user_reporter])

    # created on empty (only the user reporter present)
    reporter = _build_reporter()
    status = ensure_failure_reporter(settings, reporter=reporter)
    assert status == "created"
    assert len(settings.raw_reporters) == 2
    assert user_reporter in settings.raw_reporters

    pulse_idx = next(
        i for i, r in enumerate(settings.raw_reporters) if r.get("name") == PULSE_REPORTER_NAME
    )
    # Simulate server normalization: an extra "id" key gets added by the server.
    settings.raw_reporters[pulse_idx]["id"] = "server-generated-id"
    stored_with_id = dict(settings.raw_reporters[pulse_idx])

    # unchanged when called again with the same reporter
    status = ensure_failure_reporter(settings, reporter=reporter)
    assert status == "unchanged"
    assert settings.raw_reporters[pulse_idx] == stored_with_id
    assert user_reporter in settings.raw_reporters

    # updated when recipient changes
    updated_reporter = _build_reporter(recipient="new-alerts@example.com")
    status = ensure_failure_reporter(settings, reporter=updated_reporter)
    assert status == "updated"
    pulse_idx = next(
        i for i, r in enumerate(settings.raw_reporters) if r.get("name") == PULSE_REPORTER_NAME
    )
    assert (
        settings.raw_reporters[pulse_idx]["messaging"]["configuration"]["recipient"]
        == "new-alerts@example.com"
    )
    # server-generated id preserved through the update
    assert settings.raw_reporters[pulse_idx]["id"] == "server-generated-id"
    assert user_reporter in settings.raw_reporters

    # removed when reporter=None
    status = ensure_failure_reporter(settings, reporter=None)
    assert status == "removed"
    assert all(r.get("name") != PULSE_REPORTER_NAME for r in settings.raw_reporters)
    assert user_reporter in settings.raw_reporters

    # removing again when already absent is a no-op
    status = ensure_failure_reporter(settings, reporter=None)
    assert status == "unchanged"
    assert settings.raw_reporters == [user_reporter]
