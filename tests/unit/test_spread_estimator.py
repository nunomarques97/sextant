"""The two spread estimators of rule E1, and what they can and cannot resolve.

The important tests here are the two simulations. An estimator that returns implausible
figures on real data is either wrong or is being asked an impossible question, and the
only way to tell the two apart is to feed it a market whose spread is known. So this
module builds one: an efficient log price on a random walk, trades printing alternately
at the bid and the ask, and daily high, low and close taken from the prints.

At a spread of 100 and of 50 basis points both estimators recover it. As the spread falls
relative to the volatility, both break down - Abdi-Ranaldo into a floored zero and
Corwin-Schultz into a large number unrelated to anything. That is the estimators'
documented regime of validity, not a defect in this code, and these tests are what makes
that claim checkable rather than asserted.
"""

from __future__ import annotations

import math
import random
from decimal import Decimal

import pytest

from sextant.app.spike_006_f1 import ESTIMATOR_RANK_FLOOR
from sextant.engine.execution.spread_estimator import (
    ABDI_RANALDO,
    BASIS_POINTS,
    CORWIN_SCHULTZ,
    DailyRange,
    UnusableDailyRange,
    abdi_ranaldo,
    corwin_schultz,
    two_day_terms,
)
from sextant.engine.statistics.rank import MINIMUM_PAIRS, ranks, spearman_rank_correlation

#: Trades per simulated day. Enough that the high and the low are both near the
#: efficient range's edges, which is the condition both estimators are derived under.
TRADES_PER_DAY = 2000

#: The seed for every simulation here. One seed, fixed, so a failure is a real change.
SEED = 7


def simulate(
    *, spread_bps: float, daily_volatility_pct: float, days: int = 31, seed: int = SEED
) -> tuple[DailyRange, ...]:
    """A quote-driven market with a known spread, as daily high, low and close.

    The efficient log price is a random walk scaled so that a day's standard deviation is
    ``daily_volatility_pct``. Each print lands at the efficient price times one plus or
    minus half the spread, with equal probability, which is the bid-ask bounce both
    estimators are built to detect.
    """
    rng = random.Random(seed)
    half = spread_bps / float(BASIS_POINTS) / 2.0
    step = (daily_volatility_pct / 100.0) / math.sqrt(TRADES_PER_DAY)
    log_price = 0.0
    out: list[DailyRange] = []
    for _ in range(days):
        prints: list[float] = []
        for _ in range(TRADES_PER_DAY):
            log_price += rng.gauss(0.0, step)
            side = 1.0 if rng.random() < 0.5 else -1.0
            prints.append(math.exp(log_price) * (1.0 + side * half))
        out.append(DailyRange(high=max(prints), low=min(prints), close=prints[-1]))
    return tuple(out)


# ---------------------------------------------------------------------------
# The two that matter: does the implementation recover a spread it is given?
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spread_bps", [100.0, 50.0])
def test_both_estimators_recover_a_known_spread_within_a_factor_of_two(
    spread_bps: float,
) -> None:
    """A quote-driven market at 100 and at 50 bps, against 1 per cent daily volatility.

    Within a factor of two is the bar, deliberately: these are estimators over a month of
    daily bars and neither paper claims better. What the test establishes is that the
    arithmetic is right, so that a wild figure on real data is a statement about the data.
    """
    days = simulate(spread_bps=spread_bps, daily_volatility_pct=1.0)
    for estimate in (abdi_ranaldo(days), corwin_schultz(days)):
        assert estimate is not None
        assert not estimate.floored
        ratio = estimate.basis_points / spread_bps
        assert 0.5 <= ratio <= 2.0, f"{estimate.estimator} returned {estimate.basis_points}"


def test_both_break_down_when_the_spread_is_small_against_the_volatility() -> None:
    """The regime real perpetuals sit in: a spread of one basis point against 4 per cent.

    Abdi-Ranaldo floors at zero, because the mean of its two-day terms comes out negative
    as often as positive once the noise dominates. Corwin-Schultz returns a large number
    with no relation to the spread it was given, which is the more dangerous of the two
    failures: it looks like an answer.
    """
    days = simulate(spread_bps=1.0, daily_volatility_pct=4.0)
    registered = abdi_ranaldo(days)
    comparison = corwin_schultz(days)
    assert registered is not None
    assert comparison is not None
    assert registered.floored
    assert registered.basis_points == 0.0
    assert comparison.basis_points > 50.0


def test_the_term_scatter_says_how_far_from_resolvable_it_is() -> None:
    """The ratio that decides the whole question, on a simulated one-basis-point market.

    Each two-day term estimates the squared spread plus noise. When the scatter of the
    terms is many orders of magnitude above the squared spread, no averaging window helps:
    the pair count needed grows as the square of that ratio.
    """
    days = simulate(spread_bps=1.0, daily_volatility_pct=4.0)
    terms = two_day_terms(days)
    scatter = math.sqrt(
        sum((term - sum(terms) / len(terms)) ** 2 for term in terms) / (len(terms) - 1)
    )
    squared = (1.0 / float(BASIS_POINTS)) ** 2
    assert (scatter / squared) ** 2 > 1e6


# ---------------------------------------------------------------------------
# The arithmetic, on inputs whose answer is known without a simulation
# ---------------------------------------------------------------------------


def test_a_flat_market_estimates_no_spread() -> None:
    """Every day identical and every close at the mid-range: nothing bounced."""
    days = tuple(DailyRange(high=100.0, low=100.0, close=100.0) for _ in range(10))
    registered = abdi_ranaldo(days)
    comparison = corwin_schultz(days)
    assert registered is not None
    assert comparison is not None
    assert registered.relative == 0.0
    assert comparison.relative == 0.0
    assert registered.floored
    assert registered.non_positive_terms == registered.pairs


def test_a_close_at_the_top_of_every_range_estimates_a_positive_spread() -> None:
    """The bounce the estimator is built for, in its cleanest form.

    Every close at its own high and every mid-range below it, so every two-day term is a
    product of two positive numbers. This is the sign convention pinned down: a positive
    mean is a spread, and the direction cannot silently invert.
    """
    days = tuple(DailyRange(high=101.0, low=99.0, close=101.0) for _ in range(10))
    estimate = abdi_ranaldo(days)
    assert estimate is not None
    assert not estimate.floored
    assert estimate.basis_points > 0.0
    assert estimate.non_positive_terms == 0


def test_a_single_day_estimates_nothing() -> None:
    """Both estimators are two-day estimators, so one day answers None."""
    days = (DailyRange(high=101.0, low=99.0, close=100.0),)
    assert abdi_ranaldo(days) is None
    assert corwin_schultz(days) is None


def test_the_pair_count_is_one_below_the_day_count() -> None:
    """Thirty-one days make thirty consecutive pairs, and the count is reported."""
    days = simulate(spread_bps=100.0, daily_volatility_pct=1.0, days=31)
    estimate = abdi_ranaldo(days)
    assert estimate is not None
    assert estimate.pairs == 30
    assert len(two_day_terms(days)) == 30


#: One narrow day, used as the first of a pair in the gap tests below.
FIRST_DAY = DailyRange(high=100.5, low=100.0, close=100.2)


@pytest.mark.parametrize("gap", [10.0, 50.0, 500.0])
def test_the_size_of_an_overnight_gap_does_not_reach_the_estimate(gap: float) -> None:
    """A pure gap is a level change, and the estimator must read none of it as a spread.

    The property the paper's adjustment exists for: three pairs whose second day is the
    same shape at three wildly different levels give the *same* estimate. Without the
    adjustment the two-day range would grow with the gap, gamma with it, and the estimate
    would fall towards zero as the gap widened.
    """
    baseline = corwin_schultz((FIRST_DAY, DailyRange(high=110.5, low=110.0, close=110.2)))
    shifted = corwin_schultz(
        (FIRST_DAY, DailyRange(high=100.5 + gap, low=100.0 + gap, close=100.2 + gap))
    )
    assert baseline is not None
    assert shifted is not None
    assert shifted.relative == pytest.approx(baseline.relative, rel=1e-9)


@pytest.mark.parametrize("gap", [10.0, 50.0])
def test_a_gap_downward_is_removed_the_same_way(gap: float) -> None:
    """The other direction, because an adjustment applied on one side only is a bias."""
    up = corwin_schultz(
        (FIRST_DAY, DailyRange(high=100.5 + gap, low=100.0 + gap, close=100.2 + gap))
    )
    down = corwin_schultz(
        (FIRST_DAY, DailyRange(high=100.5 - gap, low=100.0 - gap, close=100.2 - gap))
    )
    assert up is not None
    assert down is not None
    assert down.relative == pytest.approx(up.relative, rel=1e-9)


def test_the_adjustment_moves_a_days_level_and_not_its_width() -> None:
    """A gap-adjusted day keeps its own range, and its close keeps its place inside it.

    Stated as a test because the adjustment is the one discretionary-looking step in
    either estimator, and a version of it that widened or narrowed the second day would
    change every figure while still looking like the paper.
    """
    adjacent = corwin_schultz((FIRST_DAY, DailyRange(high=101.0, low=100.5, close=100.7)))
    gapped = corwin_schultz((FIRST_DAY, DailyRange(high=110.5, low=110.0, close=110.2)))
    assert adjacent is not None
    assert gapped is not None
    assert gapped.relative == pytest.approx(adjacent.relative, rel=1e-12)


def test_basis_points_are_ten_thousand_times_the_proportion() -> None:
    """One conversion, in one place, so a figure cannot be reported in the wrong unit."""
    days = simulate(spread_bps=100.0, daily_volatility_pct=1.0)
    estimate = abdi_ranaldo(days)
    assert estimate is not None
    assert estimate.basis_points == pytest.approx(estimate.relative * float(BASIS_POINTS))


def test_both_estimators_name_themselves_in_their_own_result() -> None:
    """A figure that cannot say what produced it is a figure nobody can audit."""
    days = simulate(spread_bps=100.0, daily_volatility_pct=1.0)
    registered = abdi_ranaldo(days)
    comparison = corwin_schultz(days)
    assert registered is not None
    assert comparison is not None
    assert registered.as_json()["estimator"] == ABDI_RANALDO
    assert comparison.as_json()["estimator"] == CORWIN_SCHULTZ


# ---------------------------------------------------------------------------
# A bad bar is named, never skipped
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("high", "low", "close"),
    [
        (0.0, 0.0, 0.0),
        (-1.0, -2.0, -1.5),
        (99.0, 100.0, 99.5),
        (101.0, 99.0, 102.0),
        (101.0, 99.0, 98.0),
    ],
)
def test_an_unusable_daily_bar_is_refused_at_construction(
    high: float, low: float, close: float
) -> None:
    """Non-positive prices, an inverted range, a close outside it. Each is a data defect."""
    with pytest.raises(UnusableDailyRange):
        DailyRange(high=high, low=low, close=close)


# ---------------------------------------------------------------------------
# The rank correlation rule E1's ordering clause is computed with
# ---------------------------------------------------------------------------


def test_a_perfect_ordering_correlates_at_one() -> None:
    """Approximately, because the statistic is a ratio of float sums and says so."""
    rho = spearman_rank_correlation([1.0, 2.0, 3.0, 4.0], [10.0, 20.0, 30.0, 40.0])
    assert rho == pytest.approx(1.0)


def test_a_reversed_ordering_correlates_at_minus_one() -> None:
    rho = spearman_rank_correlation([1.0, 2.0, 3.0, 4.0], [40.0, 30.0, 20.0, 10.0])
    assert rho == pytest.approx(-1.0)


def test_a_monotone_transform_leaves_the_rank_correlation_alone() -> None:
    """The property the statistic is chosen for: it reads the ordering, not the scale."""
    xs = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    assert spearman_rank_correlation(xs, [math.exp(x) for x in xs]) == pytest.approx(1.0)


def test_ties_share_their_average_rank() -> None:
    """Ranking by input order would make the statistic depend on how the caller sorted."""
    assert ranks([5.0, 1.0, 5.0, 3.0]) == (3.5, 1.0, 3.5, 2.0)


def test_a_constant_sequence_has_no_rank_correlation() -> None:
    """The denominator is zero, so any number returned would be an invention."""
    assert spearman_rank_correlation([1.0, 1.0, 1.0, 1.0], [1.0, 2.0, 3.0, 4.0]) is None


def test_too_few_pairs_answer_none() -> None:
    """Two pairs are always plus or minus one whatever the numbers are."""
    assert MINIMUM_PAIRS == 3
    assert spearman_rank_correlation([1.0, 2.0], [2.0, 1.0]) is None


def test_unpaired_inputs_are_refused() -> None:
    with pytest.raises(ValueError, match="paired inputs"):
        spearman_rank_correlation([1.0, 2.0, 3.0], [1.0, 2.0])


def test_the_ordering_clause_threshold_is_reachable_at_six_symbols() -> None:
    """A sanity check on the registered floor, which is not a number nobody can reach.

    Six symbols ranked perfectly except for one swap of neighbours give rho = 0.943, which
    clears the floor; a shuffle gives 0.086, which does not. So the clause admits an
    estimator that gets the ordering nearly right and refuses one that scrambles it, which
    is what it was chosen to do.
    """
    perfect = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    one_swap = [2.0, 1.0, 3.0, 4.0, 5.0, 6.0]
    shuffled = [4.0, 1.0, 6.0, 2.0, 5.0, 3.0]
    assert Decimal("0.943") > ESTIMATOR_RANK_FLOOR
    assert spearman_rank_correlation(perfect, one_swap) == pytest.approx(0.942857, abs=1e-5)
    assert spearman_rank_correlation(perfect, shuffled) == pytest.approx(0.085714, abs=1e-5)
