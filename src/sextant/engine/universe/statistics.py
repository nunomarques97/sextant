"""Point-in-time market statistics.

Every function here answers a question *as of* an instant, using only
observations that had closed by that instant. That is not politeness towards
the backtester: a median volume computed over a window that includes tomorrow
admits instruments on the strength of information the decision could not have
had, and the resulting universe is a list of things that turned out well.

Pure computation. No I/O, no clock, no venue.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from sextant.domain.instrument import InstrumentKey
from sextant.domain.money import Notional, Price
from sextant.domain.time import Timeframe, Timestamp


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
    """One instrument's closed daily observations, in ascending time order."""

    key: InstrumentKey
    observations: tuple[DailyObservation, ...]

    @classmethod
    def of(cls, key: InstrumentKey, observations: Iterable[DailyObservation]) -> InstrumentHistory:
        """Build a history, sorting defensively so callers need not."""
        return cls(key=key, observations=tuple(sorted(observations, key=lambda o: o.open_time)))

    def known_at(self, at: Timestamp) -> Sequence[DailyObservation]:
        """Observations that had closed by ``at``. The look-ahead boundary."""
        return tuple(item for item in self.observations if item.close_time() <= at)

    def window_ending_at(self, at: Timestamp, lookback_days: int) -> Sequence[DailyObservation]:
        """Closed observations in the ``lookback_days`` before ``at``."""
        floor = at.plus(-timedelta(days=lookback_days))
        return tuple(item for item in self.known_at(at) if item.open_time >= floor)

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
        known = self.known_at(at)
        return known[-1].close if known else None

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
