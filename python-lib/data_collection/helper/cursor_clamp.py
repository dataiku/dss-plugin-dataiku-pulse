"""High-water-mark cursor clamping.

A gather run advances a cursor to the max timestamp it processed. When part of
the run fails, advancing past the failed data silently loses it forever. This
helper tracks successes and failures separately and resolves a final cursor
that never advances past the earliest failure (and never moves backwards).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


def _as_utc(ts) -> pd.Timestamp | None:
    if ts is None:
        return None
    out = pd.to_datetime(ts, utc=True, errors="coerce")
    if pd.isna(out):
        return None
    return pd.Timestamp(out)


@dataclass(frozen=True)
class ClampResolution:
    """Outcome of resolving a cursor at the end of a run.

    - `final_ts` is None when the cursor should not be updated at all
      (nothing succeeded, or an unlocatable failure pinned the cursor and no
      previous value exists).
    - `clamped_by_failures` is True when failures pulled the cursor below the
      max successfully processed timestamp — i.e. some data will be re-read.
    """

    final_ts: pd.Timestamp | None
    clamped_by_failures: bool
    reasons: tuple[str, ...] = ()


@dataclass
class CursorClamp:
    """Collects success/failure timestamps during a run.

    Usage:
    - `advance(ts)` after each successfully processed unit (max wins).
    - `record_failure(ts, reason=...)` for each failed unit, passing the
      earliest timestamp the failed unit's data may contain. Pass `ts=None`
      when that position is unknown — the cursor is then pinned to its
      previous value (no advance at all).
    - `resolve(previous=...)` at the end of the run.
    """

    _pending: pd.Timestamp | None = None
    _min_failure: pd.Timestamp | None = None
    _pin_to_previous: bool = False
    _reasons: list[str] = field(default_factory=list)

    def advance(self, ts) -> None:
        parsed = _as_utc(ts)
        if parsed is None:
            return
        if self._pending is None or parsed > self._pending:
            self._pending = parsed

    def record_failure(self, ts, *, reason: str) -> None:
        self._reasons.append(reason)
        parsed = _as_utc(ts)
        if parsed is None:
            self._pin_to_previous = True
            return
        if self._min_failure is None or parsed < self._min_failure:
            self._min_failure = parsed

    @property
    def has_failures(self) -> bool:
        return self._pin_to_previous or self._min_failure is not None

    @property
    def pending(self) -> pd.Timestamp | None:
        return self._pending

    def resolve(self, *, previous=None) -> ClampResolution:
        previous_ts = _as_utc(previous)

        if self._pending is None:
            # Nothing succeeded: never advance. Report the previous cursor so
            # callers can distinguish "keep as-is" from "no cursor at all".
            return ClampResolution(
                final_ts=previous_ts if self.has_failures else None,
                clamped_by_failures=self.has_failures,
                reasons=tuple(self._reasons),
            )

        final = self._pending
        if self._pin_to_previous:
            final = previous_ts if previous_ts is not None else None
        elif self._min_failure is not None and self._min_failure < final:
            final = self._min_failure

        # Never move the cursor backwards.
        if final is not None and previous_ts is not None and final < previous_ts:
            final = previous_ts

        clamped = final is None or final < self._pending
        return ClampResolution(
            final_ts=final,
            clamped_by_failures=clamped and self.has_failures,
            reasons=tuple(self._reasons),
        )
