"""Decay, and the difference between rejecting and running out of months.

The three statistics registered in `docs/PRE-REGISTRATION-006-F1.md` section
16.1, and the one distinction that decides whether a decayed edge gets reported
as a decayed edge or as an open question.

The tests that matter most here are the ones about **not** concluding: a
negative point estimate whose interval spans zero is not decay, and a positive
recent window that misses its null by less than the noise has not rejected
anything. Both are easy to get wrong in the flattering direction, and both would
change what the Sponsor is told.
"""

from __future__ import annotations

import numpy as np
import pytest

from sextant.engine.statistics.boundary import ReturnSeries
from sextant.engine.statistics.persistence import (
    MINIMUM_MONTHS_FOR_A_YEAR,
    RECENT_WINDOW_MONTHS,
    SubWindowVerdict,
    sub_window,
    trend_of,
    two_year_comparison,
    year_slices,
)

MONTHS_PER_YEAR = 12


def series(values: list[float]) -> ReturnSeries:
    """A monthly return series at the annualisation every result here uses."""
    return ReturnSeries(
        values=np.asarray(values, dtype=np.float64), periods_per_year=MONTHS_PER_YEAR
    )


def calm(months: int = 24, mean: float = 0.01, spread: float = 0.03) -> list[float]:
    """A series with a known mean and a known spread, and a thin tail.

    Alternating rather than random, so that every statistic derived from it is
    the same on every machine and a threshold in a test is a threshold and not a
    seed.
    """
    return [mean + spread if index % 2 == 0 else mean - spread for index in range(months)]


def fat(months: int = 24, mean: float = 0.01, spread: float = 0.03) -> list[float]:
    """The same mean and the same sample spread as :func:`calm`, on a fat tail.

    Twenty of the months sit exactly on the mean and four are large symmetric
    excursions, scaled so the sample variance matches ``calm`` exactly. Mean,
    standard deviation and therefore Sharpe are identical between the two; the
    only thing that differs is the fourth moment, which is what the test is for.
    """
    if months != 24:
        raise ValueError("the matched-variance construction is written for 24 months")
    excursion = spread * float(np.sqrt(6.0))
    return [mean] * 20 + [mean + excursion, mean + excursion, mean - excursion, mean - excursion]


def years_for(counts: dict[int, int]) -> list[int]:
    """A year label per month, from a mapping of year to month count."""
    labels: list[int] = []
    for year in sorted(counts):
        labels.extend([year] * counts[year])
    return labels


# ---------------------------------------------------------------------------
# The per-year split
# ---------------------------------------------------------------------------


def test_a_year_with_too_few_months_reports_its_return_and_no_sharpe() -> None:
    """A statistic that cannot be estimated is absent, never computed anyway."""
    labels = years_for({2022: 12, 2023: 3})
    returns = [0.01] * 12 + [0.02] * 3
    sliced = year_slices(labels, returns)
    full, short = sliced[0], sliced[1]
    assert full.months == 12
    assert full.is_conclusive is True
    assert short.months == 3
    assert short.is_conclusive is False
    assert short.annualised_sharpe is None
    assert short.sharpe_standard_error is None
    assert short.compounded_return == pytest.approx(1.02**3 - 1)


def test_the_boundary_month_count_is_the_registered_one() -> None:
    """Six months, per the pre-registration, and not a number chosen later."""
    labels = years_for({2022: MINIMUM_MONTHS_FOR_A_YEAR})
    sliced = year_slices(labels, [0.01] * MINIMUM_MONTHS_FOR_A_YEAR)
    assert sliced[0].is_conclusive is True
    labels = years_for({2022: MINIMUM_MONTHS_FOR_A_YEAR - 1})
    sliced = year_slices(labels, [0.01] * (MINIMUM_MONTHS_FOR_A_YEAR - 1))
    assert sliced[0].is_conclusive is False


def test_a_label_per_month_is_required() -> None:
    """Mismatched lengths are a defect in the caller, not a thing to align."""
    with pytest.raises(ValueError, match="a label per"):
        year_slices([2022, 2022], [0.01])


# ---------------------------------------------------------------------------
# The trend, and what may be called decay
# ---------------------------------------------------------------------------


def test_a_clean_downward_ramp_is_measured_decay() -> None:
    """A series that falls steadily and unambiguously must say so."""
    values = [0.05 - 0.002 * month for month in range(48)]
    trend = trend_of(series(values))
    assert trend is not None
    assert trend.slope_per_month < 0
    assert trend.excludes_zero is True
    assert trend.is_decay is True


def test_a_noisy_series_with_a_negative_point_estimate_is_not_decay() -> None:
    """The test that stops a story being told from a slope that means nothing."""
    generator = np.random.default_rng(12345)
    values = list(generator.normal(0.01, 0.08, 48) - 0.00002 * np.arange(48))
    trend = trend_of(series(values))
    assert trend is not None
    assert trend.excludes_zero is False
    assert trend.is_decay is False


def test_a_flat_series_has_a_slope_of_zero_and_is_not_decay() -> None:
    """Nothing changed, so nothing decayed."""
    trend = trend_of(series([0.01] * 36))
    assert trend is not None
    assert trend.slope_per_month == pytest.approx(0.0, abs=1e-12)
    assert trend.is_decay is False


def test_a_series_too_short_for_a_slope_returns_none() -> None:
    """Two points make a line and not a trend."""
    assert trend_of(series([0.01, 0.02])) is None


# ---------------------------------------------------------------------------
# Declared expectation D1
# ---------------------------------------------------------------------------


def test_d1_is_confirmed_when_the_recent_years_are_clearly_worse() -> None:
    """The Sponsor's prediction, written as arithmetic and able to come true."""
    labels = years_for({2021: 12, 2022: 12, 2023: 12, 2024: 12})
    returns = [0.04] * 24 + [0.001] * 24
    sliced = year_slices(labels, returns)
    comparison = two_year_comparison(sliced, labels, returns)
    assert comparison is not None
    assert comparison.early_years == (2021, 2022)
    assert comparison.recent_years == (2023, 2024)
    assert comparison.difference < 0
    assert comparison.confirms_d1 is True
    assert comparison.refutes_d1 is False


def test_d1_is_refuted_when_the_recent_years_are_clearly_better() -> None:
    """A prediction that cannot be refuted is not a prediction."""
    labels = years_for({2021: 12, 2022: 12, 2023: 12, 2024: 12})
    returns = [0.001] * 24 + [0.04] * 24
    sliced = year_slices(labels, returns)
    comparison = two_year_comparison(sliced, labels, returns)
    assert comparison is not None
    assert comparison.difference > 0
    assert comparison.refutes_d1 is True
    assert comparison.confirms_d1 is False


def test_d1_is_neither_when_the_difference_is_inside_the_noise() -> None:
    """A drop the sample cannot resolve confirms nothing."""
    generator = np.random.default_rng(999)
    labels = years_for({2021: 12, 2022: 12, 2023: 12, 2024: 12})
    returns = list(generator.normal(0.01, 0.09, 48))
    sliced = year_slices(labels, returns)
    comparison = two_year_comparison(sliced, labels, returns)
    assert comparison is not None
    assert comparison.confirms_d1 is False
    assert comparison.refutes_d1 is False


def test_d1_cannot_be_evaluated_without_four_complete_years() -> None:
    """Rather than comparing whatever happens to be available."""
    labels = years_for({2022: 12, 2023: 12, 2024: 12})
    returns = [0.01] * 36
    sliced = year_slices(labels, returns)
    assert two_year_comparison(sliced, labels, returns) is None


# ---------------------------------------------------------------------------
# The recent window: the distinction the whole amendment turns on
# ---------------------------------------------------------------------------


def test_a_recent_window_that_earns_and_beats_its_null_clears() -> None:
    """The only outcome that lets a variant be reported as a persisting edge."""
    window = sub_window(calm(), null_sharpes=[0.5] * 500)
    assert window is not None
    assert window.compounded_return > 0
    assert window.annualised_sharpe > window.null_percentile_95
    assert window.verdict is SubWindowVerdict.CLEARS


def test_a_recent_window_that_lost_money_rejects() -> None:
    """A realised loss is a fact about an account, not an open question.

    This is the case the amendment exists for: a variant that earns across the
    full window and loses across the recent one is a decayed edge, and must not
    be reported as verdict (A).
    """
    window = sub_window([-0.01] * 40, null_sharpes=[-5.0] * 500)
    assert window is not None
    assert window.compounded_return < 0
    assert window.verdict is SubWindowVerdict.REJECTS


def test_a_losing_window_rejects_even_when_it_beats_a_deeply_negative_null() -> None:
    """SEXTANT-005's criterion-1 misfire, refused here by construction.

    Losing less than chance lost is not an edge. The null percentile is set far
    below the variant's own Sharpe so that the *only* thing that can produce a
    rejection is the sign of the return, which is exactly the condition
    SEXTANT-005's criterion 1 was missing.
    """
    window = sub_window([-0.005] * 40, null_sharpes=[-9.0] * 500)
    assert window is not None
    assert window.annualised_sharpe > window.null_percentile_95
    assert window.verdict is SubWindowVerdict.REJECTS


def test_a_positive_window_that_misses_its_null_narrowly_is_unresolved() -> None:
    """Ran out of months. Reporting this as a rejection would be the softening."""
    values = calm()
    bar = sub_window(values, null_sharpes=[0.0] * 10)
    assert bar is not None
    window = sub_window(values, null_sharpes=[bar.annualised_sharpe + 0.5] * 500)
    assert window is not None
    assert window.compounded_return > 0
    assert 0 < window.gap_to_null < window.detectable_effect
    assert window.verdict is SubWindowVerdict.UNRESOLVED


def test_a_positive_window_that_misses_its_null_by_a_mile_rejects() -> None:
    """Far enough short that the sample size is not the explanation."""
    window = sub_window([0.0005] * 40, null_sharpes=[50.0] * 500)
    assert window is not None
    assert window.compounded_return > 0
    assert window.gap_to_null > window.detectable_effect
    assert window.verdict is SubWindowVerdict.REJECTS


def test_the_window_uses_only_its_trailing_months() -> None:
    """The recent window is recent. Early months must not leak into it."""
    values = [0.5] * 40 + [-0.01] * RECENT_WINDOW_MONTHS
    window = sub_window(values, null_sharpes=[0.0] * 100)
    assert window is not None
    assert window.months == RECENT_WINDOW_MONTHS
    assert window.compounded_return < 0


def test_a_series_shorter_than_the_window_is_not_scored_over_it() -> None:
    """A 24-month test on 19 months is a different test and is refused."""
    assert sub_window([0.01] * (RECENT_WINDOW_MONTHS - 5), null_sharpes=[0.0]) is None


def test_the_detectable_effect_widens_with_a_fat_tail() -> None:
    """The skewness and kurtosis correction, in the direction that matters.

    A fat-tailed series is harder to draw a conclusion from, so its detectable
    effect must be larger. Using the uncorrected standard error would make the
    bar for calling a rejection *lower* on exactly the distributions where a
    Sharpe estimate is least trustworthy - and SEXTANT-005 measured skewness
    2.46 and kurtosis 10.36 on this asset class.
    """
    thin = sub_window(calm(), null_sharpes=[0.0] * 10)
    heavy = sub_window(fat(), null_sharpes=[0.0] * 10)
    assert thin is not None
    assert heavy is not None
    # Same mean, same spread, same Sharpe: only the fourth moment differs.
    assert heavy.annualised_sharpe == pytest.approx(thin.annualised_sharpe, rel=1e-9)
    assert heavy.detectable_effect > thin.detectable_effect
