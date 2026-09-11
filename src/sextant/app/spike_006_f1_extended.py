"""Rule M1: the mid and thin bands, which rule S1's sample never reached.

Amendment 12, section 34.4. All six symbols in rule S1's sample fall in the **deep** band,
because at 2023-05-15 every member of the carry universe cleared the deep band's floor. So
the mid and thin defaults are extrapolation from a band they are not in, and rule M1
measures them.

**Occupancy decided that this had to run.** Rule M1 registers an empty band as an answer
rather than a gap, and the occupancy count in
``research/spike-006-f1-bands.json`` says neither band is empty: the mid band is occupied
at forty of sixty-nine rebalances and the thin band at nine. Their defaults are charged, so
they are worth measuring.

The selection rule, registered before anything was selected
-----------------------------------------------------------

A12.4 fixed the counts and not the instant, and rule S1's instant cannot be reused because
both bands were empty at it. So, per band independently: the **earliest** rebalance instant
at or after 2023-05-15 at which the band holds at least four members; the four members
nearest that band's median trailing 30-day quote turnover at that instant; ties broken by
symbol ascending. Rule S1's own six days, unchanged. A symbol-day the venue never published
is reported as unpublished and never replaced.

Everything else is rule S1's machinery, unchanged and reused: the same archive tree, the
same two windows, the same streaming reduction, the same publisher checksum on every object.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from sextant.adapters.exchanges.binance.costs import SLIPPAGE_ASSUMPTION, SPREAD_ASSUMPTION
from sextant.adapters.exchanges.binance.futures_archive import (
    BinanceFuturesArchive,
    FuturesDailyObject,
    FuturesDailyTree,
)
from sextant.app.futures_archive import PERP_VENUE, RAW_ROOT
from sextant.app.spike_006_f1 import (
    EXTENDED_SAMPLE_DAYS,
    EXTENDED_SAMPLE_PER_BAND,
    EXTENDED_SAMPLE_RULE,
    FUNDING_TRAILING_DAYS,
    RESEARCH_ROOT,
)
from sextant.app.spike_006_f1_bands import band_of, rebalance_instants
from sextant.app.spike_006_f1_spread import (
    SPREAD_DAYS,
    SPREAD_SELECTED_AT,
    MeasuredDay,
    Picked,
    measure,
)
from sextant.app.spike_006_f1_world import World, carry_bases_at
from sextant.domain.money import Notional
from sextant.domain.time import Timestamp

#: Where the bytes land and where the measurement is written.
EXTENDED_ROOT = RAW_ROOT / "bookTicker"
EXTENDED_RESULTS = RESEARCH_ROOT / "spike-006-f1-extended.json"

#: The bands rule S1 never reached, in the order the cost model lists them.
EXTENDED_BANDS = ("mid", "thin")

NEWLINE = chr(10)


class ExtendedSampleIncomplete(RuntimeError):
    """The sample cannot be assembled as registered, and nothing is substituted."""


@dataclass(frozen=True, slots=True)
class BandSelection:
    """One band's four symbols, and the instant the rule picked them at."""

    band: str
    instant: str
    members_at_that_instant: int
    chosen: tuple[Picked, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "band": self.band,
            "selected_at": self.instant,
            "members_in_the_band_at_that_instant": self.members_at_that_instant,
            "symbols": [item.as_json() for item in self.chosen],
        }


def _ranked(world: World, at: Timestamp, band: str) -> list[tuple[str, str, Notional]]:
    """Every carry-universe perpetual in one band at one instant, by turnover descending."""
    bases = set(carry_bases_at(world, at))
    rows: list[tuple[str, str, Notional]] = []
    for key, history in world.perpetual_histories.items():
        instrument = world.instruments.get(key)
        if instrument is None or instrument.base not in bases:
            continue
        median = history.median_quote_volume(at, FUNDING_TRAILING_DAYS)
        if median is None or band_of(median) != band:
            continue
        rows.append((key.symbol, instrument.base, median))
    rows.sort(key=lambda item: (-item[2].amount, item[0]))
    return rows


def select(world: World, band: str) -> BandSelection:
    """Rule M1's selection for one band: earliest qualifying instant, nearest the median."""
    start = Timestamp.parse(f"{SPREAD_SELECTED_AT}T00:00:00+00:00")
    for at in rebalance_instants():
        if at < start:
            continue
        ranked = _ranked(world, at, band)
        if len(ranked) < EXTENDED_SAMPLE_PER_BAND:
            continue
        amounts = sorted(item[2].amount for item in ranked)
        target = amounts[len(amounts) // 2]
        order = sorted(
            range(len(ranked)),
            key=lambda index: (abs(ranked[index][2].amount - target), ranked[index][0]),
        )
        chosen = tuple(
            Picked(
                symbol=ranked[index][0],
                base=ranked[index][1],
                band=band,
                quantile="nearest the band median",
                median_quote_turnover=ranked[index][2],
            )
            for index in sorted(order[:EXTENDED_SAMPLE_PER_BAND], key=lambda i: ranked[i][0])
        )
        return BandSelection(
            band=band,
            instant=at.isoformat()[:10],
            members_at_that_instant=len(ranked),
            chosen=chosen,
        )
    raise ExtendedSampleIncomplete(
        f"no rebalance instant at or after {SPREAD_SELECTED_AT} holds "
        f"{EXTENDED_SAMPLE_PER_BAND} members of the {band} band, so rule M1's sample "
        "cannot be selected as registered and nothing is substituted for it."
    )


@dataclass(frozen=True, slots=True)
class ExtendedSample:
    """What rule M1 measured, per band and per symbol."""

    selections: tuple[BandSelection, ...]
    days_requested: tuple[str, ...]
    measured: tuple[MeasuredDay, ...]
    missing: Mapping[str, tuple[str, ...]]

    @property
    def megabytes(self) -> Decimal:
        total = sum(item.size_bytes for item in self.measured)
        return (Decimal(total) / Decimal(1024 * 1024)).quantize(Decimal("0.1"))

    def symbol_days_measured(self) -> int:
        """How many symbol-days the acquisition actually reduced."""
        return len(self.measured)

    def verified(self) -> int:
        """How many of them matched the publisher's own digest."""
        return sum(1 for item in self.measured if item.verified)

    @property
    def bands_of_symbol(self) -> Mapping[str, tuple[str, ...]]:
        """Which bands each symbol was selected into. Usually one, sometimes two.

        A band is a property of an instrument at an instant, and rule M1 picks each band at
        its own earliest qualifying instant, so a symbol that was mid in 2023 and thin in
        2025 is legitimately in both selections. It contributes to both bands' figures
        rather than to whichever was written last.
        """
        out: dict[str, list[str]] = {}
        for selection in self.selections:
            for item in selection.chosen:
                out.setdefault(item.symbol, []).append(selection.band)
        return {symbol: tuple(bands) for symbol, bands in out.items()}

    @property
    def in_more_than_one_band(self) -> tuple[str, ...]:
        """Symbols selected into two bands, named rather than silently collapsed."""
        return tuple(
            sorted(symbol for symbol, bands in self.bands_of_symbol.items() if len(bands) > 1)
        )

    def days_measured(self, symbol: str) -> int:
        """How many of the registered days this symbol actually published."""
        return len({item.day for item in self.measured if item.symbol == symbol})

    def coverage(self, band: str) -> tuple[int, int, int]:
        """For one band: symbols selected, symbols with every day, symbol-days measured."""
        chosen = [
            item.symbol
            for selection in self.selections
            if selection.band == band
            for item in selection.chosen
        ]
        complete = sum(
            1 for symbol in chosen if self.days_measured(symbol) >= len(self.days_requested)
        )
        days = sum(self.days_measured(symbol) for symbol in chosen)
        return len(chosen), complete, days

    def meets_the_requirement(self, band: str) -> bool:
        """Whether this band was measured as rule M1 registered it.

        Four symbols, each over the registered days. Applied rather than invented: a band
        that falls short is reported as not measured, with what is missing named, and no
        substitution is made for it.
        """
        _, complete, _ = self.coverage(band)
        return complete >= EXTENDED_SAMPLE_PER_BAND

    def per_symbol_median(self) -> Mapping[str, Decimal | None]:
        """Each symbol's median daily median quoted spread, across the days it published."""
        rows: dict[str, list[Decimal | None]] = {}
        for item in self.measured:
            rows.setdefault(item.symbol, []).append(item.whole_day.median_bps)
        return {symbol: _median(values) for symbol, values in sorted(rows.items())}

    def by_band(self) -> Mapping[str, Decimal | None]:
        """Each band's median across its symbols' medians, where the band was measured.

        ``None`` for a band that did not meet rule M1's registered requirement. A median of
        whatever happened to publish is not the band's spread, and printing one would be
        exactly the substitution the rule forbids.
        """
        medians = self.per_symbol_median()
        rows: dict[str, list[Decimal | None]] = {}
        for selection in self.selections:
            for item in selection.chosen:
                rows.setdefault(selection.band, []).append(medians.get(item.symbol))
        return {
            band: _median(values) if self.meets_the_requirement(band) else None
            for band, values in sorted(rows.items())
        }

    def as_json(self) -> dict[str, object]:
        return {
            "rule": EXTENDED_SAMPLE_RULE,
            "why_it_was_acquired": (
                "All six symbols in rule S1's sample fall in the DEEP band, so the mid and "
                "thin defaults were extrapolation from a band they are not in. Rule M1's "
                "occupancy precondition says neither band is empty in practice, so both "
                "defaults are charged and both are worth measuring."
            ),
            "tree": FuturesDailyTree.BOOK_TICKER.value,
            "venue_series": PERP_VENUE.name,
            "selections": [item.as_json() for item in self.selections],
            "days_requested": list(self.days_requested),
            "symbol_days_requested": sum(len(item.chosen) for item in self.selections)
            * len(self.days_requested),
            "symbol_days_measured": len(self.measured),
            "megabytes_fetched": str(self.megabytes),
            "days_not_published": {
                symbol: list(days) for symbol, days in sorted(self.missing.items()) if days
            },
            "verified_against_the_publisher": sum(1 for item in self.measured if item.verified),
            "assumed_spread_bps_by_band": {
                band.value: str(value) for band, value in SPREAD_ASSUMPTION.by_band.items()
            },
            "assumed_slippage_bps_by_band": {
                band.value: str(value) for band, value in SLIPPAGE_ASSUMPTION.by_band.items()
            },
            "measured_median_bps_by_symbol": {
                symbol: None if value is None else str(value)
                for symbol, value in self.per_symbol_median().items()
            },
            "measured_median_bps_by_band": {
                band: None if value is None else str(value)
                for band, value in self.by_band().items()
            },
            "coverage_by_band": {
                selection.band: {
                    "symbols_selected": self.coverage(selection.band)[0],
                    "symbols_that_published_every_registered_day": self.coverage(selection.band)[1],
                    "symbol_days_measured": self.coverage(selection.band)[2],
                    "symbol_days_requested": len(selection.chosen) * len(self.days_requested),
                    "meets_the_registered_requirement": self.meets_the_requirement(selection.band),
                }
                for selection in self.selections
            },
            "bands_measured_as_registered": [
                selection.band
                for selection in self.selections
                if self.meets_the_requirement(selection.band)
            ],
            "bands_not_measured_as_registered": [
                selection.band
                for selection in self.selections
                if not self.meets_the_requirement(selection.band)
            ],
            "symbols_selected_into_two_bands": list(self.in_more_than_one_band),
            "a_band_that_falls_short_is_not_averaged": (
                "A band whose selected symbols did not publish the registered days is "
                "reported as NOT MEASURED, with the missing symbol-days named. A median of "
                "whatever happened to publish is not that band's spread, and substituting "
                "one would be the move rule M1 forbids in the clause about unpublished "
                "days."
            ),
            "unverified_because_the_publisher_supplied_no_digest": sum(
                1 for item in self.measured if item.publisher_digest is None
            ),
            "unverified_because_the_digest_did_not_match": sum(
                1
                for item in self.measured
                if item.publisher_digest is not None and not item.verified
            ),
            "per_symbol_day": [item.as_json() for item in self.measured],
            "dispersion_is_the_finding": (
                "The per-symbol table is the result. A band median is printed beside it "
                "because the cost model charges by band, not because the dispersion inside "
                "a band is noise to be averaged away: rule S1 found two orders of magnitude "
                "of it inside the deep band and that is what rule B1 exists to repair."
            ),
            "what_it_cannot_do": (
                "It changes no F1 figure. F1 is costed at the registered assumption in every "
                "cell and its verdict was settled before any of this was measured."
            ),
        }


def _median(values: Sequence[Decimal | None]) -> Decimal | None:
    present = sorted(value for value in values if value is not None)
    if not present:
        return None
    middle = len(present) // 2
    if len(present) % 2 == 1:
        return present[middle]
    return (present[middle - 1] + present[middle]) / Decimal(2)


def acquire(
    world: World,
    *,
    raw_root: Path,
    days: Sequence[str] | None = None,
    workers: int = 4,
) -> ExtendedSample:
    """Download and reduce rule M1's sample. ``raw_root`` is never defaulted here."""
    wanted = tuple(days) if days is not None else SPREAD_DAYS
    if len(wanted) < EXTENDED_SAMPLE_DAYS:
        raise ExtendedSampleIncomplete(
            f"rule M1 requires at least {EXTENDED_SAMPLE_DAYS} days and {len(wanted)} were "
            "asked for. A shorter sample is a different sample."
        )
    selections = tuple(select(world, band) for band in EXTENDED_BANDS)
    for selection in selections:
        print(
            f"[f1] rule {EXTENDED_SAMPLE_RULE}: {selection.band} band at "
            f"{selection.instant}, {selection.members_at_that_instant} members, chose "
            + ", ".join(item.symbol for item in selection.chosen)
        )
    measured: list[MeasuredDay] = []
    missing: dict[str, tuple[str, ...]] = {}

    def one(symbol: str) -> tuple[str, list[MeasuredDay], tuple[str, ...]]:
        rows: list[MeasuredDay] = []
        with BinanceFuturesArchive() as archive:
            published = {
                item.day: item
                for item in archive.daily_objects(
                    FuturesDailyTree.BOOK_TICKER, symbol, first=min(wanted), last=max(wanted)
                )
            }
            absent = tuple(day for day in wanted if day not in published)
            for day in wanted:
                item: FuturesDailyObject | None = published.get(day)
                if item is None:
                    continue
                destination = raw_root / symbol / item.name
                outcome = archive.download(item, destination)
                whole, opening = measure(destination, day)
                rows.append(
                    MeasuredDay(
                        symbol=symbol,
                        day=day,
                        size_bytes=destination.stat().st_size,
                        digest=outcome.digest,
                        publisher_digest=outcome.publisher_digest,
                        whole_day=whole,
                        opening=opening,
                    )
                )
                print(
                    f"[f1] extended {symbol} {day}: {whole.quotes:,} quotes, "
                    f"median {whole.median_bps} bps"
                )
        return symbol, rows, absent

    # De-duplicated, and not only to save bytes. A symbol can qualify in two bands at two
    # instants - a band is a property of an instrument at an instant - and two workers
    # fetching the same object to the same path race each other for the file and for its
    # checksum. The selections keep both memberships; the download happens once.
    symbols = list(
        dict.fromkeys(item.symbol for selection in selections for item in selection.chosen)
    )
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for symbol, rows, absent in pool.map(one, symbols):
            measured.extend(rows)
            missing[symbol] = absent

    return ExtendedSample(
        selections=selections,
        days_requested=wanted,
        measured=tuple(sorted(measured, key=lambda item: (item.symbol, item.day))),
        missing=missing,
    )


def write(sample: ExtendedSample, destination: Path) -> Path:
    """Write the measurement. The destination is a parameter, always."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline=NEWLINE) as handle:
        json.dump(sample.as_json(), handle, indent=2, sort_keys=True)
        handle.write(NEWLINE)
    return destination


__all__ = [
    "EXTENDED_BANDS",
    "EXTENDED_RESULTS",
    "EXTENDED_ROOT",
    "BandSelection",
    "ExtendedSample",
    "ExtendedSampleIncomplete",
    "acquire",
    "select",
    "write",
]
