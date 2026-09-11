"""The depth sample rule C3 called for: 20 perpetuals, 17 days, and nothing else.

Section 12 of the pre-registration fixed *what* would be sampled before anything was
measured; rule C3, on the executed result file, decided *whether* it was needed. It
was: three variants earn positively inside the depth window even though every one of
them loses over the full window, which is C3's fourth row and reports as measured.

What this acquires, and what it refuses to acquire
--------------------------------------------------

The twenty symbols are the perpetual legs of the twenty carry-universe members with
the largest trailing 30-day median quote turnover **at 2022-12-31**, ties broken by
symbol ascending. Membership is re-evaluated at that exact date through the same rule
cascade the grid ran on, not at the nearest rebalance: a sample selected at a
different instant is not the sample that was registered.

The seventeen days are the first day of each calendar month from 2023-01-01 to
2024-05-01. At about 0.47 MB a symbol-day that is roughly 160 MB, and it is the whole
acquisition. The `bookTicker` spread sample of the same section is **not** acquired:
rule S1 read the same result file and was false, because no variant earns at research
fees and a measured spread can only make a variant look worse.

What the numbers can and cannot say
-----------------------------------

The perpetual leg only. A cash-and-carry needs both legs to fill, and nothing here
measures the spot book, so every figure is an upper bound on the pair. Seventeen days
inside a seventeen-month stretch of a window spanning 2021 to 2026, which is rule
C2's boundary: a capacity figure outside the depth window is an extrapolation and is
never printed beside a measured one.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from sextant.adapters.exchanges.binance.futures_archive import (
    BinanceFuturesArchive,
    FuturesDailyObject,
    FuturesDailyTree,
    parse_book_depth,
)
from sextant.app.futures_archive import PERP_VENUE, RAW_ROOT
from sextant.app.spike_006_f1 import (
    DEPTH_WINDOW_ENDS,
    DEPTH_WINDOW_STARTS,
    FUNDING_TRAILING_DAYS,
)
from sextant.app.spike_006_f1_world import World, carry_bases_at
from sextant.domain.money import Notional
from sextant.domain.time import Timestamp
from sextant.engine.execution.depth import DepthBand, DepthSummary, across_days, summarise_day

#: Where the sample lands. Its own directory under the futures raw tree, because it is
#: a different grain from everything else there and a reader must be able to see at a
#: glance which bytes are the 160 MB this rule asked for.
DEPTH_ROOT = RAW_ROOT / "bookDepth"

#: Where the reduced sample is written. Beside the grid's result file, not inside it:
#: the grid's file is what the verdict was computed from and is not rewritten by a
#: later acquisition.
DEPTH_RESULTS = Path("research") / "spike-006-f1-depth.json"

#: Section 12. Twenty symbols, and the instant their turnover is ranked at.
DEPTH_SYMBOL_COUNT = 20
DEPTH_SELECTED_AT = "2022-12-31"


class DepthSampleIncomplete(RuntimeError):
    """The sample cannot be assembled as registered, and nothing is substituted."""


@dataclass(frozen=True, slots=True)
class Chosen:
    """One selected symbol and the turnover that selected it."""

    symbol: str
    base: str
    median_quote_turnover: Notional

    def as_json(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "base": self.base,
            "trailing_30d_median_quote_turnover": str(self.median_quote_turnover.amount),
        }


@dataclass(frozen=True, slots=True)
class Fetched:
    """One downloaded symbol-day, with both digests and the publisher's verdict."""

    symbol: str
    day: str
    size_bytes: int
    digest: str
    publisher_digest: str | None

    @property
    def verified(self) -> bool:
        """Whether the publisher supplied a digest and it matched the bytes."""
        return self.publisher_digest is not None and self.publisher_digest == self.digest

    def as_json(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "day": self.day,
            "bytes": self.size_bytes,
            "sha256": self.digest,
            "publisher_sha256": self.publisher_digest,
            "verified_against_the_publisher": self.verified,
        }


@dataclass(frozen=True, slots=True)
class SymbolDepth:
    """One symbol's seventeen days, reduced, plus the median across them."""

    symbol: str
    days: tuple[DepthSummary, ...]
    near: Notional | None
    far: Notional | None

    @property
    def days_measured(self) -> int:
        """How many of the requested days carried a snapshot at all."""
        return sum(1 for item in self.days if item.is_evaluable)

    @property
    def opening_days_measured(self) -> int:
        """How many carried a snapshot inside the five minutes after midnight."""
        return sum(1 for item in self.days if item.opening_is_evaluable)

    def as_json(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "days_requested": len(self.days),
            "days_measured": self.days_measured,
            "days_with_an_opening_window": self.opening_days_measured,
            "median_across_days_within_1pct": None if self.near is None else str(self.near.amount),
            "median_across_days_within_5pct": None if self.far is None else str(self.far.amount),
            "per_day": [item.as_json() for item in self.days],
        }


def depth_days() -> tuple[str, ...]:
    """The first day of each calendar month in the registered depth window.

    Seventeen days, from the window's own start to the last month it opens. Crypto
    trades every day, so "the first trading day of each calendar month" is the first
    calendar day, and no exchange holiday calendar is consulted or needed.
    """
    start = Timestamp.parse(f"{DEPTH_WINDOW_STARTS}T00:00:00+00:00").value
    end = Timestamp.parse(f"{DEPTH_WINDOW_ENDS}T00:00:00+00:00").value
    days: list[str] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        days.append(f"{year:04d}-{month:02d}-01")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return tuple(days)


def depth_symbols(world: World) -> tuple[Chosen, ...]:
    """The registered twenty, by the registered rule, at the registered instant."""
    at = Timestamp.parse(f"{DEPTH_SELECTED_AT}T00:00:00+00:00")
    bases = set(carry_bases_at(world, at))
    ranked: list[Chosen] = []
    for key, history in world.perpetual_histories.items():
        instrument = world.instruments.get(key)
        if instrument is None or instrument.base not in bases:
            continue
        median = history.median_quote_volume(at, FUNDING_TRAILING_DAYS)
        if median is None:
            continue
        ranked.append(Chosen(symbol=key.symbol, base=instrument.base, median_quote_turnover=median))
    ranked.sort(key=lambda item: (-item.median_quote_turnover.amount, item.symbol))
    if len(ranked) < DEPTH_SYMBOL_COUNT:
        raise DepthSampleIncomplete(
            f"the carry universe at {DEPTH_SELECTED_AT} offers {len(ranked)} rankable "
            f"perpetuals and the registered sample needs {DEPTH_SYMBOL_COUNT}."
        )
    return tuple(ranked[:DEPTH_SYMBOL_COUNT])


def _wanted(
    archive: BinanceFuturesArchive, symbol: str, days: Sequence[str]
) -> tuple[tuple[FuturesDailyObject, ...], tuple[str, ...]]:
    """The published objects for one symbol's requested days, and the days that are not.

    A day the publisher never published is returned as absent rather than raised on.
    Invariant 10: an empty answer about coverage is not a failure, and the report
    carries the count either way.
    """
    published = {
        item.day: item
        for item in archive.daily_objects(
            FuturesDailyTree.BOOK_DEPTH, symbol, first=min(days), last=max(days)
        )
    }
    found = tuple(published[day] for day in days if day in published)
    missing = tuple(day for day in days if day not in published)
    return found, missing


def _bands(payload: bytes, name: str) -> tuple[DepthBand, ...]:
    """The adapter's rows as the engine's bands, which is the whole translation."""
    return tuple(
        DepthBand(at=row.at, percentage=row.percentage, notional=Notional(Decimal(row.notional)))
        for row in parse_book_depth(payload, source=name)
    )


@dataclass(frozen=True, slots=True)
class DepthSample:
    """Everything the acquisition produced, ready to be written and reported."""

    selected_at: str
    window: str
    days_requested: tuple[str, ...]
    chosen: tuple[Chosen, ...]
    fetched: tuple[Fetched, ...]
    per_symbol: tuple[SymbolDepth, ...]
    missing: Mapping[str, tuple[str, ...]]

    @property
    def megabytes(self) -> Decimal:
        """What the acquisition actually weighed."""
        total = sum(item.size_bytes for item in self.fetched)
        return (Decimal(total) / Decimal(1024 * 1024)).quantize(Decimal("0.1"))

    def as_json(self) -> dict[str, object]:
        near, far = across_days([day for symbol in self.per_symbol for day in symbol.days])
        return {
            "rule": "C3, and section 12's sampling rules",
            "why_it_was_acquired": (
                "Rule C3 landed on a measured outcome for at least one variant of the "
                "executed grid. Had it not, this sample would not exist: acquiring depth "
                "in order to print the word UNESTABLISHED would be acquiring data the "
                "registered rule does not read."
            ),
            "tree": FuturesDailyTree.BOOK_DEPTH.value,
            "venue_series": PERP_VENUE.name,
            "depth_window": self.window,
            "symbols_selected_at": self.selected_at,
            "symbol_count": len(self.chosen),
            "symbols": [item.as_json() for item in self.chosen],
            "days_requested": list(self.days_requested),
            "symbol_days_requested": len(self.chosen) * len(self.days_requested),
            "symbol_days_fetched": len(self.fetched),
            "megabytes_fetched": str(self.megabytes),
            "days_not_published": {
                symbol: list(days) for symbol, days in sorted(self.missing.items()) if days
            },
            "verified_against_the_publisher": sum(1 for item in self.fetched if item.verified),
            "median_of_every_measured_day_within_1pct": None if near is None else str(near.amount),
            "median_of_every_measured_day_within_5pct": None if far is None else str(far.amount),
            "per_symbol": [item.as_json() for item in self.per_symbol],
            "objects": [item.as_json() for item in self.fetched],
            "what_it_cannot_say": (
                "The perpetual leg only, on seventeen days, inside a window shorter than "
                "the evaluation window. Nothing about the spot leg, so every figure is an "
                "upper bound on what the pair would have filled; nothing about the years "
                "outside the window, where rule C2 makes any figure an extrapolation."
            ),
        }


def acquire(
    world: World,
    *,
    raw_root: Path,
    days: Sequence[str] | None = None,
    workers: int = 8,
) -> DepthSample:
    """Download and reduce the registered sample. ``raw_root`` is never defaulted here.

    The path is a parameter with no default for the reason invariant 11 gives: a
    function that writes to a fixed path is a function a test cannot safely call.
    """
    wanted = tuple(days) if days is not None else depth_days()
    chosen = depth_symbols(world)
    fetched: list[Fetched] = []
    per_symbol: list[SymbolDepth] = []
    missing: dict[str, tuple[str, ...]] = {}

    def one(symbol: str) -> tuple[str, list[Fetched], list[DepthSummary], tuple[str, ...]]:
        with BinanceFuturesArchive() as archive:
            objects, absent = _wanted(archive, symbol, wanted)
            rows: list[Fetched] = []
            summaries: list[DepthSummary] = []
            for item in objects:
                destination = raw_root / symbol / item.name
                outcome = archive.download(item, destination)
                rows.append(
                    Fetched(
                        symbol=symbol,
                        day=item.day,
                        size_bytes=destination.stat().st_size,
                        digest=outcome.digest,
                        publisher_digest=outcome.publisher_digest,
                    )
                )
                summaries.append(
                    summarise_day(item.day, _bands(destination.read_bytes(), item.name))
                )
        return symbol, rows, summaries, absent

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for symbol, rows, summaries, absent in pool.map(one, [item.symbol for item in chosen]):
            fetched.extend(rows)
            near, far = across_days(summaries)
            per_symbol.append(SymbolDepth(symbol=symbol, days=tuple(summaries), near=near, far=far))
            missing[symbol] = absent
            print(
                f"[f1] depth {symbol}: {len(summaries)} of {len(wanted)} days, "
                f"{sum(1 for item in summaries if item.opening_is_evaluable)} with an opening"
            )

    return DepthSample(
        selected_at=DEPTH_SELECTED_AT,
        window=f"{DEPTH_WINDOW_STARTS}/{DEPTH_WINDOW_ENDS}",
        days_requested=wanted,
        chosen=chosen,
        fetched=tuple(sorted(fetched, key=lambda item: (item.symbol, item.day))),
        per_symbol=tuple(sorted(per_symbol, key=lambda item: item.symbol)),
        missing=missing,
    )


def write(sample: DepthSample, destination: Path) -> Path:
    """Write the reduced sample. The destination is a parameter, always."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(sample.as_json(), indent=2, sort_keys=True), encoding="utf-8")
    return destination


__all__ = [
    "DEPTH_RESULTS",
    "DEPTH_ROOT",
    "DEPTH_SELECTED_AT",
    "DEPTH_SYMBOL_COUNT",
    "Chosen",
    "DepthSample",
    "DepthSampleIncomplete",
    "Fetched",
    "SymbolDepth",
    "acquire",
    "depth_days",
    "depth_symbols",
    "write",
]
