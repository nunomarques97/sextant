"""The ExchangeClient port.

Every method takes the instrument or instruments it operates on explicitly.
There is no ambient "current symbol" and no configured default: a call that
resolves its own symbol from global state is the defect this port is shaped to
prevent, because it makes a cross-sectional strategy impossible to express and
a single-symbol assumption impossible to see in review.

Callers never ask which venue they are talking to. They ask whether the
capability they need is in ``capabilities()``.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Protocol, runtime_checkable

from sextant.domain.availability import VenueHealth
from sextant.domain.capability import CapabilitySet
from sextant.domain.instrument import Instrument
from sextant.domain.market_data import Bar, OrderBookSnapshot
from sextant.domain.time import Timeframe, Timestamp
from sextant.domain.venue import Venue


class WithdrawalPermission(StrEnum):
    """Whether the configured API key can move funds off the venue.

    ``UNKNOWN`` is treated as unsafe. A key whose permissions cannot be read is
    not a key that has been shown to be harmless.
    """

    ABSENT = "absent"
    PRESENT = "present"
    UNKNOWN = "unknown"


@runtime_checkable
class ExchangeClient(Protocol):
    """One venue, as the rest of the system sees it."""

    @property
    def venue(self) -> Venue:
        """The identity of this venue. Used for labelling and records, never for branching."""
        ...

    def capabilities(self) -> CapabilitySet:
        """The intersection of the venue, account and jurisdiction layers."""
        ...

    def health(self) -> VenueHealth:
        """Current runtime availability. Transient, and never a capability."""
        ...

    def instruments(self, at: Timestamp) -> Sequence[Instrument]:
        """Every instrument this venue listed as of ``at``, including later delistings."""
        ...

    def get_bars(
        self,
        instruments: Sequence[Instrument],
        timeframe: Timeframe,
        start: Timestamp,
        end: Timestamp,
    ) -> Sequence[Bar]:
        """Bars for an explicit collection of instruments over an explicit window."""
        ...

    def get_order_book(self, instrument: Instrument) -> OrderBookSnapshot:
        """A depth snapshot for one explicitly named instrument."""
        ...

    def withdrawal_permission(self) -> WithdrawalPermission:
        """Whether the configured key can withdraw. Checked before LIVE may start."""
        ...
