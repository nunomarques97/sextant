"""Point-in-time market statistics.

Every function here answers a question *as of* an instant, using only
observations that had closed by that instant. That is not politeness towards
the backtester: a median volume computed over a window that includes tomorrow
admits instruments on the strength of information the decision could not have
had, and the resulting universe is a list of things that turned out well.

Pure computation. No I/O, no clock, no venue.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from sextant.domain.instrument import InstrumentKey
from sextant.domain.money import Notional, Price
from sextant.domain.time import Timeframe, Timestamp

_DAY_MILLIS = int(Timeframe.D1.duration.total_seconds() * 1000)


@dataclass(frozen=True, slots=True)
class DailyObservation:
    """One closed daily bar, reduced to the fields the universe rules need."""

    open_time: Timestamp
    close: Price
    quote_volume: Notional | None

    def close_time(self, timeframe: Timeframe = Timeframe.D1) -> Timestamp:
        """When this observation became knowable."""
        return self.open_time.plus(timeframe.duration)


@dataclass(frozen=True, slots=True)
class InstrumentHistory:
    """One instrument's closed daily observations, in ascending time order.

    ``open_millis`` mirrors the observation times so that the point-in-time
    lookups are a binary search rather than a scan. That is not premature: the
    universe is re-evaluated at every monthly refresh for every candidate, and a
    linear scan there turns a report into an overnight job.
    """

    key: InstrumentKey
    observations: tuple[DailyObservation, ...]
    open_millis: tuple[int, ...] = field(default=(), repr=False)

    @classmethod
    def of(cls, key: InstrumentKey, observations: Iterable[DailyObservation]) -> InstrumentHistory:
        """Build a history, sorting defensively so callers need not."""
        ordered = tuple(sorted(observations, key=lambda item: item.open_time))
        return cls(
            key=key,
            observations=ordered,
            open_millis=tuple(item.open_time.epoch_millis for item in ordered),
        )

    def _closed_count(self, at: Timestamp) -> int:
        """How many observations had closed by ``at``.

        A daily bar closes one day after it opens, so "closed by ``at``" is
        "opened at or before ``at`` minus one day". This is the look-ahead
        boundary, and it is the only place it is expressed.
        """
        cutoff = at.epoch_millis - _DAY_MILLIS
        return bisect_right(self.open_millis, cutoff)

    def known_at(self, at: Timestamp) -> Sequence[DailyObservation]:
        """Observations that had closed by ``at``."""
        return self.observations[: self._closed_count(at)]

    def window_ending_at(self, at: Timestamp, lookback_days: int) -> Sequence[DailyObservation]:
        """Closed observations in the ``lookback_days`` before ``at``."""
        end = self._closed_count(at)
        floor = at.plus(-timedelta(days=lookback_days)).epoch_millis
        start = bisect_left(self.open_millis, floor, 0, end)
        return self.observations[start:end]

    def median_quote_volume(self, at: Timestamp, lookback_days: int) -> Notional | None:
        """Median quote turnover over the trailing window, or None if unknowable.

        None rather than zero. An instrument with no observations and an
        instrument that traded nothing are different facts, and collapsing them
        would let a data gap masquerade as a liquidity verdict.
        """
        volumes = [
            item.quote_volume.amount
            for item in self.window_ending_at(at, lookback_days)
            if item.quote_volume is not None
        ]
        if not volumes:
            return None
        return Notional(_median(volumes))

    def close_as_of(self, at: Timestamp) -> Price | None:
        """The most recent close knowable at ``at``, or None if there is none."""
        count = self._closed_count(at)
        return self.observations[count - 1].close if count else None

    def observation_count_at(self, at: Timestamp, lookback_days: int) -> int:
        """How many closed observations back the trailing window."""
        return len(self.window_ending_at(at, lookback_days))

    @property
    def first_open_time(self) -> Timestamp | None:
        """The earliest observation, or None when the history is empty."""
        return self.observations[0].open_time if self.observations else None

    @property
    def last_open_time(self) -> Timestamp | None:
        """The latest observation, or None when the history is empty."""
        return self.observations[-1].open_time if self.observations else None


def _median(values: Sequence[Decimal]) -> Decimal:
    """Exact median of a non-empty sequence of Decimals.

    Median rather than mean throughout the universe rules: both volume and
    spread distributions are dominated by outliers, and a mean lets one frantic
    day admit an instrument that was untradable for the other twenty-nine.
    """
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)
