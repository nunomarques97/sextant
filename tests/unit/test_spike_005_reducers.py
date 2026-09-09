"""The pure parts of the SEXTANT-005 runner: what it reduces a run to.

The grid itself needs the ingested archive and eighty minutes, so it is not what
these tests drive. What they drive is everything the runner decides *around* a
run: how a month is attributed to a regime, when a criterion is met, what a null
distribution is reduced to, and what the world looks like to the engine before
any bar is read.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from sextant.adapters.exchanges.binance.archive import Month, months_between
from sextant.adapters.exchanges.binance.listing_calendar import BinanceListingCalendar
from sextant.adapters.storage.panel import PanelRow
from sextant.app.binance_archive import FetchReport
from sextant.app.spike_005 import (
    ACCOUNT_EQUITY,
    MAX_POSITIONS,
    SEED_REDUCTION_LADDER,
    CalendarSeriesEnd,
    PrecomputedUniverse,
    _fx_rates,
    _reference_closes,
    fresh_clock,
    seeds_within_budget,
    write_json,
)
from sextant.app.spike_005_run import (
    BTC_SYMBOL,
    NullDistribution,
    _code_version,
    _criteria,
    _dataset_checksums,
    _fully_invested,
    _matched,
    _no_breadth,
    opening_instants,
    panels,
    regime_returns,
)
from sextant.domain.instrument import InstrumentKey
from sextant.domain.time import Timestamp
from sextant.domain.venue import Venue
from sextant.engine.execution.markout import SeriesEnd
from sextant.engine.regime.segmentation import Regime

VENUE = Venue("binance")


def _at(day: int) -> Timestamp:
    return Timestamp(datetime(2022, 1, 1, tzinfo=UTC) + timedelta(days=30 * day))


# ---------------------------------------------------------------------------
# Panels and cells
# ---------------------------------------------------------------------------


def test_the_headline_panel_runs_four_cells_and_the_secondary_runs_one() -> None:
    """Deviation D2, made visible in the shape of the code rather than in prose."""
    headline, secondary = panels()
    assert headline.policy == "USDT"
    assert len(headline.cells) == 4
    assert headline.single_asset == BTC_SYMBOL
    assert secondary.policy == "EUR"
    assert len(secondary.cells) == 1
    assert secondary.cells[0].is_headline
    assert secondary.single_asset == "BTCEUR"


# ---------------------------------------------------------------------------
# Attributing a month to a regime
# ---------------------------------------------------------------------------


def test_a_month_is_attributed_to_the_regime_that_was_knowable_when_it_opened() -> None:
    """Keying it to the close would assign it a regime the decision could not see."""
    monthly = [(_at(1), Decimal("0.10")), (_at(2), Decimal("-0.20"))]
    opening = opening_instants(monthly)
    assert opening == {_at(2): _at(1)}
    labels = {_at(1): Regime.BULL.value, _at(2): Regime.CRASH.value}
    totals = regime_returns(monthly, labels, opening)
    # The -20% month closed at instant 2 but opened at instant 1, which was bull.
    assert totals[Regime.BULL.value] == (Decimal("-0.20"), 1)
    assert Regime.CRASH.value not in totals


def test_returns_inside_one_regime_compound_rather_than_sum() -> None:
    monthly = [(_at(index), Decimal("0.10")) for index in range(4)]
    opening = opening_instants(monthly)
    labels = dict.fromkeys((_at(index) for index in range(4)), Regime.BULL.value)
    value, months = regime_returns(monthly, labels, opening)[Regime.BULL.value]
    assert months == 3
    assert value == Decimal("1.1") ** 3 - Decimal(1)


def test_a_month_whose_opening_regime_is_unknown_is_labelled_not_evaluable() -> None:
    """Never silently folded into whichever regime happened to be nearby."""
    monthly = [(_at(0), Decimal("0.05")), (_at(1), Decimal("0.05"))]
    totals = regime_returns(monthly, {}, opening_instants(monthly))
    assert Regime.NOT_EVALUABLE.value in totals


# ---------------------------------------------------------------------------
# The criteria
# ---------------------------------------------------------------------------


def _null(sharpes: tuple[float, ...]) -> NullDistribution:
    return NullDistribution(
        construct="x/exposure-matched",
        policy="USDT",
        cell_id="binance_vip0",
        fill_mix="50/50 maker/taker (assumed)",
        seed_start=1,
        seed_count=len(sharpes),
        sharpes=sharpes,
        terminal_returns=tuple(-0.5 for _ in sharpes),
        sortinos=tuple(-0.1 for _ in sharpes),
        drawdowns=tuple(0.8 for _ in sharpes),
        seconds=1.0,
    )


def _regimes(**values: tuple[str, int]) -> dict[str, tuple[Decimal, int]]:
    return {name: (Decimal(value), months) for name, (value, months) in values.items()}


def test_criterion_one_compares_against_the_ninety_fifth_percentile() -> None:
    null = _null(tuple(float(index) / 100.0 for index in range(100)))
    answers = _criteria(
        sharpe=0.99,
        null=null,
        deflated_value=0.5,
        combined_return=Decimal("-0.5"),
        selection_return=Decimal("-0.6"),
        regimes={},
        equal_regimes={},
        label_counts={},
        effective=52.0,
    )
    assert answers["1_beats_exposure_matched_null"] is True
    below = _criteria(
        sharpe=0.10,
        null=null,
        deflated_value=0.5,
        combined_return=Decimal("-0.5"),
        selection_return=Decimal("-0.6"),
        regimes={},
        equal_regimes={},
        label_counts={},
        effective=52.0,
    )
    assert below["1_beats_exposure_matched_null"] is False


def test_a_variant_that_lost_money_can_never_satisfy_criterion_three() -> None:
    """The win must be selection; a loss has no win to attribute."""
    answers = _criteria(
        sharpe=0.5,
        null=None,
        deflated_value=None,
        combined_return=Decimal("-0.78"),
        selection_return=Decimal("-0.10"),
        regimes={},
        equal_regimes={},
        label_counts={},
        effective=52.0,
    )
    assert answers["3_win_is_selection"] is False


def test_criterion_three_needs_selection_to_carry_half_the_gain() -> None:
    shared = {
        "sharpe": 0.5,
        "null": None,
        "deflated_value": None,
        "regimes": {},
        "equal_regimes": {},
        "label_counts": {},
        "effective": 52.0,
    }
    carried = _criteria(combined_return=Decimal("0.20"), selection_return=Decimal("0.15"), **shared)
    assert carried["3_win_is_selection"] is True
    mostly_timing = _criteria(
        combined_return=Decimal("0.20"), selection_return=Decimal("0.05"), **shared
    )
    assert mostly_timing["3_win_is_selection"] is False


def test_criterion_four_counts_only_regimes_with_six_scored_months() -> None:
    counts = {Regime.BULL: 15, Regime.BEAR: 29, Regime.RECOVERY: 8, Regime.CRASH: 1}
    regimes = _regimes(
        bull=("0.10", 15), bear=("0.05", 29), recovery=("0.02", 8), crash=("-0.90", 1)
    )
    equal = _regimes(bull=("0.01", 15), bear=("0.01", 29), recovery=("0.01", 8))
    answers = _criteria(
        sharpe=0.5,
        null=None,
        deflated_value=None,
        combined_return=Decimal("0.2"),
        selection_return=Decimal("0.2"),
        regimes=regimes,
        equal_regimes=equal,
        label_counts=counts,
        effective=52.0,
    )
    assert answers["4_regimes_countable"] == ["bear", "bull", "recovery"]
    assert answers["4_regimes_positive"] == 3
    assert answers["4_regime_stability"] is True


def test_a_regime_where_the_variant_did_worse_than_passive_fails_criterion_four() -> None:
    counts = {Regime.BULL: 15, Regime.BEAR: 29, Regime.RECOVERY: 8}
    regimes = _regimes(bull=("0.10", 15), bear=("0.05", 29), recovery=("0.02", 8))
    equal = _regimes(bull=("0.50", 15), bear=("0.01", 29), recovery=("0.01", 8))
    answers = _criteria(
        sharpe=0.5,
        null=None,
        deflated_value=None,
        combined_return=Decimal("0.2"),
        selection_return=Decimal("0.2"),
        regimes=regimes,
        equal_regimes=equal,
        label_counts=counts,
        effective=52.0,
    )
    assert answers["4_regime_stability"] is False


def test_an_unanswerable_criterion_is_none_rather_than_false() -> None:
    """A criterion nobody could evaluate is not a criterion that was failed."""
    answers = _criteria(
        sharpe=None,
        null=None,
        deflated_value=None,
        combined_return=Decimal("-0.5"),
        selection_return=None,
        regimes={},
        equal_regimes={},
        label_counts={},
        effective=10.0,
    )
    assert answers["1_beats_exposure_matched_null"] is None
    assert answers["2_survives_deflation"] is None
    assert answers["3_win_is_selection"] is None
    assert answers["4_regime_stability"] is None
    assert answers["effective_observations_met"] is False


# ---------------------------------------------------------------------------
# What a null distribution is reduced to
# ---------------------------------------------------------------------------


def test_a_null_distribution_carries_its_percentiles_with_intervals() -> None:
    null = _null(tuple(float(index) / 100.0 for index in range(200)))
    payload = null.as_json()
    sharpe = payload["sharpe"]
    assert isinstance(sharpe, dict)
    percentiles = sharpe["percentiles"]
    assert isinstance(percentiles, dict)
    assert set(percentiles) == {"50.0", "90.0", "95.0", "99.0"}
    ninety_five = percentiles["95.0"]
    assert isinstance(ninety_five, dict)
    assert ninety_five["ci_low"] <= ninety_five["value"] <= ninety_five["ci_high"]
    assert payload["denomination"] == "EUR"
    assert payload["seed_count"] == 200


def test_a_percentile_is_reproducible_from_the_same_distribution() -> None:
    """The bootstrap generator is seeded, so an interval is not a fresh guess."""
    null = _null(tuple(float(index) / 100.0 for index in range(200)))
    assert null.estimate(95.0).low == null.estimate(95.0).low
    assert null.percentile(50.0) == pytest.approx(0.995, abs=0.01)


def test_a_null_builder_makes_a_fresh_allocator_for_every_seed() -> None:
    """These allocators are stateful: one seed is one whole path, not one draw."""
    build = _fully_invested(MAX_POSITIONS)
    assert build(1) is not build(1)
    matched = _matched({_at(1): 3}, MAX_POSITIONS)
    first = matched(7)
    assert first.parameter_set_id == f"positions={MAX_POSITIONS};seed=7"
    assert matched(7) is not first


def test_absent_breadth_has_one_shape_and_says_it_is_not_evaluable() -> None:
    absent = _no_breadth()
    assert absent["is_evaluable"] is False
    assert absent["measured_pairs"] == 0
    assert absent["effective_positions"] == 0.0


# ---------------------------------------------------------------------------
# The world the engine runs in
# ---------------------------------------------------------------------------


def test_a_precomputed_universe_answers_only_for_instants_it_resolved() -> None:
    key = InstrumentKey(VENUE, "AAAUSDT")
    universe = PrecomputedUniverse(label="executable/USDT", members={_at(1): (key,)})
    assert universe.policy_name == "executable/USDT"
    assert universe.executable_at(_at(1)) == (key,)
    assert universe.executable_at(_at(2)) == ()


def test_the_haircut_applies_to_a_delisting_and_to_nothing_else() -> None:
    """An undetermined membership takes no haircut; nor does an archive that stops."""
    held = months_between(Month(2024, 1), Month(2024, 4))
    calendar = BinanceListingCalendar.from_presence(
        VENUE, {"AAAUSDT": (True, True, False, False)}, held
    )
    oracle = CalendarSeriesEnd(calendar=calendar)
    key = InstrumentKey(VENUE, "AAAUSDT")
    listed = Timestamp(datetime(2024, 2, 15, tzinfo=UTC))
    bracket = Timestamp(datetime(2024, 3, 15, tzinfo=UTC))
    gone = Timestamp(datetime(2024, 4, 15, tzinfo=UTC))
    assert oracle.series_end_at(key, listed) is SeriesEnd.STILL_LISTED
    assert oracle.series_end_at(key, bracket) is SeriesEnd.UNDETERMINED
    assert oracle.series_end_at(key, gone) is SeriesEnd.DELISTED
    unknown = InstrumentKey(VENUE, "NEVERHEARDOFIT")
    assert oracle.series_end_at(unknown, listed) is SeriesEnd.UNDETERMINED


def _panel(symbol: str, closes: list[str]) -> dict[str, tuple[PanelRow, ...]]:
    start = int(datetime(2022, 1, 1, tzinfo=UTC).timestamp() * 1000)
    return {
        symbol: tuple(
            PanelRow(open_time_ms=start + day * 86_400_000, close=close, volume="10")
            for day, close in enumerate(closes)
        )
    }


def test_the_fx_rate_is_the_reciprocal_of_the_pair_dated_by_bar_close() -> None:
    """A rate is knowable when the bar carrying it has finished forming."""
    rates = _fx_rates(_panel("EURUSDT", ["1.25", "1.00"]))
    first_close = Timestamp(datetime(2022, 1, 2, tzinfo=UTC))
    assert rates.rate_at(first_close) == Decimal(1) / Decimal("1.25")
    assert rates.foreign_currency == "USDT"
    assert rates.account_currency == "EUR"


def test_a_missing_fx_series_refuses_rather_than_running_the_counterfactual() -> None:
    with pytest.raises(ValueError, match="EURUSDT"):
        _fx_rates(_panel("BTCUSDT", ["100"]))


def test_the_regime_reference_series_is_converted_into_the_account_currency() -> None:
    panel = {**_panel("EURUSDT", ["1.25", "1.25"]), **_panel(BTC_SYMBOL, ["100", "200"])}
    closes = _reference_closes(panel, _fx_rates(panel))
    assert closes[Timestamp(datetime(2022, 1, 2, tzinfo=UTC))] == Decimal(100) / Decimal("1.25")


def test_a_missing_reference_series_stops_the_regime_cut() -> None:
    panel = _panel("EURUSDT", ["1.25"])
    with pytest.raises(ValueError, match=BTC_SYMBOL):
        _reference_closes(panel, _fx_rates(panel))


# ---------------------------------------------------------------------------
# Budget, clocks and provenance
# ---------------------------------------------------------------------------


def test_the_seed_budget_only_ever_steps_down_its_registered_ladder() -> None:
    """A smaller count widens the interval; it cannot move a threshold favourably."""
    assert seeds_within_budget(0.1, 1_000_000.0, 1) == SEED_REDUCTION_LADDER[0]
    assert seeds_within_budget(1.0, 300.0, 1) == 250
    assert seeds_within_budget(1000.0, 1.0, 100) == SEED_REDUCTION_LADDER[-1]


def test_every_run_starts_from_a_clock_that_has_reached_nothing() -> None:
    """A simulated clock refuses to move backwards, and rightly."""
    assert fresh_clock().now() == Timestamp.from_epoch_millis(0)
    assert fresh_clock() is not fresh_clock()


def test_a_result_payload_is_written_sorted_so_two_runs_diff_cleanly(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "result.json"
    write_json({"b": 2, "a": Decimal("1.5")}, path)
    text = path.read_text(encoding="utf-8")
    assert text.index('"a"') < text.index('"b"')
    assert json.loads(text)["a"] == "1.5"


def test_the_dataset_fingerprint_is_taken_over_every_object(tmp_path: Path) -> None:
    report = FetchReport(
        digests={"a.zip": "0" * 64, "b.zip": "1" * 64},
        bytes_written=2,
        seconds=1.0,
        failures={},
    )
    report.write_json(tmp_path / "fetch_report.json")
    first = _dataset_checksums(tmp_path)

    changed = FetchReport(
        digests={"a.zip": "0" * 64, "b.zip": "2" * 64},
        bytes_written=2,
        seconds=1.0,
        failures={},
    )
    changed.write_json(tmp_path / "fetch_report.json")
    assert _dataset_checksums(tmp_path) != first


def test_the_code_version_is_a_revision_or_an_explicit_unknown() -> None:
    version = _code_version()
    assert version == "unknown" or len(version) == 12


def test_the_account_is_the_registered_one() -> None:
    assert ACCOUNT_EQUITY.amount == Decimal(1500)
    assert MAX_POSITIONS == 10
