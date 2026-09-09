"""The venue registry.

This is the one place that knows which adapter classes exist, and it is a
lookup table rather than a chain of comparisons. Selecting a venue is a
dictionary read keyed by a configured string; adding a venue is one entry.

No venue here stands in for another and none is preferred. The mapping is
unordered by intent: the peers are peers.
"""

from __future__ import annotations

from collections.abc import Mapping

from sextant.adapters.exchanges.base import BaseExchangeClient
from sextant.adapters.exchanges.binance.client import BinanceClient
from sextant.adapters.exchanges.kraken.client import KrakenClient
from sextant.domain.errors import SextantError


class UnknownVenue(SextantError):
    """Configuration named a venue that has no adapter."""

    def __init__(self, name: str, known: Mapping[str, type[BaseExchangeClient]]) -> None:
        self.name = name
        super().__init__(
            f"No adapter is registered for venue {name!r}. "
            f"Known venues: {', '.join(sorted(known))}."
        )


EXCHANGE_ADAPTERS: Mapping[str, type[BaseExchangeClient]] = {
    BinanceClient.VENUE.name: BinanceClient,
    KrakenClient.VENUE.name: KrakenClient,
}


def adapter_for(name: str) -> type[BaseExchangeClient]:
    """Return the adapter class registered under ``name``."""
    try:
        return EXCHANGE_ADAPTERS[name]
    except KeyError as exc:
        raise UnknownVenue(name, EXCHANGE_ADAPTERS) from exc
