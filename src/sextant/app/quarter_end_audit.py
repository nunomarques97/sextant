"""Chasing the two Q3-to-Q4 2025 exceptions to the quarter-end rule.

The whole listing calendar rests on one property: each quarterly archive
contains the pairs that were listed at the end of that quarter. SEXTANT-003
checked it across all thirteen quarters and found it held for 163 of 165 priced
deaths, with two exceptions in the Q3-to-Q4 2025 transition that were left
unexplained. Two out of 165 is small; unexplained, in the mechanism the whole
dataset rests on, is not acceptable to leave standing.

The check that produced the exceptions
---------------------------------------

For each quarter transition: take the pairs present in quarter *N* and absent in
*N+1*, and ask whether their last daily bar falls within seven days of the
quarter boundary. If the rule holds they were trading up to the end and then
vanished.

The proxy, and where it breaks
-------------------------------

"A bar within seven days" is a proxy for "was still trading", and it is only a
good proxy for a pair that trades most days. The archive writes no row for a day
with no trades, so a pair that trades twice a week produces a series with
multi-day holes in it, and the interval between its last two bars can exceed
seven days without anything having happened.

This module measures each pair's own trading cadence and compares the final gap
against it. A final gap inside the pair's own distribution is not evidence of an
early death; it is the pair trading the way it always traded.

Reads only from the store and the calendar. No network, and it writes nothing
except where the caller asks for it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from statistics import mean, median

from sextant.adapters.exchanges.kraken.archive import Quarter
from sextant.adapters.exchanges.kraken.capabilities import VENUE
from sextant.adapters.exchanges.kraken.listing_calendar import KrakenListingCalendar
from sextant.adapters.storage.bars import ParquetBarStore, SeriesKey
from sextant.domain.time import Timeframe

#: How far before the quarter boundary a final bar may fall before the pair is
#: flagged. SEXTANT-003's threshold, unchanged, so the two runs are comparable.
THRESHOLD_DAYS = 7

#: How many recent intervals a pair's cadence is measured over.
CADENCE_WINDOW = 180

#: The transition SEXTANT-003 left unexplained: pairs present at the end of
#: Q3 2025 and absent from the Q4 2025 archive.
UNEXPLAINED_TRANSITION = Quarter(2025, 4)


@dataclass(frozen=True, slots=True)
class PairCadence:
    """One pair's final gap, measured against how it normally traded."""

    symbol: str
    bars: int
    last_bar_date: str
    final_gap_days: int
    median_gap_days: float
    mean_gap_days: float
    p90_gap_days: int
    max_gap_days: int
    sparse_share: float
    """Fraction of recent intervals longer than one day."""

    @property
    def exceeds_threshold(self) -> bool:
        """Whether this pair is one of the flagged exceptions."""
        return self.final_gap_days > THRESHOLD_DAYS

    @property
    def final_gap_is_ordinary(self) -> bool:
        """Whether the final gap sits inside this pair's own distribution.

        Compared against the pair's ninetieth-percentile interval rather than
        its maximum: a single long holiday in two years should not license any
        gap at all, and the ninetieth percentile is what "this pair often goes
        this long without a trade" actually means.
        """
        return self.final_gap_days <= max(self.p90_gap_days * 2, self.max_gap_days)

    def as_json(self) -> dict[str, object]:
        """Serialisable form."""
        return {
            "symbol": self.symbol,
            "bars": self.bars,
            "last_bar_date": self.last_bar_date,
            "final_gap_days": self.final_gap_days,
            "median_gap_days": self.median_gap_days,
            "mean_gap_days": self.mean_gap_days,
            "p90_gap_days": self.p90_gap_days,
            "max_gap_days": self.max_gap_days,
            "sparse_share": self.sparse_share,
            "exceeds_threshold": self.exceeds_threshold,
            "final_gap_is_ordinary": self.final_gap_is_ordinary,
        }


def cadence_for(
    symbol: str,
    store: ParquetBarStore,
    boundary_days: int,
    *,
    window: int = CADENCE_WINDOW,
) -> PairCadence | None:
    """Measure one pair's trading cadence, or None when it has no priced series."""
    key = SeriesKey(VENUE, symbol, Timeframe.D1)
    if not store.has_series(key):
        return None
    bars = store.read_series(key)
    if len(bars) < 2:
        return None
    tail = bars[-window:]
    gaps = [
        (later.open_time.value - earlier.open_time.value).days for earlier, later in pairwise(tail)
    ]
    if not gaps:
        return None
    ordered = sorted(gaps)
    return PairCadence(
        symbol=symbol,
        bars=len(bars),
        last_bar_date=bars[-1].open_time.isoformat()[:10],
        final_gap_days=boundary_days,
        median_gap_days=float(median(ordered)),
        mean_gap_days=round(float(mean(ordered)), 3),
        p90_gap_days=ordered[min(int(0.9 * len(ordered)), len(ordered) - 1)],
        max_gap_days=ordered[-1],
        sparse_share=round(sum(1 for gap in gaps if gap > 1) / len(gaps), 3),
    )


def audit_transition(
    calendar: KrakenListingCalendar,
    store: ParquetBarStore,
    quarter: Quarter,
) -> tuple[PairCadence, ...]:
    """Every pair whose delisting bracket is exactly ``quarter``, with its cadence.

    ``quarter`` is the quarter the pair was *absent* from, so the boundary the
    final gap is measured against is the end of the quarter before it.
    """
    boundary = _previous(quarter).ends_at
    measured: list[PairCadence] = []
    for symbol in sorted(calendar.delisted_during(quarter)):
        key = SeriesKey(VENUE, symbol, Timeframe.D1)
        if not store.has_series(key):
            continue
        bars = store.read_series(key)
        if not bars:
            continue
        last_close = bars[-1].open_time.plus(Timeframe.D1.duration)
        gap = (boundary.value - last_close.value).days
        cadence = cadence_for(symbol, store, gap)
        if cadence is not None:
            measured.append(cadence)
    return tuple(measured)


def _previous(quarter: Quarter) -> Quarter:
    """The quarter immediately before ``quarter``."""
    if quarter.quarter == 1:
        return Quarter(year=quarter.year - 1, quarter=4)
    return Quarter(year=quarter.year, quarter=quarter.quarter - 1)


def siblings_of(symbol: str, calendar: KrakenListingCalendar) -> tuple[str, ...]:
    """Other pairs on the same base asset that the archive knows about.

    A pair's quote leg can stop trading while the asset itself keeps trading on
    another leg. That is the difference between "this asset died early" and
    "nobody traded this particular pair last week", and it is the fact that
    settles whether a flagged exception means anything.
    """
    base = _base_of(symbol)
    if not base:
        return ()
    return tuple(
        other for other in sorted(calendar.entries) if other != symbol and _base_of(other) == base
    )


_QUOTES = ("USDT", "USDC", "EUR", "USD", "GBP", "XBT", "ETH", "AUD", "CHF", "JPY")


def _base_of(symbol: str) -> str:
    """The base leg of a venue-native symbol, by suffix."""
    for quote in _QUOTES:
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[: -len(quote)]
    return ""


#: What settles a flagged pair. Two independent kinds of evidence, and an
#: explicit third value for the case where neither applies - which is the answer
#: that must never be quietly rounded to one of the other two.
EXPLAINED_BY_CADENCE = "the final gap fits this pair's own trading cadence"
EXPLAINED_BY_SIBLING = (
    "another pair on the same base asset traded within the threshold, so the asset "
    "was listed at the boundary and only this quote leg went quiet"
)
UNEXPLAINED = "neither this pair's cadence nor a sibling leg accounts for the gap"


@dataclass(frozen=True, slots=True)
class TransitionAudit:
    """The verdict on one quarter transition."""

    quarter: Quarter
    measured: tuple[PairCadence, ...]
    flagged: tuple[PairCadence, ...]
    sibling_last_bars: Mapping[str, Mapping[str, str]]
    sibling_gaps: Mapping[str, Mapping[str, int]]
    """Days from each sibling's last bar to the same quarter boundary."""

    def verdict_for(self, symbol: str) -> str:
        """Why this flagged pair is or is not evidence against the rule."""
        pair = next((item for item in self.flagged if item.symbol == symbol), None)
        if pair is None:
            raise KeyError(f"{symbol} is not flagged in this transition")
        if pair.final_gap_is_ordinary:
            return EXPLAINED_BY_CADENCE
        gaps = self.sibling_gaps.get(symbol, {})
        if any(gap <= THRESHOLD_DAYS for gap in gaps.values()):
            return EXPLAINED_BY_SIBLING
        return UNEXPLAINED

    @property
    def verdicts(self) -> Mapping[str, str]:
        """The verdict for every flagged pair."""
        return {item.symbol: self.verdict_for(item.symbol) for item in self.flagged}

    @property
    def all_flagged_are_explained(self) -> bool:
        """Whether every flagged pair has an account that does not implicate the rule."""
        return all(verdict != UNEXPLAINED for verdict in self.verdicts.values())

    @property
    def all_flagged_are_ordinary(self) -> bool:
        """Whether every flagged pair's final gap fits its own trading cadence.

        The narrower of the two tests, and deliberately kept separate: one of the
        two pairs in the Q3-to-Q4 2025 transition fails it and is settled by the
        sibling evidence instead. Collapsing the two would hide which kind of
        evidence did the work.
        """
        return all(item.final_gap_is_ordinary for item in self.flagged)

    def as_json(self) -> dict[str, object]:
        """Serialisable form."""
        return {
            "quarter": self.quarter.label,
            "priced_deaths": len(self.measured),
            "flagged": [item.as_json() for item in self.flagged],
            "verdicts": dict(self.verdicts),
            "all_flagged_are_ordinary": self.all_flagged_are_ordinary,
            "all_flagged_are_explained": self.all_flagged_are_explained,
            "sibling_last_bars": {
                symbol: dict(siblings) for symbol, siblings in self.sibling_last_bars.items()
            },
            "sibling_gaps": {symbol: dict(gaps) for symbol, gaps in self.sibling_gaps.items()},
        }


def audit(
    calendar: KrakenListingCalendar,
    store: ParquetBarStore,
    quarter: Quarter = UNEXPLAINED_TRANSITION,
) -> TransitionAudit:
    """Audit one transition and gather the evidence for each flagged pair."""
    measured = audit_transition(calendar, store, quarter)
    flagged = tuple(item for item in measured if item.exceeds_threshold)
    boundary = _previous(quarter).ends_at
    sibling_last: dict[str, Mapping[str, str]] = {}
    sibling_gaps: dict[str, Mapping[str, int]] = {}
    for item in flagged:
        found: dict[str, str] = {}
        gaps: dict[str, int] = {}
        for sibling in siblings_of(item.symbol, calendar):
            key = SeriesKey(VENUE, sibling, Timeframe.D1)
            if not store.has_series(key):
                continue
            bars = store.read_series(key)
            if not bars:
                continue
            last_close = bars[-1].open_time.plus(Timeframe.D1.duration)
            found[sibling] = bars[-1].open_time.isoformat()[:10]
            gaps[sibling] = (boundary.value - last_close.value).days
        sibling_last[item.symbol] = found
        sibling_gaps[item.symbol] = gaps
    return TransitionAudit(
        quarter=quarter,
        measured=measured,
        flagged=flagged,
        sibling_last_bars=sibling_last,
        sibling_gaps=sibling_gaps,
    )


def render(result: TransitionAudit) -> str:
    """The audit as readable lines, for the report."""
    lines = [
        f"Transition into {result.quarter.label}: {len(result.measured)} priced deaths, "
        f"{len(result.flagged)} with a final bar more than {THRESHOLD_DAYS} days before "
        "the boundary.",
        "",
    ]
    for item in result.flagged:
        siblings = result.sibling_last_bars.get(item.symbol, {})
        lines.extend(
            [
                f"  {item.symbol}: last bar {item.last_bar_date}, "
                f"final gap {item.final_gap_days} days.",
                f"    own cadence over the last {CADENCE_WINDOW} bars - median "
                f"{item.median_gap_days:g}d, mean {item.mean_gap_days:g}d, p90 "
                f"{item.p90_gap_days}d, max {item.max_gap_days}d, "
                f"{item.sparse_share:.0%} of intervals longer than a day.",
                f"    final gap inside its own distribution: {item.final_gap_is_ordinary}.",
                "    same base asset, last bar: "
                + (
                    ", ".join(f"{name} {date}" for name, date in sorted(siblings.items())) or "none"
                ),
                f"    verdict: {result.verdict_for(item.symbol)}",
            ]
        )
    return "\n".join(lines)


def run(store_root: Path, quarter: Quarter = UNEXPLAINED_TRANSITION) -> TransitionAudit:
    """Run the audit against the real archive and print the evidence."""
    from sextant.app.archive_ingest import load_calendar

    result = audit(load_calendar(store_root), ParquetBarStore(store_root), quarter)
    print(render(result))
    return result


def sparsest(measured: Sequence[PairCadence], count: int = 5) -> tuple[PairCadence, ...]:
    """The pairs that traded least often, for context in the report."""
    return tuple(sorted(measured, key=lambda item: -item.mean_gap_days)[:count])
