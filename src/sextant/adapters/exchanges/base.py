"""Shared scaffolding for exchange adapters.

This holds only what is genuinely venue-neutral: how the three capability
layers are combined, and unimplemented port methods. It contains no branch on
any venue's identity, and it never will - a venue's differences belong in that
venue's own package, expressed as declared data and its own request handling.

Every port method raises ``NotImplementedError`` in SEXTANT-001. No adapter
makes a network call yet.
"""

from __future__ import annotations

from collections.abc import Sequence

from sextant.domain.availability import VenueHealth
from sextant.domain.capability import Capability, CapabilitySet
from sextant.domain.instrument import Instrument
from sextant.domain.market_data import Bar, OrderBookSnapshot
from sextant.domain.time import Timeframe, Timestamp
from sextant.domain.venue import Venue
from sextant.ports.exchange import WithdrawalPermission


class BaseExchangeClient:
    """Common construction and capability arithmetic for a venue adapter.

    Subclasses declare ``VENUE`` and ``VENUE_CAPABILITIES``. Everything else is
    supplied at construction from configuration, because account permissions and
    jurisdictional eligibility are runtime facts, not properties of the code.
    """

    #: The venue this adapter speaks to. Declared by the subclass.
    VENUE: Venue
    #: What this exchange supports at all, irrespective of who is asking.
    VENUE_CAPABILITIES: frozenset[Capability] = frozenset()

    def __init__(
        self,
        account_permitted: frozenset[Capability],
        jurisdiction_eligible: frozenset[Capability],
    ) -> None:
        self._account_permitted = account_permitted
        self._jurisdiction_eligible = jurisdiction_eligible

    @property
    def venue(self) -> Venue:
        """The identity of this venue. Used for labelling and records, never for branching."""
        return self.VENUE

    def capabilities(self) -> CapabilitySet:
        """The intersection of the venue, account and jurisdiction layers."""
        return CapabilitySet(
            venue=self.VENUE,
            venue_supported=self.VENUE_CAPABILITIES,
            account_permitted=self._account_permitted,
            jurisdiction_eligible=self._jurisdiction_eligible,
        )

    def health(self) -> VenueHealth:
        """Current runtime availability. Wired in the exchange phase."""
        raise NotImplementedError(f"{type(self).__name__}.health is not wired yet")

    def instruments(self, at: Timestamp) -> Sequence[Instrument]:
        """Every instrument listed as of ``at``. Wired in the exchange phase."""
        raise NotImplementedError(f"{type(self).__name__}.instruments is not wired yet")

    def get_bars(
        self,
        instruments: Sequence[Instrument],
        timeframe: Timeframe,
        start: Timestamp,
        end: Timestamp,
    ) -> Sequence[Bar]:
        """Bars for an explicit collection of instruments. Wired in the exchange phase."""
        raise NotImplementedError(f"{type(self).__name__}.get_bars is not wired yet")

    def get_order_book(self, instrument: Instrument) -> OrderBookSnapshot:
        """A depth snapshot for one instrument. Wired in the exchange phase."""
        raise NotImplementedError(f"{type(self).__name__}.get_order_book is not wired yet")

    def withdrawal_permission(self) -> WithdrawalPermission:
        """Whether the configured key can withdraw funds.

        Until a venue implements a real probe this reports ``UNKNOWN``, and
        preflight treats unknown as unsafe. LIVE therefore cannot start today,
        which is the correct behaviour rather than a gap.
        """
        return WithdrawalPermission.UNKNOWN
