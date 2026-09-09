"""Re-running the Phase 0 universe rules on real Kraken history.

SEXTANT-002 measured this venue through its public API and got 24 monthly
refreshes over a two-year window, in which 489 of 1,260 pairs had an
unverifiable listing date and could therefore never enter a point-in-time
universe at all. That was a measurement of the API's truncation, not of the
venue.

Here the same rules run against the archive, where membership comes from file
presence and every listing window carries ``VENUE_ARCHIVE``. Two things change
as a result and both are reported rather than assumed:

* the listing-age rule becomes evaluable, because a bracketed listing is still
  a sourced listing;
* a new rule appears - ``sourced_membership`` - which returns *not evaluable*
  for any instant falling inside a listing or delisting bracket. That column is
  the honest cost of a quarterly archive, and it is the number a reader should
  look at before any other.

Reads only from the store and the calendar. No network.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sextant.adapters.exchanges.kraken.archive import ArchiveManifest
from sextant.adapters.exchanges.kraken.capabilities import VENUE
from sextant.adapters.exchanges.kraken.listing_calendar import KrakenListingCalendar
from sextant.adapters.storage.bars import ParquetBarStore, SeriesKey
from sextant.app.archive_ingest import (
    STORE_ROOT,
    HaircutRow,
    default_account,
    default_haircut,
    haircut_report,
)
from sextant.app.spike import EXCLUDED_BASES
from sextant.app.spike_report import MIN_LISTING_AGE_DAYS, MIN_MEDIAN_QUOTE_VOLUME
from sextant.app.universe_snapshot import SURVIVORSHIP_WINDOW_NOTE
from sextant.domain.instrument import Instrument, InstrumentKey
from sextant.domain.money import Notional, Price, Quantity
from sextant.domain.time import Timeframe, Timestamp
from sextant.engine.universe.policy import (
    UniverseEvaluation,
    UniversePolicy,
    executable_from,
    power_band,
)
from sextant.engine.universe.rules import (
    AccountParameters,
    ExcludedAssetClassRule,
    ListingAgeRule,
    LotSizeFeasibilityRule,
    MedianQuoteVolumeRule,
    MinNotionalFeasibilityRule,
    QuoteCurrencyRule,
    SourcedMembershipRule,
)
from sextant.engine.universe.statistics import DailyObservation, InstrumentHistory

NEWLINE = chr(10)
"""Explicit, so generated markdown is LF on every host."""

TABLES_PATH = Path("docs") / "kraken-archive-tables.md"

#: The two policies the brief asks for. USDT is not measured: research and
#: execution now share a venue, so the reason it existed - a dollar rail on the
#: research venue that the execution venue could not fund - is gone.
QUOTE_POLICIES: Mapping[str, frozenset[str]] = {
    "EUR": frozenset({"EUR"}),
    "EUR+USD": frozenset({"EUR", "USD"}),
}


def build_candidates(
    calendar: KrakenListingCalendar,
    store: ParquetBarStore,
    metadata: Mapping[str, Mapping[str, str]],
) -> tuple[tuple[Instrument, ...], Mapping[InstrumentKey, InstrumentHistory]]:
    """Every pair we can both date and price, in a stable order.

    ``listed_at`` is the *latest* instant by which listing had certainly
    happened - the upper bound of the bracket - because that is the only instant
    from it usable without claiming more than the archive says. It makes every
    pair look younger than it is, which delays admission rather than hastening
    it.

    ``delisted_at`` is deliberately never set. A point delisting date does not
    exist in this dataset, and membership is answered by the calendar through
    ``SourcedMembershipRule`` instead.

    A pair the calendar carries but the store has no series for is dropped and
    counted by the caller, never silently folded into the candidate set with a
    fabricated history.
    """
    instruments: list[Instrument] = []
    histories: dict[InstrumentKey, InstrumentHistory] = {}

    for symbol in sorted(calendar.entries):
        entry = calendar.entries[symbol]
        listed_by = entry.spells[0].certainly_listed_by
        if listed_by is None:
            continue
        key = SeriesKey(VENUE, symbol, Timeframe.D1)
        if not store.has_series(key):
            continue
        constraints = metadata.get(symbol, {})
        instrument = Instrument(
            venue=VENUE,
            symbol=symbol,
            base=constraints.get("base", _split_base(symbol)),
            quote=constraints.get("quote", _split_quote(symbol)),
            listed_at=listed_by,
            tick_size=Price(Decimal(constraints.get("tick_size", "0"))),
            lot_size=Quantity(Decimal(constraints.get("lot_size", "0"))),
            min_notional=Notional(Decimal(constraints.get("min_notional", "0"))),
            delisted_at=None,
            provenance=entry.provenance,
        )
        instruments.append(instrument)
        instrument_key = InstrumentKey(VENUE, symbol)
        histories[instrument_key] = InstrumentHistory.of(
            instrument_key,
            (
                DailyObservation(
                    open_time=bar.open_time,
                    close=bar.close_price,
                    quote_volume=bar.quote_volume,
                )
                for bar in store.read_series(key)
            ),
        )
    return tuple(instruments), histories


def policies(
    calendar: KrakenListingCalendar,
    quotes: frozenset[str],
    histories: Mapping[InstrumentKey, InstrumentHistory],
    account: AccountParameters,
) -> tuple[UniversePolicy, UniversePolicy, UniversePolicy]:
    """Liquidity, research and executable, with membership sourced from the archive.

    The spread rule is absent, not relaxed. SEXTANT-002 measured that neither
    venue publishes historical quotes, so at every past instant it returns *not
    evaluable* and the whole research policy collapses to empty. Carrying a
    column of zeros teaches nothing that the earlier report has not already
    said; the rule is unchanged and simply has no data to run on here.
    """
    membership = SourcedMembershipRule(oracle=calendar)
    quote_rule = QuoteCurrencyRule(allowed=quotes)
    volume = MedianQuoteVolumeRule(minimum=MIN_MEDIAN_QUOTE_VOLUME, histories=histories)
    asset_class = ExcludedAssetClassRule(excluded_bases=EXCLUDED_BASES)

    liquidity = UniversePolicy.of("liquidity", (quote_rule, volume, asset_class))
    research = UniversePolicy.of(
        "research",
        (
            quote_rule,
            membership,
            ListingAgeRule(minimum_days=MIN_LISTING_AGE_DAYS),
            volume,
            asset_class,
        ),
    )
    executable = executable_from(
        research,
        (
            MinNotionalFeasibilityRule(account=account),
            LotSizeFeasibilityRule(account=account, histories=histories),
        ),
    )
    return liquidity, research, executable


@dataclass(frozen=True, slots=True)
class MonthlyRow:
    """One monthly refresh, for one quote policy."""

    month: str
    liquidity: UniverseEvaluation
    research: UniverseEvaluation
    executable: UniverseEvaluation
    undetermined: int

    def as_json(self) -> dict[str, object]:
        """Serialisable form, sorted so two runs diff cleanly."""
        return {
            "month": self.month,
            "liquidity_size": self.liquidity.size,
            "research_size": self.research.size,
            "executable_size": self.executable.size,
            "membership_undetermined": self.undetermined,
            "power_band": power_band(self.research.size),
            "candidates": self.research.candidates,
            "research_rejected": dict(sorted(self.research.rejection_counts().items())),
            "research_not_evaluable": dict(sorted(self.research.not_evaluable_counts().items())),
            "executable_rejected": dict(sorted(self.executable.rejection_counts().items())),
        }


def month_starts(first: Timestamp, last: Timestamp) -> tuple[Timestamp, ...]:
    """Monthly refresh instants, on the first UTC day of each covered month."""
    months: list[Timestamp] = []
    year, month = first.value.year, first.value.month
    while True:
        instant = Timestamp(datetime(year, month, 1, tzinfo=UTC))
        if instant > last:
            break
        if instant >= first:
            months.append(instant)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return tuple(months)


def measure(
    calendar: KrakenListingCalendar,
    instruments: Sequence[Instrument],
    histories: Mapping[InstrumentKey, InstrumentHistory],
    quotes: frozenset[str],
    account: AccountParameters,
    months: Sequence[Timestamp],
) -> tuple[MonthlyRow, ...]:
    """Evaluate every universe variant at every refresh instant."""
    liquidity, research, executable = policies(calendar, quotes, histories, account)
    return tuple(
        MonthlyRow(
            month=f"{at.value.year:04d}-{at.value.month:02d}",
            liquidity=liquidity.evaluate(instruments, at),
            research=research.evaluate(instruments, at),
            executable=executable.evaluate(instruments, at),
            undetermined=len(calendar.undetermined_at(at)),
        )
        for at in months
    )


def render_markdown(policy_name: str, rows: Sequence[MonthlyRow]) -> str:
    """One table, as markdown."""
    header = (
        f"#### kraken archive - quote policy `{policy_name}`\n\n"
        "| month | liquidity | research | executable | band | membership n/e | "
        "rej: quote | rej: membership | n/e: membership | rej: age | rej: volume | "
        "n/e: volume | rej: class | rej: min_notional | rej: lot_size |\n"
        "|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n"
    )
    lines = []
    for row in rows:
        rejected = row.research.rejection_counts()
        unknown = row.research.not_evaluable_counts()
        exec_rejected = row.executable.rejection_counts()
        lines.append(
            f"| {row.month} | {row.liquidity.size} | {row.research.size} | "
            f"{row.executable.size} | {power_band(row.research.size)} | "
            f"{row.undetermined} | "
            f"{rejected.get('quote_currency', 0)} | "
            f"{rejected.get('sourced_membership', 0)} | "
            f"{unknown.get('sourced_membership', 0)} | "
            f"{rejected.get('listing_age', 0)} | "
            f"{rejected.get('median_quote_volume', 0)} | "
            f"{unknown.get('median_quote_volume', 0)} | "
            f"{rejected.get('asset_class', 0)} | "
            f"{exec_rejected.get('min_notional_feasible', 0)} | "
            f"{exec_rejected.get('lot_size_feasible', 0)} |"
        )
    return header + "\n".join(lines) + "\n"


def render_haircut(rows: Sequence[HaircutRow]) -> str:
    """The delisting-haircut sensitivity, as markdown."""
    header = (
        "#### delisting haircut sensitivity\n\n"
        "Every pair with a priced daily series, held at the account's target position "
        "size and marked out at its last stored close. Equal notional rather than equal "
        "units, so the number is not dominated by whichever pairs happen to be "
        "expensive. Not a backtest: this is the assumption's exposure surface.\n\n"
        "| haircut | positions | haircut applied to | gross proceeds | proceeds | "
        "cost | cost / gross |\n|---:|---:|---:|---:|---:|---:|---:|\n"
    )
    lines = [
        f"| {row.fraction} | {row.marked_out} | {row.haircut_applied_to} | "
        f"{_round(row.gross_proceeds)} | {_round(row.proceeds)} | "
        f"{_round(row.total_cost)} | {_round(row.cost_fraction_of_gross, 4)} |"
        for row in rows
    ]
    return header + "\n".join(lines) + "\n"


def measure_all(
    calendar: KrakenListingCalendar,
    store: ParquetBarStore,
    manifest: ArchiveManifest,
    *,
    metadata: Mapping[str, Mapping[str, str]] | None = None,
    tables_path: Path = TABLES_PATH,
    store_root: Path = STORE_ROOT,
) -> Mapping[str, object]:
    """Recompute every table and write both the markdown and the raw JSON."""
    account = default_account()
    constraints = metadata if metadata is not None else _load_venue_constraints()
    instruments, histories = build_candidates(calendar, store, constraints)
    if not calendar.quarters:
        raise ValueError("an empty calendar cannot be measured")

    window = (calendar.quarters[0].starts_at, calendar.quarters[-1].ends_at)
    months = month_starts(*window)
    print(
        f"[measure] {len(instruments)} candidates, {len(months)} monthly refreshes, "
        f"{window[0].isoformat()[:10]} to {window[1].isoformat()[:10]}"
    )

    sections: list[str] = [
        "<!-- generated by `uv run sextant archive measure`; do not hand-edit -->",
        "",
        f"Quarters held: {', '.join(q.label for q in calendar.quarters) or 'none'}.",
        f"Quarters missing: {', '.join(q.label for q in manifest.missing) or 'none'}.",
        "",
        f"> {SURVIVORSHIP_WINDOW_NOTE}",
        "",
        f"Delisting mark-out assumption: {default_haircut()}.",
        "",
        _age_rule_note(calendar, months),
        "",
    ]
    summary: dict[str, object] = {
        "quarters_held": [q.label for q in calendar.quarters],
        "quarters_missing": [q.label for q in manifest.missing],
        "candidates": len(instruments),
        "calendar_entries": len(calendar.entries),
        "listed_and_untraded": sorted(calendar.untraded_symbols()),
        "survivorship_window_note": SURVIVORSHIP_WINDOW_NOTE,
        "delisting_haircut": dict(default_haircut().as_metadata()),
    }

    for name in sorted(QUOTE_POLICIES):
        rows = measure(calendar, instruments, histories, QUOTE_POLICIES[name], account, months)
        sections.append(render_markdown(name, rows))
        summary[name] = [row.as_json() for row in rows]
        peak = max((row.research.size for row in rows), default=0)
        print(f"[measure] {name}: peak research universe {peak}")

    haircut_rows = haircut_report(calendar, store)
    sections.append(render_haircut(haircut_rows))
    summary["haircut_sensitivity"] = [asdict(row) for row in haircut_rows]

    tables_path.parent.mkdir(parents=True, exist_ok=True)
    tables_path.write_text(NEWLINE.join(sections), encoding="utf-8", newline=NEWLINE)
    _write_json(store_root / "archive_universe_tables.json", summary)
    print(f"[measure] wrote {tables_path}")
    return summary


def _age_rule_note(calendar: KrakenListingCalendar, months: Sequence[Timestamp]) -> str:
    """State plainly when the archive is too short for the listing-age rule.

    With only part of the archive in hand, the earliest instant any pair is
    *certainly* listed by is the end of the first held quarter. A 180-day age
    requirement measured from there can fall beyond the last month the archive
    covers, in which case the rule rejects every candidate at every refresh and
    the research column is zero for a reason that is arithmetic rather than
    empirical. Saying so is the difference between a measurement and a mistake.
    """
    if not calendar.quarters or not months:
        return ""
    earliest_certain = calendar.quarters[0].ends_at
    reachable = earliest_certain.plus(timedelta(days=MIN_LISTING_AGE_DAYS))
    if reachable <= months[-1]:
        return ""
    return (
        f"> **The research column is zero throughout, and it is arithmetic rather than a "
        f"finding.** The earliest instant any pair is certainly listed by is "
        f"{earliest_certain.isoformat()[:10]}, the end of the first held quarter. Rule 2 "
        f"needs {MIN_LISTING_AGE_DAYS} days from there, which is "
        f"{reachable.isoformat()[:10]}, and the held archive ends "
        f"{months[-1].isoformat()[:10]}. No pair can clear the listing-age rule inside a "
        f"window this short, so `rej: age` equals the candidate count in every row. The "
        f"liquidity column, which omits rule 2, is the only one with content in it here. "
        f"This resolves itself as soon as earlier quarters are acquired."
    )


def _load_venue_constraints() -> Mapping[str, Mapping[str, str]]:
    """Current tick, lot and minimum-notional values, from the SEXTANT-002 fetch.

    These are *current* values applied to past dates, which is itself a
    reconstruction and is stated as one in ``docs/DATA-AVAILABILITY.md``. The
    venue publishes no history of them. Where the fetch is absent the executable
    universe simply cannot be computed, so the constraints default to zero and
    the account rules admit everything - which the tables then show as an
    executable universe identical to research, rather than as a silent pass.
    """
    path = Path("data") / "spike" / "kraken" / "pair_metadata.json"
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    return {
        str(symbol): {str(key): str(value) for key, value in entry.items()}
        for symbol, entry in raw.items()
    }


def _split_base(symbol: str) -> str:
    """Split the base asset out of the symbol when the venue no longer names it.

    Used only for pairs the archive carries but ``AssetPairs`` has forgotten. The
    quote list is ordered longest-first so that USDT is not read as USD."""
    for quote in ("EUR", "USD", "USDT", "USDC", "XBT", "ETH", "GBP", "AUD", "CAD", "CHF", "JPY"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[: -len(quote)]
    return symbol


def _split_quote(symbol: str) -> str:
    """Split the quote asset out of the symbol when the venue no longer names it."""
    for quote in ("USDT", "USDC", "EUR", "USD", "XBT", "ETH", "GBP", "AUD", "CAD", "CHF", "JPY"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return quote
    return ""


def _round(value: str, places: int = 2) -> str:
    """Render a decimal string at a fixed number of places, for a table cell."""
    return str(Decimal(value).quantize(Decimal(1).scaleb(-places)))


def _write_json(path: Path, payload: object) -> None:
    """Write UTF-8 JSON with stable ordering, creating parents as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=1, sort_keys=True, default=str)
        handle.write("\n")
