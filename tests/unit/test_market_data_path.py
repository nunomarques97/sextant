"""The public market-data read path, driven by recorded venue responses.

Every payload in ``tests/fixtures`` is a real response captured from the live
endpoint during the data-availability spike, trimmed but not reshaped. Parsing is therefore
tested against what the venues actually send, while CI stays offline.

The distinction these tests exist to protect: **a failed request and an empty
result are not the same thing**. A pipeline that cannot tell them apart will
record "this delisted instrument has no history" when what happened was a
timeout, and the resulting dataset is survivorship-biased in a way no downstream
check can detect.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from sextant.adapters.exchanges.binance.client import BinanceClient
from sextant.adapters.exchanges.http import (
    HttpTransport,
    RateLimiter,
    RequestJournal,
    VenueRequestRejected,
)
from sextant.adapters.exchanges.kraken.client import KrakenClient
from sextant.adapters.exchanges.listing_calendar import CalendarEntry, ListingCalendar
from sextant.domain.availability import VenueUnavailable
from sextant.domain.capability import Capability
from sextant.domain.instrument import Instrument
from sextant.domain.market_data import OpenBarConsumed
from sextant.domain.money import Notional, Price, Quantity
from sextant.domain.provenance import ListingWindow, PointInTimeUnavailable, Provenance
from sextant.domain.time import Timeframe, Timestamp
from sextant.domain.venue import Venue
from tests.conftest import ts

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

Handler = Callable[[httpx.Request], httpx.Response]


def fixture(*parts: str) -> object:
    """Load one recorded response."""
    with (FIXTURES.joinpath(*parts)).open(encoding="utf-8") as handle:
        loaded: object = json.load(handle)
    return loaded


def transport_for(venue: Venue, base_url: str, handler: Handler) -> HttpTransport:
    """A transport wired to a recorded responder, with retries made instant."""
    return HttpTransport(
        venue=venue,
        base_url=base_url,
        limiter=RateLimiter(rate_per_second=1000.0, burst=1000.0),
        journal=RequestJournal(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_attempts=2,
        backoff_base_seconds=0.0,
        backoff_cap_seconds=0.0,
    )


def binance_handler(overrides: Mapping[str, httpx.Response] | None = None) -> Handler:
    """Serve recorded Binance payloads, with per-path overrides for failure cases."""

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if overrides and path in overrides:
            return overrides[path]
        if path == "/api/v3/exchangeInfo":
            return httpx.Response(200, json=fixture("binance", "exchange_info.json"))
        if path == "/api/v3/klines":
            symbol = request.url.params.get("symbol")
            if symbol == "BTCEUR":
                return httpx.Response(200, json=fixture("binance", "klines_btceur_1d.json"))
            return httpx.Response(200, json=[])
        if path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 1704067200000})
        return httpx.Response(404, json={"code": -1121, "msg": "Invalid symbol."})

    return handle


def kraken_handler(overrides: Mapping[str, httpx.Response] | None = None) -> Handler:
    """Serve recorded Kraken payloads, with per-path overrides for failure cases."""

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if overrides and path in overrides:
            return overrides[path]
        if path == "/0/public/AssetPairs":
            return httpx.Response(200, json=fixture("kraken", "asset_pairs.json"))
        if path == "/0/public/OHLC":
            pair = request.url.params.get("pair")
            if pair == "XBTEUR":
                return httpx.Response(200, json=fixture("kraken", "ohlc_xbteur_1d.json"))
            return httpx.Response(200, json=fixture("kraken", "error_unknown_pair.json"))
        if path == "/0/public/SystemStatus":
            return httpx.Response(200, json={"error": [], "result": {"status": "online"}})
        return httpx.Response(200, json={"error": ["EGeneral:Unknown method"]})

    return handle


def calendar_for(venue: Venue, entries: Mapping[str, Timestamp]) -> ListingCalendar:
    """A calendar whose windows are stated, sourced and open-ended."""
    return ListingCalendar.of(
        venue=venue,
        entries=[
            CalendarEntry(
                symbol=symbol,
                window=ListingWindow(
                    listed_at=listed_at,
                    delisted_at=None,
                    provenance=Provenance.VENUE_ANNOUNCEMENT,
                    note="fixture",
                ),
                metadata_available=True,
            )
            for symbol, listed_at in entries.items()
        ],
        methodology="fixture calendar for tests",
    )


def binance_client(
    handler: Handler | None = None,
    calendar: ListingCalendar | None = None,
) -> BinanceClient:
    """A Binance client that never touches the network."""
    return BinanceClient(
        account_permitted=frozenset(Capability),
        jurisdiction_eligible=frozenset(Capability),
        transport=transport_for(
            BinanceClient.VENUE, "https://api.binance.com", handler or binance_handler()
        ),
        calendar=calendar,
    )


def kraken_client(
    handler: Handler | None = None,
    calendar: ListingCalendar | None = None,
) -> KrakenClient:
    """A Kraken client that never touches the network."""
    return KrakenClient(
        account_permitted=frozenset(Capability),
        jurisdiction_eligible=frozenset(Capability),
        transport=transport_for(
            KrakenClient.VENUE, "https://api.kraken.com", handler or kraken_handler()
        ),
        calendar=calendar,
    )


def instrument_for(venue: Venue, symbol: str) -> Instrument:
    """A minimal instrument used to address a fetch in these tests."""
    return Instrument(
        venue=venue,
        symbol=symbol,
        base="BTC",
        quote="EUR",
        listed_at=ts("2017-01-01T00:00:00"),
        tick_size=Price(Decimal("0.01")),
        lot_size=Quantity(Decimal("0.00001")),
        min_notional=Notional(Decimal("10")),
    )


# ---------------------------------------------------------------------------
# instruments(at)
# ---------------------------------------------------------------------------


def test_binance_instruments_at_excludes_an_instrument_listed_after_that_date() -> None:
    calendar = calendar_for(
        BinanceClient.VENUE,
        {"BTCEUR": ts("2019-04-01T00:00:00"), "ETHEUR": ts("2025-01-01T00:00:00")},
    )
    client = binance_client(calendar=calendar)

    symbols = {item.symbol for item in client.instruments(ts("2024-06-01T00:00:00"))}

    assert symbols == {"BTCEUR"}


def test_kraken_instruments_at_excludes_an_instrument_listed_after_that_date() -> None:
    calendar = calendar_for(
        KrakenClient.VENUE,
        {"XBTEUR": ts("2015-01-01T00:00:00"), "ETHEUR": ts("2025-01-01T00:00:00")},
    )
    client = kraken_client(calendar=calendar)

    symbols = {item.symbol for item in client.instruments(ts("2024-06-01T00:00:00"))}

    assert symbols == {"XBTEUR"}


def test_an_instrument_carries_the_provenance_of_its_listing_window() -> None:
    calendar = calendar_for(BinanceClient.VENUE, {"BTCEUR": ts("2019-04-01T00:00:00")})
    client = binance_client(calendar=calendar)

    instruments = client.instruments(ts("2024-06-01T00:00:00"))

    assert instruments[0].provenance is Provenance.VENUE_ANNOUNCEMENT
    assert instruments[0].provenance.is_directly_verified


@pytest.mark.parametrize("build", [binance_client, kraken_client])
def test_without_a_calendar_a_historical_question_is_refused(
    build: Callable[[], BinanceClient | KrakenClient],
) -> None:
    """The refusal names the remedy, so the caller is handed the next step."""
    with pytest.raises(PointInTimeUnavailable) as raised:
        build().instruments(ts("2021-06-01T00:00:00"))
    assert "Smallest resolution" in str(raised.value)


# ---------------------------------------------------------------------------
# get_bars
# ---------------------------------------------------------------------------


def test_binance_get_bars_parses_recorded_klines() -> None:
    client = binance_client()
    instrument = instrument_for(BinanceClient.VENUE, "BTCEUR")

    bars = client.get_bars(
        [instrument], Timeframe.D1, ts("2024-01-01T00:00:00"), ts("2024-01-06T00:00:00")
    )

    assert len(bars) == 5
    assert bars[0].open_time == ts("2024-01-01T00:00:00")
    assert bars[0].open == Price(Decimal("38423.69000000"))
    assert bars[0].quote_volume is not None
    assert all(bar.is_closed for bar in bars)


def test_kraken_get_bars_parses_recorded_candles() -> None:
    client = kraken_client()
    instrument = instrument_for(KrakenClient.VENUE, "XBTEUR")

    bars = client.get_bars(
        [instrument], Timeframe.D1, ts("2020-01-01T00:00:00"), ts("2030-01-01T00:00:00")
    )

    assert len(bars) == 5
    assert bars[0].quote_volume is not None
    # vwap * volume, so turnover is consistent with the price level
    assert bars[0].quote_volume.amount > 0
    assert all(bar.is_closed for bar in bars)


def test_bars_are_closed_only_by_default_against_a_live_shaped_open_bar() -> None:
    """The open-bar guard, exercised on a real payload shape.

    The recorded rows are all long closed by the time this runs, so the final
    row is re-stamped to the current day. Its *shape* is the venue's; only its
    instant is ours, which is the only part that can be made deterministic.
    """
    recorded = fixture("binance", "klines_btceur_1d.json")
    assert isinstance(recorded, list)
    rows = [list(row) for row in recorded if isinstance(row, list)]
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    open_row = list(rows[-1])
    open_row[0] = int(today.timestamp() * 1000)
    open_row[6] = open_row[0] + 86_400_000 - 1
    payload = [*rows[:-1], open_row]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v3/klines":
            return httpx.Response(200, json=payload)
        return httpx.Response(200, json=fixture("binance", "exchange_info.json"))

    client = binance_client(handler)
    instrument = instrument_for(BinanceClient.VENUE, "BTCEUR")
    window_end = Timestamp(today + timedelta(days=1))

    closed = client.get_bars([instrument], Timeframe.D1, ts("2024-01-01T00:00:00"), window_end)
    with_open = client.get_bars(
        [instrument], Timeframe.D1, ts("2024-01-01T00:00:00"), window_end, include_open=True
    )

    assert len(with_open) == len(closed) + 1
    assert all(bar.is_closed for bar in closed)
    assert not with_open[-1].is_closed
    with pytest.raises(OpenBarConsumed):
        with_open[-1].require_closed()


# ---------------------------------------------------------------------------
# Failure is never emptiness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 500, 503])
def test_a_retryable_status_raises_venue_unavailable_rather_than_returning_nothing(
    status: int,
) -> None:
    handler = binance_handler({"/api/v3/klines": httpx.Response(status, json={"msg": "nope"})})
    client = binance_client(handler)

    with pytest.raises(VenueUnavailable) as raised:
        client.get_bars(
            [instrument_for(BinanceClient.VENUE, "BTCEUR")],
            Timeframe.D1,
            ts("2024-01-01T00:00:00"),
            ts("2024-01-06T00:00:00"),
        )
    assert raised.value.retryable is True


def test_a_rate_limited_response_is_retried_before_it_is_given_up_on() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.path == "/api/v3/klines":
            attempts += 1
            if attempts == 1:
                return httpx.Response(429, headers={"Retry-After": "0"}, json={"msg": "slow down"})
            return httpx.Response(200, json=fixture("binance", "klines_btceur_1d.json"))
        return httpx.Response(200, json=fixture("binance", "exchange_info.json"))

    client = binance_client(handler)
    bars = client.get_bars(
        [instrument_for(BinanceClient.VENUE, "BTCEUR")],
        Timeframe.D1,
        ts("2024-01-01T00:00:00"),
        ts("2024-01-06T00:00:00"),
    )

    assert attempts == 2
    assert len(bars) == 5


def test_a_rejected_request_is_not_an_empty_result() -> None:
    """A 4xx is a refusal, and refusal is a different fact from absence."""
    handler = binance_handler(
        {"/api/v3/klines": httpx.Response(400, json={"code": -1121, "msg": "Invalid symbol."})}
    )
    client = binance_client(handler)

    with pytest.raises(VenueRequestRejected) as raised:
        client.get_bars(
            [instrument_for(BinanceClient.VENUE, "NOPE")],
            Timeframe.D1,
            ts("2024-01-01T00:00:00"),
            ts("2024-01-06T00:00:00"),
        )
    assert raised.value.status == 400
    assert raised.value.retryable is False


def test_kraken_puts_its_refusals_inside_a_200_and_they_are_still_refusals() -> None:
    """The recorded payload is the venue's real answer for a delisted pair."""
    client = kraken_client()

    with pytest.raises(VenueRequestRejected) as raised:
        client.get_bars(
            [instrument_for(KrakenClient.VENUE, "WAVESEUR")],
            Timeframe.D1,
            ts("2020-01-01T00:00:00"),
            ts("2030-01-01T00:00:00"),
        )
    assert "Invalid asset pair" in raised.value.detail


def test_an_empty_result_is_distinguishable_from_a_failure() -> None:
    """The two outcomes are asserted side by side, because that is the point.

    One call returns zero bars and raises nothing. The other raises and returns
    nothing. Any code that collapses these two into "no data" is building a
    biased dataset, and this test is what makes that collapse visible.
    """
    empty_client = binance_client(binance_handler({"/api/v3/klines": httpx.Response(200, json=[])}))
    failing_client = binance_client(
        binance_handler({"/api/v3/klines": httpx.Response(503, text="down")})
    )
    instrument = instrument_for(BinanceClient.VENUE, "BTCEUR")
    start, end = ts("2024-01-01T00:00:00"), ts("2024-01-06T00:00:00")

    assert empty_client.get_bars([instrument], Timeframe.D1, start, end) == ()

    with pytest.raises(VenueUnavailable):
        failing_client.get_bars([instrument], Timeframe.D1, start, end)


# ---------------------------------------------------------------------------
# Traceability
# ---------------------------------------------------------------------------


def test_every_call_is_journalled_with_endpoint_parameters_and_timestamps() -> None:
    """A claim in the report must be traceable to the call that produced it."""
    journal = RequestJournal()
    client = BinanceClient(
        account_permitted=frozenset(Capability),
        jurisdiction_eligible=frozenset(Capability),
        transport=HttpTransport(
            venue=BinanceClient.VENUE,
            base_url="https://api.binance.com",
            limiter=RateLimiter(rate_per_second=1000.0, burst=1000.0),
            journal=journal,
            client=httpx.Client(transport=httpx.MockTransport(binance_handler())),
            max_attempts=2,
            backoff_base_seconds=0.0,
        ),
    )

    client.get_bars(
        [instrument_for(BinanceClient.VENUE, "BTCEUR")],
        Timeframe.D1,
        ts("2024-01-01T00:00:00"),
        ts("2024-01-06T00:00:00"),
    )

    record = journal.records[-1]
    assert record.url.endswith("/api/v3/klines")
    assert dict(record.params)["symbol"] == "BTCEUR"
    assert record.responded_at is not None
    assert record.responded_at >= record.requested_at
    assert record.status == 200
    assert record.outcome == "ok"


def test_venue_health_reports_an_outage_as_a_value_rather_than_raising() -> None:
    client = binance_client(binance_handler({"/api/v3/time": httpx.Response(503, text="down")}))

    health = client.health()

    assert not health.is_usable
