from __future__ import annotations

import types

from pulse_init import notifications as n


class FakeChannel:
    def __init__(self, *, id: str, name: str | None = None, label: str | None = None):
        self.id = id
        self.name = name
        self.label = label


class FakeClient:
    def __init__(self, channels):
        self._channels = channels

    def list_messaging_channels(self):
        return self._channels


class FakeSettings:
    def __init__(self, reporters):
        self.raw_reporters = reporters
        self.saved = False

    def get_raw(self):
        return {"reporters": self.raw_reporters}

    def save(self):
        self.saved = True


class FakeScenario:
    def __init__(self, settings):
        self._settings = settings

    def get_settings(self):
        return self._settings


def test_notification_enabled_requires_both_fields():
    assert n.notification_enabled(recipient="a@example.com", channel_name="mail_engine")
    assert not n.notification_enabled(recipient="", channel_name="mail_engine")
    assert not n.notification_enabled(recipient="a@example.com", channel_name="")


def test_find_matching_channel_by_id_or_name():
    client = FakeClient([
        FakeChannel(id="mazzei_designer", name="mazzei_designer"),
        FakeChannel(id="other", name="other"),
    ])

    channel_id, reason = n._find_matching_channel(client, "mazzei_designer")
    assert channel_id == "mazzei_designer"
    assert reason is None


def test_find_matching_channel_missing_warns():
    client = FakeClient([FakeChannel(id="other", name="other")])
    channel_id, reason = n._find_matching_channel(client, "mazzei_designer")
    assert channel_id is None
    assert "not found" in str(reason)


def test_find_matching_channel_ambiguous_warns():
    client = FakeClient([
        FakeChannel(id="dup", name="dup"),
        FakeChannel(id="other", name="dup"),
    ])
    channel_id, reason = n._find_matching_channel(client, "dup")
    assert channel_id is None
    assert "multiple" in str(reason)


def test_build_failure_reporter_shape():
    reporter = n._build_failure_reporter(
        recipient="alerts@example.com",
        channel_id="mazzei_designer",
    )
    assert reporter["name"] == n.REPORTER_NAME
    assert reporter["messaging"]["type"] == "mail-scenario"
    assert reporter["messaging"]["configuration"]["channelId"] == "mazzei_designer"
    assert reporter["messaging"]["configuration"]["recipient"] == "alerts@example.com"
    assert reporter["runCondition"] == "outcome != 'SUCCESS'"
    assert reporter["phase"] == "END"


def test_ensure_failure_reporter_skips_when_incomplete():
    settings = FakeSettings([])
    scenario = FakeScenario(settings)
    status, reason = n.ensure_failure_reporter(
        client=FakeClient([]),
        scenario=scenario,
        recipient="",
        channel_name="",
    )
    assert status == "skipped"
    assert "incomplete" in str(reason)
    assert not settings.saved


def test_ensure_failure_reporter_creates_and_preserves_user_reporters():
    user_reporter = {"name": "user_x", "foo": "bar"}
    settings = FakeSettings([user_reporter])
    scenario = FakeScenario(settings)

    status, reason = n.ensure_failure_reporter(
        client=FakeClient([FakeChannel(id="mazzei_designer", name="mazzei_designer")]),
        scenario=scenario,
        recipient="alerts@example.com",
        channel_name="mazzei_designer",
    )
    assert status == "created"
    assert reason is None
    assert settings.saved
    assert user_reporter in settings.raw_reporters
    pulse_reporters = [r for r in settings.raw_reporters if r.get("name") == n.REPORTER_NAME]
    assert len(pulse_reporters) == 1


def test_ensure_failure_reporter_updates_existing_pulse_reporter_only():
    user_reporter = {"name": "user_x", "foo": "bar"}
    pulse_reporter = n._build_failure_reporter(
        recipient="old@example.com",
        channel_id="mazzei_designer",
    )
    pulse_reporter["id"] = "server-generated-id"
    settings = FakeSettings([user_reporter, pulse_reporter])
    scenario = FakeScenario(settings)

    status, reason = n.ensure_failure_reporter(
        client=FakeClient([FakeChannel(id="mazzei_designer", name="mazzei_designer")]),
        scenario=scenario,
        recipient="new@example.com",
        channel_name="mazzei_designer",
    )
    assert status == "updated"
    assert reason is None
    assert user_reporter in settings.raw_reporters
    updated = next(r for r in settings.raw_reporters if r.get("name") == n.REPORTER_NAME)
    assert updated["messaging"]["configuration"]["recipient"] == "new@example.com"
    assert updated["id"] == "server-generated-id"


def test_ensure_failure_reporter_warns_on_unknown_channel():
    settings = FakeSettings([])
    scenario = FakeScenario(settings)
    status, reason = n.ensure_failure_reporter(
        client=FakeClient([FakeChannel(id="other", name="other")]),
        scenario=scenario,
        recipient="alerts@example.com",
        channel_name="mazzei_designer",
    )
    assert status == "warning"
    assert "not found" in str(reason)
    assert not settings.saved
