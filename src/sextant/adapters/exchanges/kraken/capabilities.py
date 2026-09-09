"""What this exchange supports at all - the venue layer of the capability model.

This declaration answers only "can the exchange do this?". It says nothing about
whether a particular account may do it, or whether it is permitted in the
account holder's jurisdiction. Those are the other two layers, and both are
configuration.

Source of truth: the venue's public API documentation. Reviewed by hand; the
review date belongs in the commit that changes this set.
"""

from __future__ import annotations

from sextant.domain.capability import Capability
from sextant.domain.venue import Venue

VENUE = Venue("kraken")

VENUE_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        Capability.PUBLIC_MARKET_DATA,
        Capability.HISTORICAL_OHLCV,
        Capability.ORDER_BOOK,
        Capability.FUNDING_RATE,
        Capability.ACCOUNT_DATA,
        Capability.SPOT_TRADING,
        Capability.MARGIN_TRADING,
        Capability.FUTURES_TRADING,
        Capability.POST_ONLY_ORDERS,
        Capability.WEBSOCKET_STREAMS,
    }
)
