"""Is the edge still there, or was it there once.

An average taken over six years answers a question nobody is asking. What
matters to a decision made in 2026 is whether a strategy earns in 2026, and a
mean that is dominated by 2021 says the opposite of what it appears to say. This
module computes the three things
`docs/PRE-REGISTRATION-006-F1.md` section 16.1 registers for answering that, and
it computes them the way that section fixed them, before any of them had a value.

**The slope**, of monthly net return on months elapsed, with a bootstrap
interval. A negative slope whose interval excludes zero is measured decay. A
negative slope whose interval spans zero is a negative point estimate and
nothing more, and :attr:`Trend.is_decay` is the only thing that may be reported
as decay.

**The two-year comparison**, which is the declared expectation D1 written as
arithmetic: the mean monthly return of the two most recent complete scored years
against the two earliest.

**The sub-window**, which re-scores an existing series over its final months.
This is where the honesty about power has to live, and it cuts both ways.

A window that **lost money** rejects. That is a fact about an account rather than
an estimate of a parameter, and the question the Sponsor asked - do the recent
years stand on their own - is answered no by a realised loss whatever the
confidence interval on the population mean turns out to be.

A window that **earned** but fell short of its own null is where the sample size
starts to matter, because twenty-four months carries a Sharpe standard error near
0.7 and a shortfall smaller than that is noise. Such a window has not rejected;
it has run out of months, and that is (C) for the persistence question
specifically.

:class:`SubWindowVerdict` has three values for exactly that reason, and no code
path collapses them to two.

Float work, over a boundary that already exists. No monetary type is imported
here: a return series arrives having already crossed in
:mod:`sextant.engine.statistics.boundary`, which is the only crossing there is.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import numpy.typing as npt

from sextant.engine.statistics.bootstrap import generator_for
from sextant.engine.statistics.boundary import MONTHS_PER_YEAR, ReturnSeries

#: How many trailing scored months the standalone recent-window test uses.
#: Registered in section 16.1 and fixed before any of it was computed.
RECENT_WINDOW_MONTHS = 24

#: Resamples for every interval in this module, matching section 14's bootstrap.
RESAMPLES = 10_000

#: The generator seed section 14 already fixed for every bootstrap in this task.
BOOTSTRAP_SEED = 987654321

#: A calendar year carrying fewer scored months than this is reported with its
#: return and an explicit refusal to conclude anything from it, exactly as an
#: under-populated regime is.
MINIMUM_MONTHS_FOR_A_YEAR = 6


class SubWindowVerdict(StrEnum):
    """What a sub-window says about whether an edge survived into it."""

    CLEARS = "clears"
    """Positive, and above the bar. The edge is present in this window."""

    REJECTS = "rejects"
    """The window lost money, or earned and fell short of its own null by more
    than the window could have been wrong about. The edge is absent from this
    window, and that is a finding rather than an absence of one."""

    UNRESOLVED = "unresolved"
    """The window earned but fell short of its null by less than it could
    detect. Too short to say, which is (C) for the persistence question and is
    never reported as a rejection."""


@dataclass(frozen=True, slots=True)
class YearSlice:
    """One calendar year of a scored series, and whether it carries enough months."""

    year: int
    months: int
    compounded_return: float
    mean_monthly_return: float
    annualised_sharpe: float | None
    """None when the year carries fewer than :data:`MINIMUM_MONTHS_FOR_A_YEAR`
    months. Not zero, and not a Sharpe computed anyway: a statistic that cannot
    be estimated is absent, and a reader must be able to see that it is."""
    sharpe_standard_error: float | None

    @property
    def is_conclusive(self) -> bool:
        """Whether anything at all may be concluded from this year."""
        return self.months >= MINIMUM_MONTHS_FOR_A_YEAR


@dataclass(frozen=True, slots=True)
class Trend:
    """The slope of monthly return on time, with the interval that qualifies it."""

    slope_per_month: float
    """Change in monthly return per month elapsed. Tiny by construction; it is
    the sign and the interval that carry the meaning, not the magnitude."""
    ci_low: float
    ci_high: float
    months: int

    @property
    def excludes_zero(self) -> bool:
        """Whether the interval is entirely one side of zero."""
        return (self.ci_low > 0 and self.ci_high > 0) or (self.ci_low < 0 and self.ci_high < 0)

    @property
    def is_decay(self) -> bool:
        """The only property that may be reported as decay.

        A negative point estimate is not decay. A negative estimate whose
        interval excludes zero is.
        """
        return self.slope_per_month < 0 and self.excludes_zero


@dataclass(frozen=True, slots=True)
class TwoYearComparison:
    """Declared expectation D1, as arithmetic.

    ``difference`` is recent minus early, so D1 predicts a **negative** number.
    Whether the interval excludes zero decides whether the prediction was
    confirmed, refuted, or left unresolved by a sample too small to say.
    """

    early_years: tuple[int, ...]
    recent_years: tuple[int, ...]
    early_mean_monthly: float
    recent_mean_monthly: float
    difference: float
    ci_low: float
    ci_high: float

    @property
    def excludes_zero(self) -> bool:
        """Whether the difference's interval is entirely one side of zero."""
        return (self.ci_low > 0 and self.ci_high > 0) or (self.ci_low < 0 and self.ci_high < 0)

    @property
    def confirms_d1(self) -> bool:
        """D1 predicted a fall. Confirmed only when the interval agrees."""
        return self.difference < 0 and self.excludes_zero

    @property
    def refutes_d1(self) -> bool:
        """A rise whose interval excludes zero refutes the prediction outright."""
        return self.difference > 0 and self.excludes_zero


@dataclass(frozen=True, slots=True)
class SubWindow:
    """A trailing slice of a scored series, judged against its own null."""

    months: int
    compounded_return: float
    annualised_sharpe: float
    """The point estimate over the sub-window."""
    null_percentile_95: float
    """The 95th percentile of the same null draws restricted to the same months.
    The same draws, not a fresh sample: a sub-window test that redrew its null
    would be answering a different question with different noise."""
    standard_error: float
    """On the annualised Sharpe, from the sub-window's own effective sample."""
    detectable_effect: float
    """The smallest Sharpe gap this many months could have resolved. What
    separates a rejection from a shrug."""

    @property
    def gap_to_null(self) -> float:
        """How far the point estimate falls short of the bar. Negative means above it."""
        return self.null_percentile_95 - self.annualised_sharpe

    @property
    def verdict(self) -> SubWindowVerdict:
        """Clears, rejects, or cannot say - and the three are never merged.

        **A realised loss is a rejection, not an open question.** The window's
        compounded net return is a fact about an account: over these months the
        strategy lost money. Whether its *population* mean is negative is a
        different question and a harder one, but it is not the question the
        Sponsor asked, which was whether the recent years stand on their own.
        They did not if they lost money, and dressing that as "too short to say"
        would be the softening the brief forbids.

        **A positive window that misses the bar is where power matters**, and
        that is the only place :attr:`detectable_effect` is allowed to decide
        anything. A window that earned but fell short of its null by less than it
        could have resolved has not rejected: it has run out of months.
        """
        if self.compounded_return > 0 and self.annualised_sharpe > self.null_percentile_95:
            return SubWindowVerdict.CLEARS
        if self.compounded_return <= 0:
            return SubWindowVerdict.REJECTS
        if self.gap_to_null > self.detectable_effect:
            return SubWindowVerdict.REJECTS
        return SubWindowVerdict.UNRESOLVED


# ---------------------------------------------------------------------------
# Computing them
# ---------------------------------------------------------------------------


def year_slices(years: Sequence[int], returns: Sequence[float]) -> tuple[YearSlice, ...]:
    """Split a monthly return series by the calendar year each month fell in.

    ``years`` and ``returns`` are parallel and must be the same length: one
    calendar year per monthly return, in the order the months were scored.
    """
    if len(years) != len(returns):
        raise ValueError(
            f"{len(years)} year labels for {len(returns)} monthly returns; a label per "
            "month is what makes the split meaningful"
        )
    grouped: dict[int, list[float]] = {}
    for year, value in zip(years, returns, strict=True):
        grouped.setdefault(year, []).append(value)
    slices: list[YearSlice] = []
    for year in sorted(grouped):
        values = np.asarray(grouped[year], dtype=np.float64)
        conclusive = len(values) >= MINIMUM_MONTHS_FOR_A_YEAR
        sharpe = _annualised_sharpe(values) if conclusive else None
        error = _sharpe_standard_error(len(values), values=values) if conclusive else None
        slices.append(
            YearSlice(
                year=year,
                months=len(values),
                compounded_return=float(np.prod(1.0 + values) - 1.0),
                mean_monthly_return=float(values.mean()),
                annualised_sharpe=sharpe,
                sharpe_standard_error=error,
            )
        )
    return tuple(slices)


def trend_of(series: ReturnSeries, *, resamples: int = RESAMPLES) -> Trend | None:
    """Least-squares slope of monthly return on months elapsed, with an interval.

    ``None`` when there are fewer than three months, where a slope is either
    undefined or is a line through two points and means nothing.

    The interval resamples **pairs** of (month index, return) rather than
    residuals, because a residual bootstrap assumes the linear model is right and
    the question here is precisely whether there is a trend at all.
    """
    values = np.asarray(series.values, dtype=np.float64)
    if values.size < 3:
        return None
    months = np.arange(values.size, dtype=np.float64)
    slope = float(np.polyfit(months, values, 1)[0])
    generator = generator_for(BOOTSTRAP_SEED)
    drawn = np.empty(resamples, dtype=np.float64)
    for index in range(resamples):
        picks = generator.integers(0, values.size, values.size)
        sample_months = months[picks]
        if float(sample_months.std()) == 0.0:
            drawn[index] = 0.0
            continue
        drawn[index] = float(np.polyfit(sample_months, values[picks], 1)[0])
    low, high = (float(value) for value in np.percentile(drawn, [2.5, 97.5]))
    return Trend(slope_per_month=slope, ci_low=low, ci_high=high, months=int(values.size))


def two_year_comparison(
    slices: Sequence[YearSlice],
    years: Sequence[int],
    returns: Sequence[float],
    *,
    resamples: int = RESAMPLES,
) -> TwoYearComparison | None:
    """Declared expectation D1: the recent two complete years against the earliest two.

    "Complete" means carrying at least :data:`MINIMUM_MONTHS_FOR_A_YEAR` scored
    months, the same threshold that decides whether a year's Sharpe is reported
    at all. ``None`` when fewer than four such years exist, in which case D1
    cannot be evaluated and the report says so rather than comparing whatever is
    available.
    """
    conclusive = [item.year for item in slices if item.is_conclusive]
    if len(conclusive) < 4:
        return None
    early = tuple(conclusive[:2])
    recent = tuple(conclusive[-2:])
    early_values = _values_in(early, years, returns)
    recent_values = _values_in(recent, years, returns)
    difference = float(recent_values.mean() - early_values.mean())
    generator = generator_for(BOOTSTRAP_SEED)
    drawn = np.empty(resamples, dtype=np.float64)
    for index in range(resamples):
        left = early_values[generator.integers(0, early_values.size, early_values.size)]
        right = recent_values[generator.integers(0, recent_values.size, recent_values.size)]
        drawn[index] = float(right.mean() - left.mean())
    low, high = (float(value) for value in np.percentile(drawn, [2.5, 97.5]))
    return TwoYearComparison(
        early_years=early,
        recent_years=recent,
        early_mean_monthly=float(early_values.mean()),
        recent_mean_monthly=float(recent_values.mean()),
        difference=difference,
        ci_low=low,
        ci_high=high,
    )


def sub_window(
    returns: Sequence[float],
    null_sharpes: Sequence[float],
    *,
    months: int = RECENT_WINDOW_MONTHS,
) -> SubWindow | None:
    """Score the trailing ``months`` of a series against its own null over the same months.

    ``null_sharpes`` must already be the null draws computed over the *same*
    trailing months. Passing full-window null Sharpes here would compare a short
    window's estimate against a long window's threshold, which is a comparison
    with no meaning; the runner computes both from the same seeds and hands the
    right one in.

    ``None`` when the series is shorter than the window asked for, because a
    24-month test on 19 months is a different test.
    """
    values = np.asarray(returns, dtype=np.float64)
    if values.size < months or not null_sharpes:
        return None
    tail = values[-months:]
    return SubWindow(
        months=months,
        compounded_return=float(np.prod(1.0 + tail) - 1.0),
        annualised_sharpe=_annualised_sharpe(tail),
        null_percentile_95=float(np.percentile(np.asarray(null_sharpes, dtype=np.float64), 95)),
        standard_error=_sharpe_standard_error(months, values=tail),
        detectable_effect=_detectable_effect(months, values=tail),
    )


# ---------------------------------------------------------------------------
# The arithmetic the three of them share
# ---------------------------------------------------------------------------


def _annualised_sharpe(values: npt.NDArray[np.float64]) -> float:
    """Annualised Sharpe against a zero benchmark, or zero for a flat series."""
    deviation = float(values.std(ddof=1)) if values.size > 1 else 0.0
    if deviation == 0.0:
        return 0.0
    return float(values.mean()) / deviation * float(np.sqrt(MONTHS_PER_YEAR))


def _sharpe_standard_error(months: int, *, values: npt.NDArray[np.float64] | None = None) -> float:
    """Standard error on an annualised Sharpe estimated from ``months`` months.

    The uncorrected form is ``sqrt(1/n)`` on the per-period statistic. This uses
    the skewness and kurtosis correction instead, because the uncorrected one is
    **anti-conservative in the direction that matters here**: a narrower standard
    error means a smaller detectable effect, which means a shortfall counts as a
    rejection sooner. On a distribution with the skewness 2.46 and kurtosis 10.36
    SEXTANT-005 measured on this asset class, that would call rejections the data
    cannot support.

    The corrected variance of the per-period Sharpe estimate is

        ( 1 + SR^2 / 2 - g3 * SR + (g4 - 3) / 4 * SR^2 ) / n

    with ``g3`` the skewness and ``g4`` the kurtosis of the return series, which
    is the standard result and is the same one the Deflated Sharpe Ratio already
    rests on. With no series to measure them from it falls back to the
    uncorrected form, which is the right default only because there is nothing
    else to compute.
    """
    if months <= 1:
        return float("inf")
    annualise = float(np.sqrt(MONTHS_PER_YEAR))
    if values is None or values.size < 3:
        return annualise / float(np.sqrt(months))
    deviation = float(values.std(ddof=1))
    if deviation == 0.0:
        return annualise / float(np.sqrt(months))
    sharpe = float(values.mean()) / deviation
    centred = values - values.mean()
    skewness = float((centred**3).mean() / deviation**3)
    kurtosis = float((centred**4).mean() / deviation**4)
    variance = (
        1.0 + sharpe**2 / 2.0 - skewness * sharpe + (kurtosis - 3.0) / 4.0 * sharpe**2
    ) / float(months)
    return annualise * float(np.sqrt(max(variance, 0.0)))


def _detectable_effect(months: int, *, values: npt.NDArray[np.float64] | None = None) -> float:
    """The smallest Sharpe gap ``months`` months could resolve at 95 per cent.

    1.96 standard errors. A gap smaller than this is inside the noise, and a
    window that misses the bar by less than it has not rejected anything.
    """
    return 1.96 * _sharpe_standard_error(months, values=values)


def _values_in(
    years: Sequence[int], labels: Sequence[int], returns: Sequence[float]
) -> npt.NDArray[np.float64]:
    """Every monthly return whose calendar year is in ``years``."""
    wanted = set(years)
    return np.asarray(
        [value for label, value in zip(labels, returns, strict=True) if label in wanted],
        dtype=np.float64,
    )
