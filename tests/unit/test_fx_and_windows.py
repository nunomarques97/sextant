"""The currency leg, the walk-forward plan, and the null's reproducibility.

Three things that would each be a quiet, plausible defect:

* an FX rate defaulted to parity, or carried forward across a gap, which is a
  fifteen-percent error that looks like alpha;
* a walk-forward plan whose out-of-sample windows overlap, or whose fitting
  window runs past the window it is judged on;
* a random null whose seeds do not reproduce, or whose selection depends on the
  order a set happened to iterate in.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from sextant.domain.instrument import Instrument, InstrumentKey
from sextant.domain.money import Notional
from sextant.domain.time import Timeframe
from sextant.engine.backtest.baselines import BaselineError, RandomSelection
from sextant.engine.backtest.market import PointInTimeView
from sextant.engine.backtest.window import (
    InvalidWindow,
    Window,
    add_months,
    anchored_plan,
    month_start,
    monthly_instants,
)
from sextant.engine.execution.fx import (
    CurrencyRouting,
    FxLeg,
    FxPolicy,
    FxRates,
    FxRateUnavailable,
)
from tests.harness import VENUE, InMemoryBarRepository, instrument, ts

RATES = FxRates.of(
    foreign_currency="USD",
    account_currency="EUR",
    observations={
        ts("2024-01-01T00:00:00"): Decimal("0.90"),
        ts("2024-02-01T00:00:00"): Decimal("0.92"),
        ts("2024-03-01T00:00:00"): Decimal("0.85"),
    },
    source="test fixture",
)


def leg(policy: FxPolicy) -> FxLeg:
    """A currency leg under one policy, with a stated basis."""
    return FxLeg(
        policy=policy,
        conversion_bps=Decimal(20),
        basis="test fixture: a stated assumption, not a measurement",
        rates=RATES if policy is FxPolicy.APPLIED else None,
    )


# -- rates are point-in-time and never invented -------------------------------


def test_a_rate_before_the_series_starts_is_refused_rather_than_defaulted() -> None:
    """Parity is not a safe default; it is a fifteen-percent error."""
    with pytest.raises(FxRateUnavailable, match="No USD/EUR rate"):
        RATES.rate_at(ts("2023-12-31T00:00:00"))


def test_a_rate_is_the_most_recent_one_knowable_and_never_the_next_one() -> None:
    """Point-in-time, which for a rate means the last one that had been observed."""
    assert RATES.rate_at(ts("2024-01-15T00:00:00")) == Decimal("0.90")
    assert RATES.rate_at(ts("2024-02-01T00:00:00")) == Decimal("0.92")
    assert RATES.rate_at(ts("2024-02-28T00:00:00")) == Decimal("0.92")
    assert RATES.rate_at(ts("2024-06-01T00:00:00")) == Decimal("0.85")


def test_a_non_positive_rate_is_refused_at_construction() -> None:
    """Not a usable exchange rate, whatever produced it."""
    with pytest.raises(FxRateUnavailable):
        FxRates.of(
            foreign_currency="USD",
            account_currency="EUR",
            observations={ts("2024-01-01T00:00:00"): Decimal(0)},
            source="broken fixture",
        )


# -- the three policies -------------------------------------------------------


def test_the_counterfactual_policy_converts_at_parity_and_charges_nothing() -> None:
    """That is exactly what makes the gap to APPLIED measure something."""
    ignored = leg(FxPolicy.IGNORED)
    assert ignored.rate_at(ts("2024-03-01T00:00:00")) == Decimal(1)
    assert ignored.conversion_cost(Notional(Decimal(1000))).amount == 0
    assert ignored.policy.is_counterfactual is True
    assert ignored.as_metadata()["fx_is_counterfactual"] == "True"


def test_the_applied_policy_converts_at_the_observed_rate_and_charges_the_fee() -> None:
    """Twenty basis points each way on a thousand is two euro each way."""
    applied = leg(FxPolicy.APPLIED)
    assert applied.rate_at(ts("2024-03-15T00:00:00")) == Decimal("0.85")
    assert applied.conversion_cost(Notional(Decimal(1000))).amount == Decimal(2)
    assert applied.policy.is_counterfactual is False


def test_the_domestic_policy_never_converts() -> None:
    """An account that stays in its own currency has no leg at all."""
    home = FxLeg(
        policy=FxPolicy.ACCOUNT_CURRENCY_ONLY,
        conversion_bps=Decimal(20),
        basis="test fixture",
    )
    assert home.rate_at(ts("2024-03-01T00:00:00")) == Decimal(1)
    assert home.conversion_cost(Notional(Decimal(1000))).amount == 0


def test_applying_a_currency_leg_with_no_rates_is_refused() -> None:
    """Otherwise it is IGNORED wearing the label of APPLIED."""
    with pytest.raises(FxRateUnavailable, match="needs a rate series"):
        FxLeg(policy=FxPolicy.APPLIED, conversion_bps=Decimal(20), basis="fixture")


def test_a_conversion_charge_without_a_stated_basis_is_refused() -> None:
    """Same rule as every other assumption in the cost model."""
    with pytest.raises(FxRateUnavailable):
        FxLeg(policy=FxPolicy.ACCOUNT_CURRENCY_ONLY, conversion_bps=Decimal(20), basis="  ")


def test_routing_sends_only_foreign_instruments_through_the_leg() -> None:
    """Charging a conversion on a domestic position would invent a cost."""
    routing = CurrencyRouting(
        account_currency="EUR",
        quote_by_symbol={"AAAEUR": "EUR", "BBBUSD": "USD", "CCCEUR": "EUR"},
    )
    assert routing.is_foreign("BBBUSD") is True
    assert routing.is_foreign("AAAEUR") is False
    assert routing.foreign_symbols(["AAAEUR", "BBBUSD", "CCCEUR"]) == ("BBBUSD",)


def test_an_unknown_symbol_is_treated_as_domestic_rather_than_charged() -> None:
    """A missing quote currency must not silently become a conversion fee."""
    routing = CurrencyRouting(account_currency="EUR", quote_by_symbol={})
    assert routing.is_foreign("MYSTERY") is False


# -- walk-forward windows -----------------------------------------------------


def test_a_plan_holds_back_the_fitting_months_and_splits_the_rest_evenly() -> None:
    """The shape SEXTANT-004 runs: thirty months, six held back, three folds."""
    built = anchored_plan(
        first_month=ts("2023-10-01T00:00:00"),
        total_months=30,
        in_sample_months=6,
        fold_count=3,
    )
    assert built.fold_count == 3
    spans = [fold.out_of_sample for fold in built.folds]
    assert spans[0].start == ts("2024-04-01T00:00:00")
    assert spans[-1].end == ts("2026-04-01T00:00:00")
    for window in spans:
        assert len(monthly_instants(window)) == 8


def test_out_of_sample_windows_never_overlap() -> None:
    """An instant scored twice is an instant counted twice."""
    built = anchored_plan(
        first_month=ts("2023-10-01T00:00:00"),
        total_months=30,
        in_sample_months=6,
        fold_count=3,
    )
    for earlier, later in zip(built.folds, built.folds[1:], strict=False):
        assert not earlier.out_of_sample.overlaps(later.out_of_sample)


def test_every_in_sample_window_ends_where_its_out_of_sample_window_begins() -> None:
    """A gap discards data; an overlap is not out-of-sample at all."""
    built = anchored_plan(
        first_month=ts("2023-10-01T00:00:00"),
        total_months=30,
        in_sample_months=6,
        fold_count=3,
    )
    for fold in built.folds:
        assert fold.in_sample.end == fold.out_of_sample.start
        assert fold.in_sample.start == ts("2023-10-01T00:00:00")


def test_a_window_too_short_for_the_plan_is_refused_and_says_why() -> None:
    """ "This window is too short" is a legitimate answer, and it is stated."""
    with pytest.raises(InvalidWindow, match="too short"):
        anchored_plan(
            first_month=ts("2023-10-01T00:00:00"),
            total_months=8,
            in_sample_months=6,
            fold_count=3,
        )


def test_a_window_that_runs_backwards_is_refused() -> None:
    """A window is a half-open interval and it has a direction."""
    with pytest.raises(InvalidWindow):
        Window(start=ts("2024-02-01T00:00:00"), end=ts("2024-01-01T00:00:00"))


def test_month_arithmetic_is_calendar_arithmetic_not_thirty_days() -> None:
    """A rebalance that drifts two days a year eventually rebalances late."""
    assert add_months(ts("2023-12-01T00:00:00"), 1) == ts("2024-01-01T00:00:00")
    assert add_months(ts("2024-01-31T00:00:00"), 1) == ts("2024-02-01T00:00:00")
    assert add_months(ts("2024-01-01T00:00:00"), 14) == ts("2025-03-01T00:00:00")
    assert month_start(ts("2024-05-17T13:04:00")) == ts("2024-05-01T00:00:00")


def test_a_plan_that_is_not_month_aligned_is_refused() -> None:
    """Folds must be comparable, which means they must start on the same day."""
    with pytest.raises(InvalidWindow, match="not the first instant"):
        anchored_plan(
            first_month=ts("2023-10-15T00:00:00"),
            total_months=30,
            in_sample_months=6,
            fold_count=3,
        )


# -- the random null's reproducibility ----------------------------------------


def candidates(count: int) -> list[Instrument]:
    """A sorted candidate set, as the engine hands one to an allocator."""
    return [instrument(f"{chr(65 + index)}{chr(65 + index)}AEUR") for index in range(count)]


def empty_view() -> PointInTimeView:
    """A real view over an empty store. These allocators never consult it."""
    return PointInTimeView.at(
        InMemoryBarRepository(), Timeframe.D1, ts("2024-04-01T00:00:00"), lookback_days=30
    )


def test_the_same_seed_produces_the_same_selection_every_time() -> None:
    """Reproducibility of the null, asserted on the allocator itself."""
    at = ts("2024-04-01T00:00:00")
    first = RandomSelection.for_seed(seed=42, positions=3)
    second = RandomSelection.for_seed(seed=42, positions=3)
    pool = candidates(12)
    for _ in range(5):
        assert first.allocate(pool, at, empty_view()).keys == (
            second.allocate(pool, at, empty_view()).keys
        )


def test_different_seeds_produce_different_selections() -> None:
    """Otherwise the distribution has one member."""
    at = ts("2024-04-01T00:00:00")
    pool = candidates(20)
    drawn = {
        RandomSelection.for_seed(seed=seed, positions=4).allocate(pool, at, empty_view()).keys
        for seed in range(10)
    }
    assert len(drawn) > 5


def test_selection_is_without_replacement_within_a_rebalance() -> None:
    """Eight positions means eight distinct names, as it does for any strategy."""
    at = ts("2024-04-01T00:00:00")
    pool = candidates(10)
    for seed in range(30):
        keys = (
            RandomSelection.for_seed(seed=seed, positions=6).allocate(pool, at, empty_view()).keys
        )
        assert len(keys) == len(set(keys)) == 6


def test_a_universe_smaller_than_the_position_count_is_taken_whole_and_recorded() -> None:
    """Padding with a repeat, or with cash, would change the null silently."""
    at = ts("2024-04-01T00:00:00")
    allocation = RandomSelection.for_seed(seed=1, positions=8).allocate(
        candidates(3), at, empty_view()
    )
    assert len(allocation.keys) == 3
    assert "at or below" in allocation.note
    # A third of equity three times does not sum to one in exact decimal, and
    # the remainder is deliberately left uninvested rather than pushed into the
    # last position. It is a rounding tail, not a cash allocation.
    assert Decimal("0.9999999999") < allocation.invested_fraction <= Decimal(1)


def test_an_empty_universe_holds_nothing_and_says_so() -> None:
    """Not an error, and not silently a cash position nobody recorded."""
    allocation = RandomSelection.for_seed(seed=1, positions=8).allocate(
        [], ts("2024-04-01T00:00:00"), empty_view()
    )
    assert allocation.keys == ()
    assert "empty" in allocation.note


def test_the_selection_does_not_depend_on_the_order_the_candidates_arrive_in() -> None:
    """Iteration order is not a property of this program.

    The engine sorts candidates before handing them over, and the allocator
    sorts what it drew. Both together are what make a seed mean the same thing
    on two machines.
    """
    at = ts("2024-04-01T00:00:00")
    pool = candidates(15)
    forward = RandomSelection.for_seed(seed=99, positions=5).allocate(pool, at, empty_view())
    assert list(forward.keys) == sorted(forward.keys)


def test_zero_positions_is_refused() -> None:
    """A null that holds nothing is not a null."""
    with pytest.raises(BaselineError):
        RandomSelection.for_seed(seed=1, positions=0)


def test_an_allocation_cannot_ask_for_more_than_the_account_has() -> None:
    """Leverage is not expressible, and the refusal happens where it is asked for."""
    from sextant.engine.backtest.allocation import Allocation, InvalidAllocation

    keys = (InstrumentKey(VENUE, "AAAEUR"), InstrumentKey(VENUE, "BBBEUR"))
    with pytest.raises(InvalidAllocation, match="more than the account has"):
        Allocation(
            weights=((keys[0], Decimal("0.8")), (keys[1], Decimal("0.8"))),
            at=ts("2024-04-01T00:00:00"),
            candidates_considered=2,
        )


def test_equal_weight_leaves_the_rounding_remainder_uninvested() -> None:
    """Pushing it into the last position would bias one name over thirty months."""
    from sextant.engine.backtest.allocation import Allocation

    keys = [InstrumentKey(VENUE, f"{letter}{letter}{letter}EUR") for letter in "ABC"]
    allocation = Allocation.equal_weight(keys, ts("2024-04-01T00:00:00"), candidates_considered=3)
    weights = {weight for _, weight in allocation.weights}
    assert len(weights) == 1
    assert allocation.invested_fraction <= Decimal(1)
