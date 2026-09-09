"""The research spike's fetch and measure stages, driven against a stub venue.

The transport is real: a stub sits underneath ``httpx``, so the rate limiter,
the journal, the retry ladder, the error-envelope classification and the venue
parsers all execute exactly as they do against the live hosts. Only the network
is replaced.

What is being asserted is the spike's contract rather than any particular
number: every stage is separately runnable, every stage is idempotent, a refusal
is recorded as a refusal rather than as an absence of data, and the measure
stage never reaches the network at all.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from sextant.adapters.exchanges.http import HttpTransport, RateLimiter, RequestJournal
from sextant.app import spike
from sextant.domain.time import Timestamp
from sextant.domain.venue import Venue

DAY_MILLIS = 86_400_000
RECENT = datetime(2026, 1, 1, tzinfo=UTC)


def kline(open_millis: int, close: str, quote_volume: str) -> list[object]:
    """One ``klines`` row in the venue's wire order."""
    return [
        open_millis,
        "1",
        "2",
        "0.5",
        close,
        "10",
        open_millis + DAY_MILLIS - 1,
        quote_volume,
        7,
        "5",
        "5",
        "0",
    ]


def candle(open_seconds: int, close: str) -> list[object]:
    """One ``OHLC`` row in the venue's wire order."""
    return [open_seconds, "1", "2", "0.5", close, "3", "9000", 11]


def daily_millis(days: int) -> list[int]:
    """``days`` consecutive daily open instants, ending well before today."""
    first = Timestamp(RECENT - timedelta(days=days + 10)).epoch_millis
    return [first + index * DAY_MILLIS for index in range(days)]


BINANCE_EXCHANGE_INFO: dict[str, object] = {
    "symbols": [
        {
            "symbol": "BTCEUR",
            "baseAsset": "BTC",
            "quoteAsset": "EUR",
            "status": "TRADING",
            "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                {"filterType": "LOT_SIZE", "stepSize": "0.00001"},
                {"filterType": "NOTIONAL", "minNotional": "5"},
            ],
        },
        {
            "symbol": "DEADEUR",
            "baseAsset": "DEAD",
            "quoteAsset": "EUR",
            "status": "BREAK",
            "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                {"filterType": "LOT_SIZE", "stepSize": "0.00001"},
                {"filterType": "MIN_NOTIONAL", "minNotional": "5"},
            ],
        },
        {
            "symbol": "BTCJPY",
            "baseAsset": "BTC",
            "quoteAsset": "JPY",
            "status": "TRADING",
            "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                {"filterType": "LOT_SIZE", "stepSize": "0.00001"},
            ],
        },
    ]
}

ARCHIVE_XML = (
    "<ListBucketResult><Prefix>data/spot/monthly/klines/</Prefix>"
    "<CommonPrefixes><Prefix>data/spot/monthly/klines/BTCEUR/</Prefix></CommonPrefixes>"
    "<CommonPrefixes><Prefix>data/spot/monthly/klines/DEADEUR/</Prefix></CommonPrefixes>"
    "<IsTruncated>false</IsTruncated></ListBucketResult>"
)

KRAKEN_ASSET_PAIRS: dict[str, object] = {
    "XXBTZEUR": {
        "altname": "XBTEUR",
        "wsname": "XBT/EUR",
        "status": "online",
        "tick_size": "0.1",
        "lot_decimals": 8,
        "costmin": "0.45",
    },
    "DEADEUR": {
        "altname": "DEADEUR",
        "wsname": "DEAD/EUR",
        "status": "delisted",
        "tick_size": "0.1",
        "lot_decimals": 8,
        "costmin": "0.45",
    },
    "XXBTZJPY": {
        "altname": "XBTJPY",
        "wsname": "XBT/JPY",
        "status": "online",
        "tick_size": "0.1",
        "lot_decimals": 8,
        "costmin": "0.45",
    },
    "NOWSNAME": {"altname": "NOWSNAME", "status": "online"},
}


def binance_handler(request: httpx.Request) -> httpx.Response:
    """Answer the four Binance endpoints the spike calls."""
    path = request.url.path
    if path.endswith("/api/v3/time"):
        return httpx.Response(200, json={"serverTime": 1735689600000})
    if path.endswith("/api/v3/exchangeInfo"):
        return httpx.Response(200, json=BINANCE_EXCHANGE_INFO)
    if path.endswith("/api/v3/klines"):
        symbol = request.url.params.get("symbol", "")
        if symbol == "NOTLISTED":
            return httpx.Response(400, text='{"code":-1121,"msg":"Invalid symbol."}')
        opens = daily_millis(4 if symbol == "DEADEUR" else 30)
        start = int(request.url.params.get("startTime", "0"))
        rows = [kline(value, "100", "9000000") for value in opens if value >= start]
        return httpx.Response(200, json=rows)
    if path.endswith("/api/v3/ticker/bookTicker"):
        return httpx.Response(
            200,
            json=[
                {"symbol": "BTCEUR", "bidPrice": "100.0", "askPrice": "100.1"},
                {"symbol": "DEADEUR", "bidPrice": "0", "askPrice": "0"},
                {"symbol": "IGNORED", "bidPrice": "1", "askPrice": "2"},
            ],
        )
    return httpx.Response(200, text=ARCHIVE_XML)


def kraken_handler(request: httpx.Request) -> httpx.Response:
    """Answer the four Kraken endpoints, including one refusal inside a 200."""
    path = request.url.path
    if path.endswith("/SystemStatus"):
        return httpx.Response(200, json={"error": [], "result": {"status": "online"}})
    if path.endswith("/AssetPairs"):
        if request.url.params.get("country_code") == "PT":
            return httpx.Response(
                200,
                json={"error": [], "result": {"XXBTZEUR": KRAKEN_ASSET_PAIRS["XXBTZEUR"]}},
            )
        return httpx.Response(200, json={"error": [], "result": KRAKEN_ASSET_PAIRS})
    if path.endswith("/OHLC"):
        symbol = request.url.params.get("pair", "")
        if symbol in {"WAVESEUR", "ANTEUR", "ANTUSD", "WAVESUSD", "REPXBT", "GNOXBT", "USTEUR"}:
            return httpx.Response(200, json={"error": ["EQuery:Invalid asset pair"], "result": {}})
        opens = [value // 1000 for value in daily_millis(30)]
        return httpx.Response(
            200,
            json={
                "error": [],
                "result": {symbol: [candle(value, "100") for value in opens], "last": opens[-1]},
            },
        )
    return httpx.Response(
        200,
        json={
            "error": [],
            "result": {
                "XXBTZEUR": {"a": ["100.1", "1", "1"], "b": ["100.0", "1", "1"]},
                "DEADEUR": {"a": ["0", "1", "1"], "b": ["0", "1", "1"]},
            },
        },
    )


def stub_transport(venue: Venue, base_url: str, handler: object) -> HttpTransport:
    """A real transport with a stub socket underneath and no rate-limit wait."""
    client = httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    return HttpTransport(
        venue=venue,
        base_url=base_url,
        limiter=RateLimiter(rate_per_second=100_000.0, burst=100_000.0),
        journal=RequestJournal(),
        client=client,
    )


@pytest.fixture(autouse=True)
def stub_venues(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point both collect stages at the stub venues instead of the real hosts."""
    binance = Venue("binance")
    kraken = Venue("kraken")
    monkeypatch.setattr(
        spike,
        "binance_transport",
        lambda journal=None: stub_transport(binance, "https://api.binance.test", binance_handler),
    )
    monkeypatch.setattr(
        spike,
        "default_archive_transport",
        lambda journal=None: stub_transport(binance, "https://archive.test", binance_handler),
    )
    monkeypatch.setattr(
        spike,
        "kraken_transport",
        lambda journal=None: stub_transport(kraken, "https://api.kraken.test", kraken_handler),
    )
    yield


# -- probe instrument --------------------------------------------------------


def test_a_probe_instrument_states_that_nothing_established_its_window() -> None:
    """It exists to address a fetch, and must never be mistaken for evidence."""
    probe = spike.probe_instrument(Venue("kraken"), "XBTEUR")

    assert probe.symbol == "XBTEUR"
    assert probe.provenance.value == "unverified"
    assert not probe.provenance.is_directly_verified


def test_json_helpers_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "payload.json"

    spike.write_json(target, {"b": 2, "a": 1})

    assert spike.read_json(target) == {"a": 1, "b": 2}
    assert target.read_text(encoding="utf-8").endswith("\n")


# -- collect stages ----------------------------------------------------------


def test_the_first_venue_stage_writes_every_artifact_the_report_needs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spike.collect_binance(tmp_path, quotes=frozenset({"EUR"}))

    out = tmp_path / "binance"
    metadata = json.loads((out / "symbol_metadata.json").read_text(encoding="utf-8"))
    assert set(metadata) == {"BTCEUR", "DEADEUR", "BTCJPY"}
    assert metadata["DEADEUR"]["status"] == "BREAK"
    assert json.loads((out / "archive_symbols.json").read_text(encoding="utf-8")) == [
        "BTCEUR",
        "DEADEUR",
    ]
    # Only the in-scope quote currency is fetched; JPY is not asked for.
    assert {path.stem for path in (out / "bars").glob("*.json")} == {"BTCEUR", "DEADEUR"}
    assert (out / "requests.jsonl").exists()
    assert "halted=1" in capsys.readouterr().out


def test_the_second_venue_stage_records_the_venues_own_jurisdiction_filter(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The filtered and unfiltered pair sets are a measurement, not a configured guess."""
    spike.collect_kraken(tmp_path, quotes=frozenset({"EUR"}))

    out = tmp_path / "kraken"
    assert json.loads((out / "pair_metadata_pt.json").read_text(encoding="utf-8")) == ["XBTEUR"]
    metadata = json.loads((out / "pair_metadata.json").read_text(encoding="utf-8"))
    # The entry without a wsname is skipped rather than guessed at.
    assert "NOWSNAME" not in metadata
    assert metadata["XBTEUR"]["canonical"] == "XXBTZEUR"
    assert "pairs visible to PT=1 of 3" in capsys.readouterr().out


def test_a_refused_delisted_probe_is_recorded_as_a_refusal_not_as_absence(
    tmp_path: Path,
) -> None:
    """This is the distinction the whole dataset rests on.

    A venue that answers "invalid asset pair" has refused. Recording that as
    "no data" is precisely how a survivorship-biased dataset gets built without
    anybody noticing.
    """
    spike.collect_kraken(tmp_path, quotes=frozenset({"EUR"}))

    probe = json.loads((tmp_path / "kraken" / "delisted_probe.json").read_text(encoding="utf-8"))
    outcomes = {entry["symbol"]: entry["outcome"] for entry in probe}
    assert outcomes["ANTEUR"] == "rejected"
    assert all(outcome == "rejected" for outcome in outcomes.values())
    assert "Invalid asset pair" in next(
        entry["detail"] for entry in probe if entry["symbol"] == "ANTEUR"
    )


def test_a_retrieved_delisted_probe_reports_its_full_trading_life(tmp_path: Path) -> None:
    spike.collect_binance(tmp_path, quotes=frozenset({"EUR"}))

    probe = json.loads((tmp_path / "binance" / "delisted_probe.json").read_text(encoding="utf-8"))
    found = [entry for entry in probe if entry["outcome"] == "found"]
    assert len(found) == len(probe)
    assert all(entry["bar_count"] > 0 for entry in found)
    assert all("first_bar" in entry and "last_bar" in entry for entry in found)


def test_a_fetch_stage_resumes_rather_than_refetching(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The fetch takes minutes; regenerating a report must never require re-running it."""
    spike.collect_binance(tmp_path, quotes=frozenset({"EUR"}))
    first = (tmp_path / "binance" / "bars" / "BTCEUR.json").read_text(encoding="utf-8")
    capsys.readouterr()

    spike.collect_binance(tmp_path, quotes=frozenset({"EUR"}))

    assert (tmp_path / "binance" / "bars" / "BTCEUR.json").read_text(encoding="utf-8") == first


def test_a_spread_snapshot_holds_only_usable_two_sided_quotes(tmp_path: Path) -> None:
    """A zero bid is not a spread of zero; it is no quote at all."""
    spike.collect_binance(tmp_path, quotes=frozenset({"EUR"}))

    snapshot = json.loads(
        (tmp_path / "binance" / "spread_snapshot.json").read_text(encoding="utf-8")
    )
    assert set(snapshot["bps"]) == {"BTCEUR"}
    assert snapshot["observed_at"].endswith("+00:00")


def test_a_failed_spread_snapshot_is_recorded_rather_than_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refusing(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/Ticker"):
            return httpx.Response(200, json={"error": ["EGeneral:Invalid arguments"]})
        return kraken_handler(request)

    monkeypatch.setattr(
        spike,
        "kraken_transport",
        lambda journal=None: stub_transport(Venue("kraken"), "https://api.kraken.test", refusing),
    )

    spike.collect_kraken(tmp_path, quotes=frozenset({"EUR"}))

    snapshot = json.loads(
        (tmp_path / "kraken" / "spread_snapshot.json").read_text(encoding="utf-8")
    )
    assert snapshot["bps"] == {}
    assert "Invalid arguments" in snapshot["error"]
    assert "spread snapshot failed" in capsys.readouterr().out


def test_a_symbol_the_venue_refuses_lands_in_the_failure_file_not_in_the_bars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    info: dict[str, object] = {
        "symbols": [
            *[dict(row) for row in BINANCE_EXCHANGE_INFO["symbols"]],  # type: ignore[list-item]
            {
                "symbol": "NOTLISTED",
                "baseAsset": "NOT",
                "quoteAsset": "EUR",
                "status": "BREAK",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.00001"},
                ],
            },
        ]
    }

    def with_refusal(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/v3/exchangeInfo"):
            return httpx.Response(200, json=info)
        return binance_handler(request)

    monkeypatch.setattr(
        spike,
        "binance_transport",
        lambda journal=None: stub_transport(
            Venue("binance"), "https://api.binance.test", with_refusal
        ),
    )

    spike.collect_binance(tmp_path, quotes=frozenset({"EUR"}))

    failures = json.loads(
        (tmp_path / "binance" / "fetch_failures.json").read_text(encoding="utf-8")
    )
    assert "NOTLISTED" in failures
    assert failures["NOTLISTED"].startswith("rejected:")
    assert not (tmp_path / "binance" / "bars" / "NOTLISTED.json").exists()


# -- measure stage -----------------------------------------------------------


def test_the_measure_stage_writes_the_tables_and_a_calendar_per_venue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    spike.collect_binance(tmp_path, quotes=frozenset({"EUR"}))
    spike.collect_kraken(tmp_path, quotes=frozenset({"EUR"}))
    monkeypatch.chdir(tmp_path)

    spike.measure_all(tmp_path)

    assert (tmp_path / "binance" / "listing_calendar.json").exists()
    assert (tmp_path / "kraken" / "listing_calendar.json").exists()
    tables = (tmp_path / "docs" / "universe-tables.md").read_text(encoding="utf-8")
    assert "#### binance - quote policy `EUR`" in tables
    assert "#### kraken - quote policy `EUR+USD+USDT`" in tables
    summary = json.loads((tmp_path / "universe_tables.json").read_text(encoding="utf-8"))
    assert set(summary) == {"binance", "kraken"}
    assert summary["binance"]["series"] == 2
    assert "peak research universe" in capsys.readouterr().out


def test_the_measure_stage_skips_a_venue_with_nothing_fetched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Absent data is reported and skipped, never substituted for."""
    monkeypatch.chdir(tmp_path)

    spike.measure_all(tmp_path)

    captured = capsys.readouterr().out
    assert "no fetched data" in captured
    assert json.loads((tmp_path / "universe_tables.json").read_text(encoding="utf-8")) == {}


def test_the_measure_stage_skips_a_venue_whose_metadata_has_no_bars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "kraken"
    root.mkdir(parents=True)
    (root / "pair_metadata.json").write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    spike.measure_all(tmp_path)

    assert "no bars fetched" in capsys.readouterr().out


def test_the_kraken_profile_marks_history_as_truncated_and_names_what_is_missing() -> None:
    """The two facts that stop the venue's own truncation being read as a listing date."""
    profile = spike.RESEARCH_PROFILES["kraken"]

    assert profile.truncates_history
    assert dict(profile.known_missing)["ANTEUR"] == "2024-09-25T14:00:00+00:00"
    assert not spike.RESEARCH_PROFILES["binance"].truncates_history
    assert spike.RESEARCH_PROFILES["binance"].known_missing == ()


def test_the_three_quote_policies_are_nested_subsets() -> None:
    policies = spike.quote_policies()

    assert policies["EUR"] < policies["EUR+USD"] < policies["EUR+USD+USDT"]
    assert policies["EUR+USD+USDT"] <= spike.FETCHED_QUOTES


def test_the_default_account_is_the_one_decision_d2_states() -> None:
    account = spike.default_account()

    assert account.equity_quote.amount == 1500
    assert account.max_positions == 8


def test_stablecoins_and_leveraged_tokens_are_excluded_by_construction() -> None:
    assert {"USDT", "USDC", "WBTC", "STETH", "BTCUP", "BTCDOWN"} <= spike.EXCLUDED_BASES
