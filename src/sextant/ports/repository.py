"""The BarRepository port.

Storage is keyed by ``(venue, symbol, timeframe)``. The same pair on two venues
is two series, because its prices, its liquidity and its costs are different,
and averaging them would invent a market nobody can trade.

Reads take an explicit collection of instruments. There is no ambient symbol.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from sextant.domain.instrument import Instrument
from sextant.domain.market_data import Bar
from sextant.domain.time import Timeframe, Timestamp


@runtime_checkable
class BarRepository(Protocol):
    """Persistent, point-in-time bar storage."""

    def read(
        self,
        instruments: Sequence[Instrument],
        timeframe: Timeframe,
        start: Timestamp,
        end: Timestamp,
        *,
        closed_only: bool = True,
    ) -> Sequence[Bar]:
        """Bars in ``[start, end)``.

        ``closed_only`` defaults to True so that the safe read is the one you get
        by not thinking about it. Consuming a partially formed bar is opt-in and
        visible at the call site.
        """
        ...

    def write(self, bars: Sequence[Bar]) -> int:
        """Persist bars, returning how many were stored. Must be idempotent."""
        ...

    def latest_close_time(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
    ) -> Timestamp | None:
        """The close time of the most recent stored closed bar, or None if empty."""
        ...
