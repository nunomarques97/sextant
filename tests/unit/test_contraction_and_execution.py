"""Amendment 4's two additions: an unrepresentative month, and the venue that trades.

Both are reporting requirements and neither decides anything, so the tests are about
the two ways reporting can mislead:

- **claiming a difference that is not there.** The composition check must call a
  difference systematic only when its bootstrap interval excludes zero. An interval
  that spans zero is not a finding, and a report that described one as though it were
  would manufacture a caveat out of noise;
- **losing the venue label.** The execution-venue re-cost is a hypothetical - one
  venue's fees on another venue's universe - and its whole value depends on never
  being mistaken for a measurement. It must never be a grid cell, never be charged
  against the trial budget, and never be read by a criterion.

The two-sample difference bootstrap is tested for the property that matters most
here: a small group keeps its own wide interval instead of borrowing precision from a
large one, because at a contraction the two groups differ in size by a factor of two.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from sextant.adapters.exchanges.binance.costs import FUTURES_FEES as BINANCE_FUTURES
from sextant.adapters.exchanges.binance.costs import SPOT_FEES as BINANCE_SPOT
from sextant.adapters.exchanges.kraken.costs import FUTURES_FEES as KRAKEN_FUTURES
from sextant.adapters.exchanges.kraken.costs import SPOT_FEES as KRAKEN_SPOT
from sextant.app.spike_006_f1 import (
    CONTRACTION_ATTRIBUTES,
    budget,
    cell_labels,
    execution_sensitivity,
    headline_cell,
    round_trip_fee_bps,
)
from sextant.app.spike_006_f1_contraction import (
    ATTRIBUTES,
    ContractionReport,
    GroupSummary,
    without_month,
)
from sextant.engine.backtest.budget import BudgetLedger, UnregisteredTrial
from sextant.engine.execution.costs import LiquidityBand
from sextant.engine.statistics.bootstrap import (
    DifferenceEstimate,
    EmptyDistribution,
    GroupStatistic,
    difference_with_interval,
    generator_for,
)
from tests.harness import ts

SEED = 987654321


def group(name: str, *, size: int, deep: int, cadence: str = "8") -> GroupSummary:
    """One group summary with a stated band split."""
    return GroupSummary(
        name=name,
        size=size,
        with_a_funding_median=size,
        band_counts={
            LiquidityBand.DEEP.value: deep,
            LiquidityBand.MID.value: size - deep,
        },
        cadence_counts={cadence: size},
    )


def report(**differences: DifferenceEstimate) -> ContractionReport:
    """A contraction report with the differences supplied directly."""
    return ContractionReport(
        at=ts("2026-07-01T00:00:00"),
        pairs_before=339,
        pairs_after=108,
        admitted=group("admitted", size=108, deep=48),
        excluded=group("excluded_by_funding_evaluability", size=231, deep=61, cadence="4"),
        differences=differences,
        admitted_profiles=(),
        excluded_profiles=(),
    )


# ---------------------------------------------------------------------------
# The two-sample difference bootstrap
# ---------------------------------------------------------------------------


def test_two_clearly_separated_groups_produce_an_interval_excluding_zero() -> None:
    """The positive case, which the real contraction turns out to be on two attributes."""
    estimate = difference_with_interval(
        [10.0] * 40 + [11.0] * 40,
        [1.0] * 40 + [2.0] * 40,
        statistic=GroupStatistic.MEDIAN,
        generator=generator_for(SEED),
    )
    assert estimate.difference > 0
    assert estimate.excludes_zero is True


def test_two_identical_groups_produce_an_interval_spanning_zero() -> None:
    """The test that stops a caveat being manufactured out of noise."""
    values = [float(index % 7) for index in range(120)]
    estimate = difference_with_interval(
        values,
        list(reversed(values)),
        statistic=GroupStatistic.MEDIAN,
        generator=generator_for(SEED),
    )
    assert estimate.excludes_zero is False


def test_a_small_group_keeps_its_own_wide_interval() -> None:
    """Each group is resampled at its own size, so precision is not borrowed.

    At a contraction the two groups differ in size by a factor of two or more. A
    bootstrap that pooled them would hand the small group the large group's precision
    and narrow the interval on a comparison that does not deserve it.
    """
    generator = generator_for(SEED)
    wide = difference_with_interval(
        [1.0, 5.0, 9.0],
        [0.0] * 300,
        statistic=GroupStatistic.MEDIAN,
        generator=generator,
    )
    narrow = difference_with_interval(
        [1.0, 5.0, 9.0] * 100,
        [0.0] * 300,
        statistic=GroupStatistic.MEDIAN,
        generator=generator,
    )
    assert wide.left_size == 3
    assert narrow.left_size == 300
    assert wide.width > narrow.width


def test_the_mean_statistic_measures_a_share() -> None:
    """A share is the mean of a zero-or-one indicator, so the band uses one method."""
    estimate = difference_with_interval(
        [1.0] * 48 + [0.0] * 60,
        [1.0] * 61 + [0.0] * 170,
        statistic=GroupStatistic.MEAN,
        generator=generator_for(SEED),
    )
    assert estimate.left_value == pytest.approx(48 / 108)
    assert estimate.right_value == pytest.approx(61 / 231)
    assert estimate.difference > 0


def test_an_empty_group_raises_rather_than_reading_as_zero() -> None:
    """An empty group is a real condition and the caller decides what it means."""
    with pytest.raises(EmptyDistribution, match="empty group"):
        difference_with_interval(
            [], [1.0], statistic=GroupStatistic.MEDIAN, generator=generator_for(SEED)
        )


def test_the_interval_is_reproducible_from_the_registered_seed() -> None:
    """Two runs at the same seed agree exactly, or no interval here is citable."""
    first = difference_with_interval(
        [1.0, 2.0, 3.0, 9.0],
        [0.5, 1.5, 2.5],
        statistic=GroupStatistic.MEDIAN,
        generator=generator_for(SEED),
    )
    second = difference_with_interval(
        [1.0, 2.0, 3.0, 9.0],
        [0.5, 1.5, 2.5],
        statistic=GroupStatistic.MEDIAN,
        generator=generator_for(SEED),
    )
    assert (first.low, first.high) == (second.low, second.high)


# ---------------------------------------------------------------------------
# The composition verdict
# ---------------------------------------------------------------------------


def test_no_systematic_difference_is_reported_as_not_shown_unrepresentative() -> None:
    """The wording matters: unmeasured is not the same as shown to be fine."""
    spanning = difference_with_interval(
        [1.0, 2.0, 3.0],
        [1.0, 2.0, 3.0],
        statistic=GroupStatistic.MEDIAN,
        generator=generator_for(SEED),
    )
    outcome = report(median_funding_rate=spanning, contract_age_days=spanning)
    assert outcome.is_unrepresentative is False
    assert outcome.systematic_attributes == ()
    assert "not shown to be unrepresentative" in outcome.verdict()


def test_a_systematic_difference_names_the_attribute_and_refuses_a_direction() -> None:
    """The bias direction is not knowable from the count, and is not claimed."""
    separated = difference_with_interval(
        [100.0] * 30,
        [1.0] * 30,
        statistic=GroupStatistic.MEDIAN,
        generator=generator_for(SEED),
    )
    outcome = report(contract_age_days=separated)
    assert outcome.is_unrepresentative is True
    assert outcome.systematic_attributes == ("contract_age_days",)
    assert "contract_age_days" in outcome.verdict()
    assert "not knowable" in outcome.verdict()


def test_the_attributes_are_reported_in_registered_order() -> None:
    """So two runs' reports read the same way, and match the amendment's table."""
    separated = difference_with_interval(
        [100.0] * 30, [1.0] * 30, statistic=GroupStatistic.MEDIAN, generator=generator_for(SEED)
    )
    outcome = report(
        liquidity_band=separated, median_funding_rate=separated, contract_age_days=separated
    )
    assert outcome.systematic_attributes == ATTRIBUTES
    assert ATTRIBUTES == CONTRACTION_ATTRIBUTES


def test_the_report_says_it_decides_nothing() -> None:
    """Criterion 6 is judged on the full series, and the payload has to say so."""
    payload = report().as_json()
    assert "Nothing" in str(payload["what_this_decides"])
    assert "unchanged" in str(payload["what_this_decides"])


def test_the_mechanism_is_labelled_described_rather_than_tested() -> None:
    """The cadence explains the split and must not become a fourth test."""
    payload = report().as_json()
    assert "DESCRIBED, NOT TESTED" in str(payload["mechanism_status"])
    assert "8 hours" in str(payload["mechanism"])
    assert "4 hours" in str(payload["mechanism"])


def test_a_group_with_no_dominant_cadence_gets_no_mechanism_claim() -> None:
    """A plurality is not a separation, and must not be described as one."""
    outcome = ContractionReport(
        at=ts("2026-07-01T00:00:00"),
        pairs_before=10,
        pairs_after=4,
        admitted=GroupSummary(
            name="admitted",
            size=4,
            with_a_funding_median=4,
            band_counts={},
            cadence_counts={"4": 2, "8": 2},
        ),
        excluded=group("excluded", size=6, deep=1, cadence="4"),
        differences={},
        admitted_profiles=(),
        excluded_profiles=(),
    )
    assert "does not hold here" in outcome.mechanism()


# ---------------------------------------------------------------------------
# Dropping the affected month
# ---------------------------------------------------------------------------


def test_the_affected_month_is_removed_by_calendar_month() -> None:
    """Not by exact instant: a monthly series is stamped at its own convention."""
    series = (
        (ts("2026-06-01T00:00:00"), Decimal("0.01")),
        (ts("2026-07-31T23:59:59"), Decimal("-0.05")),
        (ts("2026-08-01T00:00:00"), Decimal("0.02")),
    )
    kept = without_month(series, ts("2026-07-01T00:00:00"))
    assert [at.isoformat()[:7] for at, _ in kept] == ["2026-06", "2026-08"]


def test_removing_a_month_that_is_not_there_changes_nothing() -> None:
    series = ((ts("2026-06-01T00:00:00"), Decimal("0.01")),)
    assert without_month(series, ts("2020-01-01T00:00:00")) == series


# ---------------------------------------------------------------------------
# The execution-venue sensitivity
# ---------------------------------------------------------------------------


def test_the_sensitivity_carries_the_execution_venue_s_published_rates() -> None:
    """Looked up rather than assumed, and wired from the venue's own module."""
    cell = execution_sensitivity()
    assert cell.spot_maker_bps == KRAKEN_SPOT.maker_bps == Decimal(40)
    assert cell.spot_taker_bps == KRAKEN_SPOT.taker_bps == Decimal(80)
    assert cell.futures_maker_bps == KRAKEN_FUTURES.maker_bps == Decimal(2)
    assert cell.futures_taker_bps == KRAKEN_FUTURES.taker_bps == Decimal(5)


def test_the_perpetual_leg_costs_the_same_at_both_venues() -> None:
    """The lookup's most useful finding, asserted so a later edit cannot lose it.

    The whole cost differential between the research venue and the execution venue is
    on the SPOT leg, which is the long leg of every carry pair. If a future schedule
    change breaks this equality, the report's framing changes and this test says so.
    """
    assert KRAKEN_FUTURES.maker_bps == BINANCE_FUTURES.maker_bps
    assert KRAKEN_FUTURES.taker_bps == BINANCE_FUTURES.taker_bps
    assert KRAKEN_SPOT.maker_bps == 4 * BINANCE_SPOT.maker_bps
    assert KRAKEN_SPOT.taker_bps == 8 * BINANCE_SPOT.taker_bps


def test_the_round_trip_arithmetic_is_the_registered_one() -> None:
    """27 basis points against 127, the figures amendment 26.2 states in advance."""
    assert round_trip_fee_bps(headline_cell()) == Decimal(27)
    assert round_trip_fee_bps(execution_sensitivity()) == Decimal(127)


def test_the_sensitivity_is_not_a_grid_cell() -> None:
    """Criterion 5's sign-stability test is over four cells and stays over four."""
    assert execution_sensitivity().label not in cell_labels()
    assert len(cell_labels()) == 4


def test_the_sensitivity_cannot_be_charged_against_the_trial_budget() -> None:
    """It consumes no budget, and the ledger refuses it as an unregistered cell.

    That refusal is the right one: the budget covers the registered grid, and this is
    deliberately outside it. It is recorded in the registry instead, where its only
    possible effect is to raise the deflation bar.
    """
    ledger = BudgetLedger(budget=budget())
    with pytest.raises(UnregisteredTrial, match="kraken_execution"):
        ledger.charge("carry-basket-10", execution_sensitivity().label)
    assert ledger.spent == 0


def test_the_sensitivity_is_never_the_headline() -> None:
    """No criterion reads it, so it cannot be the cell criteria are judged in."""
    assert execution_sensitivity().is_headline is False
    assert execution_sensitivity().runs_nulls is False
    assert headline_cell().label == "vip0_even"


def test_the_sensitivity_can_only_make_a_variant_look_worse() -> None:
    """The one-directional property the budget exemption rests on.

    Every fee in the execution cell is greater than or equal to the research cell's at
    the same fill mix. If that ever stopped being true, the exemption's reasoning would
    fail and this test would fail with it.
    """
    research = headline_cell()
    execution = execution_sensitivity()
    assert execution.maker_fraction == research.maker_fraction
    assert execution.spot_maker_bps >= research.spot_maker_bps
    assert execution.spot_taker_bps >= research.spot_taker_bps
    assert execution.futures_maker_bps >= research.futures_maker_bps
    assert execution.futures_taker_bps >= research.futures_taker_bps
    assert round_trip_fee_bps(execution) > round_trip_fee_bps(research)
