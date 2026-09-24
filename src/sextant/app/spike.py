"""Data-availability spike.

A research entrypoint, not a product surface. It exists to answer three
questions by fetching rather than by expecting:

* can a point-in-time universe *including delisted instruments* be rebuilt per
  venue, and over what window?
* how large are the research and executable universes, month by month, under
  each quote-currency policy?
* what could a cost model honestly be calibrated from?

Everything it writes lands under a git-ignored ``data/spike/`` tree, alongside a
journal of every HTTP call made, so that any number in
``docs/DATA-AVAILABILITY.md`` can be traced back to the request that produced it.

Stages are separately runnable and each is idempotent, because the fetch stages
take minutes and a report should never require re-running them.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from sextant.adapters.exchanges.binance.client import (
    BinanceClient,
    default_archive_transport,
)
from sextant.adapters.exchanges.binance.client import (
    default_transport as binance_transport,
)
from sextant.adapters.exchanges.http import (
    RequestJournal,
    VenueRequestRejected,
    utc_now,
)
from sextant.adapters.exchanges.kraken.client import KrakenClient
from sextant.adapters.exchanges.kraken.client import (
    default_transport as kraken_transport,
)
from sextant.app.spike_report import (
    VenueDataset,
    measure,
    month_starts,
    observed_window,
    reconstruct_calendar,
    render_markdown,
    target_position_note,
)
from sextant.domain.availability import VenueUnavailable
from sextant.domain.capability import Capability
from sextant.domain.instrument import Instrument
from sextant.domain.money import Notional, Price, Quantity
from sextant.domain.provenance import Provenance
from sextant.domain.time import Timeframe, Timestamp
from sextant.domain.venue import Venue
from sextant.engine.universe.rules import AccountParameters

DATA_ROOT = Path("data") / "spike"

NEWLINE = chr(10)
"""Explicit, so generated markdown is LF on every host."""

#: Quote currencies we fetch history for. The three policies in decision D3
#: are subsets of this set, so one fetch serves all three measurements.
FETCHED_QUOTES: frozenset[str] = frozenset({"EUR", "USD", "USDT"})

#: Earliest instant worth asking either venue about. Binance spot opened in 2017.
HISTORY_START = Timestamp(datetime(2017, 7, 1, tzinfo=UTC))

#: Instruments each venue is *known* to have listed and no longer trades.
#:
#: Named here rather than discovered from the venue's current instrument list,
#: because discovering them from that list is exactly the circularity that makes
#: a survivorship-biased dataset look complete. Each entry carries the source
#: that established it; the Kraken entries come from the venue's own delisting
#: announcements, which state the pair and the instant trading stopped.
DELISTED_PROBE: Mapping[str, tuple[tuple[str, str], ...]] = {
    "binance": (
        ("BCCBTC", "Bitcoin Cash ABC, removed 2018; archive directory + exchangeInfo BREAK"),
        ("VENBTC", "VeChain token swap, removed 2018; archive directory + exchangeInfo BREAK"),
        ("SALTBTC", "SALT, removed; archive directory + exchangeInfo BREAK"),
        ("MITHBTC", "Mithril, removed; archive directory + exchangeInfo BREAK"),
        ("ERDBTC", "Elrond pre-swap, removed; archive directory + exchangeInfo BREAK"),
        ("LENDBTC", "Aave migration from LEND, removed; archive + exchangeInfo BREAK"),
        ("HCBTC", "HyperCash, removed; archive directory + exchangeInfo BREAK"),
    ),
    "kraken": (
        ("WAVESEUR", "announcement: trading stopped 2024-07-08 12:00 UTC (WAVES/EUR)"),
        ("WAVESUSD", "announcement: trading stopped 2024-07-08 12:00 UTC (WAVES/USD)"),
        ("ANTEUR", "announcement: delisted 2024-09-25 14:00 UTC (ANT/EUR)"),
        ("ANTUSD", "announcement: delisted 2024-09-25 14:00 UTC (ANT/USD)"),
        ("REPXBT", "announcement: spot pair removed 2024-04-05 10:00 UTC (REP/BTC)"),
        ("GNOXBT", "announcement: spot pair removed 2024-04-05 10:00 UTC (GNO/BTC)"),
        ("USTEUR", "announcement: trading disabled 2025-12-12 14:00 UTC (UST)"),
    ),
}

#: Excluded by construction, per docs/PHASE-0-FINDINGS.md §5. A curated map, not
#: a derived one: no venue publishes an asset-class taxonomy, so this is data we
#: maintain and must keep honest. Phase 0 listed its absence as risk 9.
EXCLUDED_BASES: frozenset[str] = frozenset(
    {
        # stablecoins: the return process is a peg, not an asset
        "USDT",
        "USDC",
        "DAI",
        "TUSD",
        "BUSD",
        "USDP",
        "PAX",
        "EURT",
        "EURS",
        "EUROC",
        "FDUSD",
        "PYUSD",
        "USDS",
        "USDD",
        "GUSD",
        "SUSD",
        "LUSD",
        "USTC",
        "UST",
        "EURC",
        "USDG",
        "RLUSD",
        "USDR",
        "AUDIO_STABLE",
        # wrapped and duplicated exposure
        "WBTC",
        "WETH",
        "WBETH",
        "WAXL",
        "WSOL",
        "RENBTC",
        "TBTC",
        "CBBTC",
        "LBTC",
        # staked and yield-bearing derivatives
        "STETH",
        "WSTETH",
        "RETH",
        "CBETH",
        "SETH2",
        "ETH2",
        "METH",
        "EZETH",
        "WEETH",
        "SOLV",
        "JITOSOL",
        "MSOL",
        "BSOL",
        # leveraged tokens: path-dependent decay, not the underlying
        "BTCUP",
        "BTCDOWN",
        "ETHUP",
        "ETHDOWN",
        "ADAUP",
        "ADADOWN",
        "LINKUP",
        "LINKDOWN",
        "BNBUP",
        "BNBDOWN",
        "XRPUP",
        "XRPDOWN",
        "DOTUP",
        "DOTDOWN",
        "TRXUP",
        "TRXDOWN",
        "EOSUP",
        "EOSDOWN",
        "LTCUP",
        "LTCDOWN",
        "XTZUP",
        "XTZDOWN",
        "FILUP",
        "FILDOWN",
        "YFIUP",
        "YFIDOWN",
        "UNIUP",
        "UNIDOWN",
        "SXPUP",
        "SXPDOWN",
        "AAVEUP",
        "AAVEDOWN",
        "SUSHIUP",
        "SUSHIDOWN",
        "1INCHUP",
        "1INCHDOWN",
    }
)


def _all_capabilities() -> frozenset[Capability]:
    """The spike reads only public data, so the capability layers are not the point."""
    return frozenset(Capability)


def probe_instrument(venue: Venue, symbol: str) -> Instrument:
    """A minimal instrument used only to address a fetch.

    Its listing window is explicitly ``UNVERIFIED``: nothing established it, and
    nothing downstream may treat it as evidence. It exists so that every fetch
    goes through the same ``Instrument``-taking code path rather than a second,
    symbol-string path that would quietly reintroduce an ambient symbol.
    """
    return Instrument(
        venue=venue,
        symbol=symbol,
        base="",
        quote="",
        listed_at=HISTORY_START,
        tick_size=Price(Decimal(0)),
        lot_size=Quantity(Decimal(0)),
        min_notional=Notional(Decimal(0)),
        provenance=Provenance.UNVERIFIED,
    )


@dataclass(frozen=True, slots=True)
class BarSeries:
    """A fetched daily series, reduced to what the universe rules consume."""

    symbol: str
    rows: tuple[tuple[int, str, str], ...]
    """``(open_time_millis, close, quote_volume)``, exact decimal strings."""

    def as_json(self) -> dict[str, object]:
        """Serialisable form."""
        return {"symbol": self.symbol, "timeframe": "1d", "rows": [list(row) for row in self.rows]}


def write_json(path: Path, payload: object) -> None:
    """Write UTF-8 JSON with stable ordering, creating parents as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=1, sort_keys=True, default=str)
        handle.write("\n")


def read_json(path: Path) -> object:
    """Read UTF-8 JSON written by :func:`write_json`."""
    with path.open(encoding="utf-8") as handle:
        loaded: object = json.load(handle)
    return loaded


# ----------------------------------------------------------------------------
# Fetch stages
# ----------------------------------------------------------------------------


def collect_binance(root: Path = DATA_ROOT, *, quotes: frozenset[str] = FETCHED_QUOTES) -> None:
    """Fetch everything the report needs from the first venue."""
    out = root / "binance"
    journal = RequestJournal()
    transport = binance_transport(journal)
    archive = default_archive_transport(journal)
    client = BinanceClient(
        account_permitted=_all_capabilities(),
        jurisdiction_eligible=_all_capabilities(),
        transport=transport,
        archive_transport=archive,
    )

    health = client.health()
    print(f"[binance] health={health.status.value} {health.detail}")

    metadata = client.symbol_metadata()
    write_json(
        out / "symbol_metadata.json",
        {
            symbol: {
                "base": item.base,
                "quote": item.quote,
                "status": item.status,
                "tick_size": str(item.tick_size.amount),
                "lot_size": str(item.lot_size.amount),
                "min_notional": str(item.min_notional.amount),
            }
            for symbol, item in sorted(metadata.items())
        },
    )
    trading = sum(1 for item in metadata.values() if item.is_trading)
    print(f"[binance] symbols={len(metadata)} trading={trading} halted={len(metadata) - trading}")

    archive_symbols = client.archive_symbols()
    write_json(out / "archive_symbols.json", sorted(archive_symbols))
    print(f"[binance] archive symbol directories={len(archive_symbols)}")

    candidates = sorted(symbol for symbol, item in metadata.items() if item.quote in quotes)
    print(f"[binance] fetching daily bars for {len(candidates)} symbols")
    _fetch_all(client, BinanceClient.VENUE, candidates, out, HISTORY_START)

    _probe_delisted(client, BinanceClient.VENUE, out)
    _snapshot_spreads(client, BinanceClient.VENUE, candidates, out)

    journal.write_jsonl(out / "requests.jsonl")
    print(f"[binance] journal lines={len(journal.records)} ok={journal.successes}")


def collect_kraken(root: Path = DATA_ROOT, *, quotes: frozenset[str] = FETCHED_QUOTES) -> None:
    """Fetch everything the report needs from the second venue."""
    out = root / "kraken"
    journal = RequestJournal()
    transport = kraken_transport(journal)
    client = KrakenClient(
        account_permitted=_all_capabilities(),
        jurisdiction_eligible=_all_capabilities(),
        transport=transport,
    )

    health = client.health()
    print(f"[kraken] health={health.status.value} {health.detail}")

    metadata = client.pair_metadata()
    write_json(
        out / "pair_metadata.json",
        {
            symbol: {
                "canonical": item.canonical,
                "base": item.base,
                "quote": item.quote,
                "status": item.status,
                "tick_size": str(item.tick_size.amount),
                "lot_size": str(item.lot_size.amount),
                "min_notional": str(item.min_notional.amount),
            }
            for symbol, item in sorted(metadata.items())
        },
    )
    print(f"[kraken] pairs={len(metadata)}")

    # The venue applies its own jurisdictional filter when asked. Comparing the
    # filtered set against the unfiltered one turns the jurisdiction layer from
    # a configuration guess into a measurement.
    restricted = KrakenClient(
        account_permitted=_all_capabilities(),
        jurisdiction_eligible=_all_capabilities(),
        transport=transport,
        country_code="PT",
    )
    pt_pairs = restricted.pair_metadata()
    write_json(out / "pair_metadata_pt.json", sorted(pt_pairs))
    print(f"[kraken] pairs visible to PT={len(pt_pairs)} of {len(metadata)}")

    candidates = sorted(symbol for symbol, item in metadata.items() if item.quote in quotes)
    print(f"[kraken] fetching daily bars for {len(candidates)} pairs")
    _fetch_all(client, KrakenClient.VENUE, candidates, out, HISTORY_START)

    _probe_delisted(client, KrakenClient.VENUE, out)
    _snapshot_spreads(client, KrakenClient.VENUE, candidates, out)

    journal.write_jsonl(out / "requests.jsonl")
    print(f"[kraken] journal lines={len(journal.records)} ok={journal.successes}")


def _fetch_all(
    client: BinanceClient | KrakenClient,
    venue: Venue,
    symbols: Sequence[str],
    out: Path,
    start: Timestamp,
) -> None:
    """Fetch daily bars for each symbol, one file each, resuming where possible."""
    end = utc_now()
    bars_dir = out / "bars"
    bars_dir.mkdir(parents=True, exist_ok=True)
    failures: dict[str, str] = {}
    for index, symbol in enumerate(symbols, start=1):
        target = bars_dir / f"{symbol}.json"
        if target.exists():
            continue
        try:
            bars = client.bars_for(probe_instrument(venue, symbol), Timeframe.D1, start, end)
        except VenueRequestRejected as exc:
            failures[symbol] = f"rejected: {exc.detail[:160]}"
            continue
        except VenueUnavailable as exc:
            failures[symbol] = f"unavailable: {exc}"
            continue
        series = BarSeries(
            symbol=symbol,
            rows=tuple(
                (
                    bar.open_time.epoch_millis,
                    str(bar.close.amount),
                    "" if bar.quote_volume is None else str(bar.quote_volume.amount),
                )
                for bar in bars
            ),
        )
        write_json(target, series.as_json())
        if index % 50 == 0:
            print(f"[{venue.name}] {index}/{len(symbols)} fetched")
    write_json(out / "fetch_failures.json", failures)
    print(f"[{venue.name}] fetch complete, failures={len(failures)}")


def _probe_delisted(client: BinanceClient | KrakenClient, venue: Venue, out: Path) -> None:
    """Attempt to retrieve the trading life of instruments known to be gone."""
    results: list[dict[str, object]] = []
    for symbol, source in DELISTED_PROBE[venue.name]:
        record: dict[str, object] = {"symbol": symbol, "evidence_source": source}
        try:
            bars = client.bars_for(
                probe_instrument(venue, symbol), Timeframe.D1, HISTORY_START, utc_now()
            )
        except VenueRequestRejected as exc:
            record["outcome"] = "rejected"
            record["detail"] = exc.detail[:200]
        except VenueUnavailable as exc:
            record["outcome"] = "unavailable"
            record["detail"] = str(exc)[:200]
        else:
            record["outcome"] = "found" if bars else "empty"
            record["bar_count"] = len(bars)
            if bars:
                record["first_bar"] = bars[0].open_time.isoformat()
                record["last_bar"] = bars[-1].open_time.isoformat()
        results.append(record)
    write_json(out / "delisted_probe.json", results)
    found = sum(1 for item in results if item.get("outcome") == "found")
    print(f"[{venue.name}] delisted probe: {found}/{len(results)} retrieved")


def _snapshot_spreads(
    client: BinanceClient | KrakenClient,
    venue: Venue,
    symbols: Sequence[str],
    out: Path,
) -> None:
    """One live top-of-book snapshot for the candidate set.

    A snapshot is not a history. Neither venue publishes historical quote data,
    so this calibrates the spread rule at exactly one instant - today - and the
    report says so rather than implying the number applies to 2022.
    """
    try:
        spreads = client.top_of_book_spreads_bps(symbols)
    except (VenueRequestRejected, VenueUnavailable) as exc:
        write_json(out / "spread_snapshot.json", {"error": str(exc)[:300], "bps": {}})
        print(f"[{venue.name}] spread snapshot failed: {exc}")
        return
    write_json(
        out / "spread_snapshot.json",
        {
            "observed_at": utc_now().isoformat(),
            "bps": {symbol: str(value) for symbol, value in sorted(spreads.items())},
        },
    )
    print(f"[{venue.name}] spread snapshot for {len(spreads)} instruments")


# ----------------------------------------------------------------------------
# Offline analysis
# ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VenueResearchProfile:
    """Per-venue research parameters, as data rather than as a branch.

    Looked up by name, never compared against one. Adding a venue is one entry
    here; it is never an ``if`` anywhere.
    """

    venue: Venue
    live_statuses: frozenset[str]
    truncates_history: bool
    """Whether the venue refuses to serve bars beyond a fixed recent window.

    When it does, the earliest bar we can obtain for *every* symbol is the same
    day, and that day is a property of the endpoint rather than of any listing.
    Treating it as a listing date would give every instrument the same
    fabricated birthday, so the calendar marks those windows unverified."""

    known_missing: tuple[tuple[str, str], ...]
    """Pairs the venue's instrument endpoint no longer describes at all, each
    with the instant its own announcement says trading stopped. Only pairs named
    verbatim in an announcement appear here: inferring a pair name from an asset
    name would be exactly the fabrication this task forbids."""


RESEARCH_PROFILES: Mapping[str, VenueResearchProfile] = {
    "binance": VenueResearchProfile(
        venue=Venue("binance"),
        live_statuses=frozenset({"TRADING"}),
        truncates_history=False,
        # Nothing is missing: the venue keeps delisted symbols in exchangeInfo
        # with status BREAK and keeps serving their klines.
        known_missing=(),
    ),
    "kraken": VenueResearchProfile(
        venue=Venue("kraken"),
        live_statuses=frozenset(
            {"online", "post_only", "cancel_only", "limit_only", "reduce_only"}
        ),
        truncates_history=True,
        known_missing=(
            ("WAVESEUR", "2024-07-08T12:00:00+00:00"),
            ("WAVESUSD", "2024-07-08T12:00:00+00:00"),
            ("ANTEUR", "2024-09-25T14:00:00+00:00"),
            ("ANTUSD", "2024-09-25T14:00:00+00:00"),
        ),
    ),
}


def quote_policies() -> Mapping[str, frozenset[str]]:
    """The three quote policies, measured rather than chosen (decision D3)."""
    return {
        "EUR": frozenset({"EUR"}),
        "EUR+USD": frozenset({"EUR", "USD"}),
        "EUR+USD+USDT": frozenset({"EUR", "USD", "USDT"}),
    }


def default_account() -> AccountParameters:
    """Decision D2: 1,500 EUR of equity, at most 8 concurrent positions."""
    return AccountParameters(equity_quote=Notional(Decimal(1500)), max_positions=8)


def measure_all(root: Path = DATA_ROOT) -> None:
    """Compute the R4 tables for every venue and every quote policy.

    Reads only from disk. No network, so the tables can be regenerated and
    diffed without asking either venue anything.
    """
    account = default_account()
    sections: list[str] = [
        "<!-- generated by `uv run sextant spike measure`; do not hand-edit -->",
        f"Account applied to the executable universe: {target_position_note(account)}.",
    ]
    summary: dict[str, object] = {}

    for name in sorted(RESEARCH_PROFILES):
        profile = RESEARCH_PROFILES[name]
        venue_root = root / name
        if not venue_root.exists():
            print(f"[{name}] no fetched data at {venue_root}; skipping")
            continue
        dataset = VenueDataset.load(profile.venue, venue_root)
        window = observed_window(dataset)
        if window is None:
            print(f"[{name}] no bars fetched; skipping")
            continue
        calendar = reconstruct_calendar(
            dataset,
            live_statuses=profile.live_statuses,
            truncation_boundary=window[0] if profile.truncates_history else None,
            known_missing=tuple(
                (symbol, Timestamp.parse(instant)) for symbol, instant in profile.known_missing
            ),
        )
        calendar.write_json(venue_root / "listing_calendar.json")
        months = month_starts(*window)
        print(
            f"[{name}] {len(dataset.series)} series, "
            f"{window[0].isoformat()[:10]} to {window[1].isoformat()[:10]}, "
            f"{len(months)} monthly refreshes"
        )

        venue_summary: dict[str, object] = {
            "observed_from": window[0].isoformat(),
            "observed_to": window[1].isoformat(),
            "series": len(dataset.series),
            "candidates": len(dataset.metadata),
            "provenance": {
                key.value: value for key, value in calendar.provenance_counts().items() if value
            },
        }
        for policy_name, quotes in sorted(quote_policies().items()):
            rows = measure(dataset, calendar, quotes, EXCLUDED_BASES, account, months)
            sections.append(render_markdown(profile.venue, policy_name, rows))
            venue_summary[policy_name] = [row.as_json() for row in rows]
            peak = max((row.research.size for row in rows), default=0)
            print(f"[{name}] {policy_name}: peak research universe {peak}")
        summary[name] = venue_summary

    write_json(root / "universe_tables.json", summary)
    # The tables are evidence, so they are committed rather than left in the
    # git-ignored data tree. The raw JSON stays out of the repository.
    tables = Path("docs") / "universe-tables.md"
    tables.parent.mkdir(parents=True, exist_ok=True)
    tables.write_text(NEWLINE.join(sections), encoding="utf-8", newline=NEWLINE)
    print(f"[measure] wrote {tables}")
