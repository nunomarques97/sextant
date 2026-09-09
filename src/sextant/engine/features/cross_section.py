"""Ranking across a universe slice.

This is a primitive, not a strategy. It computes no signal, holds no view and
decides nothing: it turns a set of per-instrument numbers into their relative
positions within the set that was passed to it. Every cross-sectional allocator
this project ever writes will need it, and the correctness property worth
testing belongs to it rather than to any of them.

**The property.** A rank is a function of the whole slice. Add an instrument or
remove one and every other instrument's rank must move. A "cross-sectional"
feature that is stable under a change to the universe is a per-symbol feature
wearing a different name, and
``tests/unit/test_backtest_correctness.py::test_cross_sectional_ranks_move_when_the_universe_changes``
is what stops one being introduced by accident.

**Ties share a rank.** Two instruments with the same value get the same
fractional rank - the midpoint of the positions they jointly occupy - rather
than being separated by whichever came first in the input. Order-dependent
tie-breaking is a silent dependence on iteration order, and iteration order is
not a property of this program.

Exact ``Decimal`` throughout. Ranks are ratios of counts, which are exact, so
there is no reason to cross the numeric boundary for them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

from sextant.domain.errors import DomainError
from sextant.domain.instrument import InstrumentKey


class EmptySlice(DomainError):
    """A cross-sectional statistic was asked for over nothing."""


def rank_fractions(values: Mapping[InstrumentKey, Decimal]) -> dict[InstrumentKey, Decimal]:
    """Each instrument's position in the slice, as a fraction in ``[0, 1]``.

    Zero is the smallest value, one the largest. A slice of one produces a rank
    of one half, because with nothing to compare against the honest answer is
    the middle rather than either extreme.
    """
    if not values:
        raise EmptySlice("A rank across an empty slice is not defined.")
    ordered: Sequence[tuple[InstrumentKey, Decimal]] = sorted(
        values.items(), key=lambda item: (item[1], str(item[0]))
    )
    count = len(ordered)
    if count == 1:
        return {ordered[0][0]: Decimal("0.5")}

    positions: dict[InstrumentKey, Decimal] = {}
    index = 0
    while index < count:
        end = index
        while end + 1 < count and ordered[end + 1][1] == ordered[index][1]:
            end += 1
        midpoint = Decimal(index + end) / Decimal(2)
        for position in range(index, end + 1):
            positions[ordered[position][0]] = midpoint / Decimal(count - 1)
        index = end + 1
    return positions


def top_n(values: Mapping[InstrumentKey, Decimal], count: int) -> tuple[InstrumentKey, ...]:
    """The ``count`` highest-valued instruments, in descending order.

    Ties are broken by instrument key so that the answer does not depend on the
    order the mapping happened to be built in.
    """
    if count <= 0:
        raise ValueError(f"count must be positive, got {count}")
    ordered = sorted(values.items(), key=lambda item: (-item[1], str(item[0])))
    return tuple(key for key, _ in ordered[:count])


def demeaned(values: Mapping[InstrumentKey, Decimal]) -> dict[InstrumentKey, Decimal]:
    """Each value less the slice's mean. Exact, and cross-sectional by definition."""
    if not values:
        raise EmptySlice("A cross-sectional mean over an empty slice is not defined.")
    mean = sum(values.values(), Decimal(0)) / Decimal(len(values))
    return {key: value - mean for key, value in values.items()}
