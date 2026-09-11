"""Estimating a spread from daily high, low and close prices.

Rule E1, registered in pre-registration section 33.4. The cost model's spread is
currently one constant per liquidity band, the bands are cut on quote turnover, and
section 7.5 measured two orders of magnitude of spread *inside a single band*. A better
constant cannot repair a partition cut on the wrong variable, so the replacement is an
estimate per instrument and per period, computed from prices the archive already holds.

Two estimators, and only one of them decides anything
-----------------------------------------------------

**Abdi and Ranaldo (2017)** is the registered estimator. With ``c`` the log close, ``h``
the log high, ``l`` the log low and ``eta = (h + l) / 2`` the log mid-range, the two-day
term is

    s2_t = 4 * (c_t - eta_t) * (c_t - eta_{t+1})

and the period estimate is the square root of the **mean** of those terms. It needs only
close, high and low, and it contains no step at which an implementer chooses anything.

**Corwin and Schultz (2012)** is computed beside it for comparison and is never
substituted for it. It uses the two-day high-low range against the sum of the two daily
ranges, with the paper's overnight-gap adjustment and the paper's own correction of
setting negative two-day estimates to zero before averaging.

Both produce a **proportional** spread: a fraction of price, not an amount of it.
:attr:`EstimatedSpread.basis_points` is ten thousand times that fraction.

What an estimate is, and what it is not
---------------------------------------

Neither of these is a measurement. Invariant 12 distinguishes an assumption from a
measurement; an estimate is a third thing, and it is reported as one - with its
estimator named and its calibration error beside it - wherever a cost depends on it. An
estimator validated against 36 symbol-days of real quotes is on firmer ground than a
constant carried over from another venue, and it is still an estimator.

Non-positive periods
--------------------

Both estimators can produce a non-positive figure: they are moments that happen to have
the units of a spread, not quantities constrained to be positive. A non-positive period
estimate is reported as **zero and flagged**, never rooted and never quietly dropped.
Rule E1's positivity clause is a condition on how often that happens, which is why the
count is carried in the result rather than absorbed by it.

No monetary type and no array library
-------------------------------------

Log prices are dimensionless and this module holds no ``Decimal``, no ``Price`` and no
numpy. The conversion from the exact decimal text the store holds into these floats
happens in the caller, which is the layer allowed to read prices.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from sextant.domain.errors import SextantError

#: Basis points in one unit. A proportional spread times this is a spread in bps.
BASIS_POINTS = 10_000

#: The registered estimator's identifier, and the comparison's. Held here beside the
#: arithmetic so a reported figure can name what produced it without the caller
#: retyping a string.
ABDI_RANALDO = "abdi-ranaldo-2017"
CORWIN_SCHULTZ = "corwin-schultz-2012"

#: ``3 - 2 * sqrt(2)``, the constant in Corwin and Schultz's alpha. Named because it
#: appears twice and a mistyped second occurrence would be invisible.
_CS_CONSTANT = 3.0 - 2.0 * math.sqrt(2.0)

#: A two-day term needs two days, so an estimate needs at least two bars. The registered
#: minimum number of *usable pairs* below which the banded assumption is charged instead
#: is a pre-registration value and lives with the rest of them, not here: this module
#: reports how many pairs it had and refuses to decide what is enough.
MINIMUM_DAYS = 2


class UnusableDailyRange(SextantError):
    """A daily bar that cannot enter either estimator, named rather than skipped.

    Non-positive prices, a high below its own low, or a close outside its own range.
    Each is a data defect rather than a quiet market, and each would produce a plausible
    number from arithmetic that has stopped meaning anything. The caller counts these
    and reports the count; nothing substitutes a value for one.
    """


@dataclass(frozen=True, slots=True)
class DailyRange:
    """One day's high, low and close, as dimensionless floats.

    Validated at construction, because every check here is a check that cannot then be
    forgotten at one of the call sites.
    """

    high: float
    low: float
    close: float

    def __post_init__(self) -> None:
        if not (self.high > 0.0 and self.low > 0.0 and self.close > 0.0):
            raise UnusableDailyRange(
                f"A daily range needs positive prices, got high={self.high}, "
                f"low={self.low}, close={self.close}."
            )
        if self.high < self.low:
            raise UnusableDailyRange(
                f"High {self.high} is below low {self.low}. That is a defect in the data, "
                "not a thin market, and no spread can be estimated from it."
            )
        if not (self.low <= self.close <= self.high):
            raise UnusableDailyRange(
                f"Close {self.close} lies outside the range [{self.low}, {self.high}]. "
                "Both estimators measure where the close sits inside the range, so a "
                "close outside it produces arithmetic that no longer means anything."
            )

    @property
    def log_mid_range(self) -> float:
        """``eta``: the mean of the log high and the log low."""
        return (math.log(self.high) + math.log(self.low)) / 2.0

    @property
    def log_close(self) -> float:
        return math.log(self.close)

    @property
    def log_range(self) -> float:
        """``ln(high / low)``, which is zero on a day that did not move."""
        return math.log(self.high / self.low)


@dataclass(frozen=True, slots=True)
class EstimatedSpread:
    """One period's estimate, with everything needed to distrust it.

    ``mean_term`` is the mean of whatever the estimator averages, before any flooring:
    for Abdi-Ranaldo a squared spread, for Corwin-Schultz a spread. It is carried
    unfloored so that a reader can see a period the estimator wanted to call negative,
    which a floored figure of zero hides.
    """

    estimator: str
    pairs: int
    """How many two-day terms entered the mean."""
    non_positive_terms: int
    """How many of those terms were themselves non-positive."""
    mean_term: float
    relative: float
    """The proportional spread: a fraction of price, floored at zero."""
    floored: bool
    """Whether the reported figure was floored, i.e. the estimate was non-positive."""

    @property
    def basis_points(self) -> float:
        """The estimate in basis points of price."""
        return self.relative * float(BASIS_POINTS)

    def as_json(self) -> dict[str, object]:
        return {
            "estimator": self.estimator,
            "two_day_pairs": self.pairs,
            "non_positive_terms": self.non_positive_terms,
            "mean_term_before_flooring": self.mean_term,
            "relative_spread": self.relative,
            "basis_points": self.basis_points,
            "floored_at_zero": self.floored,
        }


def two_day_terms(days: Sequence[DailyRange]) -> tuple[float, ...]:
    """Abdi and Ranaldo's two-day terms, before they are averaged.

    Exposed because the *scatter* of these terms is what decides whether the estimator
    can resolve anything. Each term is an unbiased estimate of the squared spread plus
    noise whose scale is set by the daily variance, so the ratio of the two says how many
    terms an average would need before the signal emerged. An estimator reported only
    through its mean hides that ratio completely, and the ratio is the finding.
    """
    return tuple(
        4.0 * (first.log_close - first.log_mid_range) * (first.log_close - second.log_mid_range)
        for first, second in pairwise(days)
    )


def abdi_ranaldo(days: Sequence[DailyRange]) -> EstimatedSpread | None:
    """Abdi and Ranaldo (2017), over one period. ``None`` when there are too few days.

    The mean of ``4 * (c_t - eta_t) * (c_t - eta_{t+1})`` across consecutive days, then
    the square root. A non-positive mean is reported as zero with ``floored`` set rather
    than rooted: the estimator is a moment with the units of a squared spread, and a
    negative sample moment means the sample was too small or too quiet, not that the
    spread was imaginary.
    """
    if len(days) < MINIMUM_DAYS:
        return None
    terms = two_day_terms(days)
    mean = sum(terms) / float(len(terms))
    return EstimatedSpread(
        estimator=ABDI_RANALDO,
        pairs=len(terms),
        non_positive_terms=sum(1 for term in terms if term <= 0.0),
        mean_term=mean,
        relative=math.sqrt(mean) if mean > 0.0 else 0.0,
        floored=mean <= 0.0,
    )


def corwin_schultz(days: Sequence[DailyRange]) -> EstimatedSpread | None:
    """Corwin and Schultz (2012), over one period. ``None`` when there are too few days.

    Per consecutive pair: the overnight gap is removed from the second day, ``beta`` is
    the sum of the two squared log ranges, ``gamma`` is the squared log range of the
    two-day window, and the spread follows from ``alpha``. Negative pair estimates are
    set to zero before averaging, which is the paper's own correction.

    Computed for comparison only. Rule E1 never adopts it, whatever it says.
    """
    if len(days) < MINIMUM_DAYS:
        return None
    estimates: list[float] = []
    negative = 0
    for first, second in pairwise(days):
        adjusted = _without_the_overnight_gap(first, second)
        beta = first.log_range**2 + adjusted.log_range**2
        gamma = math.log(max(first.high, adjusted.high) / min(first.low, adjusted.low)) ** 2
        alpha = (math.sqrt(2.0 * beta) - math.sqrt(beta)) / _CS_CONSTANT - math.sqrt(
            gamma / _CS_CONSTANT
        )
        spread = 2.0 * (math.exp(alpha) - 1.0) / (1.0 + math.exp(alpha))
        if spread <= 0.0:
            negative += 1
        estimates.append(max(spread, 0.0))
    mean = sum(estimates) / float(len(estimates))
    return EstimatedSpread(
        estimator=CORWIN_SCHULTZ,
        pairs=len(estimates),
        non_positive_terms=negative,
        mean_term=mean,
        relative=max(mean, 0.0),
        floored=mean <= 0.0,
    )


def _without_the_overnight_gap(first: DailyRange, second: DailyRange) -> DailyRange:
    """The second day's prices, shifted so the two days' ranges overlap.

    The paper's adjustment, and the reason it exists: the estimator reads the two-day
    range as the same quantity as the two daily ranges, and an overnight gap inflates
    the two-day range without any intraday movement having happened. When the second
    day's low is above the first day's high, both of the second day's prices come down
    by the gap; when its high is below the first day's low, both go up by it. The close
    moves with them, so that it stays inside its own range.
    """
    if second.low > first.high:
        gap = second.low - first.high
        return DailyRange(high=second.high - gap, low=second.low - gap, close=second.close - gap)
    if second.high < first.low:
        gap = first.low - second.high
        return DailyRange(high=second.high + gap, low=second.low + gap, close=second.close + gap)
    return second


__all__ = [
    "ABDI_RANALDO",
    "BASIS_POINTS",
    "CORWIN_SCHULTZ",
    "MINIMUM_DAYS",
    "DailyRange",
    "EstimatedSpread",
    "UnusableDailyRange",
    "abdi_ranaldo",
    "corwin_schultz",
    "two_day_terms",
]
