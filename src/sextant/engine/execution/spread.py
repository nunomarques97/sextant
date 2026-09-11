"""Reducing a day of top-of-book quotes to the spread statistics section 12 registers.

One published symbol-day is roughly seven million quotes and 600 MB of text, so this
is the one reducer in the system that is **fed** rather than handed a sequence. It
accumulates and never materialises: a list of seven million anything would be the
wrong shape on a laptop, and a median over a counter is exact anyway.

Why the arithmetic is integer
-----------------------------

The venue publishes prices as fixed-point text with a constant number of decimals, so
a quote arrives as two exact integers. The spread in basis points is then

    10_000 * 2 * (ask - bid) / (ask + bid)

which is a ratio of integers. Scaling by :data:`MICRO` and taking floor division keeps
the whole reduction in exact integers, truncating at one millionth of a basis point,
which is four orders below anything reported. No float appears here, and no Decimal
is constructed per quote: seven million Decimals a day would make the measurement cost
more than the download.

Two windows, and why the second one exists
------------------------------------------

The whole day, and the five minutes after midnight UTC. A monthly rebalance decides at
00:00 and would execute inside that window, so the spread it would actually have paid
is the second figure rather than the first. They differ: the book is thinner at the
turn of the day than it is across it.

Time weighting
--------------

A quote's weight is the time until the next quote, in milliseconds. That makes the
mean a statement about *what rested on the book* rather than about how often the venue
happened to publish, and it is what "time-weighted" in the registered statistic means.
Quotes sharing an instant are superseded and weigh nothing. The last quote of a window
is weighted to the window's end.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal

from sextant.domain.errors import DomainError

#: The scale the reduction carries basis points at: one millionth of a basis point.
MICRO = 1_000_000

#: Basis points in one.
BASIS_POINTS = 10_000

#: Milliseconds in the day, and in the opening window the second statistic covers.
DAY_MILLIS = 24 * 60 * 60 * 1000
OPENING_MILLIS = 5 * 60 * 1000


class QuoteUnreadable(DomainError):
    """A quote that no spread can be defined over, and nothing is guessed from it."""


@dataclass(frozen=True, slots=True)
class Quote:
    """One top-of-book observation, in the fixed-point form the venue publishes.

    ``bid`` and ``ask`` are integers at a common scale, so the reduction never has to
    parse a decimal. ``at_millis`` is milliseconds since midnight of the day being
    reduced, which is what makes the opening-window test a comparison on an integer.
    """

    at_millis: int
    bid: int
    ask: int


@dataclass(frozen=True, slots=True)
class SpreadSummary:
    """One symbol-day in one window, reduced.

    Every figure is in basis points of the midpoint. ``quotes`` and ``covered_millis``
    travel with them because a mean over four minutes of a five-minute window and a
    mean over the whole of it are not the same measurement.
    """

    window: str
    quotes: int
    covered_millis: int
    time_weighted_mean_bps: Decimal | None
    median_bps: Decimal | None
    crossed_quotes: int
    """Quotes where the ask was at or below the bid. Counted, never silently dropped."""

    @property
    def is_evaluable(self) -> bool:
        """Whether any quote was seen at all in this window."""
        return self.quotes > 0

    def as_json(self) -> dict[str, object]:
        return {
            "window": self.window,
            "quotes": self.quotes,
            "milliseconds_covered": self.covered_millis,
            "time_weighted_mean_bps": _text(self.time_weighted_mean_bps),
            "median_bps": _text(self.median_bps),
            "crossed_or_locked_quotes": self.crossed_quotes,
        }


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


@dataclass(slots=True)
class SpreadAccumulator:
    """Fed one quote at a time, in publication order, and asked for a summary at the end.

    Stateful on purpose, and it is the only stateful reducer in the engine. The input
    does not fit in memory and the alternative shapes all do worse: a generator
    pipeline would still need the counter, and a two-pass approach would double a
    600 MB decompression.
    """

    window: str
    starts_millis: int = 0
    ends_millis: int = DAY_MILLIS
    _counts: Counter[int] = field(default_factory=Counter)
    _weighted: int = 0
    _weight: int = 0
    _pending: tuple[int, int] | None = None
    _crossed: int = 0

    def add(self, quote: Quote) -> None:
        """Take one quote, closing the previous one's weight at this instant."""
        if quote.at_millis < self.starts_millis or quote.at_millis >= self.ends_millis:
            return
        if quote.bid <= 0 or quote.ask <= 0:
            raise QuoteUnreadable(
                f"a quote at {quote.at_millis} ms carries a non-positive price; "
                "a spread over it would be a number with no meaning"
            )
        if quote.ask <= quote.bid:
            self._crossed += 1
            return
        scaled = 2 * (quote.ask - quote.bid) * BASIS_POINTS * MICRO // (quote.ask + quote.bid)
        self._close(quote.at_millis)
        self._counts[scaled] += 1
        self._pending = (quote.at_millis, scaled)

    def _close(self, at_millis: int) -> None:
        """Give the quote we were holding its weight, which ends now."""
        if self._pending is None:
            return
        began, scaled = self._pending
        held = at_millis - began
        if held <= 0:
            return
        self._weighted += scaled * held
        self._weight += held

    def summary(self) -> SpreadSummary:
        """Close the last quote at the window's end and reduce."""
        self._close(self.ends_millis)
        self._pending = None
        return SpreadSummary(
            window=self.window,
            quotes=sum(self._counts.values()),
            covered_millis=self._weight,
            time_weighted_mean_bps=(
                _scaled(self._weighted // self._weight) if self._weight > 0 else None
            ),
            median_bps=_scaled(_median_of(self._counts)),
            crossed_quotes=self._crossed,
        )


def _scaled(value: int | None) -> Decimal | None:
    """A micro-basis-point integer as a decimal number of basis points."""
    return None if value is None else Decimal(value) / Decimal(MICRO)


def _median_of(counts: Counter[int]) -> int | None:
    """The median of a counted distribution, exact and without materialising it.

    Even counts average the two middle values, which is the same convention the depth
    reduction uses. Nothing is interpolated between them.
    """
    total = sum(counts.values())
    if total == 0:
        return None
    middle = total // 2
    seen = 0
    lower: int | None = None
    for value in sorted(counts):
        seen += counts[value]
        if lower is None and seen > middle - (1 if total % 2 == 0 else 0):
            lower = value
        if seen > middle:
            return value if total % 2 == 1 or lower is None else (lower + value) // 2
    return lower


__all__ = [
    "BASIS_POINTS",
    "DAY_MILLIS",
    "MICRO",
    "OPENING_MILLIS",
    "Quote",
    "QuoteUnreadable",
    "SpreadAccumulator",
    "SpreadSummary",
]
