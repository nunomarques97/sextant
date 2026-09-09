"""The one place a monetary value becomes a float.

Invariant 4 says money is ``Decimal``. The statistics this project needs -
Sharpe, Sortino, skewness, kurtosis, the normal CDF inside the Deflated Sharpe
Ratio, ten thousand bootstrap resamples - are float work, and pretending
otherwise would either be unusably slow or would imply a precision a Sharpe
ratio estimated from thirty observations does not remotely have.

So there is a crossing. The point of this module is that there is exactly one,
it runs in one direction, it is named, and the reverse crossing is a different
function with a different name. A boundary that exists in twelve places is not a
boundary; it is a habit.

**The rule, enforced by ``tests/unit/test_numeric_boundary.py``.** No module in
``src`` may import both an array library (numpy, scipy, polars) and a monetary
type (``Decimal``, ``Price``, ``Quantity``, ``Notional``). This module is the
single exemption, and it is exempt because it is the crossing.

**Direction.** ``equity_curve_as_returns`` takes a ``Decimal`` equity curve and
produces a float return series. That is the only way money enters the array
world. ``statistic_as_decimal`` takes a dimensionless float statistic back to a
``Decimal`` for reporting, and it quantises explicitly so that a statistic can
never be mistaken for an exact monetary quantity. Nothing in this module
converts a float back into a ``Price``, a ``Quantity`` or a ``Notional``, and
nothing should: a monetary value that has been through a float is no longer a
monetary value.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

import numpy as np
import numpy.typing as npt

from sextant.domain.errors import SextantError
from sextant.domain.money import Notional

#: How many monthly rebalances make a year. Every return series in this project
#: is monthly, because the engine rebalances monthly and a return between two
#: rebalances is the only return an allocation actually earns.
MONTHS_PER_YEAR = 12

#: Places a dimensionless statistic is quantised to on the way back to Decimal.
#: Six is far beyond the precision of any of these estimates and is chosen only
#: so that a reported figure round-trips through JSON unchanged.
STATISTIC_PLACES = Decimal("0.000001")


class DegenerateCurve(SextantError):
    """An equity curve that cannot produce a return series.

    Raised rather than returning an empty array or a zero. A curve with fewer
    than two points has no returns, and a curve that touches zero has no
    defined return across that step. Both are real conditions - a strategy can
    be wiped out - and both must reach the caller as themselves rather than as a
    quietly plausible number.
    """


@dataclass(frozen=True, slots=True)
class ReturnSeries:
    """A float return series, and how many of them make a year.

    Deliberately not a bare array. Every statistic downstream needs to know the
    observation frequency to annualise correctly, and passing the two separately
    is how a monthly Sharpe gets annualised as if it were daily.
    """

    values: npt.NDArray[np.float64]
    periods_per_year: int

    def __post_init__(self) -> None:
        if self.periods_per_year <= 0:
            raise ValueError(f"periods_per_year must be positive, got {self.periods_per_year}")

    @property
    def count(self) -> int:
        """How many return observations there are."""
        return int(self.values.size)


def equity_curve_as_returns(
    curve: Sequence[Notional],
    *,
    periods_per_year: int = MONTHS_PER_YEAR,
) -> ReturnSeries:
    """Convert a Decimal equity curve into a float return series.

    **This is the numeric boundary.** It is the only function in the system that
    reads a monetary value and produces a float, and every statistic in
    ``engine.statistics`` is computed from what comes out of it.

    The conversion is per-step simple returns, ``e[i] / e[i-1] - 1``, computed in
    ``Decimal`` and only then cast. Doing the division in ``Decimal`` first
    matters: a float division of two float-cast equity values loses exactness in
    the equity figures themselves, which are the numbers the accounting tests
    assert on. What crosses the boundary is a dimensionless ratio, not money.

    Raises :class:`DegenerateCurve` rather than inventing a value when the curve
    is too short or passes through zero.
    """
    if len(curve) < 2:
        raise DegenerateCurve(
            f"A return series needs at least two equity observations, got {len(curve)}."
        )
    ratios: list[float] = []
    for index in range(1, len(curve)):
        previous = curve[index - 1].amount
        if previous == 0:
            raise DegenerateCurve(
                f"Equity is zero at observation {index - 1}; the return across that step "
                "is undefined. A wiped-out account is a real outcome and must be reported "
                "as one, not divided through."
            )
        ratios.append(float(curve[index].amount / previous - Decimal(1)))
    return ReturnSeries(
        values=np.asarray(ratios, dtype=np.float64),
        periods_per_year=periods_per_year,
    )


def returns_as_series(
    returns: Sequence[Decimal],
    *,
    periods_per_year: int = MONTHS_PER_YEAR,
) -> ReturnSeries:
    """Convert an already-dimensionless Decimal return sequence into floats.

    The same crossing as :func:`equity_curve_as_returns`, for callers that hold
    returns rather than an equity curve. It lives here, beside the other one,
    rather than in the caller, because the whole value of this module is that
    the list of crossings is short and visible in one file.
    """
    return ReturnSeries(
        values=np.asarray([float(value) for value in returns], dtype=np.float64),
        periods_per_year=periods_per_year,
    )


def returns_panel(rows: Sequence[Sequence[Decimal | None]]) -> npt.NDArray[np.float64]:
    """Convert a Decimal ``(periods, names)`` return panel into floats.

    The third and last crossing, for the breadth statistic: a correlation matrix
    across the names a strategy held needs a rectangular float array, and the
    returns arrive as Decimals because that is what a price is. It lives here
    with the other two rather than in the caller, because the whole value of
    this module is that the list of crossings is short and visible in one file.

    Rows must all be the same length. A missing value is carried as ``None`` and
    crosses as ``NaN``, never as a zero: a name that did not trade on a day did
    not return zero that day, and a zero would be counted as a real observation
    by everything downstream. A ragged row is refused outright, because it means
    the caller has lost track of which column is which name.
    """
    if not rows:
        return np.zeros((0, 0), dtype=np.float64)
    width = len(rows[0])
    for row in rows:
        if len(row) != width:
            raise DegenerateCurve(
                f"A return panel row has {len(row)} values where the first has {width}. "
                "Padding it would invent a return for a day the name did not trade."
            )
    return np.asarray(
        [[float("nan") if value is None else float(value) for value in row] for row in rows],
        dtype=np.float64,
    )


def statistic_as_decimal(value: float) -> Decimal:
    """Bring a dimensionless float statistic back for reporting.

    The reverse crossing, named separately and used deliberately. Quantised to
    :data:`STATISTIC_PLACES` so that the result carries a precision consistent
    with what it is - an estimate - and so that a report round-trips through
    JSON without acquiring a trailing tail of binary-float noise.

    Never used to reconstruct money. A float that was once a euro is not a euro
    any more.
    """
    if not np.isfinite(value):
        raise DegenerateCurve(f"Statistic is not finite: {value}. It has no Decimal form.")
    return Decimal(repr(float(value))).quantize(STATISTIC_PLACES)
