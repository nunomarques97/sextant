"""Realised funding on a perpetual position, per settlement, from published rates.

Spot has no financing leg, so SEXTANT-004 and SEXTANT-005 carried funding as a
single ``funding_bps_per_day`` that was zero and reported anyway. A perpetual
does have one, it is not a constant, it is not an assumption, and for the carry
family it is not a cost at all: **it is the return**. This module is where a
published settlement becomes an amount in the ledger.

The sign, which is the thing to get right
------------------------------------------

The venue publishes one rate per settlement. A **positive** rate means the longs
pay the shorts. A position's value in this engine is signed - positive long,
negative short - so the amount a position pays at one settlement is

    value * rate

and that expression already has the right sign for both sides. A long with a
positive rate pays; a short with a positive rate has a negative payment, which is
a receipt. The ledger's ``funding`` line is a cost, subtracted from equity, so a
negative funding line is money coming in and reads correctly without a special
case anywhere.

There is no scaling by the interval. The rate published against a settlement is
the rate applied at that settlement, whatever number of hours it covered. The
venue moved some symbols from an eight-hour cycle to a four-hour one, which
changes how *many* settlements fall inside a month and not what each one is
worth, and counting settlements is exactly what this module does.

A missing settlement is not a zero
-----------------------------------

:class:`RealisedFunding` answers with what it holds and says nothing about what
it does not. Deciding that a position may not be held across a period with no
published rate is a universe rule, not an accounting one, and it lives in the
family's admission rules where it can be counted and reported. Silently
returning zero here would put a fabricated cash flow into the one line this
family's whole result is made of, so this module never invents one:
:meth:`RealisedFunding.covers` is how a caller asks whether a period is
evaluable, and it is a different question from what the accrual comes to.

Pure computation. No I/O, no venue, no floats.
"""

from __future__ import annotations

import bisect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol, runtime_checkable

from sextant.domain.instrument import InstrumentKey
from sextant.domain.time import Timestamp


@dataclass(frozen=True, slots=True)
class Settlement:
    """One funding payment: when it was made and at what rate."""

    at: Timestamp
    rate: Decimal


@runtime_checkable
class FundingSchedule(Protocol):
    """What a perpetual paid, and when, as published."""

    def settlements(
        self, key: InstrumentKey, after: Timestamp, until: Timestamp
    ) -> tuple[Settlement, ...]:
        """Every settlement strictly after ``after`` and at or before ``until``.

        Half-open at the start and closed at the end, so that consecutive holding
        periods partition the settlements between them exactly: a payment at a
        rebalance instant belongs to the period that ends there and is charged to
        the book that was held into it, never to the book that replaces it.
        """
        ...

    def covers(self, key: InstrumentKey, after: Timestamp, until: Timestamp) -> bool:
        """Whether every settlement this instrument should have in the span is held.

        Distinct from :meth:`settlements` returning something, because an
        instrument with three of a month's ninety payments would answer that
        question with a non-empty tuple and still be unevaluable.
        """
        ...


@dataclass(frozen=True, slots=True)
class RealisedFunding:
    """Published settlements, indexed for lookup by span.

    Built once per run from the store and shared across every variant and every
    null, because it is a read-through view of published data and cannot differ
    between them.
    """

    by_instrument: Mapping[InstrumentKey, tuple[Settlement, ...]]
    """Ascending by instant. Construction sorts; nothing downstream re-sorts."""

    _index: dict[InstrumentKey, tuple[int, ...]] = field(default_factory=dict, repr=False)
    """Settlement instants as epoch milliseconds, for a bisect over a span.

    Mutable on a frozen dataclass, which is legitimate: the object is frozen
    against rebinding its fields, and this one is filled once during
    construction and never again."""

    def __post_init__(self) -> None:
        ordered = {
            key: tuple(sorted(value, key=lambda item: item.at))
            for key, value in self.by_instrument.items()
        }
        object.__setattr__(self, "by_instrument", ordered)
        self._index.clear()
        for key, value in ordered.items():
            self._index[key] = tuple(item.at.epoch_millis for item in value)

    @classmethod
    def of(cls, rows: Mapping[InstrumentKey, Sequence[Settlement]]) -> RealisedFunding:
        """Build from any mapping of instrument to settlements."""
        return cls(by_instrument={key: tuple(value) for key, value in rows.items()})

    def settlements(
        self, key: InstrumentKey, after: Timestamp, until: Timestamp
    ) -> tuple[Settlement, ...]:
        """Every settlement in ``(after, until]`` for one instrument."""
        held = self.by_instrument.get(key)
        if not held:
            return ()
        stamps = self._index[key]
        start = bisect.bisect_right(stamps, after.epoch_millis)
        stop = bisect.bisect_right(stamps, until.epoch_millis)
        return held[start:stop]

    def covers(self, key: InstrumentKey, after: Timestamp, until: Timestamp) -> bool:
        """Whether the published settlements span ``(after, until]`` without a hole.

        The test is a cadence test rather than a count, because the cadence is
        the only thing the data itself states. Every settlement in the span
        carries the interval it covered, so the span is covered when the first
        settlement is no further past ``after`` than one interval, the last is no
        further before ``until`` than one interval, and no two consecutive
        settlements are more than one interval apart. An instrument with no
        settlement at all in the span is not covered, which is the answer that
        keeps a symbol with no published funding out of a carry universe.
        """
        found = self.settlements(key, after, until)
        if not found:
            return False
        held = self.by_instrument[key]
        stamps = self._index[key]
        first = bisect.bisect_right(stamps, after.epoch_millis)
        interval_ms = _interval_millis(held, first)
        if found[0].at.epoch_millis - after.epoch_millis > interval_ms:
            return False
        if until.epoch_millis - found[-1].at.epoch_millis >= interval_ms:
            return False
        previous = found[0].at.epoch_millis
        for index, item in enumerate(found[1:], start=first + 1):
            step = _interval_millis(held, index)
            if item.at.epoch_millis - previous > step:
                return False
            previous = item.at.epoch_millis
        return True

    def total_rate(self, key: InstrumentKey, after: Timestamp, until: Timestamp) -> Decimal:
        """The sum of every rate settled in ``(after, until]``.

        The trailing-funding signal the carry variants rank on. A sum rather than
        a mean because it is what the position actually received over the span,
        and because a mean needs a divisor that a varying settlement interval
        makes ambiguous.
        """
        return sum((item.rate for item in self.settlements(key, after, until)), Decimal(0))


def _interval_millis(held: Sequence[Settlement], index: int) -> int:
    """The settlement cadence in force around position ``index``.

    Read from the gap between neighbouring published settlements rather than from
    a constant, because the venue changed the cadence on some symbols partway
    through their history. Falls back to eight hours when there is no neighbour
    to measure against, which is the cadence every symbol in this archive
    started on.
    """
    eight_hours = 8 * 60 * 60 * 1000
    if len(held) < 2:
        return eight_hours
    position = min(max(index, 1), len(held) - 1)
    gap = held[position].at.epoch_millis - held[position - 1].at.epoch_millis
    return gap if gap > 0 else eight_hours
