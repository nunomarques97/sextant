"""Kraken ExchangeClient. No network call exists yet.

Every port method inherits a ``NotImplementedError`` body from
``BaseExchangeClient``. The adapter is created now so that the layering
contract, the capability model and the wiring have something real to bind to
before any request code is written.
"""

from __future__ import annotations

from sextant.adapters.exchanges.base import BaseExchangeClient
from sextant.adapters.exchanges.kraken.capabilities import VENUE, VENUE_CAPABILITIES
from sextant.domain.capability import Capability
from sextant.domain.venue import Venue


class KrakenClient(BaseExchangeClient):
    """Kraken, as an ExchangeClient."""

    VENUE: Venue = VENUE
    VENUE_CAPABILITIES: frozenset[Capability] = VENUE_CAPABILITIES
