"""The correctness tests from docs/PHASE-0-FINDINGS.md section 8.

These are not extras attached to the engine. They are how we know the engine is
not lying, and each one exists because a backtester that is wrong in a
flattering direction produces confident, plausible, worthless numbers.

Eight properties, in the order Phase 0 states them:

1. look-ahead, by two independent mechanisms - poisoned future and structural;
2. survivorship - the result must *differ* with and without delistings;
3. exact Decimal cost accounting - lives in ``test_cost_accounting.py``;
4. partially formed bars are never consumed;
5. reproducibility, as a golden-file test on the decision stream;
6. clock equivalence between a simulated clock and a recorded trace;
7. venue agnosticism - identical decisions, differing net results;
8. cross-sectional correctness - ranks move when the universe changes.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from sextant.adapters.clocks import SimulatedClock
from sextant.domain.decision import DecisionRecord
from sextant.domain.instrument import Instrument, InstrumentKey
from sextant.domain.market_data import OpenBarConsumed
from sextant.domain.money import Notional
from sextant.domain.time import Timeframe
from sextant.engine.backtest.allocation import ParameterFreeStrategy
from sextant.engine.backtest.baselines import EqualWeightPassive, RandomSelection
from sextant.engine.backtest.engine import BacktestEngine, EngineError, RunSummary
from sextant.engine.backtest.market import FutureReadRefused, PointInTimeView
from sextant.engine.backtest.window import WalkForwardPlan, anchored_plan
from sextant.engine.execution.costs import ItemisedCostModel
from sextant.engine.execution.fx import CurrencyRouting
from sextant.engine.execution.markout import SeriesEnd
from sextant.engine.features.cross_section import demeaned, rank_fractions, top_n
from sextant.ports.clock import Clock
from tests.harness import (
    OTHER_VENUE,
    VENUE,
    InMemoryBarRepository,
    RecordedClock,
    StaticSeriesEnd,
    StaticUniverse,
    TracingClock,
    cost_model,
    daily_bars,
    haircut,
    instrument,
    no_fx,
    ramp,
    ts,
)

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "decision_stream.json"

FIRST_MONTH = ts("2024-01-01T00:00:00")
DATA_START = "2023-09-01T00:00:00"
DATA_DAYS = 340

#: Four instruments on four different deterministic ramps, so that a
#: cross-sectional decision has something to be cross-sectional about.
PATHS: Mapping[str, tuple[str, str]] = {
    "AAAEUR": ("100", "0.50"),
    "BBBEUR": ("50", "0.10"),
    "CCCEUR": ("200", "-0.20"),
    "DDDEUR": ("10", "0.03"),
}


def build_instruments() -> dict[InstrumentKey, Instrument]:
    """The four candidates, keyed."""
    items = [instrument(symbol) for symbol in sorted(PATHS)]
    return {item.key: item for item in items}


def build_repository(
    instruments: Mapping[InstrumentKey, Instrument],
    *,
    stop_after: Mapping[str, int] | None = None,
) -> InMemoryBarRepository:
    """A repository holding every candidate's ramp.

    ``stop_after`` truncates a symbol's series to that many days, which is how a
    delisting is expressed in a fixture: the venue simply stops publishing.
    """
    repository = InMemoryBarRepository()
    cut = stop_after or {}
    for key, item in instruments.items():
        start, step = PATHS[key.symbol]
        days = cut.get(key.symbol, DATA_DAYS)
        repository.add(
            daily_bars(item, first_day=DATA_START, closes=ramp(days, start=start, step=step))
        )
    return repository


def build_engine(
    repository: InMemoryBarRepository,
    instruments: Mapping[InstrumentKey, Instrument],
    *,
    universe_keys: Sequence[InstrumentKey] | None = None,
    clock: Clock | None = None,
    series_end: StaticSeriesEnd | None = None,
    costs: ItemisedCostModel | None = None,
    record_decisions: bool = True,
) -> BacktestEngine:
    """An engine over the fixture, with every assumption stated by the caller."""
    keys = tuple(universe_keys if universe_keys is not None else sorted(instruments))
    return BacktestEngine(
        repository=repository,
        clock=clock or SimulatedClock(current=ts("2000-01-01T00:00:00")),
        timeframe=Timeframe.D1,
        instruments=instruments,
        universe=StaticUniverse(members={}, default=keys),
        cost_model=costs or cost_model(),
        fx=no_fx(),
        routing=CurrencyRouting(
            account_currency="EUR",
            quote_by_symbol={key.symbol: "EUR" for key in instruments},
        ),
        haircut=haircut(),
        series_end=series_end or StaticSeriesEnd(),
        initial_equity=Notional(Decimal(1500)),
        account_currency="EUR",
        record_decisions=record_decisions,
    )


def plan(total_months: int = 6, in_sample_months: int = 2, folds: int = 2) -> WalkForwardPlan:
    """The walk-forward plan every test in this module runs."""
    return anchored_plan(
        first_month=FIRST_MONTH,
        total_months=total_months,
        in_sample_months=in_sample_months,
        fold_count=folds,
    )


def run(engine: BacktestEngine, run_id: str = "test-run") -> RunSummary:
    """Run the equal-weight passive construct through the plan."""
    return engine.run(plan(), ParameterFreeStrategy(EqualWeightPassive()), run_id)


def projection(records: Sequence[DecisionRecord]) -> list[tuple[str, ...]]:
    """A decision reduced to what was decided, for comparing two runs."""
    return [
        (
            record.timestamp.isoformat(),
            record.symbol,
            record.strategy,
            record.signal,
            str(record.entry.amount if record.entry else None),
            str(record.size.amount if record.size else None),
            str(record.outcome),
        )
        for record in records
    ]


# -- 1. look-ahead, mechanism one: the poisoned future ------------------------


def test_poisoning_every_bar_after_an_instant_changes_no_earlier_decision() -> None:
    """Replace the future with garbage; everything decided before it must be identical.

    The cut is a rebalance instant, so every decision with an earlier timestamp
    both opened and closed on data that predates the poisoning. Those records
    are compared whole, including the realised outcome. If any of them moves,
    something in the engine read forward.
    """
    instruments = build_instruments()
    cut = ts("2024-05-01T00:00:00")

    clean = run(build_engine(build_repository(instruments), instruments))
    poisoned_repository = build_repository(instruments)
    poisoned_repository.replace_after(cut, "999999")
    poisoned = run(build_engine(poisoned_repository, instruments))

    before_clean = [record for record in clean.decisions if record.timestamp < cut]
    before_poisoned = [record for record in poisoned.decisions if record.timestamp < cut]

    assert before_clean, "the fixture produced no decisions before the cut"
    assert projection(before_clean) == projection(before_poisoned)

    after = [record for record in poisoned.decisions if record.timestamp >= cut]
    assert projection(after) != projection(
        [record for record in clean.decisions if record.timestamp >= cut]
    ), "poisoning the future changed nothing at all, so the fixture is not exercising anything"


# -- 1. look-ahead, mechanism two: structural ---------------------------------


def test_no_bar_handed_to_the_engine_closed_after_the_instant_it_was_asked_for() -> None:
    """Every read is checked against the ``as_of`` it was made under."""
    instruments = build_instruments()
    repository = build_repository(instruments)
    run(build_engine(repository, instruments))

    assert repository.reads, "the run read nothing, so this asserts nothing"
    leaks = [
        (as_of.isoformat(), str(bar.instrument), bar.close_time.isoformat())
        for as_of, bar in repository.reads
        if bar.close_time > as_of
    ]
    assert leaks == []


def test_a_repository_that_leaks_the_future_makes_the_view_refuse() -> None:
    """The view does not absorb a broken read path; it raises and names it."""
    instruments = build_instruments()
    repository = build_repository(instruments)
    repository.leak_future = True
    view = PointInTimeView.at(repository, Timeframe.D1, ts("2024-03-01T00:00:00"), lookback_days=60)
    with pytest.raises(FutureReadRefused):
        view.bars(list(instruments.values()))


# -- 2. survivorship ----------------------------------------------------------


def test_excluding_delisted_instruments_changes_the_result() -> None:
    """The point-in-time universe and the survivors-only universe must disagree.

    Asserting that membership resolves correctly is not enough. What has to be
    shown is that the bias is *live* in this fixture: excluding the instrument
    that dies changes the answer, and the instrument that dies actually appears
    in the point-in-time run's decisions.
    """
    instruments = build_instruments()
    dying = InstrumentKey(VENUE, "CCCEUR")
    # CCCEUR stops publishing after 200 days, which lands inside the second fold.
    repository = build_repository(instruments, stop_after={"CCCEUR": 200})
    ended = StaticSeriesEnd(reasons={dying: SeriesEnd.DELISTED})

    point_in_time = run(
        build_engine(repository, instruments, series_end=ended), run_id="point-in-time"
    )
    survivors_only = run(
        build_engine(
            build_repository(instruments, stop_after={"CCCEUR": 200}),
            instruments,
            universe_keys=[key for key in sorted(instruments) if key != dying],
            series_end=ended,
        ),
        run_id="survivors-only",
    )

    traded = {record.symbol for record in point_in_time.decisions}
    assert "CCCEUR" in traded, "the delisted instrument never entered the point-in-time run"
    assert "CCCEUR" not in {record.symbol for record in survivors_only.decisions}
    assert point_in_time.ledger.net_pnl != survivors_only.ledger.net_pnl
    assert point_in_time.ledger.costs.delisting.amount > 0


def test_the_haircut_only_applies_where_a_source_says_the_instrument_was_delisted() -> None:
    """A series that stops because the archive stops is not a delisting."""
    instruments = build_instruments()
    dying = InstrumentKey(VENUE, "CCCEUR")

    delisted = run(
        build_engine(
            build_repository(instruments, stop_after={"CCCEUR": 200}),
            instruments,
            series_end=StaticSeriesEnd(reasons={dying: SeriesEnd.DELISTED}),
        )
    )
    undetermined = run(
        build_engine(
            build_repository(instruments, stop_after={"CCCEUR": 200}),
            instruments,
            series_end=StaticSeriesEnd(reasons={dying: SeriesEnd.UNDETERMINED}),
        )
    )
    assert delisted.ledger.costs.delisting.amount > 0
    assert undetermined.ledger.costs.delisting.amount == 0
    assert delisted.ledger.net_pnl.amount < undetermined.ledger.net_pnl.amount


# -- 4. partially formed bars -------------------------------------------------


def test_an_open_bar_is_refused_where_a_closed_one_is_required() -> None:
    """``Bar.require_closed`` raises rather than returning the forming bar."""
    item = instrument("AAAEUR")
    (bar,) = daily_bars(item, first_day=DATA_START, closes=["100"], is_closed=False)
    with pytest.raises(OpenBarConsumed):
        bar.require_closed()


def test_the_default_read_excludes_open_bars() -> None:
    """``closed_only`` defaults to True, so the safe read is the unthinking one."""
    item = instrument("AAAEUR")
    repository = InMemoryBarRepository()
    repository.add(daily_bars(item, first_day=DATA_START, closes=["100", "101"]))
    repository.add(
        daily_bars(item, first_day="2023-09-03T00:00:00", closes=["102"], is_closed=False)
    )
    window = (ts(DATA_START), ts("2024-01-01T00:00:00"))
    assert len(repository.read([item], Timeframe.D1, *window)) == 2
    assert len(repository.read([item], Timeframe.D1, *window, closed_only=False)) == 3


def test_a_run_over_data_ending_in_an_open_bar_does_not_consume_it() -> None:
    """A full engine pass must produce the same answer with and without the open bar."""
    instruments = build_instruments()
    without = run(build_engine(build_repository(instruments), instruments))

    with_open = build_repository(instruments)
    for key, item in instruments.items():
        last = with_open.series[key][-1]
        with_open.series[key] = (
            *with_open.series[key],
            replace(last, open_time=last.open_time.plus(Timeframe.D1.duration), is_closed=False),
        )
        del item
    result = run(build_engine(with_open, instruments))
    assert projection(result.decisions) == projection(without.decisions)
    assert result.ledger.net_pnl == without.ledger.net_pnl


# -- 5. reproducibility -------------------------------------------------------


def test_the_decision_stream_is_byte_identical_to_the_golden_file() -> None:
    """Same seed, same data, same configuration, same bytes.

    A golden file rather than a self-comparison, because this is also what
    catches accidental dependence on dictionary ordering, on wall-clock time or
    on an unseeded generator - none of which a run compared against itself in
    the same process would reveal.
    """
    instruments = build_instruments()
    summary = build_engine(build_repository(instruments), instruments).run(
        plan(),
        ParameterFreeStrategy(RandomSelection.for_seed(seed=20260909, positions=2)),
        "golden",
    )
    produced = json.dumps(projection(summary.decisions), indent=1, sort_keys=True) + "\n"
    assert GOLDEN.is_file(), (
        f"{GOLDEN} is missing. It is committed on purpose: a golden file the test "
        "regenerates when absent is not a golden file."
    )
    assert produced == GOLDEN.read_text(encoding="utf-8")


def test_two_runs_of_the_same_seed_agree() -> None:
    """The weaker in-process check, kept because it localises a failure."""
    instruments = build_instruments()
    first = build_engine(build_repository(instruments), instruments).run(
        plan(), ParameterFreeStrategy(RandomSelection.for_seed(seed=11, positions=2)), "a"
    )
    second = build_engine(build_repository(instruments), instruments).run(
        plan(), ParameterFreeStrategy(RandomSelection.for_seed(seed=11, positions=2)), "b"
    )
    assert [record.symbol for record in first.decisions] == [
        record.symbol for record in second.decisions
    ]


def test_different_seeds_choose_differently() -> None:
    """Otherwise the seed is decorative and the null has one member."""
    instruments = build_instruments()
    chosen = []
    for seed in (1, 2, 3, 4):
        summary = build_engine(build_repository(instruments), instruments).run(
            plan(), ParameterFreeStrategy(RandomSelection.for_seed(seed, positions=2)), str(seed)
        )
        chosen.append(tuple(record.symbol for record in summary.decisions))
    assert len(set(chosen)) > 1


# -- 6. clock equivalence -----------------------------------------------------


def test_a_simulated_clock_and_a_recorded_trace_produce_the_same_decisions() -> None:
    """The claim "backtest and live run the same code", made testable.

    The first run drives a clock the engine can advance and records every
    instant it reported. The second replays that trace through a clock the
    engine *cannot* advance - the shape of a wall clock - and must decide
    identically.
    """
    instruments = build_instruments()
    tracing = TracingClock(inner=SimulatedClock(current=ts("2000-01-01T00:00:00")))
    simulated = run(build_engine(build_repository(instruments), instruments, clock=tracing))

    assert tracing.trace, "the engine never read the clock, so it is not driving anything"
    replayed = run(
        build_engine(
            build_repository(instruments),
            instruments,
            clock=RecordedClock(instants=list(tracing.trace)),
        )
    )
    assert projection(replayed.decisions) == projection(simulated.decisions)
    assert replayed.ledger.net_pnl == simulated.ledger.net_pnl


def test_a_clock_that_has_not_reached_the_rebalance_stops_the_run() -> None:
    """A rebalance the clock has not arrived at is refused, in any mode."""
    instruments = build_instruments()
    stuck = RecordedClock(instants=[ts("2000-01-01T00:00:00")])
    with pytest.raises(EngineError):
        run(build_engine(build_repository(instruments), instruments, clock=stuck))


# -- 7. venue agnosticism -----------------------------------------------------


def test_the_same_prices_on_two_venues_decide_identically_and_cost_differently() -> None:
    """Identical decisions prove the strategy is venue-blind; differing net proves
    the cost model is doing work.

    The decisions are compared symbol by symbol rather than key by key, because
    the venue is part of the key and the whole question is whether anything
    *other* than the venue changed.
    """
    here = build_instruments()
    there = {
        InstrumentKey(OTHER_VENUE, key.symbol): instrument(key.symbol, venue=OTHER_VENUE)
        for key in here
    }

    def repository_for(items: Mapping[InstrumentKey, Instrument]) -> InMemoryBarRepository:
        repository = InMemoryBarRepository()
        for key, item in items.items():
            start, step = PATHS[key.symbol]
            repository.add(
                daily_bars(
                    item, first_day=DATA_START, closes=ramp(DATA_DAYS, start=start, step=step)
                )
            )
        return repository

    cheap = cost_model(maker_bps="5", spread_bps="1", slippage_bps="1")
    dear = cost_model(maker_bps="80", spread_bps="60", slippage_bps="25")

    on_here = run(build_engine(repository_for(here), here, costs=cheap))
    on_there = run(build_engine(repository_for(there), there, costs=dear))

    assert [record.symbol for record in on_here.decisions] == [
        record.symbol for record in on_there.decisions
    ]
    assert [record.signal for record in on_here.decisions] == [
        record.signal for record in on_there.decisions
    ]
    assert on_here.ledger.net_pnl != on_there.ledger.net_pnl
    assert on_there.ledger.costs.total.amount > on_here.ledger.costs.total.amount

    same_costs = run(build_engine(repository_for(there), there, costs=cheap))
    assert same_costs.ledger.net_pnl == on_here.ledger.net_pnl


# -- 8. cross-sectional correctness -------------------------------------------


def test_cross_sectional_ranks_move_when_the_universe_changes() -> None:
    """A rank that is stable under a universe change is a per-symbol feature."""
    keys = [InstrumentKey(VENUE, symbol) for symbol in ("AAAEUR", "BBBEUR", "CCCEUR")]
    values = {key: Decimal(index + 1) for index, key in enumerate(keys)}

    before = rank_fractions(values)
    added = rank_fractions({**values, InstrumentKey(VENUE, "DDDEUR"): Decimal("1.5")})
    removed = rank_fractions({key: value for key, value in values.items() if key != keys[0]})

    assert before[keys[1]] != added[keys[1]]
    assert before[keys[1]] != removed[keys[1]]
    assert (
        demeaned(values)[keys[0]]
        != demeaned({**values, InstrumentKey(VENUE, "DDDEUR"): Decimal(100)})[keys[0]]
    )


def test_ties_share_a_rank_rather_than_being_split_by_input_order() -> None:
    """Order-dependent tie-breaking is a silent dependence on iteration order."""
    first = InstrumentKey(VENUE, "AAAEUR")
    second = InstrumentKey(VENUE, "BBBEUR")
    third = InstrumentKey(VENUE, "CCCEUR")
    ranks = rank_fractions({first: Decimal(5), second: Decimal(5), third: Decimal(9)})
    assert ranks[first] == ranks[second]
    assert ranks[third] > ranks[first]
    assert top_n({first: Decimal(5), second: Decimal(7)}, 1) == (second,)


def test_an_allocation_changes_when_the_candidate_set_changes() -> None:
    """The property, asserted on the object the engine actually executes."""
    instruments = build_instruments()
    keys = sorted(instruments)

    whole = run(build_engine(build_repository(instruments), instruments))
    smaller = run(build_engine(build_repository(instruments), instruments, universe_keys=keys[:2]))
    assert whole.ledger.net_pnl != smaller.ledger.net_pnl
    assert whole.universe_sizes != smaller.universe_sizes


def test_a_per_symbol_allocator_is_labelled_as_not_cross_sectional() -> None:
    """Expressible, and never presentable as a cross-sectional result."""
    from sextant.engine.backtest.baselines import SingleAssetBuyAndHold

    instruments = build_instruments()
    target = InstrumentKey(VENUE, "AAAEUR")
    summary = build_engine(build_repository(instruments), instruments).run(
        plan(), ParameterFreeStrategy(SingleAssetBuyAndHold(key=target)), "single"
    )
    assert summary.is_cross_sectional is False
    assert EqualWeightPassive().is_cross_sectional is True
    assert {record.symbol for record in summary.decisions} == {"AAAEUR"}


# -- the walk-forward guarantee itself ----------------------------------------


def test_no_fit_record_carries_a_performance_figure() -> None:
    """There is no object in this system holding an in-sample equity curve."""
    instruments = build_instruments()
    summary = run(build_engine(build_repository(instruments), instruments))
    for fit in summary.fits:
        payload = fit.as_json()
        assert "in_sample_performance" in payload
        assert isinstance(payload["in_sample_performance"], str)
        assert not hasattr(fit, "ledger")
        assert not hasattr(fit, "sharpe")


def test_every_scored_instant_falls_inside_an_out_of_sample_window() -> None:
    """Nothing from a fitting window reaches the reported ledger."""
    instruments = build_instruments()
    built = plan()
    summary = build_engine(build_repository(instruments), instruments).run(
        built, ParameterFreeStrategy(EqualWeightPassive()), "walk-forward"
    )
    windows = [fold.out_of_sample for fold in built.folds]
    for outcome in summary.ledger.rebalances:
        assert any(window.contains(outcome.opened_at) for window in windows)


def test_the_ledger_reconciles_on_a_real_run() -> None:
    """Gross minus every cost line equals net, exactly, on the fixture."""
    instruments = build_instruments()
    summary = run(build_engine(build_repository(instruments), instruments))
    assert summary.ledger.reconciles()


# -- positions carry, and only the difference is traded -----------------------


def test_a_buy_and_hold_construct_trades_exactly_twice_across_the_whole_plan() -> None:
    """One entry and one exit. Not one of each a month, and not one per fold.

    This is the property an earlier version of the engine did not have: it
    closed and reopened every position at every rebalance, which charged a
    monthly-rebalanced benchmark twenty-four round trips it would never pay.
    Overstating a benchmark's costs flatters every strategy measured against it,
    which is the direction this project exists to refuse.
    """
    from sextant.engine.backtest.baselines import SingleAssetBuyAndHold

    instruments = build_instruments()
    summary = build_engine(build_repository(instruments), instruments).run(
        plan(),
        ParameterFreeStrategy(SingleAssetBuyAndHold(key=InstrumentKey(VENUE, "AAAEUR"))),
        "hold",
    )
    trades = [trade for outcome in summary.ledger.rebalances for trade in outcome.trades]
    assert len(trades) == 2
    assert trades[0].is_buy
    assert not trades[-1].is_buy
    assert summary.ledger.reconciles()


def test_a_fold_boundary_does_not_make_the_account_sell_and_buy_back() -> None:
    """A fold is a reporting boundary, not an instruction to liquidate."""
    from sextant.engine.backtest.baselines import SingleAssetBuyAndHold

    instruments = build_instruments()
    strategy = ParameterFreeStrategy(SingleAssetBuyAndHold(key=InstrumentKey(VENUE, "AAAEUR")))
    two_folds = build_engine(build_repository(instruments), instruments).run(
        plan(total_months=6, in_sample_months=2, folds=2), strategy, "two"
    )
    four_folds = build_engine(build_repository(instruments), instruments).run(
        plan(total_months=6, in_sample_months=2, folds=4), strategy, "four"
    )
    assert _trade_count(two_folds) == _trade_count(four_folds) == 2
    assert two_folds.ledger.net_pnl == four_folds.ledger.net_pnl


def test_holding_still_costs_less_than_churning() -> None:
    """The cost model charges turnover, so a construct that churns pays more."""
    from sextant.engine.backtest.baselines import SingleAssetBuyAndHold

    instruments = build_instruments()
    holding = build_engine(build_repository(instruments), instruments).run(
        plan(),
        ParameterFreeStrategy(SingleAssetBuyAndHold(key=InstrumentKey(VENUE, "AAAEUR"))),
        "hold",
    )
    churning = build_engine(build_repository(instruments), instruments).run(
        plan(), ParameterFreeStrategy(RandomSelection.for_seed(seed=3, positions=2)), "churn"
    )
    assert holding.ledger.turnover.amount < churning.ledger.turnover.amount
    assert holding.ledger.costs.fees.amount < churning.ledger.costs.fees.amount


def test_an_unchanged_allocation_still_trades_only_the_drift() -> None:
    """Equal weight over a stable universe rebalances, but only by the drift.

    Turnover well below the book's value at every rebalance is what separates
    "rebalancing" from "reconstituting".
    """
    instruments = build_instruments()
    summary = run(build_engine(build_repository(instruments), instruments))
    for outcome in summary.ledger.rebalances[1:-1]:
        assert outcome.turnover.amount < outcome.equity_before.amount / Decimal(2)


def test_the_account_never_commits_more_cash_than_it_has() -> None:
    """A fully invested allocation must not leave the account short by its fees."""
    instruments = build_instruments()
    summary = run(build_engine(build_repository(instruments), instruments))
    for outcome in summary.ledger.rebalances:
        assert outcome.cash.amount >= Decimal("-0.000000001")


def _trade_count(summary: RunSummary) -> int:
    """How many orders a run actually placed."""
    return sum(len(outcome.trades) for outcome in summary.ledger.rebalances)
