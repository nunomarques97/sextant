"""The Deflated Sharpe Ratio, checked against values derived outside this code.

A passing test suite proves an implementation is self-consistent. It does not
prove the implementation computes the quantity the literature calls the Deflated
Sharpe Ratio, and that is the failure mode worth guarding against here: a DSR
that is subtly the wrong statistic would gate every strategy this project ever
writes, and nothing downstream could detect it.

So the reference values below are derived two ways, neither of which is this
module.

**Source 1 - the paper.** Bailey and Lopez de Prado, SSRN 2460551. The
expressions are transcribed in
:mod:`sextant.engine.statistics.dsr`'s docstring and the algebra below is
carried out by hand from them.

**Source 2 - an independent implementation.** ``braverock/quantstrat``'s
``R/deflated.Sharpe.R``. Its ``.deflatedSharpe`` computes::

    emc < -0.5772156649
    maxZ < -(1 - emc) * qnorm(1 - 1 / nTrials) + emc * qnorm(1 - 1 / nTrials * exp(-1))
    sr0 < -sqrt(varTrials * 1 / periodsInYear) * maxZ
    num < -(sharpe / sqrt(periodsInYear) - sr0) * sqrt(numPeriods - 1)
    den < -sqrt(
        1 - skew * sharpe / sqrt(periodsInYear) + (kurt - 1) / 4 * (sharpe / sqrt(periodsInYear))
        ^ 2
    )
    pnorm(num / den)

which is the same expression in annualised clothing: it de-annualises the Sharpe
and the trial variance before applying it. This module works in per-observation
units throughout, so the two agree once the units are matched, and
``test_the_r_implementations_intermediate_expression_is_reproduced`` reproduces
its arithmetic line for line and asserts the agreement.

**Where they diverge, deliberately.** The R function's final line returns
``sharpe * pnorm(...)``, a rescaled Sharpe ratio, under the column name
``deflated.Sharpe``. That product is not the quantity the paper defines. This
implementation returns the probability, and
``test_the_reported_quantity_is_the_probability_not_the_rescaled_sharpe`` pins
the difference so nobody reconciles the two by changing ours.

Tolerance: 1e-12 on every reference case. These are closed-form expressions over
double-precision arithmetic; anything looser would let a real error through, and
anything tighter would be asserting on the last bit of a normal CDF.
"""

from __future__ import annotations

import math
from decimal import Decimal

import pytest
from scipy.stats import norm

from sextant.engine.statistics.boundary import returns_as_series
from sextant.engine.statistics.dsr import (
    EULER_MASCHERONI,
    VARIANT,
    UndefinedDeflatedSharpe,
    deflated_sharpe_ratio,
    expected_maximum_sharpe,
    probabilistic_sharpe_ratio,
)
from sextant.engine.statistics.metrics import PerformanceStatistics, summarise

TOLERANCE = 1e-12


def statistics(
    *,
    sharpe_per_period: float,
    observations: int,
    skewness: float,
    kurtosis: float,
) -> PerformanceStatistics:
    """A hand-built summary, so a reference case states its own inputs."""
    return PerformanceStatistics(
        observations=observations,
        mean_return=sharpe_per_period,
        volatility=1.0,
        sharpe_per_period=sharpe_per_period,
        sharpe_annualised=sharpe_per_period * math.sqrt(12.0),
        sortino_annualised=0.0,
        skewness=skewness,
        kurtosis=kurtosis,
        annualisation=12,
    )


# -- reference case 1: normal returns, one trial, hand-derived ----------------


def test_reference_case_one_normal_returns_single_trial() -> None:
    """With one trial there is no selection, so the DSR collapses to the PSR.

    Hand derivation. ``SR* = 0`` because the expected maximum of a single draw
    is the draw's own mean. With ``g3 = 0`` and ``g4 = 3`` the studentising term
    is ``1 - 0 + (3-1)/4 * SR^2 = 1 + SR^2/2``. At ``SR = 0.20`` over 30
    observations::

        denominator = sqrt(1 + 0.04/2)   = sqrt(1.02)
        numerator   = 0.20 * sqrt(29)
        DSR         = Z[0.20*sqrt(29)/sqrt(1.02)]

    which is ``Z[1.066335...]``.
    """
    result = deflated_sharpe_ratio(
        statistics(sharpe_per_period=0.20, observations=30, skewness=0.0, kurtosis=3.0),
        trials=1,
        trial_sharpe_variance=0.05,
    )
    expected_argument = 0.20 * math.sqrt(29.0) / math.sqrt(1.02)
    assert result.expected_maximum_sharpe_per_period == 0.0
    assert result.deflated_sharpe_ratio == pytest.approx(
        float(norm.cdf(expected_argument)), abs=TOLERANCE
    )
    assert result.deflated_sharpe_ratio == pytest.approx(
        result.probabilistic_sharpe_ratio, abs=TOLERANCE
    )
    assert result.variant == VARIANT


# -- reference case 2: the Gumbel benchmark, hand-derived ---------------------


def test_reference_case_two_the_expected_maximum_over_ten_trials() -> None:
    """``SR*`` for ten trials, computed from the paper's expression by hand.

    ``V = 0.04`` so ``sqrt(V) = 0.2``. With ``K = 10``::

        Z^-1[1 - 1/10]        = Z^-1[0.9]        = 1.2815515655446004
        Z^-1[1 - 1/(10 * e)]  = Z^-1[0.96321...] = 1.7909061...
        SR* = 0.2 * [(1 - 0.5772156649)*1.2815515655 + 0.5772156649*1.7909061]

    The two quantiles are standard normal values; the second is written out
    through ``norm.ppf`` rather than as a literal because its argument
    ``1 - 1/(10e)`` is itself irrational.
    """
    lower = float(norm.ppf(0.9))
    upper = float(norm.ppf(1.0 - 1.0 / (10.0 * math.e)))
    expected = 0.2 * ((1.0 - EULER_MASCHERONI) * lower + EULER_MASCHERONI * upper)

    assert lower == pytest.approx(1.2815515655446004, abs=1e-12)
    assert expected_maximum_sharpe(10, 0.04) == pytest.approx(expected, abs=TOLERANCE)
    assert expected_maximum_sharpe(10, 0.04) == pytest.approx(0.31491966, abs=1e-8)


def test_more_trials_raise_the_bar_and_more_dispersion_raises_it_further() -> None:
    """Monotone in both arguments, which is the whole point of the statistic."""
    assert expected_maximum_sharpe(100, 0.04) > expected_maximum_sharpe(10, 0.04)
    assert expected_maximum_sharpe(10, 0.09) > expected_maximum_sharpe(10, 0.04)


# -- reference case 3: non-normal returns, twenty trials ----------------------


def test_reference_case_three_skewed_and_fat_tailed_over_twenty_trials() -> None:
    """The full expression with every term active, derived independently.

    ``SR = 0.25`` per period over 24 observations, ``g3 = -0.6``, ``g4 = 5.0``,
    ``K = 20`` trials whose Sharpes had variance ``0.0025``::

        SR*  = 0.05 * [(1-y)*Z^-1[0.95] + y*Z^-1[1 - 1/(20e)]]
             = 0.05 * 1.90070795... = 0.09503539...
        den  = sqrt(1 - (-0.6)(0.25) + (5-1)/4 * 0.25^2)
             = sqrt(1 + 0.15 + 0.0625) = sqrt(1.2125)
        num  = (0.25 - SR*) * sqrt(23)
        DSR  = Z[num/den]

    Negative skew raises the studentising term. With the observed Sharpe above
    the benchmark - as it is here - that lowers the DSR, which is the correction
    doing its job: a strategy whose good months are many and small and whose bad
    months are few and large has a less trustworthy Sharpe than a symmetric one.
    """
    benchmark = 0.05 * (
        (1.0 - EULER_MASCHERONI) * float(norm.ppf(0.95))
        + EULER_MASCHERONI * float(norm.ppf(1.0 - 1.0 / (20.0 * math.e)))
    )
    denominator = math.sqrt(1.0 + 0.6 * 0.25 + (5.0 - 1.0) / 4.0 * 0.25**2)
    assert denominator == pytest.approx(math.sqrt(1.2125), abs=TOLERANCE)
    numerator = (0.25 - benchmark) * math.sqrt(23.0)
    expected = float(norm.cdf(numerator / denominator))

    result = deflated_sharpe_ratio(
        statistics(sharpe_per_period=0.25, observations=24, skewness=-0.6, kurtosis=5.0),
        trials=20,
        trial_sharpe_variance=0.0025,
    )
    assert result.expected_maximum_sharpe_per_period == pytest.approx(benchmark, abs=TOLERANCE)
    assert result.expected_maximum_sharpe_per_period == pytest.approx(0.09503540, abs=1e-8)
    assert result.deflated_sharpe_ratio == pytest.approx(expected, abs=TOLERANCE)


def test_negative_skew_lowers_the_deflated_sharpe_and_positive_skew_raises_it() -> None:
    """The non-normality correction has the sign the paper says it has.

    Stated for the case that matters: the observed Sharpe above the benchmark.
    Below it, the numerator is negative and a larger studentising term moves the
    result the other way, which is arithmetic rather than a claim about skew.
    """
    negative = deflated_sharpe_ratio(
        statistics(sharpe_per_period=0.25, observations=24, skewness=-0.6, kurtosis=5.0),
        trials=20,
        trial_sharpe_variance=0.0025,
    )
    positive = deflated_sharpe_ratio(
        statistics(sharpe_per_period=0.25, observations=24, skewness=0.6, kurtosis=5.0),
        trials=20,
        trial_sharpe_variance=0.0025,
    )
    thin_tailed = deflated_sharpe_ratio(
        statistics(sharpe_per_period=0.25, observations=24, skewness=-0.6, kurtosis=3.0),
        trials=20,
        trial_sharpe_variance=0.0025,
    )
    assert negative.deflated_sharpe_ratio < positive.deflated_sharpe_ratio
    assert negative.deflated_sharpe_ratio < thin_tailed.deflated_sharpe_ratio


# -- cross-check against the independent implementation -----------------------


def r_deflated_sharpe(
    *,
    sharpe_annualised: float,
    trials: int,
    trial_variance_annualised: float,
    skew: float,
    kurt: float,
    periods: int,
    periods_in_year: int = 12,
) -> tuple[float, float]:
    """``.deflatedSharpe`` from ``braverock/quantstrat``, transcribed.

    Returns ``(pnorm(num/den), maxZ)``. The R function's own return value for
    the column it calls ``deflated.Sharpe`` is ``sharpe * pnorm(num/den)``,
    which is deliberately *not* what this returns - see the module docstring.
    """
    emc = 0.5772156649
    max_z = (1.0 - emc) * float(norm.ppf(1.0 - 1.0 / trials)) + emc * float(
        norm.ppf(1.0 - 1.0 / trials * math.exp(-1.0))
    )
    sr0 = math.sqrt(trial_variance_annualised * 1.0 / periods_in_year) * max_z
    per_period = sharpe_annualised / math.sqrt(periods_in_year)
    numerator = (per_period - sr0) * math.sqrt(periods - 1.0)
    denominator = math.sqrt(1.0 - skew * per_period + (kurt - 1.0) / 4.0 * per_period**2)
    return float(norm.cdf(numerator / denominator)), max_z


@pytest.mark.parametrize(
    ("sharpe_annualised", "trials", "trial_variance_annualised", "skew", "kurt", "periods"),
    [
        (0.8660254037844386, 10, 0.48, 0.0, 3.0, 30),
        (1.5, 50, 1.2, -0.4, 6.0, 24),
        (0.3, 200, 0.6, 0.9, 4.5, 36),
    ],
)
def test_the_r_implementations_intermediate_expression_is_reproduced(
    sharpe_annualised: float,
    trials: int,
    trial_variance_annualised: float,
    skew: float,
    kurt: float,
    periods: int,
) -> None:
    """Same number, from an implementation this project did not write."""
    expected, _ = r_deflated_sharpe(
        sharpe_annualised=sharpe_annualised,
        trials=trials,
        trial_variance_annualised=trial_variance_annualised,
        skew=skew,
        kurt=kurt,
        periods=periods,
    )
    per_period = sharpe_annualised / math.sqrt(12.0)
    result = deflated_sharpe_ratio(
        statistics(
            sharpe_per_period=per_period,
            observations=periods,
            skewness=skew,
            kurtosis=kurt,
        ),
        trials=trials,
        trial_sharpe_variance=trial_variance_annualised / 12.0,
    )
    assert result.deflated_sharpe_ratio == pytest.approx(expected, abs=TOLERANCE)


def test_the_reported_quantity_is_the_probability_not_the_rescaled_sharpe() -> None:
    """The one place this implementation and the R package deliberately differ."""
    sharpe_annualised = 1.5
    probability, _ = r_deflated_sharpe(
        sharpe_annualised=sharpe_annualised,
        trials=50,
        trial_variance_annualised=1.2,
        skew=-0.4,
        kurt=6.0,
        periods=24,
    )
    r_column = sharpe_annualised * probability
    result = deflated_sharpe_ratio(
        statistics(
            sharpe_per_period=sharpe_annualised / math.sqrt(12.0),
            observations=24,
            skewness=-0.4,
            kurtosis=6.0,
        ),
        trials=50,
        trial_sharpe_variance=1.2 / 12.0,
    )
    assert result.deflated_sharpe_ratio == pytest.approx(probability, abs=TOLERANCE)
    assert result.deflated_sharpe_ratio != pytest.approx(r_column, abs=1e-6)
    assert 0.0 <= result.deflated_sharpe_ratio <= 1.0


# -- units, and the refusals --------------------------------------------------


def test_feeding_an_annualised_sharpe_would_change_the_answer_materially() -> None:
    """The commonest error with this formula, made visible rather than assumed away."""
    per_period = 0.25
    correct = deflated_sharpe_ratio(
        statistics(sharpe_per_period=per_period, observations=24, skewness=0.0, kurtosis=3.0),
        trials=20,
        trial_sharpe_variance=0.0025,
    )
    wrong = deflated_sharpe_ratio(
        statistics(
            sharpe_per_period=per_period * math.sqrt(12.0),
            observations=24,
            skewness=0.0,
            kurtosis=3.0,
        ),
        trials=20,
        trial_sharpe_variance=0.0025,
    )
    assert wrong.deflated_sharpe_ratio > correct.deflated_sharpe_ratio


def test_the_result_records_that_it_is_not_autocorrelation_corrected() -> None:
    """The assumption is carried in the result so a report cannot omit it."""
    result = deflated_sharpe_ratio(
        statistics(sharpe_per_period=0.2, observations=30, skewness=0.0, kurtosis=3.0),
        trials=5,
        trial_sharpe_variance=0.01,
    )
    assert result.autocorrelation_corrected is False


def test_zero_trials_and_negative_variance_are_refused() -> None:
    """Neither is a number this estimator has an answer for."""
    with pytest.raises(UndefinedDeflatedSharpe):
        expected_maximum_sharpe(0, 0.04)
    with pytest.raises(UndefinedDeflatedSharpe):
        expected_maximum_sharpe(10, -0.01)


def test_a_single_observation_has_no_probabilistic_sharpe() -> None:
    """``sqrt(T - 1)`` is zero at one observation and the statistic is empty."""
    with pytest.raises(UndefinedDeflatedSharpe):
        probabilistic_sharpe_ratio(
            sharpe_per_period=0.2,
            benchmark_sharpe_per_period=0.0,
            observations=1,
            skewness=0.0,
            kurtosis=3.0,
        )


def test_a_studentising_term_that_is_not_positive_is_refused() -> None:
    """Outside the range the estimator is defined on, no probability is reported."""
    with pytest.raises(UndefinedDeflatedSharpe):
        probabilistic_sharpe_ratio(
            sharpe_per_period=1.0,
            benchmark_sharpe_per_period=0.0,
            observations=30,
            skewness=8.0,
            kurtosis=1.0,
        )


# -- the statistics the DSR consumes ------------------------------------------


def test_summarise_reports_none_below_three_observations() -> None:
    """A Sharpe from two observations is arithmetic, not evidence."""
    assert summarise(returns_as_series([Decimal("0.01"), Decimal("-0.01")])) is None


def test_kurtosis_is_the_raw_fourth_moment_and_not_the_excess() -> None:
    """Three under normality. Mixing the conventions loses a factor of three."""
    values = [Decimal(str(value)) for value in (-2, -1, -1, 0, 0, 0, 1, 1, 2)]
    stats = summarise(returns_as_series(values))
    assert stats is not None
    assert stats.kurtosis > 1.5
    assert stats.skewness == pytest.approx(0.0, abs=1e-12)


def test_the_sharpe_standard_error_is_reported_beside_the_sharpe() -> None:
    """Over a short window this number matters more than the ratio itself."""
    values = [Decimal("0.01")] * 5 + [Decimal("-0.02")] * 5 + [Decimal("0.03")] * 14
    stats = summarise(returns_as_series(values))
    assert stats is not None
    assert stats.observations == 24
    assert stats.sharpe_standard_error > 0.0
    expected = math.sqrt((1.0 + 0.5 * stats.sharpe_per_period**2) / 24.0) * math.sqrt(12.0)
    assert stats.sharpe_standard_error == pytest.approx(expected, abs=TOLERANCE)


def test_a_flat_return_series_has_a_defined_sharpe_of_zero() -> None:
    """Zero volatility is not an error; it is an account that did nothing."""
    stats = summarise(returns_as_series([Decimal("0.01")] * 10))
    assert stats is not None
    assert stats.sharpe_annualised == 0.0
    assert stats.sortino_annualised == 0.0
