"""Instruments and their trading constraints.

An instrument is keyed by ``(venue, symbol)``: the same economic pair on two
venues is two instruments, with their own tick size, lot size, minimum notional
and listing history. Delisted instruments are first-class rather than deleted,
because a historical universe that silently drops them is a survivorship-biased
universe, and survivorship bias flatters every backtest that touches it.
"""

from __future__ import annotations

from dataclasses import dataclass

from sextant.domain.errors import DomainError
from sextant.domain.money import Notional, Price, Quantity
from sextant.domain.provenance import Provenance
from sextant.domain.time import Timestamp
from sextant.domain.venue import Venue


class InvalidInstrument(DomainError):
    """An instrument was constructed with an inconsistent listing window."""


@dataclass(frozen=True, slots=True, order=True)
class InstrumentKey:
    """The identity of an instrument: a venue and a venue-native symbol."""

    venue: Venue
    symbol: str

    def __str__(self) -> str:
        return f"{self.venue.name}:{self.symbol}"


@dataclass(frozen=True, slots=True)
class Instrument:
    """A tradable pair on one venue, with its point-in-time listing window."""

    venue: Venue
    symbol: str
    base: str
    quote: str
    listed_at: Timestamp
    tick_size: Price
    lot_size: Quantity
    min_notional: Notional
    delisted_at: Timestamp | None = None
    provenance: Provenance = Provenance.UNVERIFIED
    """How the listing window above was established. Defaults to unverified on
    purpose: an instrument built without stating a source is not evidence."""

    def __post_init__(self) -> None:
        if self.delisted_at is not None and self.delisted_at <= self.listed_at:
            raise InvalidInstrument(
                f"{self.venue.name}:{self.symbol} delisted_at "
                f"({self.delisted_at.isoformat()}) must be after listed_at "
                f"({self.listed_at.isoformat()})"
            )

    @property
    def key(self) -> InstrumentKey:
        """The ``(venue, symbol)`` identity of this instrument."""
        return InstrumentKey(self.venue, self.symbol)

    def is_listed_at(self, at: Timestamp) -> bool:
        """Whether this instrument was tradable at ``at``.

        The window is inclusive of the listing instant and exclusive of the
        delisting instant.
        """
        if at < self.listed_at:
            return False
        return self.delisted_at is None or at < self.delisted_at

    def __str__(self) -> str:
        return str(self.key)
