"""Two archives, two roots, one port.

A cash-and-carry pair holds a spot leg and a perpetual leg. They are two
instruments on two venues, acquired by two pipelines into two directories, and the
engine reads both through one ``BarRepository``. These tests are about the routing
and, more importantly, about what happens when a venue is not wired: it raises.

A read for an unwired venue that came back empty would price that leg at nothing
and hand a hedged pair back as a one-sided bet with a plausible-looking return.
That is invariant 10's collapse in the place where it would be least visible, so
the refusal is the test that matters here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from sextant.adapters.storage.repository import UnroutedVenue, VenueRoutedBarRepository
from sextant.domain.instrument import Instrument
from sextant.domain.money import Notional, Price, Quantity
from sextant.domain.time import Timeframe
from sextant.domain.venue import Venue
from tests.harness import InMemoryBarRepository, daily_bars, ts

SPOT = "binance"
PERP = "binance_perp"
FIRST_DAY = "2024-01-01"


def leg(venue: str, symbol: str, base: str) -> Instrument:
    """One leg, with its base stated rather than derived from the symbol."""
    return Instrument(
        venue=Venue(venue),
        symbol=symbol,
        base=base,
        quote="USDT",
        listed_at=ts("2020-01-01T00:00:00"),
        tick_size=Price(Decimal(0)),
        lot_size=Quantity(Decimal(0)),
        min_notional=Notional(Decimal(0)),
        delisted_at=None,
    )


def routed() -> tuple[VenueRoutedBarRepository, Instrument, Instrument]:
    """A spot repository and a perpetual repository, each holding one series."""
    spot_leg = leg(SPOT, "BTCUSDT", "BTC")
    perp_leg = leg(PERP, "BTCUSDT", "BTC")
    spot_store = InMemoryBarRepository()
    perp_store = InMemoryBarRepository()
    spot_store.add(daily_bars(spot_leg, first_day=FIRST_DAY, closes=("100", "101", "102")))
    perp_store.add(daily_bars(perp_leg, first_day=FIRST_DAY, closes=("200", "201", "202")))
    repository = VenueRoutedBarRepository(
        by_venue={spot_leg.venue: spot_store, perp_leg.venue: perp_store}
    )
    return repository, spot_leg, perp_leg


def test_each_leg_is_read_from_its_own_venue_s_archive() -> None:
    """The whole point: one call, two roots, the right prices from each."""
    repository, spot_leg, perp_leg = routed()
    bars = repository.read(
        [spot_leg, perp_leg],
        Timeframe.D1,
        ts("2024-01-01T00:00:00"),
        ts("2024-01-10T00:00:00"),
    )
    by_venue = {bar.instrument.venue.name: bar.close.amount for bar in bars}
    assert by_venue[SPOT] in {Decimal(100), Decimal(101), Decimal(102)}
    assert by_venue[PERP] in {Decimal(200), Decimal(201), Decimal(202)}
    assert len(bars) == 6


def test_a_venue_nobody_wired_raises_rather_than_returning_nothing() -> None:
    """An empty answer here would price a hedged leg at nothing.

    The pair would then be reported as a hedged carry while behaving as a
    one-sided bet, with a return that looked entirely plausible. Nothing else in
    the run would notice, which is exactly why this raises.
    """
    repository, spot_leg, _ = routed()
    stranger = leg("some_other_venue", "BTCUSDT", "BTC")
    with pytest.raises(UnroutedVenue, match="some_other_venue"):
        repository.read(
            [spot_leg, stranger],
            Timeframe.D1,
            ts("2024-01-01T00:00:00"),
            ts("2024-01-10T00:00:00"),
        )


def test_the_refusal_names_the_venues_that_are_wired() -> None:
    """So the remedy is in the message rather than in somebody's head."""
    repository, _, _ = routed()
    stranger = leg("some_other_venue", "BTCUSDT", "BTC")
    with pytest.raises(UnroutedVenue, match="binance, binance_perp"):
        repository.latest_close_time(stranger, Timeframe.D1)


def test_nothing_is_read_when_one_instrument_in_the_batch_is_unrouted() -> None:
    """The check runs over the whole batch before any read happens.

    Otherwise a partial answer would be returned alongside the exception's absence
    in some future caller that swallowed it, which is a half-priced book.
    """
    repository, spot_leg, _ = routed()
    stranger = leg("some_other_venue", "BTCUSDT", "BTC")
    spot_store = repository.by_venue[spot_leg.venue]
    assert isinstance(spot_store, InMemoryBarRepository)
    with pytest.raises(UnroutedVenue):
        repository.read(
            [spot_leg, stranger],
            Timeframe.D1,
            ts("2024-01-01T00:00:00"),
            ts("2024-01-10T00:00:00"),
        )
    assert spot_store.reads == []


def test_the_latest_close_comes_from_the_right_archive() -> None:
    repository, spot_leg, perp_leg = routed()
    assert repository.latest_close_time(spot_leg, Timeframe.D1) is not None
    assert repository.latest_close_time(perp_leg, Timeframe.D1) is not None


def test_a_write_is_routed_by_the_bar_s_own_venue() -> None:
    """The adapter satisfies the whole port, not the half the backtester uses."""
    repository, spot_leg, perp_leg = routed()
    extra = daily_bars(perp_leg, first_day="2024-02-01", closes=("300",))
    assert repository.write(list(extra)) == len(list(extra))
    perp_store = repository.by_venue[perp_leg.venue]
    assert isinstance(perp_store, InMemoryBarRepository)
    assert len(perp_store.series[perp_leg.key]) == 4
    spot_store = repository.by_venue[spot_leg.venue]
    assert isinstance(spot_store, InMemoryBarRepository)
    assert len(spot_store.series[spot_leg.key]) == 3
