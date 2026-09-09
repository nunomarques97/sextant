"""Market data value objects.

A bar carries an explicit ``is_closed`` flag. A partially formed bar looks
identical to a closed one in every field that matters to a feature calculation,
so the distinction has to be carried in the type rather than inferred from a
clock comparison at the call site.
"""

from __future__ import annotations

from dataclasses import dataclass

from sextant.domain.errors import DomainError
from sextant.domain.instrument import Instrument
from sextant.domain.money import Price, Quantity
from sextant.domain.time import Timeframe, Timestamp


class OpenBarConsumed(DomainError):
    """A partially formed bar was used where a closed bar is required."""

    def __init__(self, bar: Bar) -> None:
        self.bar = bar
        super().__init__(
            f"Bar {bar.instrument} {bar.timeframe.value} opening at "
            f"{bar.open_time.isoformat()} is still open and must not be consumed."
        )


@dataclass(frozen=True, slots=True)
class Bar:
    """One OHLCV observation for one instrument at one timeframe."""

    instrument: Instrument
    timeframe: Timeframe
    open_time: Timestamp
    open: Price
    high: Price
    low: Price
    close: Price
    volume: Quantity
    is_closed: bool

    @property
    def close_time(self) -> Timestamp:
        """The instant at which this bar closes, derived from its timeframe."""
        return self.open_time.plus(self.timeframe.duration)

    def require_closed(self) -> Bar:
        """Return this bar, or raise if it is still forming."""
        if not self.is_closed:
            raise OpenBarConsumed(self)
        return self


@dataclass(frozen=True, slots=True)
class OrderBookLevel:
    """One price level of a book."""

    price: Price
    quantity: Quantity


@dataclass(frozen=True, slots=True)
class OrderBookSnapshot:
    """A depth snapshot for one instrument at one instant."""

    instrument: Instrument
    observed_at: Timestamp
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]
