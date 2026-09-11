"""The spread sample rule S1 called for: 6 perpetuals, 6 days, and the measurement.

Section 12 fixed what would be sampled before anything ran. Rule S1, as amendment 9
generalises it, decided whether it was worth acquiring: set the assumed cost to zero,
re-evaluate, and acquire when some variant would then clear criterion 1. Two did.

**What this measurement can and cannot do.** It cannot change F1's verdict and is not
allowed to: every variant stays costed at the registered assumption in every cell,
under invariant 12, and this figure is printed *beside* that assumption and never
substituted into it. What it does is replace a guess with a measurement for the
families that have not run yet, where the assumed spread is the largest single line in
the cost model.

The sampling rule, verbatim from section 12
-------------------------------------------

Six symbols, two per liquidity band, chosen by the same trailing 30-day median quote
turnover the cost model's bands are cut on, evaluated at **2023-05-15**: the two
highest-turnover members of the carry universe at that instant, the two nearest its
median, and the two nearest its 10th percentile. Ties broken by symbol ascending.

Six days: 2023-05-16, 2023-06-01, 2023-08-01, 2023-10-01, 2023-12-01, 2024-02-01.

That is 36 symbol-days at 50 to 90 MB each, and about 600 MB of text inside each one.
Nothing is kept in memory: each day is streamed through an accumulator and discarded.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from sextant.adapters.exchanges.binance.costs import (
    DEEP_BAND_FLOOR,
    MID_BAND_FLOOR,
    SLIPPAGE_ASSUMPTION,
    SPREAD_ASSUMPTION,
)
from sextant.adapters.exchanges.binance.futures_archive import (
    BinanceFuturesArchive,
    FuturesDailyObject,
    FuturesDailyTree,
    stream_book_ticker,
)
from sextant.app.futures_archive import PERP_VENUE, RAW_ROOT
from sextant.app.spike_006_f1 import FUNDING_TRAILING_DAYS
from sextant.app.spike_006_f1_world import World, carry_bases_at
from sextant.domain.money import Notional
from sextant.domain.time import Timestamp
from sextant.engine.execution.spread import (
    DAY_MILLIS,
    OPENING_MILLIS,
    Quote,
    SpreadAccumulator,
    SpreadSummary,
)

#: Where the sample lands, and where the measurement is written.
SPREAD_ROOT = RAW_ROOT / "bookTicker"
SPREAD_RESULTS = Path("research") / "spike-006-f1-spread.json"

#: Section 12's six days and the instant the six symbols are ranked at.
SPREAD_DAYS = (
    "2023-05-16",
    "2023-06-01",
    "2023-08-01",
    "2023-10-01",
    "2023-12-01",
    "2024-02-01",
)
SPREAD_SELECTED_AT = "2023-05-15"

#: Two per band, by the registered rule: the top two, the two nearest the median, and
#: the two nearest the 10th percentile of trailing turnover.
PER_BAND = 2
BAND_QUANTILES = ("top", "median", "tenth percentile")


class SpreadSampleIncomplete(RuntimeError):
    """The sample cannot be assembled as registered, and nothing is substituted."""


@dataclass(frozen=True, slots=True)
class Picked:
    """One selected symbol, the turnover that selected it, and the band it sits in."""

    symbol: str
    base: str
    band: str
    quantile: str
    median_quote_turnover: Notional

    def as_json(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "base": self.base,
            "liquidity_band": self.band,
            "chosen_as": self.quantile,
            "trailing_30d_median_quote_turnover": str(self.median_quote_turnover.amount),
        }


@dataclass(frozen=True, slots=True)
class MeasuredDay:
    """One symbol-day, in both registered windows, with the bytes it came from."""

    symbol: str
    day: str
    size_bytes: int
    digest: str
    publisher_digest: str | None
    whole_day: SpreadSummary
    opening: SpreadSummary

    @property
    def verified(self) -> bool:
        """Whether the publisher supplied a digest and it matched."""
        return self.publisher_digest is not None and self.publisher_digest == self.digest

    def as_json(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "day": self.day,
            "bytes": self.size_bytes,
            "sha256": self.digest,
            "publisher_sha256": self.publisher_digest,
            "verified_against_the_publisher": self.verified,
            "whole_day": self.whole_day.as_json(),
            "opening_window": self.opening.as_json(),
        }


def _band_of(turnover: Notional) -> str:
    """The cost model's own liquidity band, so the sample is cut where the costs are."""
    if turnover.amount >= DEEP_BAND_FLOOR.amount:
        return "deep"
    if turnover.amount >= MID_BAND_FLOOR.amount:
        return "mid"
    return "thin"


def _nearest(ranked: Sequence[tuple[str, str, Notional]], target: Decimal, count: int) -> list[int]:
    """Indices of the entries nearest a turnover level, ties broken by symbol ascending."""
    order = sorted(
        range(len(ranked)),
        key=lambda index: (abs(ranked[index][2].amount - target), ranked[index][0]),
    )
    return order[:count]


def spread_symbols(world: World) -> tuple[Picked, ...]:
    """Section 12's six, by the registered rule, at the registered instant."""
    at = Timestamp.parse(f"{SPREAD_SELECTED_AT}T00:00:00+00:00")
    bases = set(carry_bases_at(world, at))
    ranked: list[tuple[str, str, Notional]] = []
    for key, history in world.perpetual_histories.items():
        instrument = world.instruments.get(key)
        if instrument is None or instrument.base not in bases:
            continue
        median = history.median_quote_volume(at, FUNDING_TRAILING_DAYS)
        if median is not None:
            ranked.append((key.symbol, instrument.base, median))
    ranked.sort(key=lambda item: (-item[2].amount, item[0]))
    if len(ranked) < PER_BAND * len(BAND_QUANTILES):
        raise SpreadSampleIncomplete(
            f"the carry universe at {SPREAD_SELECTED_AT} offers {len(ranked)} rankable "
            f"perpetuals and the registered sample needs {PER_BAND * len(BAND_QUANTILES)}."
        )
    amounts = sorted(item[2].amount for item in ranked)
    median_level = amounts[len(amounts) // 2]
    tenth_level = amounts[max(0, (len(amounts) * 10) // 100 - 1)]

    chosen: list[Picked] = []
    taken: set[str] = set()
    for quantile, indices in (
        ("top", list(range(PER_BAND))),
        ("median", _nearest(ranked, median_level, PER_BAND * 3)),
        ("tenth percentile", _nearest(ranked, tenth_level, PER_BAND * 3)),
    ):
        added = 0
        for index in indices:
            symbol, base, turnover = ranked[index]
            if symbol in taken:
                continue
            chosen.append(
                Picked(
                    symbol=symbol,
                    base=base,
                    band=_band_of(turnover),
                    quantile=quantile,
                    median_quote_turnover=turnover,
                )
            )
            taken.add(symbol)
            added += 1
            if added == PER_BAND:
                break
    return tuple(chosen)


def _midnight_millis(day: str) -> int:
    """Epoch milliseconds at midnight UTC of a published day."""
    parsed = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1000)


def measure(path: Path, day: str) -> tuple[SpreadSummary, SpreadSummary]:
    """Stream one published day through both registered windows, once.

    One pass feeding two accumulators rather than two passes: the file is 600 MB
    decompressed and reading it twice would double the only expensive part.
    """
    whole = SpreadAccumulator(window="whole day", starts_millis=0, ends_millis=DAY_MILLIS)
    opening = SpreadAccumulator(
        window="00:00-00:05 UTC", starts_millis=0, ends_millis=OPENING_MILLIS
    )
    for at_millis, bid, ask in stream_book_ticker(path, day_starts_millis=_midnight_millis(day)):
        quote = Quote(at_millis=at_millis, bid=bid, ask=ask)
        whole.add(quote)
        opening.add(quote)
    return whole.summary(), opening.summary()


@dataclass(frozen=True, slots=True)
class SpreadSample:
    """Everything the acquisition produced, ready to be written and reported."""

    selected_at: str
    days_requested: tuple[str, ...]
    chosen: tuple[Picked, ...]
    measured: tuple[MeasuredDay, ...]
    missing: Mapping[str, tuple[str, ...]]

    @property
    def megabytes(self) -> Decimal:
        """What the acquisition actually weighed."""
        total = sum(item.size_bytes for item in self.measured)
        return (Decimal(total) / Decimal(1024 * 1024)).quantize(Decimal("0.1"))

    def by_band(self) -> Mapping[str, tuple[Decimal | None, Decimal | None]]:
        """Per liquidity band, the median of the daily medians in each window.

        The band is what the comparison needs: the cost model charges by band, so a
        measured figure is only comparable to the assumption band by band.
        """
        bands: dict[str, list[tuple[Decimal | None, Decimal | None]]] = {}
        placed = {item.symbol: item.band for item in self.chosen}
        for item in self.measured:
            band = placed.get(item.symbol, "unknown")
            bands.setdefault(band, []).append((item.whole_day.median_bps, item.opening.median_bps))
        return {
            band: (_median([w for w, _ in rows]), _median([o for _, o in rows]))
            for band, rows in bands.items()
        }

    def as_json(self) -> dict[str, object]:
        return {
            "rule": "S1 as amendment 9 states it, and section 12's sampling rules",
            "why_it_was_acquired": (
                "Rule S1 fired: with the assumed cost set to zero, two variants of the "
                "executed grid would clear criterion 1. The rule was honoured because it "
                "fired, not because acquiring was convenient."
            ),
            "what_it_cannot_do": (
                "It cannot change F1's verdict and is not permitted to. Every variant "
                "stays costed at the registered assumption in every cell under invariant "
                "12, and this measurement is printed beside that assumption rather than "
                "substituted into it."
            ),
            "tree": FuturesDailyTree.BOOK_TICKER.value,
            "venue_series": PERP_VENUE.name,
            "symbols_selected_at": self.selected_at,
            "symbols": [item.as_json() for item in self.chosen],
            "days_requested": list(self.days_requested),
            "symbol_days_requested": len(self.chosen) * len(self.days_requested),
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
            "measured_median_bps_by_band": {
                band: {
                    "whole_day": None if whole is None else str(whole),
                    "opening_window": None if opening is None else str(opening),
                }
                for band, (whole, opening) in sorted(self.by_band().items())
            },
            "per_symbol_day": [item.as_json() for item in self.measured],
            "what_it_measures": (
                "The QUOTED half-spread is the assumption's unit: the configured figure "
                "is charged per leg, so the comparable measured quantity is half the "
                "quoted spread. Both are reported. It says nothing about what a 150 EUR "
                "order would have filled at, which is slippage and is a separate "
                "assumption measured by nothing here."
            ),
        }


def _median(values: Sequence[Decimal | None]) -> Decimal | None:
    """The median of the figures that exist, or None if none do."""
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
) -> SpreadSample:
    """Download and reduce the registered sample. ``raw_root`` is never defaulted here."""
    wanted = tuple(days) if days is not None else SPREAD_DAYS
    chosen = spread_symbols(world)
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
                    f"[f1] spread {symbol} {day}: {whole.quotes:,} quotes, "
                    f"median {whole.median_bps} bps, opening {opening.median_bps} bps"
                )
        return symbol, rows, absent

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for symbol, rows, absent in pool.map(one, [item.symbol for item in chosen]):
            measured.extend(rows)
            missing[symbol] = absent

    return SpreadSample(
        selected_at=SPREAD_SELECTED_AT,
        days_requested=wanted,
        chosen=chosen,
        measured=tuple(sorted(measured, key=lambda item: (item.symbol, item.day))),
        missing=missing,
    )


def write(sample: SpreadSample, destination: Path) -> Path:
    """Write the measurement. The destination is a parameter, always."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(sample.as_json(), indent=2, sort_keys=True), encoding="utf-8")
    return destination


__all__ = [
    "SPREAD_DAYS",
    "SPREAD_RESULTS",
    "SPREAD_ROOT",
    "SPREAD_SELECTED_AT",
    "MeasuredDay",
    "Picked",
    "SpreadSample",
    "SpreadSampleIncomplete",
    "acquire",
    "measure",
    "spread_symbols",
    "write",
]
