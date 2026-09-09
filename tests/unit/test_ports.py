"""Port conformance.

Adapters implement ports structurally, so nothing forces them to match. These
tests are that force. They also pin what each adapter is allowed to do at this
phase: the public read path is wired, the trading path is not, and neither will
answer a historical question it has no source for.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal

import pytest

from sextant.adapters.clocks import SimulatedClock, SystemClock
from sextant.adapters.exchanges.base import BaseExchangeClient
from sextant.adapters.exchanges.binance.client import BinanceClient
from sextant.adapters.exchanges.kraken.client import KrakenClient
from sextant.domain.capability import Capability
from sextant.domain.instrument import Instrument
from sextant.domain.market_data import Bar, OpenBarConsumed
from sextant.domain.money import Price, Quantity
from sextant.domain.provenance import PointInTimeUnavailable
from sextant.domain.time import Timeframe, Timestamp
from sextant.ports.clock import Clock
from sextant.ports.cost import CostBreakdown
from sextant.ports.exchange import ExchangeClient, WithdrawalPermission
from sextant.ports.repository import BarRepository
from tests.conftest import make_instrument, ts

CLIENT_CLASSES = [BinanceClient, KrakenClient]


def build_client(client_class: type[BaseExchangeClient]) -> BaseExchangeClient:
    return client_class(
        account_permitted=frozenset(Capability),
        jurisdiction_eligible=frozenset(Capability),
    )


@pytest.mark.parametrize("client_class", CLIENT_CLASSES)
def test_each_adapter_satisfies_the_exchange_client_port(
    client_class: type[BaseExchangeClient],
) -> None:
    assert isinstance(build_client(client_class), ExchangeClient)


@pytest.mark.parametrize("client_class", CLIENT_CLASSES)
def test_the_trading_path_is_still_unwired(client_class: type[BaseExchangeClient]) -> None:
    """SEXTANT-002 wired public market data and nothing else.

    No network call is made here: every assertion below fails before a request
    would be built.
    """
    client = build_client(client_class)
    instrument = make_instrument("BTCEUR", venue=client.venue.name)

    with pytest.raises(NotImplementedError):
        client.get_order_book(instrument)
    assert client.withdrawal_permission() is WithdrawalPermission.UNKNOWN


@pytest.mark.parametrize("client_class", CLIENT_CLASSES)
def test_no_adapter_answers_a_point_in_time_question_without_a_source(
    client_class: type[BaseExchangeClient],
) -> None:
    """The failure mode this guards against is the quiet one.

    Neither venue publishes a spot listing date. An adapter that answered
    ``instruments(at)`` anyway would return the symbols that survived to today,
    which is a survivorship-biased universe wearing the costume of a correct
    one. It must refuse, and name what would fix it.
    """
    client = build_client(client_class)

    with pytest.raises(PointInTimeUnavailable) as raised:
        client.instruments(ts("2022-01-01T00:00:00"))
    assert raised.value.remedy


@pytest.mark.parametrize("clock", [SystemClock(), SimulatedClock(ts("2024-01-01T00:00:00"))])
def test_both_clocks_satisfy_the_clock_port(clock: Clock) -> None:
    assert isinstance(clock, Clock)
    assert clock.now().value.utcoffset() is not None


def test_the_simulated_clock_refuses_to_move_backwards() -> None:
    clock = SimulatedClock(ts("2024-06-01T00:00:00"))
    clock.advance_to(ts("2024-06-02T00:00:00"))
    assert clock.now() == ts("2024-06-02T00:00:00")

    with pytest.raises(ValueError, match="backwards"):
        clock.advance_to(ts("2024-01-01T00:00:00"))


def test_a_repository_implementation_satisfies_the_bar_repository_port() -> None:
    class InMemoryBars:
        def read(
            self,
            instruments: Sequence[Instrument],
            timeframe: Timeframe,
            start: Timestamp,
            end: Timestamp,
            *,
            closed_only: bool = True,
        ) -> Sequence[Bar]:
            del instruments, timeframe, start, end, closed_only
            return ()

        def write(self, bars: Sequence[Bar]) -> int:
            return len(bars)

        def latest_close_time(
            self, instrument: Instrument, timeframe: Timeframe
        ) -> Timestamp | None:
            del instrument, timeframe
            return None

    assert isinstance(InMemoryBars(), BarRepository)


def test_a_cost_breakdown_keeps_its_components_itemised_and_sums_them() -> None:
    breakdown = CostBreakdown(
        fee_bps=Decimal("8"),
        spread_bps=Decimal("4.5"),
        slippage_bps=Decimal("2"),
        funding_bps=Decimal("0"),
    )
    assert breakdown.total_bps == Decimal("14.5")


def test_an_open_bar_cannot_be_consumed_as_if_it_were_closed() -> None:
    instrument = make_instrument("BTCEUR")
    open_bar = Bar(
        instrument=instrument,
        timeframe=Timeframe.H1,
        open_time=ts("2024-01-01T00:00:00"),
        open=Price.parse("100"),
        high=Price.parse("110"),
        low=Price.parse("99"),
        close=Price.parse("105"),
        volume=Quantity.parse("3"),
        is_closed=False,
    )

    with pytest.raises(OpenBarConsumed):
        open_bar.require_closed()

    closed = replace(open_bar, is_closed=True)
    assert closed.require_closed() is closed
    assert closed.close_time == ts("2024-01-01T01:00:00")
