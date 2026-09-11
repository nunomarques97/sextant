"""The criteria, the verdict letter and the break-even, against hand-built results.

The analysis is the only place a criterion is answered and the only place the verdict
letter is chosen, so it is the place a mistake would be least visible and most
expensive. These tests build minimal result files by hand and assert the answers,
concentrating on the failure modes that would flatter a conclusion:

- **a criterion answered False where it should be None.** ``False`` says the variant
  failed; ``None`` says the data could not say. Collapsing them turns a (C) into a
  (B), which is the wrong kind of wrong;
- **criterion 1 without its sign condition.** SEXTANT-005 recorded its own criterion 1
  misfiring: a variant beat the 95th percentile of its null by losing less than chance
  lost. The corrected criterion requires a positive net return too;
- **a verdict letter softened.** (B) and (C) are answers, not failures to reach (A),
  and the letter is chosen by a rule rather than by judgement;
- **a break-even computed from total cost rather than the fee line.** Spread and
  slippage scale with turnover and are excluded on purpose, which is what makes the
  figures upper bounds. Charging them here would understate allowable turnover and
  overstate the fee line's implied round trips.

Nothing here writes to real project data: every payload is built in memory.
"""

from __future__ import annotations

from decimal import Decimal
from math import sqrt

import pytest

from sextant.app.spike_006_f1 import (
    ACCOUNT_EQUITY,
    EXECUTION_FEE_OF_EQUITY_BPS,
    execution_sensitivity,
)
from sextant.app.spike_006_f1_analysis import (
    Capacity,
    CapacityVerdict,
    CostLines,
    Rescue,
    ResultsIncomplete,
    SpreadAcquisition,
    Toll,
    analyse,
    assumption_could_be_carrying_the_verdict,
    break_even_of,
    capacity_of,
    combined_toll,
    depth_sample_is_needed,
    excluding_month,
    opening_instants,
    rescues,
    spread_acquisition,
    tolls,
    verdict,
)
from sextant.app.spike_006_f1_report import (
    _contraction_section,
    _estimator_section,
    _samples_section,
    _strengthened_criterion,
)
from sextant.app.spike_006_f1_run import (
    CarryWithoutFunding,
    Deterministic,
    Results,
    _refuse_a_carry_run_without_funding,
)
from sextant.domain.money import Notional
from sextant.domain.time import Timestamp
from sextant.engine.backtest.ledger import CostLines as LedgerCostLines
from sextant.engine.backtest.ledger import LedgerBuilder, RebalanceOutcome
from sextant.engine.execution.breakeven import MONTHLY_ROUND_TRIPS
from sextant.engine.statistics.dsr import corrected_sharpe_standard_error
from sextant.engine.statistics.metrics import PerformanceStatistics

HEADLINE = "vip0_even"

#: The execution-venue sensitivity cell, which rule S1 excludes: a different fee
#: schedule is a different condition.
EXECUTION_CELL = execution_sensitivity().label
CELLS = ("vip0_maker", HEADLINE, "vip0_taker", "stress")


def months(count: int, value: str, *, start_year: int = 2022) -> dict[str, str]:
    """A monthly series whose mean is ``value`` and whose volatility is not zero.

    The wobble matters: a perfectly constant series has no standard deviation, so its
    Sharpe is zero however large its mean is, and a fixture built that way would test
    the arithmetic on a degenerate case the real data never produces.
    """
    centre = Decimal(value)
    wobble = abs(centre) / 2 if centre else Decimal("0.001")
    out: dict[str, str] = {}
    for index in range(count):
        year = start_year + index // 12
        month = index % 12 + 1
        step = wobble if index % 2 == 0 else -wobble
        out[f"{year}-{month:02d}-01T00:00:00+00:00"] = str(centre + step)
    return out


def run(
    *,
    construct: str,
    cell: str = HEADLINE,
    kind: str = "variant",
    monthly: dict[str, str] | None = None,
    terminal: str = "0.10",
    gross: str = "200",
    fees: str = "50",
    rebalances: int | None = None,
) -> dict[str, object]:
    """One deterministic run, in the shape the result file writes it."""
    return {
        "construct": construct,
        "kind": kind,
        "cell_id": cell,
        "fill_mix": "half-and-half",
        "terminal_return": terminal,
        "gross_pnl": gross,
        "costs": {
            "fees": fees,
            "spread": "10",
            "slippage": "10",
            "funding": "-30",
            "fx_conversion": "3",
            "delisting": "0",
            "total": str(Decimal(fees) + Decimal(-7)),
        },
        "monthly_returns": monthly if monthly is not None else months(40, "0.002"),
        "observations": len(monthly) if monthly is not None else 40,
        "rebalances": rebalances,
    }


def null(
    *, construct: str, cell: str = HEADLINE, p95: float, recent_p95: float, variance: float = 0.04
) -> dict[str, object]:
    """One seeded null, reduced as the result file reduces it."""
    percentiles = {"95.0": {"value": p95, "ci_low": p95, "ci_high": p95, "ci_width": 0.0}}
    recent = {
        "95.0": {"value": recent_p95, "ci_low": recent_p95, "ci_high": recent_p95, "ci_width": 0.0}
    }
    return {
        "construct": construct,
        "cell_id": cell,
        "fill_mix": "half-and-half",
        "sharpe": {"variance": variance, "percentiles": percentiles},
        "recent_window_sharpe": {"variance": variance, "percentiles": recent},
        "recent_window_months": 24,
    }


def payload(
    *,
    deterministic: list[dict[str, object]],
    nulls: list[dict[str, object]],
    trials: int = 12000,
    regime_months: dict[str, int] | None = None,
) -> dict[str, object]:
    """A minimal result file carrying only what the analysis reads."""
    counts = (
        regime_months if regime_months is not None else {"bull": 20, "bear": 10, "recovery": 10}
    )
    regimes = []
    index = 0
    for label, count in counts.items():
        for _ in range(count):
            year, month = 2022 + index // 12, index % 12 + 1
            regimes.append({"at": f"{year}-{month:02d}-01T00:00:00+00:00", "regime": label})
            index += 1
    return {
        "deterministic": deterministic,
        "nulls": nulls,
        "regimes": regimes,
        "regime_month_counts_scored": counts,
        "trials": {"including_nulls": trials, "excluding_nulls": 36},
    }


# ---------------------------------------------------------------------------
# Criterion 1 and its sign condition
# ---------------------------------------------------------------------------


def one_variant(*, terminal: str, sharpe_months: str, p95: float) -> dict[str, object]:
    """A payload with a single variant in the headline cell and its own null."""
    return payload(
        deterministic=[
            run(construct="v", terminal=terminal, monthly=months(40, sharpe_months)),
        ],
        nulls=[null(construct="v/exposure-matched", p95=p95, recent_p95=p95)],
    )


def test_beating_the_null_by_losing_less_than_chance_does_not_clear_criterion_one() -> None:
    """The SEXTANT-005 misfire, refused by the sign condition amendment 1 added."""
    rows = analyse(one_variant(terminal="-0.35", sharpe_months="-0.01", p95=-9.0))
    row = rows[0]
    assert row.sharpe is not None
    assert row.sharpe > -9.0, "the fixture must actually beat its null, or it tests nothing"
    assert row.net_return < 0
    assert row.criteria.beats_exposure_matched_null is False


def test_a_positive_variant_above_its_null_clears_criterion_one() -> None:
    rows = analyse(one_variant(terminal="0.30", sharpe_months="0.01", p95=0.1))
    assert rows[0].criteria.beats_exposure_matched_null is True


def test_a_variant_with_no_null_answers_none_rather_than_false() -> None:
    """No null means the criterion could not be evaluated, not that it failed."""
    rows = analyse(
        payload(deterministic=[run(construct="v", terminal="0.30")], nulls=[]),
    )
    assert rows[0].criteria.beats_exposure_matched_null is None
    assert rows[0].criteria.survives_deflation is None


# ---------------------------------------------------------------------------
# Criterion 3, the selection share
# ---------------------------------------------------------------------------


def with_selection(*, combined: str, selection: str) -> tuple[bool | None, ...]:
    """Criterion 3's answer for one combined and selection pair."""
    rows = analyse(
        payload(
            deterministic=[
                run(construct="v", terminal=combined),
                run(construct="v/selection-only", kind="selection_only", terminal=selection),
            ],
            nulls=[null(construct="v/exposure-matched", p95=0.1, recent_p95=0.1)],
        )
    )
    return (rows[0].criteria.win_is_selection,)


def test_selection_must_account_for_half_the_combined_excess() -> None:
    assert with_selection(combined="0.20", selection="0.12") == (True,)
    assert with_selection(combined="0.20", selection="0.10") == (True,)
    assert with_selection(combined="0.20", selection="0.09") == (False,)


def test_a_losing_book_cannot_clear_the_selection_criterion() -> None:
    """A share of a negative excess is not a win, whatever the ratio says."""
    assert with_selection(combined="-0.20", selection="-0.05") == (False,)


def test_a_negative_selection_effect_fails_even_on_a_winning_book() -> None:
    assert with_selection(combined="0.20", selection="-0.01") == (False,)


# ---------------------------------------------------------------------------
# Criterion 5, sign stability
# ---------------------------------------------------------------------------


def across_cells(returns: dict[str, str]) -> bool | None:
    """Criterion 5's answer for a variant run in the named cells."""
    rows = analyse(
        payload(
            deterministic=[
                run(construct="carry-basket-5", cell=cell, terminal=value)
                for cell, value in returns.items()
            ],
            nulls=[null(construct="carry-basket-5/exposure-matched", p95=0.1, recent_p95=0.1)],
        )
    )
    return rows[0].criteria.sign_stable_across_cells


def test_the_sign_must_hold_in_all_four_cells() -> None:
    assert across_cells(dict.fromkeys(CELLS, "0.10")) is True
    assert across_cells(dict.fromkeys(CELLS, "-0.10")) is True


def test_a_sign_flip_between_cost_regimes_fails_criterion_five() -> None:
    flipped = dict.fromkeys(CELLS, "0.10")
    flipped["stress"] = "-0.02"
    assert across_cells(flipped) is False


def test_a_variant_missing_a_cell_answers_none_not_true() -> None:
    """ "The sign held in the three cells we have" is not the registered criterion."""
    partial = dict.fromkeys(CELLS[:3], "0.10")
    assert across_cells(partial) is None


# ---------------------------------------------------------------------------
# The verdict letter
# ---------------------------------------------------------------------------


def test_nothing_clearing_criterion_one_is_b() -> None:
    rows = analyse(one_variant(terminal="-0.35", sharpe_months="-0.01", p95=-9.0))
    outcome = verdict(rows, headline_cell=HEADLINE, scored_months=40)
    assert outcome.letter == "B"
    assert "Criterion 1 is the floor" in outcome.reason


def test_a_short_window_is_c_whatever_the_returns_say() -> None:
    rows = analyse(one_variant(terminal="0.30", sharpe_months="0.01", p95=0.1))
    outcome = verdict(rows, headline_cell=HEADLINE, scored_months=30)
    assert outcome.letter == "C"
    assert "scored months" in outcome.reason


def test_clearing_criterion_one_but_not_all_six_is_c_and_says_so() -> None:
    rows = analyse(one_variant(terminal="0.30", sharpe_months="0.01", p95=0.1))
    outcome = verdict(rows, headline_cell=HEADLINE, scored_months=40)
    assert outcome.letter == "C"
    assert "not a weaker (A)" in outcome.reason
    assert outcome.clearing_criterion_one == ("v",)
    assert outcome.clearing_all_six == ()


def test_the_verdict_never_reports_a_letter_outside_the_three() -> None:
    for terminal, p95, scored in (("-0.35", -9.0, 40), ("0.30", 0.1, 40), ("0.30", 0.1, 30)):
        rows = analyse(one_variant(terminal=terminal, sharpe_months="0.01", p95=p95))
        assert verdict(rows, headline_cell=HEADLINE, scored_months=scored).letter in {"A", "B", "C"}


# ---------------------------------------------------------------------------
# The break-even, on the fee line and nothing else
# ---------------------------------------------------------------------------


def lines(*, market: str, funding: str, fees: str, spread: str = "500") -> CostLines:
    """One cost breakdown, with a deliberately large spread line."""
    return CostLines(
        market_gain=Decimal(market),
        funding_received=Decimal(funding),
        fees=Decimal(fees),
        spread=Decimal(spread),
        slippage=Decimal(spread),
        conversion=Decimal("3"),
        delisting=Decimal(0),
        total=Decimal(market) + Decimal(spread) * 2 + Decimal(fees),
    )


def test_the_turnover_reads_the_fee_line_and_ignores_spread_and_slippage() -> None:
    """The defect the first render surfaced: total cost gave 40 round trips a year.

    Spread and slippage are excluded on purpose, which is what makes the figures
    upper bounds. Charging them here would inflate the implied turnover past the
    twelve-a-year a monthly rebalance can physically reach.
    """
    equity = ACCOUNT_EQUITY.amount
    fees = equity * Decimal("22.5") / Decimal(10000) * 2
    outcome = break_even_of(lines(market="0", funding="0", fees=str(fees)), 12)
    assert outcome is not None
    assert outcome.realised_round_trips_per_year == pytest.approx(Decimal(2), abs=0.01)


def test_realised_turnover_cannot_exceed_a_monthly_rebalance_on_a_monthly_book() -> None:
    """A sanity bound the arithmetic must respect, asserted rather than assumed."""
    equity = ACCOUNT_EQUITY.amount
    fees = equity * Decimal("22.5") / Decimal(10000) * 12
    outcome = break_even_of(lines(market="0", funding="0", fees=str(fees)), 12)
    assert outcome is not None
    assert outcome.realised_round_trips_per_year <= MONTHLY_ROUND_TRIPS


def test_funding_counts_towards_the_gross_carry_rather_than_against_it() -> None:
    """Funding is the return this family harvests, not a cost line."""
    with_funding = break_even_of(lines(market="0", funding="60", fees="10"), 12)
    without = break_even_of(lines(market="0", funding="0", fees="10"), 12)
    assert with_funding is not None
    assert without is not None
    assert with_funding.gross_return_bps_per_year > without.gross_return_bps_per_year


def test_a_book_with_no_gross_carry_has_no_break_even() -> None:
    outcome = break_even_of(lines(market="-100", funding="0", fees="10"), 12)
    assert outcome is not None
    assert outcome.break_even_at_research_fees is None
    assert outcome.break_even_at_execution_fees is None
    assert outcome.d2b_holds is None


def test_a_run_with_no_months_yields_no_break_even() -> None:
    assert break_even_of(lines(market="100", funding="0", fees="10"), 0) is None


def test_the_execution_denominator_is_the_registered_one() -> None:
    outcome = break_even_of(lines(market="0", funding="150", fees="10"), 12)
    assert outcome is not None
    assert outcome.execution_fee_of_equity_bps == EXECUTION_FEE_OF_EQUITY_BPS


def test_the_break_even_reaches_the_serialised_row() -> None:
    rows = analyse(one_variant(terminal="0.30", sharpe_months="0.01", p95=0.1))
    block = rows[0].as_json()["break_even"]
    assert isinstance(block, dict)
    assert block["figures_are_upper_bounds"] is True


# ---------------------------------------------------------------------------
# Reading the file at all
# ---------------------------------------------------------------------------


def test_a_run_without_monthly_returns_is_refused_rather_than_guessed() -> None:
    broken = run(construct="v")
    del broken["monthly_returns"]
    with pytest.raises(ResultsIncomplete, match="monthly returns"):
        analyse(payload(deterministic=[broken], nulls=[]))


def test_a_run_without_a_cost_breakdown_is_refused() -> None:
    broken = run(construct="v")
    del broken["costs"]
    with pytest.raises(ResultsIncomplete, match="cost breakdown"):
        analyse(payload(deterministic=[broken], nulls=[]))


def test_a_file_without_a_trial_count_is_refused() -> None:
    incomplete = payload(deterministic=[run(construct="v")], nulls=[])
    del incomplete["trials"]
    with pytest.raises(ResultsIncomplete, match="trial count"):
        analyse(incomplete)


def test_a_month_is_attributed_to_the_regime_of_its_opening_instant() -> None:
    """Keying to the close would attribute a month to a state seen after the fact."""
    series = [(Timestamp.parse(at), Decimal("0.01")) for at in sorted(months(3, "0.01"))]
    openings = opening_instants(series)
    assert len(openings) == 2
    for closed_at, opened_at in openings.items():
        assert opened_at < closed_at


def _run_with_funding(funding: str) -> Deterministic:
    """One deterministic variant result carrying a given funding line."""
    opened = Timestamp.parse("2022-01-01T00:00:00+00:00")
    closed = Timestamp.parse("2022-02-01T00:00:00+00:00")
    equity = Notional(Decimal(1500))
    builder = LedgerBuilder(account_currency="EUR", initial_equity=equity)
    builder.open(opened)
    builder.record(
        RebalanceOutcome(
            opened_at=opened,
            closed_at=closed,
            equity_before=equity,
            equity_after=equity,
            trades=(),
            holdings=(),
            market_gain=Notional(Decimal(0)),
            financing=LedgerCostLines(funding=Notional(Decimal(funding))),
            cash=equity,
            candidates_considered=0,
            turnover=Notional(Decimal(0)),
        )
    )
    return Deterministic(
        construct="v",
        kind="variant",
        cell_id=HEADLINE,
        fill_mix="half-and-half",
        ledger=builder.build(),
        monthly=(),
    )


# ---------------------------------------------------------------------------
# The defect that produced a complete, plausible and wrong result file
# ---------------------------------------------------------------------------


def test_the_runner_refuses_a_carry_result_whose_funding_was_never_applied() -> None:
    """The guard that would have caught a whole grid run computed without its return.

    A cash-and-carry book holds the basis plus the funding stream. An engine built
    without a funding schedule falls back to a flat rate, produces a book that never
    receives its carry, and says nothing about it: the first execution of this grid
    ran to completion and reached a verdict with every funding line at exactly zero.
    """
    with pytest.raises(CarryWithoutFunding, match="never given the published settlements"):
        _refuse_a_carry_run_without_funding(Results(deterministic=[_run_with_funding("0")]))


def test_a_carry_result_with_a_funding_line_is_accepted() -> None:
    _refuse_a_carry_run_without_funding(
        Results(deterministic=[_run_with_funding("0"), _run_with_funding("-12.5")])
    )


def test_a_run_with_no_variants_at_all_is_not_the_guard_s_business() -> None:
    """Benchmarks alone are not a carry result, and the guard says nothing about them."""
    _refuse_a_carry_run_without_funding(Results(deterministic=[]))


#: Engine parameters whose defaults are right for a long-only single-leg strategy
#: and wrong for this family, each registered the other way. Both were verified by
#: the drift guard and wired by nothing, which is the failure this list exists for.
MUST_BE_WIRED = (
    (
        "funding=world.funding",
        "the engine falls back to a flat rate and the carry book never receives its carry",
    ),
    (
        "haircut_is_a_loss_on_either_side=HAIRCUT_ON_EITHER_SIDE",
        "a short leg that delists is recorded as a windfall instead of a loss",
    ),
)


@pytest.mark.parametrize(("keyword", "consequence"), MUST_BE_WIRED)
def test_the_f1_engine_is_built_with_the_registered_value(keyword: str, consequence: str) -> None:
    """The wiring itself, asserted, because forgetting it is what happened twice.

    A source check rather than a behavioural one, and deliberately so: the failure
    mode is a keyword that was never typed, not a value that behaves oddly once it
    is. A drift guard comparing the configuration against a literal proves the
    specification says what it says and proves nothing about whether anything read it.
    """
    import inspect

    from sextant.app import spike_006_f1_engine

    source = inspect.getsource(spike_006_f1_engine.build_engine)
    assert keyword in source, f"build_engine must pass {keyword}, or {consequence}."


# ---------------------------------------------------------------------------
# Rule C3: capacity, decided from series that already exist
# ---------------------------------------------------------------------------


def _capacity(inside: str, outside: str, *, inside_months: int = 16) -> Capacity:
    """One variant's C3 outcome for a given inside and outside monthly mean."""
    series: list[tuple[Timestamp, Decimal]] = []
    # Months whose whole holding period lies inside 2023-01-01..2024-05-17.
    for index in range(inside_months + 1):
        year, month = (2023, index + 1) if index < 12 else (2024, index - 11)
        series.append((Timestamp.parse(f"{year}-{month:02d}-01T00:00:00+00:00"), Decimal(inside)))
    # And a stretch clearly outside it.
    for index in range(12):
        series.append(
            (Timestamp.parse(f"2021-{index + 1:02d}-01T00:00:00+00:00"), Decimal(outside))
        )
    rows = analyse(one_variant(terminal="0.10", sharpe_months="0.002", p95=0.1))
    return capacity_of(rows[0], sorted(series, key=lambda item: item[0]))


def test_a_variant_that_earned_nothing_inside_the_window_is_unestablished() -> None:
    """C3's second row. No edge inside means no capacity there to report."""
    outcome = _capacity("-0.01", "0.05")
    assert outcome.verdict is CapacityVerdict.UNESTABLISHED_NO_EDGE_INSIDE
    assert outcome.verdict.needs_depth_data is False


def test_too_few_months_inside_the_window_is_unestablished() -> None:
    """C3's first row, and the floor is twelve."""
    outcome = _capacity("0.01", "0.01", inside_months=6)
    assert outcome.verdict is CapacityVerdict.UNESTABLISHED_TOO_FEW_MONTHS
    assert outcome.depth_months < 12
    assert outcome.verdict.needs_depth_data is False


def test_earning_less_inside_the_window_is_measured_with_the_sentence_beside_it() -> None:
    """C3's third row: measured, and the caveat travels with the number."""
    outcome = _capacity("0.01", "0.05")
    assert outcome.verdict is CapacityVerdict.MEASURED_WITH_A_CAVEAT
    assert outcome.verdict.needs_depth_data is True


def test_earning_at_least_as_much_inside_the_window_is_measured() -> None:
    outcome = _capacity("0.05", "0.01")
    assert outcome.verdict is CapacityVerdict.MEASURED
    assert outcome.verdict.needs_depth_data is True


def test_unestablished_is_never_reported_as_a_capacity_of_zero() -> None:
    """A dataset that cannot say is not a strategy that cannot scale."""
    payload = _capacity("-0.01", "0.05").as_json()
    assert "unestablished" in str(payload["verdict"])
    assert "does not mean the strategy has no capacity" in str(payload["note"])


def test_the_depth_window_travels_with_every_capacity_figure() -> None:
    """Rule C1: no capacity figure in the report omits its window."""
    payload = _capacity("0.05", "0.01").as_json()
    assert payload["depth_window"] == "2023-01-01/2024-05-17"


def test_the_depth_sample_is_needed_only_when_something_is_measurable() -> None:
    """The question that decides whether any order-book data is acquired at all."""
    assert depth_sample_is_needed([_capacity("-0.01", "0.05")]) is False
    assert depth_sample_is_needed([_capacity("0.05", "0.01")]) is True
    assert depth_sample_is_needed([_capacity("-0.01", "0.05"), _capacity("0.05", "0.01")]) is True


# ---------------------------------------------------------------------------
# Rule S1: whether the spread sample is acquired at all
# ---------------------------------------------------------------------------


def _acquisition(*terminals: str, cells: tuple[str, ...] = (HEADLINE,)) -> SpreadAcquisition:
    """Rule S1 over one variant per terminal return, in the given cells."""
    runs = [
        run(construct=f"v{index}", cell=cell, terminal=terminal)
        for cell in cells
        for index, terminal in enumerate(terminals)
    ]
    return spread_acquisition(analyse(payload(deterministic=runs, nulls=[])))


def test_the_spread_sample_is_not_acquired_when_nothing_earns() -> None:
    """The case rule S1 exists for: 3 GB that could not change a conclusion.

    Spread can only make a variant look worse. Where no variant earns at research
    fees, measuring it refines a cost line on a book that does not earn.
    """
    outcome = _acquisition("-0.20", "-0.05", "-0.01")
    assert outcome.acquire is False
    assert outcome.positive_count == 0
    assert outcome.considered == 3


def test_one_earning_variant_is_enough_to_acquire_the_spread_sample() -> None:
    """The other case: the assumption is load-bearing for a verdict, so it is measured."""
    outcome = _acquisition("-0.20", "0.04")
    assert outcome.acquire is True
    assert outcome.positive_count == 1
    assert outcome.best_net_return == Decimal("0.04")


def test_rule_s1_has_no_margin_around_zero() -> None:
    """A margin would be a threshold chosen with the answer's shape already visible."""
    assert _acquisition("0").acquire is False
    assert _acquisition("0.0001").acquire is True


def test_rule_s1_reads_every_registered_cell_and_not_only_the_headline() -> None:
    """A variant that earns in the maker cell is a variant spread could kill."""
    outcome = _acquisition("-0.10", cells=(HEADLINE,))
    assert outcome.acquire is False
    both = _acquisition("-0.10", cells=(HEADLINE, "vip0_maker"))
    assert both.considered == 2
    assert both.acquire is False


def test_rule_s1_ignores_the_execution_venue_cell() -> None:
    """S1 is a condition at research fees; Kraken's schedule is a different condition."""
    outcome = _acquisition("0.30", cells=(EXECUTION_CELL,))
    assert outcome.considered == 0
    assert outcome.acquire is False


def test_the_acquisition_decision_is_reported_either_way() -> None:
    """Not acquiring is a reported decision, not a silence."""
    refused = _acquisition("-0.10").as_json()
    assert refused["acquire_the_spread_sample"] is False
    assert refused["rule"] == "S1"
    assert refused["symbol_days_if_acquired"] == 36
    assert refused["cells_excluded"] == [EXECUTION_CELL]
    taken = _acquisition("0.10").as_json()
    assert taken["acquire_the_spread_sample"] is True
    assert taken["best_net_return"] == "0.10"


def test_section_fourteen_renders_from_a_file_that_carries_neither_decision() -> None:
    """The report recomputes both rules instead of reading the runner's blocks.

    A section that read ``payload["capacity"]`` would print nothing, silently, for a
    file written by an earlier runner - which is exactly the situation the second
    execution creates, since it started before rule S1 existed. Reaching for a private
    helper here is deliberate: the failure is in one section of the page, and rendering
    the whole document would need a fixture larger than the thing under test.
    """
    payload_without = payload(
        deterministic=[run(construct="v", terminal="-0.10", monthly=months(40, "-0.002"))],
        nulls=[],
    )
    assert "capacity" not in payload_without
    assert "spread_sample" not in payload_without
    section = _samples_section(payload_without)
    assert "## 14." in section
    assert "Rule C3" in section
    assert "Rule S1" in section
    assert "Spread sample acquired: no" in section
    assert "Depth sample required: no" in section

    earning = payload(
        deterministic=[run(construct="v", terminal="0.10", monthly=months(40, "0.002"))],
        nulls=[],
    )
    both = _samples_section(earning)
    assert "Spread sample acquired: yes" in both
    assert "Depth sample required: yes" in both


# ---------------------------------------------------------------------------
# Amendment 26.1: the headline with and without the contraction month
# ---------------------------------------------------------------------------


def test_dropping_the_contraction_month_compounds_the_rest() -> None:
    """Both figures come from the same series, so the comparison is like for like."""
    series = {
        "2026-05-01T00:00:00+00:00": "0.10",
        "2026-06-01T00:00:00+00:00": "-0.50",
        "2026-07-01T00:00:00+00:00": "0.20",
    }
    payload_with = payload(
        deterministic=[run(construct="v", terminal="0.10", monthly=series)], nulls=[]
    )
    rows = excluding_month(
        payload_with, Timestamp.parse("2026-07-01T00:00:00+00:00"), cell=HEADLINE
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.net_return == Decimal("1.10") * Decimal("0.50") * Decimal("1.20") - Decimal(1)
    assert row.net_return_without == Decimal("1.10") * Decimal("0.50") - Decimal(1)
    assert row.difference == row.net_return - row.net_return_without


def test_a_variant_whose_series_never_held_the_month_is_not_listed() -> None:
    """Nothing was dropped, so there is no with-and-without to report."""
    series = {"2026-05-01T00:00:00+00:00": "0.10", "2026-06-01T00:00:00+00:00": "-0.50"}
    rows = excluding_month(
        payload(deterministic=[run(construct="v", terminal="0.1", monthly=series)], nulls=[]),
        Timestamp.parse("2026-07-01T00:00:00+00:00"),
        cell=HEADLINE,
    )
    assert rows == ()


def test_the_contraction_section_is_empty_when_no_check_was_written() -> None:
    """The section is written by the composition check, not by the grid."""
    assert _contraction_section(payload(deterministic=[], nulls=[]), None) == ""


def test_the_decomposition_table_adds_up_the_way_it_is_printed() -> None:
    """price legs + funding - charges = net, and it equals the ledger's own net.

    The two routes to the same number are the point. The ledger's cost total already
    nets the funding receipt, because a receipt is a negative cost line; the printed
    table puts funding on the return side instead and excludes it from charges. Both
    must land on the same net, or one of the two is counting funding twice.
    """
    rows = analyse(payload(deterministic=[run(construct="v", gross="200", fees="50")], nulls=[]))
    block = rows[0].decomposition
    assert block.charges == Decimal(50) + Decimal(10) + Decimal(10) + Decimal(3) + Decimal(0)
    assert block.funding == Decimal(30), "a paid receipt is a negative cost line"
    assert block.net == block.price + block.funding - block.charges
    assert block.net == block.price - block.costs, "the ledger's own route to the same net"


def test_the_carry_and_the_ledger_total_are_not_subtracted_from_each_other() -> None:
    """The arithmetic the old table invited, named so nobody repeats it."""
    rows = analyse(payload(deterministic=[run(construct="v", gross="200", fees="50")], nulls=[]))
    block = rows[0].decomposition
    double_counted = block.gross - block.costs
    assert double_counted != block.net
    assert double_counted == block.net + block.funding, "off by the funding, twice counted"


# ---------------------------------------------------------------------------
# What the toll is made of, and what rests on an assumption
# ---------------------------------------------------------------------------


def _toll() -> Toll:
    """One run's charges, from the fixture's own cost block."""
    return tolls(
        payload(deterministic=[run(construct="v", gross="200", fees="50")], nulls=[]),
        cell=HEADLINE,
    )[0]


def test_funding_is_not_one_of_the_charges() -> None:
    """The line that decides what a negative verdict claims, asserted rather than assumed."""
    line = _toll().lines
    assert (
        line.charges == line.fees + line.spread + line.slippage + line.conversion + line.delisting
    )
    assert line.charges == line.assumed + line.contractual
    assert line.funding_received not in (line.charges, -line.charges)


def test_the_assumed_share_is_spread_and_slippage_and_nothing_else() -> None:
    """Invariant 12's two configured lines, separated from the published ones."""
    line = _toll().lines
    assert line.assumed == line.spread + line.slippage
    assert line.contractual == line.fees + line.conversion + line.delisting


def test_scaling_the_assumption_moves_only_the_two_assumed_lines() -> None:
    """A sensitivity that touched a published fee would not be a sensitivity on spread."""
    line = _toll().lines
    assert line.net_at(Decimal(1)) == line.net
    assert line.net_at(Decimal(0)) == line.net + line.assumed
    halved = line.net_at(Decimal("0.5"))
    assert halved - line.net == line.assumed / 2


def test_the_flip_multiplier_is_where_the_sign_turns() -> None:
    """The number the circularity in rule S1 turns on, so it gets its own assertion."""
    line = _toll().lines
    flip = line.flip_multiplier
    assert flip is not None
    assert line.net_at(flip) == Decimal(0)


def test_a_run_that_loses_with_the_assumption_deleted_reports_a_multiplier_at_or_below_zero() -> (
    None
):
    """The stronger statement: no spread measurement could change this variant's sign."""
    losing = tolls(
        payload(deterministic=[run(construct="v", gross="-500", fees="50")], nulls=[]),
        cell=HEADLINE,
    )[0]
    flip = losing.lines.flip_multiplier
    assert flip is not None
    assert flip <= 0
    assert losing.lines.net_at(Decimal(0)) < 0


def test_the_combined_toll_adds_the_parts_and_not_the_totals() -> None:
    """A family-level statement needs a family-level total, summed line by line."""
    items = tolls(
        payload(
            deterministic=[
                run(construct="a", gross="200", fees="50"),
                run(construct="b", gross="100", fees="20"),
            ],
            nulls=[],
        ),
        cell=HEADLINE,
    )
    combined = combined_toll(items)
    assert combined.fees == Decimal(70)
    assert combined.charges == sum(item.lines.charges for item in items)
    assert combined.net == sum((item.lines.net for item in items), Decimal(0))


def test_the_toll_records_its_shares_and_says_funding_is_not_in_them() -> None:
    payload_json = _toll().as_json()
    shares = payload_json["shares"]
    assert isinstance(shares, dict)
    assert set(shares) == {"fees", "spread", "slippage", "fx_conversion", "delisting_haircut"}
    assert "Funding is not a part of this total" in str(payload_json["note"])


# ---------------------------------------------------------------------------
# Rule S1 as amendment 9 states it
# ---------------------------------------------------------------------------


def _noisy(count: int, mean: str, swing: str) -> dict[str, str]:
    """A monthly series with a chosen mean and a chosen volatility.

    ``months`` ties the wobble to the mean, which fixes the Sharpe at 2.0 a month
    however small the return is. Amendment 9's bar is a Sharpe comparison, so a fixture
    for it has to be able to hold a small mean beside a large swing.
    """
    centre, wobble = Decimal(mean), Decimal(swing)
    out: dict[str, str] = {}
    for index in range(count):
        year, month = 2022 + index // 12, index % 12 + 1
        step = wobble if index % 2 == 0 else -wobble
        out[f"{year}-{month:02d}-01T00:00:00+00:00"] = str(centre + step)
    return out


def _rescue(*, gross: str, fees: str, p95: float, monthly: dict[str, str]) -> Rescue:
    """One variant and its null, put through amendment 9's counterfactual."""
    return rescues(
        payload(
            deterministic=[run(construct="v", gross=gross, fees=fees, monthly=monthly)],
            nulls=[null(construct="v/exposure-matched", p95=p95, recent_p95=p95)],
        ),
        cell=HEADLINE,
    )[0]


def test_removing_the_assumed_cost_lifts_the_series_by_exactly_that_amount() -> None:
    """The counterfactual is exact in the total, whatever it does to the volatility."""
    item = _rescue(gross="200", fees="50", p95=0.1, monthly=_noisy(40, "-0.002", "0.05"))
    assert item.assumed_cost == Decimal(20), "the fixture charges 10 spread and 10 slippage"
    assert item.net_return_at_zero > item.net_return


def test_a_sign_change_alone_does_not_clear_the_rule() -> None:
    """Amendment 9's whole point: rescued by rounding is not rescued by the assumption.

    The variant turns positive once the assumed cost is removed and still sits below
    its own null, so no measurement of that cost could change the verdict.
    """
    item = _rescue(gross="200", fees="50", p95=1.5, monthly=_noisy(40, "0.004", "0.05"))
    assert item.net_return_at_zero > 0
    assert item.beats_its_null is False
    assert item.clears_criterion_one is False


def test_clearing_the_null_while_earning_does_fire_the_rule() -> None:
    """Both halves of criterion 1, applied to the counterfactual."""
    item = _rescue(gross="200", fees="50", p95=-9.0, monthly=_noisy(40, "0.004", "0.05"))
    assert item.net_return_at_zero > 0
    assert item.beats_its_null is True
    assert item.clears_criterion_one is True
    assert assumption_could_be_carrying_the_verdict([item]) is True


def test_a_variant_that_still_loses_at_zero_cost_never_fires_the_rule() -> None:
    """The seven-of-nine case: no spread measurement could change the sign."""
    item = _rescue(gross="-900", fees="50", p95=-9.0, monthly=_noisy(40, "-0.02", "0.05"))
    assert item.net_return_at_zero < 0
    assert item.clears_criterion_one is False
    assert assumption_could_be_carrying_the_verdict([item]) is False


def test_a_rescue_with_no_null_answers_none_rather_than_false() -> None:
    """No null means the rule could not be evaluated, not that it was not met."""
    item = rescues(
        payload(deterministic=[run(construct="v", gross="200")], nulls=[]), cell=HEADLINE
    )[0]
    assert item.beats_its_null is None
    assert item.clears_criterion_one is None


def test_clearing_criterion_one_at_zero_cost_is_not_clearing_the_verdict() -> None:
    """Criterion 1 is one of six, and criterion 2 is asked of the same counterfactual.

    A rule that fired on criterion 1 alone would invite the reading that the verdict
    was about to turn. The deflation says otherwise at this trial count, and the
    report prints both.
    """
    item = _rescue(gross="200", fees="50", p95=-9.0, monthly=_noisy(40, "0.004", "0.05"))
    assert item.clears_criterion_one is True
    assert item.deflated_at_zero is not None
    assert item.survives_deflation_at_zero is False


def test_the_counterfactual_records_the_model_it_was_computed_under() -> None:
    """A modelled figure that does not say so is indistinguishable from a measured one."""
    payload_json = _rescue(
        gross="200", fees="50", p95=0.1, monthly=_noisy(40, "0.001", "0.05")
    ).as_json()
    assert "equal instalments" in str(payload_json["model"])
    assert "approximate in the volatility" in str(payload_json["model"])


# ---------------------------------------------------------------------------
# Amendment 10: the floor, and the error bar it is stated in
# ---------------------------------------------------------------------------


def test_the_corrected_standard_error_exceeds_the_normal_one_on_a_fat_tailed_series() -> None:
    """The floor is stated in this unit, so the unit has to be the honest one.

    The plain standard error assumes normal returns and says so in its own docstring.
    On the negatively skewed, fat-tailed series this project actually produces, the
    corrected figure is larger, which makes the bar higher rather than lower.
    """
    plain = sqrt((1.0 + 0.5 * 0.1**2) / 56.0)
    corrected = corrected_sharpe_standard_error(
        sharpe_per_period=0.1, observations=56, skewness=-1.2, kurtosis=8.0
    )
    assert corrected > plain


def test_the_floor_is_stricter_than_criterion_one_alone() -> None:
    """A counterfactual just above a losing null clears the one and not the other.

    F1's recorded case, in miniature: a small positive Sharpe against a null whose 95th
    percentile is below zero. Criterion 1 is satisfied because the null is losing, and the
    settled floor is not, because the Sharpe does not exceed *zero* by one standard error
    of itself.
    """
    item = _rescue(gross="200", fees="50", p95=-0.2, monthly=_noisy(40, "0.004", "0.05"))
    assert item.sharpe_at_zero is not None
    assert item.standard_error_at_zero is not None
    assert item.clears_criterion_one is True
    assert item.sharpe_at_zero < item.standard_error_at_zero
    assert item.clears_zero_by_a_standard_error is False
    assert item.clears_by_a_standard_error is False


def test_a_sharpe_clear_of_zero_by_its_own_error_bar_clears_the_settled_floor() -> None:
    """Both clauses hold: it clears zero by a standard error and it clears its null."""
    item = _rescue(gross="200", fees="50", p95=1.0, monthly=_noisy(40, "0.02", "0.005"))
    assert item.sharpe_at_zero is not None
    assert item.standard_error_at_zero is not None
    assert item.sharpe_at_zero > item.standard_error_at_zero
    assert item.beats_its_null is True
    assert item.clears_zero_by_a_standard_error is True
    assert item.clears_by_a_standard_error is True


def test_clearing_zero_but_not_the_null_fails_the_floor() -> None:
    """Both clauses, never either. A strong result its own null also reaches is not one."""
    item = _rescue(gross="200", fees="50", p95=99.0, monthly=_noisy(40, "0.02", "0.005"))
    assert item.clears_zero_by_a_standard_error is True
    assert item.beats_its_null is False
    assert item.clears_by_a_standard_error is False


def test_the_two_anchors_disagree_on_exactly_the_case_that_prompted_the_amendment() -> None:
    """Amendment 10's floor fires here and amendment 11's does not, on one fixture.

    This is F1's own situation: the exposure-matched null loses over the window, so a bar
    stated relative to it sits below zero and a variant that merely fails to lose clears
    it. Both forms stay computable because section 33.1 supersedes amendment 10 rather
    than deleting it, and a superseded bar nobody can compute is one a later reader has to
    take on trust.
    """
    item = _rescue(gross="200", fees="50", p95=-9.0, monthly=_noisy(40, "0.004", "0.05"))
    assert item.clears_the_null_by_a_standard_error is True
    assert item.clears_by_a_standard_error is False


def test_the_floor_answers_none_where_it_cannot_be_evaluated() -> None:
    """No null means the floor could not be applied, not that it was not met."""
    item = rescues(
        payload(deterministic=[run(construct="v", gross="200")], nulls=[]), cell=HEADLINE
    )[0]
    assert item.clears_by_a_standard_error is None
    assert item.clears_the_null_by_a_standard_error is None


def test_the_floor_records_the_family_it_applies_from_and_its_anchor() -> None:
    """Prospective, so a reader of an F1 figure is told it did not govern F1."""
    payload_json = _rescue(
        gross="200", fees="50", p95=-9.0, monthly=_noisy(40, "0.004", "0.05")
    ).as_json()
    assert payload_json["floor_applies_from"] == "F2"
    assert payload_json["floor_anchor"] == "zero"
    assert payload_json["would_clear_rule_s1s_settled_floor"] is False
    assert payload_json["would_clear_amendment_10s_null_anchored_floor"] is True


# ---------------------------------------------------------------------------
# Amendment 11: criterion 1 strengthened, as a supplementary reading only
# ---------------------------------------------------------------------------


def _statistics_of_a_series(monthly: dict[str, str]) -> PerformanceStatistics:
    """The performance statistics of one monthly series, through the ordinary path."""
    rows = analyse(
        payload(
            deterministic=[run(construct="v", gross="200", monthly=monthly)],
            nulls=[null(construct="v/exposure-matched", p95=-9.0, recent_p95=-9.0)],
        )
    )
    statistics = rows[0].statistics
    assert statistics is not None
    return statistics


def test_the_t_statistic_is_the_sharpe_times_the_root_of_the_count() -> None:
    """The absolute clause, in the one form it can be written without annualising."""
    statistics = _statistics_of_a_series(_noisy(36, "0.02", "0.005"))
    assert statistics.mean_return_t_statistic == pytest.approx(
        statistics.sharpe_per_period * sqrt(float(statistics.observations))
    )


def test_the_t_statistic_does_not_move_with_the_annualisation() -> None:
    """Annualising scales the mean and its error alike, so the ratio is frequency-free.

    Asserted because a version of this that carried a root-twelve factor would look
    entirely plausible and would change which variants passed from F2 onward.
    """
    statistics = _statistics_of_a_series(_noisy(36, "0.02", "0.005"))
    assert statistics.sharpe_annualised > statistics.sharpe_per_period
    assert statistics.mean_return_t_statistic == pytest.approx(
        statistics.sharpe_per_period * sqrt(float(statistics.observations))
    )


def test_a_return_inside_its_own_error_bar_fails_the_strengthened_clause() -> None:
    """The 18.70-EUR case: positive, and not distinguishable from nothing."""
    rows = analyse(
        payload(
            deterministic=[run(construct="v", gross="200", monthly=_noisy(40, "0.0005", "0.05"))],
            nulls=[null(construct="v/exposure-matched", p95=-9.0, recent_p95=-9.0)],
        )
    )
    criteria = rows[0].criteria
    assert criteria.beats_exposure_matched_null is True
    assert criteria.return_t_statistic is not None
    assert criteria.return_is_distinguishable_from_zero is False
    assert criteria.criterion_one_strengthened is False


def test_a_return_outside_its_own_error_bar_holds_both_forms() -> None:
    """Strictly narrowing means every pass of the stronger form is a pass of the weaker."""
    rows = analyse(
        payload(
            deterministic=[run(construct="v", gross="200", monthly=_noisy(40, "0.02", "0.005"))],
            nulls=[null(construct="v/exposure-matched", p95=-9.0, recent_p95=-9.0)],
        )
    )
    criteria = rows[0].criteria
    assert criteria.beats_exposure_matched_null is True
    assert criteria.return_is_distinguishable_from_zero is True
    assert criteria.criterion_one_strengthened is True


def test_the_strengthened_clause_never_reaches_the_verdict() -> None:
    """F1 was judged on the weaker form, and no code path lets the stronger one in.

    The mechanical statement of "not applied to F1": the strengthened reading is absent
    from ``answered``, so ``all_hold`` cannot see it however the figures fall.
    """
    rows = analyse(
        payload(
            deterministic=[run(construct="v", gross="200", monthly=_noisy(40, "0.0005", "0.05"))],
            nulls=[null(construct="v/exposure-matched", p95=-9.0, recent_p95=-9.0)],
        )
    )
    criteria = rows[0].criteria
    assert criteria.criterion_one_strengthened is False
    assert len(criteria.answered) == 6
    assert criteria.beats_exposure_matched_null in criteria.answered
    assert criteria.criterion_one_strengthened not in [criteria.answered[0]]


def test_the_supplementary_block_says_it_is_supplementary_and_from_where() -> None:
    """A stronger bar printed beside a verdict must say it did not produce the verdict."""
    rows = analyse(
        payload(
            deterministic=[run(construct="v", gross="200", monthly=_noisy(40, "0.0005", "0.05"))],
            nulls=[null(construct="v/exposure-matched", p95=-9.0, recent_p95=-9.0)],
        )
    )
    block = rows[0].criteria.as_json()["supplementary_1_strengthened"]
    assert isinstance(block, dict)
    assert block["applies_from"] == "F2"
    assert block["t_statistic_floor"] == 1.0
    assert "SUPPLEMENTARY" in str(block["note"])
    assert "strictly narrower" in str(block["note"])


# ---------------------------------------------------------------------------
# The report sections amendment 11 adds
# ---------------------------------------------------------------------------


def _calibration(*, ordering: bool, magnitude: bool, positivity: bool) -> dict[str, object]:
    """A minimal rule E1 calibration, shaped as the result file writes it."""
    return {
        "all_three_hold": ordering and magnitude and positivity,
        "clauses": {
            "ordering": {
                "statistic": 0.943,
                "floor": "0.771",
                "holds": ordering,
                "comparison_statistic": 0.943,
            },
            "magnitude": {"statistic": 6.413, "ceiling": "1", "holds": magnitude},
            "positivity": {
                "floor": "0.90",
                "holds": positivity,
                "registered_estimator": {
                    "share_strictly_positive": 0.426,
                    "too_few_pairs_to_estimate": 45004,
                    "instrument_periods_asked_for": 20615,
                    "instruments_scanned": 951,
                },
                "comparison_estimator": {"share_strictly_positive": 0.922},
            },
        },
        "calibration": {
            "per_symbol": [
                {
                    "symbol": "BTCUSDT",
                    "chosen_as": "top",
                    "measured_median_quoted_spread_bps": 0.0357,
                    "estimated_median_bps": 0.0,
                    "comparison_estimated_median_bps": 93.6643,
                    "estimated_over_measured": 0.0,
                }
            ]
        },
        "resolution": {
            "per_symbol": [
                {
                    "symbol": "BTCUSDT",
                    "two_day_term_standard_deviation": 5.619e-4,
                    "measured_squared_proportional_spread": 1.276e-11,
                    "two_day_pairs_needed_to_resolve_it": 1.939e15,
                }
            ]
        },
    }


def test_no_calibration_file_renders_no_estimator_section() -> None:
    """A section that has nothing to say says nothing, rather than printing headings."""
    assert _estimator_section(None) == ""


def test_a_failed_calibration_says_so_and_keeps_the_assumption() -> None:
    """The outcome is printed whatever it is: a rule reported only when it passes is not one."""
    rendered = _estimator_section(_calibration(ordering=True, magnitude=False, positivity=False))
    assert "NOT ADOPTED" in rendered
    assert "banded **assumption** is kept" in rendered
    assert "comparison estimator is not promoted" in rendered
    assert "1.939e+15" in rendered


def test_a_passing_calibration_would_say_the_estimate_becomes_the_default() -> None:
    """The other branch, exercised, so the section cannot only have been read one way."""
    rendered = _estimator_section(_calibration(ordering=True, magnitude=True, positivity=True))
    assert "ADOPTED" in rendered
    assert "NOT ADOPTED" not in rendered
    assert "default spread cost from F2" in rendered


def test_the_estimator_section_never_claims_to_move_an_f1_figure() -> None:
    """Whatever the clauses say, every F1 cell stays costed at the assumption."""
    for magnitude in (True, False):
        rendered = _estimator_section(
            _calibration(ordering=True, magnitude=magnitude, positivity=magnitude)
        )
        assert "No F1 figure" in rendered
        assert "costed at the registered assumption" in rendered


def test_a_result_file_without_the_supplementary_block_renders_nothing() -> None:
    """An older result file must not produce a half-empty table under a heading."""
    assert _strengthened_criterion([{"criteria": {}}]) == []


def test_the_supplementary_section_says_which_form_produced_the_verdict() -> None:
    """The first thing it says, because a stronger bar beside a letter invites the reading."""
    analysed = analyse(
        payload(
            deterministic=[run(construct="v", gross="200", monthly=_noisy(40, "0.0005", "0.05"))],
            nulls=[null(construct="v/exposure-matched", p95=-9.0, recent_p95=-9.0)],
        )
    )
    rendered = chr(10).join(_strengthened_criterion([row.as_json() for row in analysed]))
    assert "F1 was judged on criterion 1 as registered when it ran" in rendered
    assert "supplementary reading, not a re-scoring" in rendered
    assert "can only ever remove" in rendered
