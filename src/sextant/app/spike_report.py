"""Offline analysis of what the spike fetched.

Everything here reads from ``data/spike`` and touches no network, so the tables
in the report can be regenerated and diffed without asking either venue
anything. That is also what makes the determinism test meaningful: the same
files must produce byte-identical membership, every time.

Two reconstructions happen here, and both are labelled rather than hidden:

* a **listing window** per symbol, inferred from the first and last bar the
  venue would serve. A first bar is a lower bound on a listing date, not the
  listing date, and it is recorded as ``RECONSTRUCTED``;
* **current** tick size, lot size and minimum notional applied to past dates,
  because neither venue publishes a history of those constraints.

Both weaken the executable universe specifically, and the report says where.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sextant.adapters.exchanges.listing_calendar import CalendarEntry, ListingCalendar
from sextant.domain.instrument import Instrument, InstrumentKey
from sextant.domain.money import Notional, Price, Quantity
from sextant.domain.provenance import ListingWindow, Provenance
from sextant.domain.time import Timeframe, Timestamp
from sextant.domain.venue import Venue
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
    MedianSpreadRule,
    MinNotionalFeasibilityRule,
    QuoteCurrencyRule,
)
from sextant.engine.universe.statistics import DailyObservation, InstrumentHistory

#: Rolling median turnover floor, in quote units, from PHASE-0-FINDINGS §5 rule 3.
MIN_MEDIAN_QUOTE_VOLUME = Notional(Decimal(250_000))

#: Median spread cap in basis points, rule 4.
MAX_MEDIAN_SPREAD_BPS = Decimal(25)

#: Minimum listing age in days, rule 2.
MIN_LISTING_AGE_DAYS = 180


@dataclass(frozen=True, slots=True)
class VenueDataset:
    """Everything one venue's fetch produced, loaded back from disk."""

    venue: Venue
    metadata: Mapping[str, Mapping[str, str]]
    series: Mapping[str, tuple[tuple[int, str, str], ...]]
    spreads_bps: Mapping[str, Decimal]
    spread_observed_at: Timestamp | None

    @classmethod
    def load(cls, venue: Venue, root: Path) -> VenueDataset:
        """Read one venue's fetched artifacts."""
        metadata_file = "symbol_metadata.json"
        if not (root / metadata_file).exists():
            metadata_file = "pair_metadata.json"
        with (root / metadata_file).open(encoding="utf-8") as handle:
            raw_metadata = json.load(handle)
        metadata = {
            str(symbol): {str(key): str(value) for key, value in entry.items()}
            for symbol, entry in raw_metadata.items()
        }

        series: dict[str, tuple[tuple[int, str, str], ...]] = {}
        bars_dir = root / "bars"
        if bars_dir.exists():
            for path in sorted(bars_dir.glob("*.json")):
                with path.open(encoding="utf-8") as handle:
                    payload = json.load(handle)
                series[str(payload["symbol"])] = tuple(
                    (int(row[0]), str(row[1]), str(row[2])) for row in payload["rows"]
                )

        spreads: dict[str, Decimal] = {}
        observed_at: Timestamp | None = None
        snapshot = root / "spread_snapshot.json"
        if snapshot.exists():
            with snapshot.open(encoding="utf-8") as handle:
                payload = json.load(handle)
            spreads = {str(k): Decimal(str(v)) for k, v in payload.get("bps", {}).items()}
            if payload.get("observed_at"):
                observed_at = Timestamp.parse(str(payload["observed_at"]))
        return cls(venue, metadata, series, spreads, observed_at)


def reconstruct_calendar(
    dataset: VenueDataset,
    *,
    live_statuses: frozenset[str],
    known_missing: Sequence[tuple[str, Timestamp]] = (),
    truncation_boundary: Timestamp | None = None,
) -> ListingCalendar:
    """Infer a listing window per symbol from observed bars and venue status.

    Methodology, stated here because the report quotes it verbatim:

    * ``listed_at`` is the open time of the first daily bar the venue will
      serve. That is a *lower bound* on the listing date: a pair listed on a day
      with no trades has no bar for that day. Recorded as ``RECONSTRUCTED``;
    * unless that first bar sits on ``truncation_boundary``, the edge of what
      the venue is willing to serve at all. Then it says nothing about when
      the pair listed, and the window is recorded as ``UNVERIFIED`` so that no
      rule downstream mistakes a truncation artefact for a listing date;
    * ``delisted_at`` is set only when the venue's own metadata says the symbol
      is no longer live. The *fact* of being delisted is the venue's statement;
      the *instant* is the close of the last observed bar, and is reconstructed;
    * a symbol the venue still lists gets no delisting date at all;
    * ``known_missing`` carries symbols established by official announcement to
      have been delisted, which the venue's instrument endpoint no longer
      describes at all. Their constraints cannot be rebuilt, so they are
      recorded as unavailable rather than dropped. Dropping them is what turns
      a universe into a list of survivors.
    """
    entries: list[CalendarEntry] = []
    for symbol in sorted(dataset.metadata):
        rows = dataset.series.get(symbol, ())
        if not rows:
            continue
        first = Timestamp.from_epoch_millis(rows[0][0])
        last = Timestamp.from_epoch_millis(rows[-1][0])
        status = dataset.metadata[symbol].get("status", "")
        still_live = status in live_statuses
        truncated = truncation_boundary is not None and first <= truncation_boundary.plus(
            Timeframe.D1.duration
        )
        entries.append(
            CalendarEntry(
                symbol=symbol,
                window=ListingWindow(
                    listed_at=first,
                    delisted_at=None if still_live else last.plus(Timeframe.D1.duration),
                    provenance=(Provenance.UNVERIFIED if truncated else Provenance.RECONSTRUCTED),
                    note=(
                        f"venue status {status!r}; first observed bar sits on the edge "
                        "of the servable window, so it dates the truncation, not the "
                        "listing"
                        if truncated
                        else f"venue status {status!r}; first and last observed daily "
                        "bar. Listing instant is a lower bound."
                    ),
                ),
                metadata_available=True,
            )
        )
    for symbol, delisted_at in known_missing:
        entries.append(
            CalendarEntry(
                symbol=symbol,
                window=ListingWindow(
                    listed_at=Timestamp(datetime(2013, 1, 1, tzinfo=UTC)),
                    delisted_at=delisted_at,
                    provenance=Provenance.VENUE_ANNOUNCEMENT,
                    note=(
                        "delisting instant from the venue's own announcement; listing "
                        "instant unknown, so membership before the delisting is an "
                        "upper bound rather than a measurement"
                    ),
                ),
                metadata_available=False,
            )
        )
    return ListingCalendar.of(
        venue=dataset.venue,
        entries=entries,
        methodology=reconstruct_calendar.__doc__ or "",
    )


def histories(dataset: VenueDataset) -> Mapping[InstrumentKey, InstrumentHistory]:
    """Turn stored rows into point-in-time queryable histories."""
    built: dict[InstrumentKey, InstrumentHistory] = {}
    for symbol in sorted(dataset.series):
        observations = [
            DailyObservation(
                open_time=Timestamp.from_epoch_millis(row[0]),
                close=Price(Decimal(row[1])),
                quote_volume=Notional(Decimal(row[2])) if row[2] else None,
            )
            for row in dataset.series[symbol]
        ]
        key = InstrumentKey(dataset.venue, symbol)
        built[key] = InstrumentHistory.of(key, observations)
    return built


def candidates(dataset: VenueDataset, calendar: ListingCalendar) -> tuple[Instrument, ...]:
    """Every instrument we can construct, in a stable order."""
    built: list[Instrument] = []
    for symbol in sorted(dataset.metadata):
        entry = calendar.entry_for(symbol)
        if entry is None or not entry.metadata_available:
            continue
        meta = dataset.metadata[symbol]
        built.append(
            Instrument(
                venue=dataset.venue,
                symbol=symbol,
                base=meta["base"],
                quote=meta["quote"],
                listed_at=entry.window.listed_at,
                tick_size=Price(Decimal(meta["tick_size"])),
                lot_size=Quantity(Decimal(meta["lot_size"])),
                min_notional=Notional(Decimal(meta["min_notional"])),
                delisted_at=entry.window.delisted_at,
                provenance=entry.window.provenance,
            )
        )
    return tuple(built)


def policies(
    quotes: frozenset[str],
    excluded_bases: frozenset[str],
    history_map: Mapping[InstrumentKey, InstrumentHistory],
    account: AccountParameters,
    spreads: Mapping[tuple[InstrumentKey, Timestamp], Decimal],
) -> tuple[UniversePolicy, UniversePolicy, UniversePolicy, UniversePolicy]:
    """Three policies, because one of the seven rules is not computable.

    The research/executable split is decision D1: rules 1, 2, 3, 4 and 7
    describe the market and belong to research; rules 5 and 6 describe our
    wallet and belong only to execution.

    Rule 4, the spread cap, is stated over a trailing median of quoted spread.
    Neither venue publishes historical quotes, so at every past instant it
    returns ``NOT_EVALUABLE`` and, since an unverifiable instrument is never
    admitted, the full research policy is empty for every month but the one we
    measured live. That is the correct behaviour and a useless table.

    So both are reported. ``research`` applies the four computable rules and is
    the number the reader should use; ``research_with_spread`` applies all five
    and shows exactly how much of the universe the missing dataset costs. The
    rule is not weakened, relaxed or removed - it is reported as unevaluable,
    which is what it is.
    """
    computable = (
        QuoteCurrencyRule(allowed=quotes),
        ListingAgeRule(minimum_days=MIN_LISTING_AGE_DAYS),
        MedianQuoteVolumeRule(minimum=MIN_MEDIAN_QUOTE_VOLUME, histories=history_map),
        ExcludedAssetClassRule(excluded_bases=excluded_bases),
    )
    spread_rule = MedianSpreadRule(maximum_bps=MAX_MEDIAN_SPREAD_BPS, observed_bps=spreads)
    liquidity = UniversePolicy.of(
        "liquidity",
        (computable[0], computable[2], computable[3]),
    )
    research = UniversePolicy.of("research", computable)
    research_with_spread = UniversePolicy.of("research+spread", (*computable, spread_rule))
    executable = executable_from(
        research,
        (
            MinNotionalFeasibilityRule(account=account),
            LotSizeFeasibilityRule(account=account, histories=history_map),
        ),
    )
    return liquidity, research, research_with_spread, executable


def month_starts(first: Timestamp, last: Timestamp) -> tuple[Timestamp, ...]:
    """Monthly refresh instants, on the first UTC day of each month."""
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


def observed_window(dataset: VenueDataset) -> tuple[Timestamp, Timestamp] | None:
    """The span the fetched bars actually cover, across all symbols."""
    firsts = [rows[0][0] for rows in dataset.series.values() if rows]
    lasts = [rows[-1][0] for rows in dataset.series.values() if rows]
    if not firsts:
        return None
    return Timestamp.from_epoch_millis(min(firsts)), Timestamp.from_epoch_millis(max(lasts))


@dataclass(frozen=True, slots=True)
class MonthlyRow:
    """One row of the R4 table."""

    month: str
    liquidity: UniverseEvaluation
    """Rules 1, 3 and 7 only. Not point-in-time complete: it drops the
    listing-age rule, so it screens for what is liquid rather than resolving a
    universe. Reported because on a venue that cannot date its own listings it
    is the only number with any content left in it."""

    research: UniverseEvaluation
    research_with_spread: UniverseEvaluation
    executable: UniverseEvaluation
    known_missing: int

    def as_json(self) -> dict[str, object]:
        """Serialisable form, sorted so two runs diff cleanly."""
        return {
            "month": self.month,
            "liquidity_size": self.liquidity.size,
            "research_size": self.research.size,
            "research_with_spread_size": self.research_with_spread.size,
            "executable_size": self.executable.size,
            "known_missing": self.known_missing,
            "power_band": power_band(self.research.size),
            "candidates": self.research.candidates,
            "research_rejected": dict(sorted(self.research.rejection_counts().items())),
            "research_not_evaluable": dict(sorted(self.research.not_evaluable_counts().items())),
            "executable_rejected": dict(sorted(self.executable.rejection_counts().items())),
            "executable_not_evaluable": dict(
                sorted(self.executable.not_evaluable_counts().items())
            ),
            "research_members": [str(key) for key in self.research.members],
        }


def measure(
    dataset: VenueDataset,
    calendar: ListingCalendar,
    quotes: frozenset[str],
    excluded_bases: frozenset[str],
    account: AccountParameters,
    months: Sequence[Timestamp],
) -> tuple[MonthlyRow, ...]:
    """Evaluate every universe variant at every refresh instant."""
    history_map = histories(dataset)
    instruments = candidates(dataset, calendar)
    spread_map: dict[tuple[InstrumentKey, Timestamp], Decimal] = {}
    if dataset.spread_observed_at is not None and months:
        latest = months[-1]
        for symbol, value in dataset.spreads_bps.items():
            spread_map[(InstrumentKey(dataset.venue, symbol), latest)] = value
    liquidity, research, research_with_spread, executable = policies(
        quotes, excluded_bases, history_map, account, spread_map
    )
    return tuple(
        MonthlyRow(
            month=f"{at.value.year:04d}-{at.value.month:02d}",
            liquidity=liquidity.evaluate(instruments, at),
            research=research.evaluate(instruments, at),
            research_with_spread=research_with_spread.evaluate(instruments, at),
            executable=executable.evaluate(instruments, at),
            known_missing=len(calendar.missing_at(at)),
        )
        for at in months
    )


def render_markdown(venue: Venue, policy_name: str, rows: Sequence[MonthlyRow]) -> str:
    """One R4 table, as markdown."""
    header = (
        f"#### {venue.name} - quote policy `{policy_name}`\n\n"
        "| month | liquidity | research | +spread | executable | band | missing | "
        "rej: quote | rej: age | n/e: age | rej: volume | n/e: volume | "
        "n/e: spread | rej: class | rej: min_notional | rej: lot_size |\n"
        "|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n"
    )
    lines = []
    for row in rows:
        rejected = row.research.rejection_counts()
        unknown = row.research_with_spread.not_evaluable_counts()
        exec_rejected = row.executable.rejection_counts()
        lines.append(
            f"| {row.month} | {row.liquidity.size} | {row.research.size} | "
            f"{row.research_with_spread.size} | {row.executable.size} | "
            f"{power_band(row.research.size)} | {row.known_missing} | "
            f"{rejected.get('quote_currency', 0)} | {rejected.get('listing_age', 0)} | "
            f"{row.research.not_evaluable_counts().get('listing_age', 0)} | "
            f"{rejected.get('median_quote_volume', 0)} | "
            f"{unknown.get('median_quote_volume', 0)} | {unknown.get('median_spread', 0)} | "
            f"{rejected.get('asset_class', 0)} | "
            f"{exec_rejected.get('min_notional_feasible', 0)} | "
            f"{exec_rejected.get('lot_size_feasible', 0)} |"
        )
    return header + "\n".join(lines) + "\n"


def target_position_note(account: AccountParameters) -> str:
    """Human-readable statement of the account thresholds actually applied."""
    return (
        f"equity {account.equity_quote.amount} / {account.max_positions} positions "
        f"= {account.target_position.amount} per position; "
        f"min_notional must be <= {account.max_min_notional.amount}; "
        f"one lot step must be worth <= {account.max_rounding_loss.amount}"
    )


def days_of_history(dataset: VenueDataset) -> int:
    """How many days the deepest fetched series spans."""
    window = observed_window(dataset)
    if window is None:
        return 0
    return (window[1].value - window[0].value) // timedelta(days=1)
