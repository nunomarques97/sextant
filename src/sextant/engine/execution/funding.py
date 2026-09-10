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
from itertools import pairwise
from typing import Protocol, runtime_checkable

from sextant.domain.instrument import InstrumentKey
from sextant.domain.time import Timestamp

#: The cadence a settlement is assumed to have run at when the source did not
#: state one. Every symbol in this archive started on an eight-hour cycle, and a
#: settlement whose interval is unknown is better treated as the widest ordinary
#: one than as a gap.
DEFAULT_INTERVAL_HOURS = 8


@dataclass(frozen=True, slots=True)
class Settlement:
    """One funding payment: when it was made, at what rate, over what interval.

    ``interval_hours`` is the venue's own column and is carried rather than
    inferred. It is not used to scale the rate - the rate published against a
    settlement is the rate applied at it, whatever it covered - but it is what
    says when the *next* payment fell due, which is the only way to tell a
    complete history from one with a hole in it. Inferring the cadence from the
    gaps between published settlements cannot do that job: a hole redefines the
    gap it sits in, so a series with a missing payment infers a wider cadence and
    declares itself complete.
    """

    at: Timestamp
    rate: Decimal
    interval_hours: int = DEFAULT_INTERVAL_HOURS

    @property
    def interval_millis(self) -> int:
        """The cadence in milliseconds, as the venue stated it."""
        hours = self.interval_hours if self.interval_hours > 0 else DEFAULT_INTERVAL_HOURS
        return hours * 60 * 60 * 1000


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

    def total_rate(self, key: InstrumentKey, after: Timestamp, until: Timestamp) -> Decimal:
        """The sum of every rate settled in ``(after, until]``.

        On the protocol rather than only on the implementation because it is what
        a variant ranks on, and a strategy must be able to ask for it without
        knowing where the settlements came from.
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

        Three conditions, all read off the venue's own stated cadence and none
        of them a count:

        the **first** payment in the span falls no more than one interval after
        ``after``, so nothing is missing at the front; each **consecutive** pair
        is no more than one interval apart, so nothing is missing in the middle;
        and ``until`` is strictly less than one interval past the **last**
        payment, so the next one has not yet fallen due. An instrument with no
        settlement at all in the span is not covered, which is the answer that
        keeps a symbol with no published funding out of a carry universe.
        """
        found = self.settlements(key, after, until)
        if not found:
            return False
        if found[0].at.epoch_millis - after.epoch_millis > found[0].interval_millis:
            return False
        if until.epoch_millis - found[-1].at.epoch_millis >= found[-1].interval_millis:
            return False
        for earlier, later in pairwise(found):
            if later.at.epoch_millis - earlier.at.epoch_millis > later.interval_millis:
                return False
        return True

    def total_rate(self, key: InstrumentKey, after: Timestamp, until: Timestamp) -> Decimal:
        """The sum of every rate settled in ``(after, until]``.

        The trailing-funding signal the carry variants rank on. A sum rather than
        a mean because it is what the position actually received over the span,
        and because a mean needs a divisor that a varying settlement interval
        makes ambiguous.
        """
        return sum((item.rate for item in self.settlements(key, after, until)), Decimal(0))
