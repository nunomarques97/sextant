"""Performance statistics over a float return series.

Everything here takes a :class:`~sextant.engine.statistics.boundary.ReturnSeries`
and returns floats. No monetary type appears in this module and none may: the
boundary is one module away, and this is the side of it where money does not
exist.

Two choices worth stating, because both change the numbers.

**Sample standard deviation, not population.** ``ddof=1``. With thirty monthly
observations the difference is about 1.7% on the Sharpe, which is small against
the estimate's own standard error and large against nothing. The sample form is
the unbiased estimator of the variance and is what the Deflated Sharpe Ratio's
derivation assumes.

**Sortino's downside deviation is taken over all observations, not only the
negative ones.** The denominator is ``sqrt(mean(min(r - target, 0)^2))`` over
the full sample. Dividing by the count of negative observations instead inflates
the ratio for any series that rarely loses, which is exactly the series a null
distribution is full of, and it is the more common of the two conventions in
software rather than in the literature. Stated because the two conventions
differ by a factor of ``sqrt(n / n_negative)`` and nothing in a reported number
reveals which was used.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from sextant.engine.statistics.boundary import ReturnSeries

#: Below this many observations, an annualised Sharpe ratio is not reported at
#: all. Two observations produce one; it means nothing. The engine reports
#: ``None`` rather than a number nobody should read.
MINIMUM_OBSERVATIONS = 3


@dataclass(frozen=True, slots=True)
class PerformanceStatistics:
    """The distributional summary of one return series.

    ``skewness`` and ``kurtosis`` are carried because the Deflated Sharpe Ratio
    needs them, not as decoration. ``kurtosis`` is the raw fourth standardised
    moment - 3.0 for a normal distribution - rather than excess kurtosis,
    because that is the convention the Bailey and Lopez de Prado formula is
    written in and converting at the call site is how a factor of three gets
    lost.
    """

    observations: int
    mean_return: float
    """Mean return per observation, not annualised."""
    volatility: float
    """Sample standard deviation per observation, not annualised."""
    sharpe_per_period: float
    """Mean over standard deviation, per observation. The DSR's input."""
    sharpe_annualised: float
    sortino_annualised: float
    skewness: float
    kurtosis: float
    """Raw fourth standardised moment. 3.0 under normality, not 0.0."""
    annualisation: int
    """Observations per year, carried through from the return series."""

    @property
    def sharpe_standard_error(self) -> float:
        """Approximate standard error of the annualised Sharpe estimate.

        ``sqrt((1 + SR^2 / 2) / n)`` per observation, annualised the same way
        the ratio is. The normal-returns approximation, which understates the
        error for the fat-tailed series this project actually produces, so it is
        a floor on the uncertainty rather than a measurement of it.

        Reported everywhere the Sharpe is, because over a thirty-month window
        this number is usually the most important one on the page.
        """
        per_period = float(
            np.sqrt((1.0 + 0.5 * self.sharpe_per_period**2) / float(self.observations))
        )
        return per_period * float(np.sqrt(self.annualisation))

    @property
    def mean_return_t_statistic(self) -> float:
        """The mean return over the standard error of that mean.

        ``mean / (sd / sqrt(n))``, which is the per-period Sharpe times the root of the
        observation count. Greater than one means the mean return exceeds one standard
        error of itself, which is the absolute clause rule P1 pairs with every
        null comparison from F2: "better than a losing null" and "distinguishable from
        nothing" are different claims, and a criterion that states only the first cannot
        tell them apart.

        Not annualised, and it must not be: annualising a mean and its standard error
        scales both, so the ratio is the same number at every frequency. A version of
        this that carried an annualisation factor would be a version with a bug.
        """
        return self.sharpe_per_period * float(np.sqrt(float(self.observations)))


def summarise(series: ReturnSeries) -> PerformanceStatistics | None:
    """Summarise a return series, or ``None`` when it is too short to summarise.

    ``None`` rather than a number. A Sharpe ratio computed from two observations
    is arithmetic rather than evidence, and returning it lets it be printed in a
    table beside ratios that mean something.
    """
    values = series.values
    if values.size < MINIMUM_OBSERVATIONS:
        return None
    mean = float(np.mean(values))
    deviation = float(np.std(values, ddof=1))
    root_periods = float(np.sqrt(series.periods_per_year))

    # An exactly constant series is detected by range rather than by its
    # standard deviation. Summing identical floats and subtracting the mean
    # leaves cancellation noise around 1e-18, so ``std`` is not exactly zero and
    # dividing by it produces a Sharpe of ten to the sixteenth. The range is
    # exact and says what is actually true: nothing varied.
    if float(np.ptp(values)) == 0.0 or deviation == 0.0:
        per_period = 0.0
        annualised = 0.0
    else:
        per_period = mean / deviation
        annualised = per_period * root_periods

    return PerformanceStatistics(
        observations=int(values.size),
        mean_return=mean,
        volatility=deviation,
        sharpe_per_period=per_period,
        sharpe_annualised=annualised,
        sortino_annualised=_sortino(values, root_periods),
        skewness=_skewness(values),
        kurtosis=_kurtosis(values),
        annualisation=series.periods_per_year,
    )


def _sortino(values: npt.NDArray[np.float64], root_periods: float) -> float:
    """Mean over downside deviation, annualised. Zero target."""
    downside = np.minimum(values, 0.0)
    deviation = float(np.sqrt(np.mean(downside**2)))
    if deviation == 0.0:
        return 0.0
    return float(np.mean(values)) / deviation * root_periods


def _skewness(values: npt.NDArray[np.float64]) -> float:
    """Third standardised moment, population form.

    Population rather than the bias-corrected sample form, because that is what
    the Deflated Sharpe Ratio's derivation uses and mixing the two is a silent
    factor of ``sqrt(n(n-1))/(n-2)``.
    """
    deviation = float(np.std(values, ddof=0))
    if deviation == 0.0:
        return 0.0
    centred = values - float(np.mean(values))
    return float(np.mean(centred**3) / deviation**3)


def _kurtosis(values: npt.NDArray[np.float64]) -> float:
    """Fourth standardised moment, population form, not excess."""
    deviation = float(np.std(values, ddof=0))
    if deviation == 0.0:
        return 3.0
    centred = values - float(np.mean(values))
    return float(np.mean(centred**4) / deviation**4)
