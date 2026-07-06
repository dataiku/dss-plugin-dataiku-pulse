from __future__ import annotations

import pandas as pd

from data_collection.helper.cursor_clamp import CursorClamp


def _ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


def test_advance_only_final_is_max_advanced_ts():
    clamp = CursorClamp()
    clamp.advance("2024-01-01T00:00:00Z")
    clamp.advance("2024-01-03T00:00:00Z")
    clamp.advance("2024-01-02T00:00:00Z")  # not the max, should not win

    resolution = clamp.resolve()

    assert resolution.final_ts == _ts("2024-01-03T00:00:00Z")
    assert resolution.clamped_by_failures is False


def test_failure_below_pending_clamps_and_carries_reason():
    clamp = CursorClamp()
    clamp.advance("2024-01-10T00:00:00Z")  # T2
    clamp.record_failure("2024-01-05T00:00:00Z", reason="boom")  # T1 < T2

    resolution = clamp.resolve()

    assert resolution.final_ts == _ts("2024-01-05T00:00:00Z")
    assert resolution.clamped_by_failures is True
    assert resolution.reasons == ("boom",)


def test_failure_above_pending_does_not_clamp():
    clamp = CursorClamp()
    clamp.advance("2024-01-05T00:00:00Z")  # T2
    clamp.record_failure("2024-01-10T00:00:00Z", reason="irrelevant")  # T3 > T2

    resolution = clamp.resolve()

    assert resolution.final_ts == _ts("2024-01-05T00:00:00Z")
    assert resolution.clamped_by_failures is False


def test_unknown_failure_position_pins_to_previous():
    clamp = CursorClamp()
    clamp.advance("2024-01-10T00:00:00Z")
    clamp.record_failure(None, reason="unknown position")

    previous = _ts("2024-01-01T00:00:00Z")
    resolution = clamp.resolve(previous=previous)

    assert resolution.final_ts == previous
    assert resolution.clamped_by_failures is True


def test_unknown_failure_position_with_no_previous_gives_none():
    clamp = CursorClamp()
    clamp.advance("2024-01-10T00:00:00Z")
    clamp.record_failure(None, reason="unknown position")

    resolution = clamp.resolve(previous=None)

    assert resolution.final_ts is None
    assert resolution.clamped_by_failures is True


def test_failure_below_previous_floors_at_previous():
    clamp = CursorClamp()
    previous = _ts("2024-01-05T00:00:00Z")
    clamp.advance("2024-01-10T00:00:00Z")
    clamp.record_failure("2024-01-01T00:00:00Z", reason="ancient failure")  # below previous

    resolution = clamp.resolve(previous=previous)

    assert resolution.final_ts == previous


def test_nothing_succeeded_but_failure_falls_back_to_previous():
    clamp = CursorClamp()
    clamp.record_failure("2024-01-01T00:00:00Z", reason="nothing succeeded")

    previous = _ts("2024-01-05T00:00:00Z")
    resolution = clamp.resolve(previous=previous)

    assert resolution.final_ts == previous
    assert resolution.clamped_by_failures is True


def test_nothing_at_all_gives_none_and_not_clamped():
    clamp = CursorClamp()

    resolution = clamp.resolve(previous=_ts("2024-01-05T00:00:00Z"))

    assert resolution.final_ts is None
    assert resolution.clamped_by_failures is False
