"""The capability model.

The behaviour under test is the one the whole architecture rests on: what an
account may do is the intersection of three layers, a denial names which layer
said no, and none of it depends on the venue's identity.
"""

from __future__ import annotations

import pytest

from sextant.adapters.exchanges.binance.client import BinanceClient
from sextant.adapters.exchanges.kraken.client import KrakenClient
from sextant.adapters.exchanges.registry import EXCHANGE_ADAPTERS, UnknownVenue, adapter_for
from sextant.domain.capability import (
    Capability,
    CapabilityLayer,
    CapabilityNotAvailable,
    CapabilitySet,
)
from sextant.domain.venue import Venue

ALL_THREE = (
    Capability.PUBLIC_MARKET_DATA,
    Capability.HISTORICAL_OHLCV,
    Capability.SPOT_TRADING,
)


def build(
    *,
    venue: str = "testvenue",
    venue_supported: tuple[Capability, ...] = ALL_THREE,
    account_permitted: tuple[Capability, ...] = ALL_THREE,
    jurisdiction_eligible: tuple[Capability, ...] = ALL_THREE,
) -> CapabilitySet:
    return CapabilitySet.build(
        Venue(venue), venue_supported, account_permitted, jurisdiction_eligible
    )


def test_effective_set_is_the_intersection_of_all_three_layers() -> None:
    capabilities = build(
        venue_supported=(Capability.PUBLIC_MARKET_DATA, Capability.SPOT_TRADING),
        account_permitted=(Capability.PUBLIC_MARKET_DATA, Capability.SPOT_TRADING),
        jurisdiction_eligible=(Capability.PUBLIC_MARKET_DATA,),
    )
    assert capabilities.effective == frozenset({Capability.PUBLIC_MARKET_DATA})


def test_jurisdiction_alone_can_remove_a_capability_the_venue_and_account_allow() -> None:
    capabilities = build(jurisdiction_eligible=(Capability.PUBLIC_MARKET_DATA,))

    assert Capability.SPOT_TRADING not in capabilities

    with pytest.raises(CapabilityNotAvailable) as raised:
        capabilities.require(Capability.SPOT_TRADING)

    assert raised.value.layer is CapabilityLayer.JURISDICTION
    assert raised.value.capability is Capability.SPOT_TRADING
    assert "jurisdiction" in str(raised.value)


def test_account_layer_is_named_when_it_is_the_one_denying() -> None:
    capabilities = build(account_permitted=(Capability.PUBLIC_MARKET_DATA,))
    with pytest.raises(CapabilityNotAvailable) as raised:
        capabilities.require(Capability.SPOT_TRADING)
    assert raised.value.layer is CapabilityLayer.ACCOUNT


def test_venue_layer_is_named_when_the_exchange_simply_cannot_do_it() -> None:
    capabilities = build(venue_supported=(Capability.PUBLIC_MARKET_DATA,))
    with pytest.raises(CapabilityNotAvailable) as raised:
        capabilities.require(Capability.SPOT_TRADING)
    assert raised.value.layer is CapabilityLayer.VENUE


def test_require_passes_silently_when_every_layer_permits() -> None:
    build().require(Capability.SPOT_TRADING)


@pytest.mark.parametrize("venue_name", sorted(EXCHANGE_ADAPTERS))
def test_the_guard_is_agnostic_to_venue_identity(venue_name: str) -> None:
    """Identical inputs must produce identical behaviour whatever the venue is called.

    If any branch on a venue's name ever appears, one parametrisation of this
    test diverges from the other.
    """
    capabilities = build(venue=venue_name, jurisdiction_eligible=(Capability.PUBLIC_MARKET_DATA,))

    assert capabilities.effective == frozenset({Capability.PUBLIC_MARKET_DATA})

    with pytest.raises(CapabilityNotAvailable) as raised:
        capabilities.require(Capability.SPOT_TRADING)
    assert raised.value.layer is CapabilityLayer.JURISDICTION
    assert raised.value.venue.name == venue_name


@pytest.mark.parametrize("client_class", [BinanceClient, KrakenClient])
def test_real_adapters_behave_identically_under_identical_configuration(
    client_class: type[BinanceClient] | type[KrakenClient],
) -> None:
    client = client_class(
        account_permitted=frozenset(ALL_THREE),
        jurisdiction_eligible=frozenset({Capability.PUBLIC_MARKET_DATA}),
    )
    capabilities = client.capabilities()

    assert Capability.PUBLIC_MARKET_DATA in capabilities
    assert Capability.SPOT_TRADING not in capabilities
    assert capabilities.denying_layer(Capability.SPOT_TRADING) is CapabilityLayer.JURISDICTION


def test_capability_accepts_the_screaming_case_spelling_used_in_configuration() -> None:
    assert Capability("SPOT_TRADING") is Capability.SPOT_TRADING
    assert Capability("spot_trading") is Capability.SPOT_TRADING


def test_capability_still_rejects_a_name_that_does_not_exist() -> None:
    with pytest.raises(ValueError, match="not a valid Capability"):
        Capability("teleportation")


def test_registry_lookup_is_a_table_not_a_comparison_chain() -> None:
    assert adapter_for("binance") is BinanceClient
    assert adapter_for("kraken") is KrakenClient
    with pytest.raises(UnknownVenue):
        adapter_for("nonexistent")


def test_neither_venue_is_declared_primary_or_a_fallback() -> None:
    """The registry is a flat mapping. Peers, not a chain."""
    assert set(EXCHANGE_ADAPTERS) == {"binance", "kraken"}
