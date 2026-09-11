"""The Deflated Sharpe Ratio, as Bailey and Lopez de Prado define it.

Which variant this is
---------------------

Bailey, D. H. and Lopez de Prado, M., *The Deflated Sharpe Ratio: Correcting for
Selection Bias, Backtest Overfitting and Non-Normality*, SSRN 2460551. Not a
blog restatement of it, and not the variant that a popular R package reports
under the same name - see "What this is not" below.

The definition, in three steps.

**1. The Probabilistic Sharpe Ratio.** Given an observed Sharpe ratio ``SR``
estimated from ``T`` returns with skewness ``g3`` and (non-excess) kurtosis
``g4``, the probability that the true Sharpe exceeds a benchmark ``SR*`` is::

    PSR(SR*) = Z[ (SR - SR*) * sqrt(T - 1)
                  / sqrt(1 - g3*SR + ((g4 - 1) / 4) * SR^2) ]

where ``Z`` is the standard normal CDF. The denominator is the studentising
term: it is exactly 1 when the returns are normal and ``SR`` is 0, it grows with
kurtosis, and it shrinks with positive skew. That is the non-normality
correction, and it is the reason this is not simply a t-test.

**2. The expected maximum Sharpe under the null.** If ``K`` independent trials
are run and none of them has any edge, the best of them still looks good. Its
expected Sharpe is approximated by the Gumbel expression::

    SR* = sqrt(V) * [ (1 - y) * Z^-1[1 - 1/K] + y * Z^-1[1 - 1/(K*e)] ]

with ``y`` the Euler-Mascheroni constant 0.5772156649, ``e`` Euler's number,
``Z^-1`` the normal quantile function, and ``V`` the **variance across the
trials** of their Sharpe ratios. ``V`` is the crucial input and the one most
often fudged: it is not the variance of the returns, it is the dispersion of the
Sharpe ratios that the search itself produced.

**3. The Deflated Sharpe Ratio** is the PSR evaluated at that benchmark::

    DSR = PSR(SR*)

It is a **probability in [0, 1]**, not a ratio. A DSR of 0.95 means: given how
many things were tried and how non-normal the returns are, there is a 95%
probability the true Sharpe is above what pure selection would have produced.

Units, stated because getting this wrong is the commonest error
---------------------------------------------------------------

``SR``, ``SR*``, ``g3``, ``g4`` and ``V`` are all **per-observation**, never
annualised. ``T`` is the number of observations. This project's returns are
monthly, so ``SR`` here is the monthly Sharpe and ``T`` is a count of months.
Feeding an annualised Sharpe into this formula with a monthly ``T`` inflates the
result by roughly ``sqrt(12)`` in the numerator, which is not a subtle error but
is an invisible one.

Autocorrelation is **not** corrected for. The formula assumes returns are
independent and identically distributed. Monthly returns from a monthly
rebalance are closer to that than daily returns would be, but the assumption is
an assumption and is stated in every report rather than assumed away. If a
strategy's monthly returns turn out to be materially autocorrelated, the
effective ``T`` is smaller than the count of months and this implementation will
overstate the DSR. Nothing here detects that; the report says so.

What this is not
----------------

The R package ``braverock/quantstrat`` computes exactly the expression above in
``.deflatedSharpe`` and then reports, in a column named ``deflated.Sharpe``, the
product ``SR * Z[...]``. That product is a rescaled Sharpe ratio, not a
probability, and it is not the quantity the paper defines as the DSR. This
module implements the probability. The R implementation's intermediate
expression is what
:func:`~sextant.engine.statistics.dsr.deflated_sharpe_ratio` was cross-checked
against, and the divergence at the last line is deliberate and asserted in
``tests/unit/test_deflated_sharpe.py``.

The one-trial case
------------------

The Gumbel expression is asymptotic and breaks at ``K = 1``: ``Z^-1[1 - 1/1]``
is ``Z^-1[0]``, negative infinity. But the answer at ``K = 1`` is not
ill-defined, it is obvious - the expected maximum of a single draw is the
expected value of that draw, which is zero under the null. So ``K = 1`` is
special-cased to ``SR* = 0``, at which point the DSR degenerates into the PSR
against a zero benchmark. That is the honest reading and it is what the code
does, rather than raising or returning a nan for the one case that will occur
first in this project's life.

Pure computation. No I/O, no clock, no money: floats only, on the far side of
the boundary in ``engine.statistics.boundary``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.stats import norm

from sextant.domain.errors import SextantError
from sextant.engine.statistics.metrics import PerformanceStatistics

#: The Euler-Mascheroni constant, to the precision the paper states it.
EULER_MASCHERONI = 0.5772156649

#: The formula's own name for itself, carried into every run manifest so that a
#: later reader knows which of several published expressions produced a number.
VARIANT = "bailey-lopez-de-prado-2014-ssrn-2460551"


class UndefinedDeflatedSharpe(SextantError):
    """The Deflated Sharpe Ratio cannot be computed from these inputs."""


@dataclass(frozen=True, slots=True)
class DeflatedSharpeResult:
    """Everything that went into one DSR, so the number can be audited.

    The inputs are carried rather than discarded because the DSR is a single
    number that four separate assumptions feed into, and a report showing only
    the output invites exactly the argument this project exists to avoid.
    """

    variant: str
    observed_sharpe_per_period: float
    observed_sharpe_annualised: float
    observations: int
    skewness: float
    kurtosis: float
    trials: int
    trial_sharpe_variance: float
    expected_maximum_sharpe_per_period: float
    probabilistic_sharpe_ratio: float
    """PSR against a zero benchmark: the same statistic before deflation."""
    deflated_sharpe_ratio: float
    """The DSR proper: a probability in [0, 1]."""
    autocorrelation_corrected: bool = False
    """Always False. Carried so a report cannot omit the fact."""


def expected_maximum_sharpe(trials: int, trial_sharpe_variance: float) -> float:
    """The Sharpe ratio ``trials`` independent worthless strategies would produce.

    Per-observation units, matching ``trial_sharpe_variance``. See the module
    docstring for the ``trials == 1`` special case.
    """
    if trials < 1:
        raise UndefinedDeflatedSharpe(f"trials must be at least 1, got {trials}")
    if trial_sharpe_variance < 0:
        raise UndefinedDeflatedSharpe(
            f"trial_sharpe_variance must not be negative, got {trial_sharpe_variance}"
        )
    if trials == 1:
        return 0.0
    count = float(trials)
    gumbel = (1.0 - EULER_MASCHERONI) * float(norm.ppf(1.0 - 1.0 / count)) + (
        EULER_MASCHERONI * float(norm.ppf(1.0 - 1.0 / (count * math.e)))
    )
    return float(np.sqrt(trial_sharpe_variance)) * gumbel


def _variance_term(*, sharpe_per_period: float, skewness: float, kurtosis: float) -> float:
    """``1 - g3*SR + (g4-1)/4*SR^2``: the variance of the Sharpe estimate, corrected.

    The one expression behind both the PSR and the standard error below, held in one
    place so the two can never disagree about what non-normality does to the estimate.
    It grows with fat tails and with negative skew, and shrinks with positive skew.
    """
    term = 1.0 - skewness * sharpe_per_period + (kurtosis - 1.0) / 4.0 * sharpe_per_period**2
    if term <= 0.0:
        raise UndefinedDeflatedSharpe(
            "The studentising term is not positive "
            f"(1 - g3*SR + (g4-1)/4*SR^2 = {term}). That combination of "
            "skewness, kurtosis and Sharpe is outside the range the estimator is "
            "defined on, and no probability can be reported for it."
        )
    return term


def corrected_sharpe_standard_error(
    *, sharpe_per_period: float, observations: int, skewness: float, kurtosis: float
) -> float:
    """The standard error of a Sharpe estimate, per observation, corrected.

    ``sqrt((1 - g3*SR + (g4-1)/4*SR^2) / (T - 1))``. The plain
    :attr:`~sextant.engine.statistics.metrics.PerformanceStatistics.sharpe_standard_error`
    assumes normal returns and is a *floor* on the uncertainty by its own docstring;
    this one uses the third and fourth moments the series actually has, and on a
    fat-tailed negatively skewed series it is the larger of the two.

    It exists because a threshold stated in units of the estimate's own uncertainty is
    only as honest as the uncertainty it is stated in.
    """
    if observations < 2:
        raise UndefinedDeflatedSharpe(
            f"A standard error needs at least two observations, got {observations}."
        )
    term = _variance_term(sharpe_per_period=sharpe_per_period, skewness=skewness, kurtosis=kurtosis)
    return math.sqrt(term / (float(observations) - 1.0))


def probabilistic_sharpe_ratio(
    *,
    sharpe_per_period: float,
    benchmark_sharpe_per_period: float,
    observations: int,
    skewness: float,
    kurtosis: float,
) -> float:
    """``Z`` of the studentised excess Sharpe. All inputs per-observation."""
    if observations < 2:
        raise UndefinedDeflatedSharpe(
            f"The PSR needs at least two observations, got {observations}."
        )
    variance_term = _variance_term(
        sharpe_per_period=sharpe_per_period, skewness=skewness, kurtosis=kurtosis
    )
    numerator = (sharpe_per_period - benchmark_sharpe_per_period) * math.sqrt(
        float(observations) - 1.0
    )
    return float(norm.cdf(numerator / math.sqrt(variance_term)))


def deflated_sharpe_ratio(
    statistics: PerformanceStatistics,
    *,
    trials: int,
    trial_sharpe_variance: float,
) -> DeflatedSharpeResult:
    """Deflate an observed Sharpe by what ``trials`` searches would have found anyway.

    ``trial_sharpe_variance`` is the variance, in per-observation units, of the
    Sharpe ratios produced across the trials. Where a real distribution of trial
    Sharpes exists - and in this project it does, because the random-selection
    null produces thousands of them - it should be measured from that rather
    than assumed.
    """
    benchmark = expected_maximum_sharpe(trials, trial_sharpe_variance)
    undeflated = probabilistic_sharpe_ratio(
        sharpe_per_period=statistics.sharpe_per_period,
        benchmark_sharpe_per_period=0.0,
        observations=statistics.observations,
        skewness=statistics.skewness,
        kurtosis=statistics.kurtosis,
    )
    deflated = probabilistic_sharpe_ratio(
        sharpe_per_period=statistics.sharpe_per_period,
        benchmark_sharpe_per_period=benchmark,
        observations=statistics.observations,
        skewness=statistics.skewness,
        kurtosis=statistics.kurtosis,
    )
    return DeflatedSharpeResult(
        variant=VARIANT,
        observed_sharpe_per_period=statistics.sharpe_per_period,
        observed_sharpe_annualised=statistics.sharpe_annualised,
        observations=statistics.observations,
        skewness=statistics.skewness,
        kurtosis=statistics.kurtosis,
        trials=trials,
        trial_sharpe_variance=trial_sharpe_variance,
        expected_maximum_sharpe_per_period=benchmark,
        probabilistic_sharpe_ratio=undeflated,
        deflated_sharpe_ratio=deflated,
    )
