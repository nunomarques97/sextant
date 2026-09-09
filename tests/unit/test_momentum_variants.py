"""The pre-registered variants, their decomposition constructs, and the cascade.

Every test here checks a sentence from `docs/PRE-REGISTRATION-005.md` part 1
rather than an implementation detail, because the specification is what a
competent stranger would reimplement against and the code is only one attempt at
it.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from sextant.domain.instrument import Instrument
from sextant.domain.time import Timeframe, Timestamp
from sextant.engine.backtest.market import PointInTimeView
from sextant.engine.regime.segmentation import (
    Regime,
    classify,
    conclusive,
    month_counts,
    segment,
)
from sextant.engine.statistics.boundary import returns_panel
from sextant.engine.statistics.independence import (
    breadth_of,
    independence_of,
    lag_one_autocorrelation,
    mean_pairwise_correlation,
    measurable_pairs,
)
from sextant.engine.strategies.momentum import (
    CROSS_SECTIONAL_POSITIONS,
    LOOKBACKS,
    CrossSectionalMomentum,
    ExposureMatched,
    FullyInvested,
    Recording,
    ScaledUniverse,
    TimeSeriesTrend,
    TrendRule,
    lookback_window,
    positions_of,
    registered_variants,
    signal_for,
)
from tests.harness import InMemoryBarRepository, daily_bars, instrument, ramp, ts


def _view(
    items: dict[Instrument, tuple[str, ...]],
    *,
    as_of: str,
    first_day: str = "2023-01-01",
    lookback_days: int = 420,
) -> tuple[PointInTimeView, tuple[Instrument, ...]]:
    """A point-in-time view over a handful of deterministic price paths."""
    bars = []
    for item, closes in items.items():
        bars.extend(daily_bars(item, first_day=first_day, closes=closes))
    repository = InMemoryBarRepository()
    repository.add(bars)
    view = PointInTimeView.at(repository, Timeframe.D1, ts(as_of), lookback_days=lookback_days)
    return view, tuple(items)


# ---------------------------------------------------------------------------
# The grid is the one that was registered
# ---------------------------------------------------------------------------


def test_the_registered_grid_is_sixteen_variants_in_two_shapes() -> None:
    """Part 1 sections 8.2 and 8.3. Anything else is a post-hoc trial."""
    variants = registered_variants()
    assert len(variants) == 16
    cross = [item for item in variants if isinstance(item, CrossSectionalMomentum)]
    series = [item for item in variants if isinstance(item, TimeSeriesTrend)]
    assert len(cross) == 8
    assert len(series) == 8
    assert {item.lookback_days for item in cross} == set(LOOKBACKS)
    assert {item.positions for item in cross} == set(CROSS_SECTIONAL_POSITIONS)
    assert {item.rule for item in series} == {TrendRule.RETURN_SIGN, TrendRule.ABOVE_SMA}
    assert len({item.name for item in variants}) == 16


def test_a_time_series_variant_says_it_is_not_cross_sectional() -> None:
    """Its eligibility does not depend on what else is listed, and the label says so."""
    variants = registered_variants()
    assert all(
        item.is_cross_sectional for item in variants if isinstance(item, CrossSectionalMomentum)
    )
    assert not any(
        item.is_cross_sectional for item in variants if isinstance(item, TimeSeriesTrend)
    )


# ---------------------------------------------------------------------------
# The signal
# ---------------------------------------------------------------------------


def test_a_window_without_both_ends_carries_no_signal() -> None:
    """Part 1 section 8.1: neither ranked nor held, and counted rather than dropped."""
    item = instrument("AAAUSDT", quote="USDT")
    view, candidates = _view({item: ramp(40, start="100", step="1")}, as_of="2023-02-10")
    window = lookback_window(view.bars(candidates)[item.key], ts("2023-02-10"), 360)
    assert not window.is_evaluable
    assert signal_for(window, rule=None) is None


def test_a_window_missing_more_than_a_tenth_of_its_days_carries_no_signal() -> None:
    """The 90-per-cent coverage rule, which is what a data gap must trigger."""
    item = instrument("AAAUSDT", quote="USDT")
    closes = ramp(100, start="100", step="1")
    bars = daily_bars(item, first_day="2023-01-01", closes=closes)
    keep = tuple(bar for index, bar in enumerate(bars) if index % 5 != 0)  # 20% removed
    repository = InMemoryBarRepository()
    repository.add(keep)
    view = PointInTimeView.at(repository, Timeframe.D1, ts("2023-03-01"), lookback_days=120)
    window = lookback_window(view.bars((item,))[item.key], ts("2023-03-01"), 30)
    assert not window.is_evaluable


def test_the_total_return_signal_is_the_move_between_the_two_ends() -> None:
    item = instrument("AAAUSDT", quote="USDT")
    view, candidates = _view({item: ramp(100, start="100", step="1")}, as_of="2023-04-01")
    window = lookback_window(view.bars(candidates)[item.key], ts("2023-04-01"), 30)
    signal = signal_for(window, rule=None)
    assert signal is not None
    assert signal > 0
    far = window.far_close
    last = window.last_close
    assert far is not None and last is not None
    assert signal == last / far - Decimal(1)


def test_the_moving_average_signal_uses_the_window_and_not_the_far_end() -> None:
    item = instrument("AAAUSDT", quote="USDT")
    view, candidates = _view({item: ramp(100, start="100", step="1")}, as_of="2023-04-01")
    window = lookback_window(view.bars(candidates)[item.key], ts("2023-04-01"), 30)
    average = sum(window.closes_in_window, Decimal(0)) / Decimal(len(window.closes_in_window))
    signal = signal_for(window, rule=TrendRule.ABOVE_SMA)
    last = window.last_close
    assert signal is not None and last is not None
    assert signal == last / average - Decimal(1)


def test_a_signal_never_reads_a_bar_that_had_not_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Structural look-ahead: everything the view hands out closed at or before it."""
    del monkeypatch
    item = instrument("AAAUSDT", quote="USDT")
    view, candidates = _view({item: ramp(200, start="100", step="1")}, as_of="2023-04-01")
    allocator = CrossSectionalMomentum(lookback_days=30, positions=1)
    allocator.allocate(candidates, ts("2023-04-01"), view)
    for bars in view.bars(candidates).values():
        assert all(bar.close_time <= ts("2023-04-01") for bar in bars)


# ---------------------------------------------------------------------------
# Ranking, ties and exposure
# ---------------------------------------------------------------------------


def test_the_top_n_are_held_and_the_rank_is_by_signal_descending() -> None:
    fast = instrument("FASTUSDT", quote="USDT")
    slow = instrument("SLOWUSDT", quote="USDT")
    flat = instrument("FLATUSDT", quote="USDT")
    view, candidates = _view(
        {
            fast: ramp(120, start="100", step="2"),
            slow: ramp(120, start="100", step="1"),
            flat: ramp(120, start="100", step="0"),
        },
        as_of="2023-04-15",
    )
    allocation = CrossSectionalMomentum(lookback_days=30, positions=2).allocate(
        candidates, ts("2023-04-15"), view
    )
    assert [key.symbol for key in allocation.keys] == ["FASTUSDT", "SLOWUSDT"]


def test_a_tie_is_broken_by_symbol_ascending_and_is_therefore_reproducible() -> None:
    """Part 1 section 8.1. Two implementations must agree on the same names."""
    first = instrument("BBBUSDT", quote="USDT")
    second = instrument("AAAUSDT", quote="USDT")
    identical = ramp(120, start="100", step="1")
    view, candidates = _view({first: identical, second: identical}, as_of="2023-04-15")
    allocation = CrossSectionalMomentum(lookback_days=30, positions=1).allocate(
        candidates, ts("2023-04-15"), view
    )
    assert [key.symbol for key in allocation.keys] == ["AAAUSDT"]


def test_the_cross_sectional_shape_is_fully_invested_when_enough_names_qualify() -> None:
    items = {
        instrument(f"S{index}USDT", quote="USDT"): ramp(120, start="100", step=str(index + 1))
        for index in range(5)
    }
    view, candidates = _view(items, as_of="2023-04-15")
    allocation = CrossSectionalMomentum(lookback_days=30, positions=5).allocate(
        candidates, ts("2023-04-15"), view
    )
    assert allocation.invested_fraction == Decimal(1)


def test_a_trend_variant_holding_fewer_names_is_less_invested() -> None:
    """The whole mechanism by which a long/flat variant reduces market exposure."""
    rising = instrument("UPPUSDT", quote="USDT")
    falling = instrument("DWNUSDT", quote="USDT")
    view, candidates = _view(
        {
            rising: ramp(120, start="100", step="1"),
            falling: ramp(120, start="200", step="-1"),
        },
        as_of="2023-04-15",
    )
    allocation = TimeSeriesTrend(
        lookback_days=30, positions=10, rule=TrendRule.RETURN_SIGN
    ).allocate(candidates, ts("2023-04-15"), view)
    assert [key.symbol for key in allocation.keys] == ["UPPUSDT"]
    assert allocation.invested_fraction == Decimal(1) / Decimal(10)


def test_a_trend_variant_with_nothing_in_trend_holds_nothing_at_all() -> None:
    """Cash is a position and is accounted for as one, not as an empty result."""
    falling = instrument("DWNUSDT", quote="USDT")
    view, candidates = _view({falling: ramp(120, start="200", step="-1")}, as_of="2023-04-15")
    allocation = TimeSeriesTrend(
        lookback_days=30, positions=10, rule=TrendRule.RETURN_SIGN
    ).allocate(candidates, ts("2023-04-15"), view)
    assert allocation.weights == ()
    assert allocation.invested_fraction == Decimal(0)


# ---------------------------------------------------------------------------
# The three decomposition constructs
# ---------------------------------------------------------------------------


def _recorded() -> tuple[Recording, PointInTimeView, tuple[Instrument, ...], Timestamp]:
    rising = instrument("UPPUSDT", quote="USDT")
    falling = instrument("DWNUSDT", quote="USDT")
    view, candidates = _view(
        {
            rising: ramp(120, start="100", step="1"),
            falling: ramp(120, start="200", step="-1"),
        },
        as_of="2023-04-15",
    )
    at = ts("2023-04-15")
    variant = TimeSeriesTrend(lookback_days=30, positions=10, rule=TrendRule.RETURN_SIGN)
    recorder = Recording(inner=variant, positions=10)
    recorder.allocate(candidates, at, view)
    return recorder, view, candidates, at


def test_the_recorder_keeps_the_path_the_variant_actually_took() -> None:
    recorder, _, _, at = _recorded()
    assert recorder.held_counts()[at] == 1
    assert recorder.invested[at] == Decimal(1) / Decimal(10)
    assert [key.symbol for key in recorder.chosen[at]] == ["UPPUSDT"]


def test_selection_only_keeps_the_names_and_deletes_the_timing() -> None:
    recorder, view, candidates, at = _recorded()
    allocation = FullyInvested(inner=recorder.inner).allocate(candidates, at, view)
    assert [key.symbol for key in allocation.keys] == ["UPPUSDT"]
    assert allocation.invested_fraction == Decimal(1)


def test_the_exposure_matched_null_matches_the_count_and_the_cash_fraction() -> None:
    """The variant's exposure path, none of its selection."""
    recorder, view, candidates, at = _recorded()
    null = ExposureMatched.for_seed(1, 10, dict(recorder.held_counts()), "exposure-matched-null")
    allocation = null.allocate(candidates, at, view)
    assert len(allocation.keys) == 1
    assert allocation.invested_fraction == recorder.invested[at]


def test_the_timing_null_holds_the_whole_universe_at_the_variant_s_exposure() -> None:
    recorder, view, candidates, at = _recorded()
    null = ScaledUniverse(invested=dict(recorder.invested))
    allocation = null.allocate(candidates, at, view)
    assert len(allocation.keys) == len(candidates)
    assert allocation.invested_fraction <= recorder.invested[at]
    assert allocation.invested_fraction > 0


def test_a_month_the_variant_spent_in_cash_leaves_both_nulls_in_cash_too() -> None:
    """Inventing a position there would answer a question nobody asked."""
    _, view, candidates, at = _recorded()
    empty = ExposureMatched.for_seed(1, 10, {}, "exposure-matched-null")
    assert empty.allocate(candidates, at, view).weights == ()
    assert ScaledUniverse(invested={}).allocate(candidates, at, view).weights == ()


def test_two_seeds_draw_differently_and_one_seed_is_reproducible() -> None:
    items = {
        instrument(f"S{index:02d}USDT", quote="USDT"): ramp(120, start="100", step="1")
        for index in range(20)
    }
    view, candidates = _view(items, as_of="2023-04-15")
    at = ts("2023-04-15")
    held = {at: 5}
    first = ExposureMatched.for_seed(1, 10, held, "null").allocate(candidates, at, view)
    again = ExposureMatched.for_seed(1, 10, held, "null").allocate(candidates, at, view)
    other = ExposureMatched.for_seed(2, 10, held, "null").allocate(candidates, at, view)
    assert first.keys == again.keys
    assert first.keys != other.keys


def test_every_variant_has_a_pre_registered_position_count() -> None:
    for variant in registered_variants():
        assert positions_of(variant) in {5, 10}


# ---------------------------------------------------------------------------
# The regime cascade
# ---------------------------------------------------------------------------


def _closes(path: list[tuple[str, str]]) -> dict[Timestamp, Decimal]:
    return {ts(day): Decimal(value) for day, value in path}


def test_the_cascade_is_total_every_instant_gets_exactly_one_label() -> None:
    start = ts("2022-01-01")
    closes = {start.plus(timedelta(days=day)): Decimal(100) + Decimal(day) for day in range(800)}
    instants = [start.plus(timedelta(days=day)) for day in (0, 400, 700)]
    labels = segment(closes, instants)
    assert len(labels) == len(instants)
    assert all(isinstance(item.regime, Regime) for item in labels)


def test_a_thirty_day_fall_of_a_quarter_is_a_crash_whatever_the_drawdown_says() -> None:
    start = ts("2022-01-01")
    closes = {start.plus(timedelta(days=day)): Decimal(100) for day in range(400)}
    for day in range(370, 400):
        closes[start.plus(timedelta(days=day))] = Decimal(60)
    labelled = classify(closes, start.plus(timedelta(days=399)))
    assert labelled.regime is Regime.CRASH


def test_a_cold_start_is_not_evaluable_rather_than_defaulted_to_a_regime() -> None:
    """Defaulting would put the warm-up months into whichever label the default was."""
    start = ts("2022-01-01")
    closes = {start.plus(timedelta(days=day)): Decimal(100) for day in range(10)}
    labelled = classify(closes, start.plus(timedelta(days=5)))
    assert labelled.regime is Regime.NOT_EVALUABLE


def test_a_label_never_reads_a_price_from_after_the_instant() -> None:
    """Point-in-time: what happens next cannot change what was decided."""
    start = ts("2022-01-01")
    closes = {start.plus(timedelta(days=day)): Decimal(100) + Decimal(day) for day in range(500)}
    at = start.plus(timedelta(days=400))
    before = classify(closes, at)
    for day in range(401, 500):
        closes[start.plus(timedelta(days=day))] = Decimal(1)
    assert classify(closes, at).regime is before.regime


def test_only_regimes_with_six_months_are_conclusive() -> None:
    counts = {Regime.BULL: 10, Regime.BEAR: 5, Regime.CRASH: 6, Regime.RECOVERY: 0}
    assert set(conclusive(counts)) == {Regime.BULL, Regime.CRASH}


def test_month_counts_include_the_unevaluable_ones_rather_than_hiding_them() -> None:
    start = ts("2022-01-01")
    closes = {start.plus(timedelta(days=day)): Decimal(100) for day in range(5)}
    counts = month_counts(segment(closes, [start.plus(timedelta(days=2))]))
    assert counts[Regime.NOT_EVALUABLE] == 1


# ---------------------------------------------------------------------------
# Effective observations
# ---------------------------------------------------------------------------


def test_positive_autocorrelation_shrinks_the_effective_count() -> None:
    rising = [0.01 * index for index in range(40)]
    result = independence_of(rising)
    assert result.lag_one_autocorrelation > 0
    assert result.effective_observations < result.observations


def test_the_effective_count_is_never_more_than_the_months_there_were() -> None:
    """Negative autocorrelation must not be allowed to claim extra evidence."""
    alternating = [0.05 if index % 2 else -0.05 for index in range(40)]
    result = independence_of(alternating)
    assert result.lag_one_autocorrelation < 0
    assert result.effective_observations == result.observations


def test_a_flat_series_has_no_autocorrelation_to_measure() -> None:
    assert lag_one_autocorrelation([0.0] * 20) == 0.0


def test_a_shortfall_against_the_registered_floor_is_a_number() -> None:
    result = independence_of([0.01] * 10)
    assert result.shortfall > 0
    assert result.detectable_at() > 0


def test_correlated_names_are_worth_fewer_bets_than_they_look() -> None:
    assert breadth_of(10.0, 0.0).effective_positions == pytest.approx(10.0)
    assert breadth_of(10.0, 1.0).effective_positions == pytest.approx(1.0)
    assert 1.0 < breadth_of(10.0, 0.35).effective_positions < 10.0


def test_a_pair_that_never_overlaps_is_left_out_rather_than_called_uncorrelated() -> None:
    """Zero is the most flattering answer available, so it is never given by accident."""
    rows: list[list[Decimal | None]] = []
    for index in range(200):
        first = Decimal("0.01") if index % 2 else Decimal("-0.01")
        rows.append([first, None] if index < 100 else [None, first])
    panel = returns_panel(rows)
    assert measurable_pairs(panel) == 0
    assert mean_pairwise_correlation(panel) == 0.0


def test_overlapping_names_are_correlated_over_their_own_overlap() -> None:
    rows: list[list[Decimal | None]] = []
    for index in range(200):
        move = Decimal("0.01") if index % 3 else Decimal("-0.02")
        second: Decimal | None = move if index >= 50 else None
        rows.append([move, second])
    panel = returns_panel(rows)
    assert measurable_pairs(panel) == 1
    assert mean_pairwise_correlation(panel) == pytest.approx(1.0, abs=1e-9)


def test_a_ragged_panel_is_refused_rather_than_padded() -> None:
    from sextant.engine.statistics.boundary import DegenerateCurve

    with pytest.raises(DegenerateCurve):
        returns_panel([[Decimal(1), Decimal(2)], [Decimal(1)]])
