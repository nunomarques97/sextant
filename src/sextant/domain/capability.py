"""The exchange capability model.

Whether an account may perform an operation on a venue is the intersection of
three semi-static layers:

* **venue** - what the exchange supports at all, declared by its adapter;
* **account** - what this account's tier, verification level and API key
  permissions allow, from configuration and/or a runtime probe;
* **jurisdiction** - what is permitted for the account's country of residence,
  supplied as configuration data and never hardcoded per venue.

Runtime availability is deliberately **not** modelled here. Outages, rate
limits, maintenance windows and halted markets are transient and belong to
``sextant.domain.availability``. Conflating the two would make a two-minute
outage indistinguishable from a permanent lack of support, and would retire a
venue that is merely blinking. See docs/adr/0002-exchange-capability-model.md.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum

from sextant.domain.errors import SextantError
from sextant.domain.venue import Venue


class Capability(StrEnum):
    """An operation a venue may or may not support for a given account."""

    PUBLIC_MARKET_DATA = "public_market_data"
    HISTORICAL_OHLCV = "historical_ohlcv"
    ORDER_BOOK = "order_book"
    FUNDING_RATE = "funding_rate"
    ACCOUNT_DATA = "account_data"
    SPOT_TRADING = "spot_trading"
    MARGIN_TRADING = "margin_trading"
    FUTURES_TRADING = "futures_trading"
    POST_ONLY_ORDERS = "post_only_orders"
    OCO_ORDERS = "oco_orders"
    WEBSOCKET_STREAMS = "websocket_streams"

    @classmethod
    def _missing_(cls, value: object) -> Capability | None:
        """Accept the SCREAMING_CASE spelling used in configuration files."""
        if isinstance(value, str):
            normalised = value.strip().lower()
            for member in cls:
                if member.value == normalised:
                    return member
        return None


class CapabilityLayer(StrEnum):
    """Which of the three semi-static layers a verdict came from."""

    VENUE = "venue"
    ACCOUNT = "account"
    JURISDICTION = "jurisdiction"


#: The order in which layers are consulted when attributing a denial. A venue
#: that cannot do something at all is the more fundamental fact, so it is
#: reported first; jurisdiction is reported last because it is the layer most
#: likely to change without any code change.
LAYER_PRECEDENCE: tuple[CapabilityLayer, ...] = (
    CapabilityLayer.VENUE,
    CapabilityLayer.ACCOUNT,
    CapabilityLayer.JURISDICTION,
)

#: Capabilities that cannot be exercised without API credentials. Used to decide
#: whether a run needs credentials at all, without naming any venue.
CREDENTIALED_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        Capability.ACCOUNT_DATA,
        Capability.SPOT_TRADING,
        Capability.MARGIN_TRADING,
        Capability.FUTURES_TRADING,
    }
)


class CapabilityNotAvailable(SextantError):
    """A capability was required but is denied by one of the three layers.

    This error is permanent for as long as the configuration stands. It is not
    retryable; see ``sextant.domain.availability.VenueUnavailable`` for the
    transient case.
    """

    def __init__(self, venue: Venue, capability: Capability, layer: CapabilityLayer) -> None:
        self.venue = venue
        self.capability = capability
        self.layer = layer
        super().__init__(
            f"{venue.name} cannot perform {capability.value}: denied by the {layer.value} layer."
        )


@dataclass(frozen=True, slots=True)
class CapabilitySet:
    """The three capability layers for one venue/account pair, and their intersection."""

    venue: Venue
    venue_supported: frozenset[Capability]
    account_permitted: frozenset[Capability]
    jurisdiction_eligible: frozenset[Capability]

    @classmethod
    def build(
        cls,
        venue: Venue,
        venue_supported: Iterable[Capability],
        account_permitted: Iterable[Capability],
        jurisdiction_eligible: Iterable[Capability],
    ) -> CapabilitySet:
        """Build a CapabilitySet from any iterables of capabilities."""
        return cls(
            venue=venue,
            venue_supported=frozenset(venue_supported),
            account_permitted=frozenset(account_permitted),
            jurisdiction_eligible=frozenset(jurisdiction_eligible),
        )

    @property
    def effective(self) -> frozenset[Capability]:
        """The capabilities permitted by all three layers simultaneously."""
        return self.venue_supported & self.account_permitted & self.jurisdiction_eligible

    def layer(self, layer: CapabilityLayer) -> frozenset[Capability]:
        """The capability set belonging to one specific layer."""
        if layer is CapabilityLayer.VENUE:
            return self.venue_supported
        if layer is CapabilityLayer.ACCOUNT:
            return self.account_permitted
        return self.jurisdiction_eligible

    def denying_layer(self, capability: Capability) -> CapabilityLayer | None:
        """Return the first layer in precedence order that denies ``capability``.

        ``None`` means every layer permits it.
        """
        for candidate in LAYER_PRECEDENCE:
            if capability not in self.layer(candidate):
                return candidate
        return None

    def require(self, capability: Capability) -> None:
        """Raise ``CapabilityNotAvailable`` unless every layer permits ``capability``.

        This is the single generic guard. Nothing in this system branches on the
        venue's name to decide what it can do; callers ask this question instead.
        """
        denied_by = self.denying_layer(capability)
        if denied_by is not None:
            raise CapabilityNotAvailable(self.venue, capability, denied_by)

    def __contains__(self, capability: object) -> bool:
        return capability in self.effective

    def __iter__(self) -> Iterator[Capability]:
        return iter(sorted(self.effective))

    def __len__(self) -> int:
        return len(self.effective)
