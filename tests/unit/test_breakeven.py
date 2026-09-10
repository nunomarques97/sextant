"""Break-even turnover, and the two ways it could be reported dishonestly.

Amendment 27 replaces a pass-or-fail verdict with three numbers, because at these fee
levels the binding constraint is holding period rather than signal quality. The tests
guard the two failure modes that would make the reframing worse than the verdict it
replaces:

- **a book that earned nothing gross reported as having a tight threshold.** Zero gross
  carry means no turnover covers the fees, and printing "0.00 round trips" invites
  reading it as a real but demanding bar. It is undefined, and it says so;
- **an upper bound printed as an estimate.** Spread and slippage also scale with
  turnover and are excluded, so the break-even on total cost is strictly lower than
  either figure here. The object cannot be serialised without saying so.

The realised-turnover definition is tested for the property the amendment rests on: it
inverts the same arithmetic the fees came from, so it cannot disagree with the fee line.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from sextant.app.spike_006_f1 import (
    D2B_THRESHOLD_ROUND_TRIPS,
    EXECUTION_FEE_OF_EQUITY_BPS,
    MARGIN_FRACTION,
    RESEARCH_FEE_OF_EQUITY_BPS,
    execution_sensitivity,
    headline_cell,
    round_trip_fee_bps,
)
from sextant.engine.execution.breakeven import (
    MONTHLY_ROUND_TRIPS,
    BreakEven,
    BreakEvenUndefined,
    d2a_holds,
    fee_of_equity_bps,
)


def case(*, gross: str, fees: str) -> BreakEven:
    """One break-even at the registered fee levels."""
    return BreakEven(
        gross_return_bps_per_year=Decimal(gross),
        research_fee_of_equity_bps=RESEARCH_FEE_OF_EQUITY_BPS,
        execution_fee_of_equity_bps=EXECUTION_FEE_OF_EQUITY_BPS,
        realised_fees_bps_per_year=Decimal(fees),
    )


# ---------------------------------------------------------------------------
# The conversion from leg notional to equity
# ---------------------------------------------------------------------------


def test_the_registered_denominators_are_the_per_leg_figures_over_the_margin() -> None:
    """22.50 and 105.83 basis points of equity, derived rather than restated."""
    assert Decimal(27) / (Decimal(1) + MARGIN_FRACTION) == RESEARCH_FEE_OF_EQUITY_BPS
    assert Decimal(127) / (Decimal(1) + MARGIN_FRACTION) == EXECUTION_FEE_OF_EQUITY_BPS
    assert Decimal("22.5") == RESEARCH_FEE_OF_EQUITY_BPS


def test_the_denominators_follow_the_cells_rather_than_being_hard_coded() -> None:
    """A fee-schedule change must move these, or the break-even goes stale silently."""
    assert (
        fee_of_equity_bps(round_trip_fee_bps(headline_cell()), MARGIN_FRACTION)
        == RESEARCH_FEE_OF_EQUITY_BPS
    )
    assert (
        fee_of_equity_bps(round_trip_fee_bps(execution_sensitivity()), MARGIN_FRACTION)
        == EXECUTION_FEE_OF_EQUITY_BPS
    )


def test_a_zero_fee_schedule_has_no_break_even_and_refuses_to_invent_one() -> None:
    """An infinite threshold is not a number anybody can act on."""
    with pytest.raises(BreakEvenUndefined, match="must cost something"):
        fee_of_equity_bps(Decimal(0), MARGIN_FRACTION)


def test_a_negative_margin_fraction_is_refused() -> None:
    with pytest.raises(BreakEvenUndefined, match="must not be negative"):
        fee_of_equity_bps(Decimal(27), Decimal("-0.1"))


# ---------------------------------------------------------------------------
# The three numbers
# ---------------------------------------------------------------------------


def test_a_single_digit_carry_breaks_even_below_a_monthly_rebalance() -> None:
    """The Sponsor's arithmetic, asserted: twelve round trips erases the yield.

    A 4 per cent gross carry pays for about 17.8 round trips a year at research fees
    and 3.8 at execution fees. Monthly rebalancing is twelve, so the execution schedule
    cannot support it and the research schedule can.
    """
    outcome = case(gross="400", fees="45")
    assert outcome.break_even_at_research_fees == pytest.approx(Decimal("17.78"), abs=0.01)
    assert outcome.break_even_at_execution_fees == pytest.approx(Decimal("3.78"), abs=0.01)
    assert outcome.break_even_at_execution_fees < MONTHLY_ROUND_TRIPS


def test_realised_turnover_inverts_the_fee_line_it_came_from() -> None:
    """Two round trips of fees charged reads back as two round trips of turnover."""
    outcome = case(gross="400", fees=str(2 * Decimal("22.5")))
    assert outcome.realised_round_trips_per_year == Decimal(2)


def test_a_variant_whose_fees_exceed_its_gross_carry_clears_neither() -> None:
    """The definitional consistency the amendment rests on: net must be negative."""
    outcome = case(gross="100", fees="500")
    assert outcome.realised_round_trips_per_year > outcome.break_even_at_research_fees
    assert outcome.survives_research_fees is False
    assert outcome.survives_execution_fees is False
    assert "clears neither" in outcome.sentence()


def test_the_middle_case_clears_research_fees_and_not_execution_fees() -> None:
    """The answer the whole sensitivity exists to make possible."""
    outcome = case(gross="400", fees="112.5")
    assert outcome.realised_round_trips_per_year == Decimal(5)
    assert outcome.survives_research_fees is True
    assert outcome.survives_execution_fees is False
    assert "research schedule and not the execution one" in outcome.sentence()


def test_a_low_turnover_book_clears_both() -> None:
    outcome = case(gross="400", fees="22.5")
    assert outcome.realised_round_trips_per_year == Decimal(1)
    assert outcome.survives_execution_fees is True
    assert "clears both" in outcome.sentence()


def test_a_book_with_no_gross_carry_reports_undefined_not_zero() -> None:
    """A tight threshold and no threshold are different facts about a strategy."""
    outcome = case(gross="0", fees="45")
    assert outcome.break_even_at_research_fees is None
    assert outcome.break_even_at_execution_fees is None
    assert outcome.survives_research_fees is False
    assert "undefined" in str(outcome.as_json()["break_even_at_research_fees"])
    assert "earned nothing gross" in outcome.sentence()


def test_a_negative_gross_carry_is_undefined_too() -> None:
    outcome = case(gross="-250", fees="45")
    assert outcome.break_even_at_execution_fees is None
    assert outcome.d2b_holds is None


def test_negative_fees_charged_are_refused() -> None:
    """A fee line cannot be a receipt; that would be a defect upstream."""
    with pytest.raises(BreakEvenUndefined, match="cannot be negative"):
        case(gross="400", fees="-1")


# ---------------------------------------------------------------------------
# Reporting honesty
# ---------------------------------------------------------------------------


def test_every_serialisation_says_the_figures_are_upper_bounds() -> None:
    """Spread and slippage also scale with turnover, so total cost breaks even lower."""
    payload = case(gross="400", fees="45").as_json()
    assert payload["figures_are_upper_bounds"] is True
    assert "strictly lower" in str(payload["upper_bound_reason"])
    assert "conversion leg is excluded" in str(payload["upper_bound_reason"])
    assert "upper bounds" in str(payload["sentence"])


def test_the_gross_numerator_is_reported_so_the_figures_can_be_recomputed() -> None:
    """The point of the reframing: a different fee schedule needs no new backtest."""
    payload = case(gross="400", fees="45").as_json()
    assert payload["gross_return_bps_per_year"] == "400"


def test_the_predicted_execution_fee_line_is_reported_for_the_cross_check() -> None:
    """Arithmetic and a run must agree, and are reported as a matched pair."""
    outcome = case(gross="400", fees="45")
    assert outcome.predicted_execution_fees_bps_per_year() == Decimal(2) * (
        Decimal(127) / Decimal("1.2")
    )


# ---------------------------------------------------------------------------
# Declared expectation D2
# ---------------------------------------------------------------------------


def test_d2b_threshold_is_a_monthly_rebalance_and_lives_in_one_place() -> None:
    assert D2B_THRESHOLD_ROUND_TRIPS == MONTHLY_ROUND_TRIPS == Decimal(12)


def test_d2b_is_refuted_when_execution_fees_survive_monthly_turnover() -> None:
    """The refutation the amendment says would make its own framing wrong.

    A gross carry large enough that 127 basis points a round trip still pays at monthly
    frequency would mean signal quality, not holding period, was the binding constraint.
    """
    generous = case(gross="2000", fees="45")
    assert generous.break_even_at_execution_fees is not None
    assert generous.break_even_at_execution_fees > MONTHLY_ROUND_TRIPS
    assert generous.d2b_holds is False


def test_d2b_holds_at_a_realistic_carry() -> None:
    assert case(gross="400", fees="45").d2b_holds is True


def test_d2a_is_unresolved_when_nothing_clears() -> None:
    """A prediction about clearing variants is not confirmed by there being none."""
    assert d2a_holds(clearing_turnovers=(), all_turnovers=(Decimal(1), Decimal(9))) is None


def test_d2a_holds_when_the_clearing_variant_is_at_or_below_the_median() -> None:
    turnovers = (Decimal(1), Decimal(3), Decimal(5), Decimal(9), Decimal(20))
    assert d2a_holds(clearing_turnovers=(Decimal(3),), all_turnovers=turnovers) is True
    assert d2a_holds(clearing_turnovers=(Decimal(5),), all_turnovers=turnovers) is True


def test_d2a_is_refuted_by_a_high_turnover_winner() -> None:
    turnovers = (Decimal(1), Decimal(3), Decimal(5), Decimal(9), Decimal(20))
    assert d2a_holds(clearing_turnovers=(Decimal(20),), all_turnovers=turnovers) is False


def test_d2a_needs_the_whole_set_to_take_a_median() -> None:
    with pytest.raises(BreakEvenUndefined, match="whole variant set"):
        d2a_holds(clearing_turnovers=(Decimal(1),), all_turnovers=())


def test_d2a_takes_an_even_sized_median_as_the_midpoint() -> None:
    """Eight variants is an even count, which is what this family actually has."""
    turnovers = tuple(Decimal(value) for value in (1, 2, 3, 4, 6, 8, 10, 12))
    assert d2a_holds(clearing_turnovers=(Decimal(5),), all_turnovers=turnovers) is True
    assert d2a_holds(clearing_turnovers=(Decimal(6),), all_turnovers=turnovers) is False
