"""The cadence amendment 6 registers, and the four ways it could go quietly wrong.

`carry-rank90-10-quarterly` is `carry-rank90-10` with one field changed, so the pair
is only a one-factor comparison if the wrapper does exactly what section 28.2 says
and nothing more. The tests here hold four properties that a plausible
implementation would break:

- **the rebalance instants are the calendar's, not the run's.** A cadence measured
  from the window start would move every rebalance date if the fold structure ever
  changed, and the variant would silently become a different variant;
- **between rebalances the target is identical, not merely similar.** An allocation
  that differs by one ulp trades, and a variant that trades every month at quarterly
  cadence is the monthly variant wearing a different label;
- **the book between rebalances can shrink and never grow.** A remembered pair whose
  legs have left the point-in-time universe is dropped rather than re-bought, which
  is the mechanism by which a delisted name would otherwise be re-entered;
- **nothing is held before the first rebalance instant.** The registration grants
  quarterly instants and no others, so a run starting off-cadence starts in cash
  rather than taking an extra decision nobody registered.

The rebalance count is asserted too, because section 28.3 requires it to be printed
beside every result and a count nobody checks is a count that can drift.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from sextant.domain.instrument import Instrument, InstrumentKey
from sextant.domain.money import Notional, Price, Quantity
from sextant.domain.time import Timeframe, Timestamp
from sextant.domain.venue import Venue
from sextant.engine.backtest.market import PointInTimeView
from sextant.engine.execution.funding import RealisedFunding, Settlement
from sextant.engine.strategies.carry import (
    CadencedCarry,
    CarryError,
    CarrySignal,
    CashAndCarry,
    PremiumIndex,
    RecordingCarry,
    is_rebalance_month,
)
from tests.harness import InMemoryBarRepository, daily_bars, ts

SPOT = Venue("testvenue")
PERP = Venue("testperp")

DATA_START = "2020-10-01T00:00:00"
DATA_DAYS = 800
MARGIN = Decimal("0.20")


def leg(base: str, venue: Venue) -> Instrument:
    """One leg, with its base asset stated rather than sliced off the symbol."""
    return Instrument(
        venue=venue,
        symbol=f"{base}USDT",
        base=base,
        quote="USDT",
        listed_at=ts("2020-01-01T00:00:00"),
        tick_size=Price(Decimal("0.0001")),
        lot_size=Quantity(Decimal("0.00000001")),
        min_notional=Notional(Decimal(1)),
    )


def settlements(rate: str) -> tuple[Settlement, ...]:
    """Eight-hourly settlements at a constant rate, for the whole fixture window."""
    start = ts(DATA_START)
    return tuple(
        Settlement(at=start.plus(timedelta(hours=8 * step)), rate=Decimal(rate))
        for step in range(DATA_DAYS * 3)
    )


def world(bases: dict[str, str]) -> tuple[list[Instrument], RealisedFunding]:
    """Legs and a funding schedule for each base, at that base's constant rate."""
    instruments: list[Instrument] = []
    rows: dict[InstrumentKey, tuple[Settlement, ...]] = {}
    for base, rate in bases.items():
        spot, perpetual = leg(base, SPOT), leg(base, PERP)
        instruments.extend((spot, perpetual))
        rows[perpetual.key] = settlements(rate)
    return instruments, RealisedFunding.of(rows)


def view_at(instruments: list[Instrument], at: Timestamp) -> PointInTimeView:
    """A point-in-time view over flat prices for every leg."""
    repository = InMemoryBarRepository()
    for item in instruments:
        repository.add(daily_bars(item, first_day=DATA_START, closes=("100",) * DATA_DAYS))
    return PointInTimeView.at(repository, Timeframe.D1, at, lookback_days=200)


def variant(funding: RealisedFunding, positions: int = 2) -> CashAndCarry:
    """`carry-rank90-10` in miniature: the 90-day funding signal, ranked."""
    return CashAndCarry(
        label="carry-rank90-10",
        signal=CarrySignal.FUNDING_90,
        positions=positions,
        funding=funding,
        premium=PremiumIndex(by_instrument={}),
        perpetual_venue=PERP.name,
        margin_fraction=MARGIN,
    )


# ---------------------------------------------------------------------------
# Which instants are rebalance instants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("month", "quarterly"),
    [(1, True), (2, False), (3, False), (4, True), (7, True), (10, True), (12, False)],
)
def test_quarterly_instants_are_january_april_july_and_october(month: int, quarterly: bool) -> None:
    """Section 28.2's anchor, asserted month by month."""
    at = ts(f"2022-{month:02d}-01T00:00:00")
    assert is_rebalance_month(at, 3) is quarterly


def test_monthly_cadence_rebalances_every_month() -> None:
    """The eight variants registered before amendment 6 are unchanged."""
    for month in range(1, 13):
        assert is_rebalance_month(ts(f"2022-{month:02d}-01T00:00:00"), 1) is True


def test_the_anchor_does_not_depend_on_the_year() -> None:
    """Calendar-anchored, so a different window cannot shift the rebalance dates."""
    for year in (2021, 2022, 2023, 2024):
        assert is_rebalance_month(ts(f"{year}-04-01T00:00:00"), 3) is True
        assert is_rebalance_month(ts(f"{year}-05-01T00:00:00"), 3) is False


def test_a_zero_or_negative_cadence_is_refused() -> None:
    with pytest.raises(CarryError, match="must be positive"):
        is_rebalance_month(ts("2022-01-01T00:00:00"), 0)


def test_a_wrapper_with_a_nonsense_cadence_refuses_construction() -> None:
    instruments, funding = world({"AAA": "0.001"})
    del instruments
    with pytest.raises(CarryError, match="rebalance_months must be positive"):
        CadencedCarry(inner=variant(funding), rebalance_months=0)


# ---------------------------------------------------------------------------
# What happens between rebalances
# ---------------------------------------------------------------------------


def test_the_carried_target_is_identical_so_the_engine_finds_nothing_to_trade() -> None:
    """Identical, not similar: a one-ulp difference is a trade and a fee."""
    instruments, funding = world({"AAA": "0.003", "BBB": "0.002", "CCC": "0.001"})
    cadenced = CadencedCarry(inner=variant(funding), rebalance_months=3)
    decided = cadenced.allocate(
        instruments, ts("2022-04-01T00:00:00"), view_at(instruments, ts("2022-04-01T00:00:00"))
    )
    carried = cadenced.allocate(
        instruments, ts("2022-05-01T00:00:00"), view_at(instruments, ts("2022-05-01T00:00:00"))
    )
    assert carried.weights == decided.weights
    assert carried.at == ts("2022-05-01T00:00:00")
    assert cadenced.rebalance_count == 1
    assert len(cadenced.carried_at) == 1


def test_a_pair_that_leaves_the_universe_is_dropped_and_not_re_bought() -> None:
    """The mechanism by which a delisted name would otherwise be re-entered."""
    instruments, funding = world({"AAA": "0.003", "BBB": "0.002"})
    cadenced = CadencedCarry(inner=variant(funding), rebalance_months=3)
    at = ts("2022-04-01T00:00:00")
    decided = cadenced.allocate(instruments, at, view_at(instruments, at))
    assert len(decided.weights) == 4

    survivors = [item for item in instruments if item.base != "BBB"]
    later = ts("2022-05-01T00:00:00")
    carried = cadenced.allocate(survivors, later, view_at(survivors, later))
    assert {key.symbol for key, _ in carried.weights} == {"AAAUSDT"}
    assert len(carried.weights) == 2
    assert "1 dropped" in carried.note


def test_dropping_a_pair_leaves_the_capital_idle_rather_than_concentrating() -> None:
    """The registered denominator is the position count, so the rest do not grow."""
    instruments, funding = world({"AAA": "0.003", "BBB": "0.002"})
    cadenced = CadencedCarry(inner=variant(funding), rebalance_months=3)
    at = ts("2022-04-01T00:00:00")
    decided = cadenced.allocate(instruments, at, view_at(instruments, at))
    kept = dict(decided.weights)

    survivors = [item for item in instruments if item.base != "BBB"]
    later = ts("2022-05-01T00:00:00")
    carried = cadenced.allocate(survivors, later, view_at(survivors, later))
    for key, weight in carried.weights:
        assert weight == kept[key]
    assert carried.gross_exposure < decided.gross_exposure


def test_the_signal_is_not_consulted_between_rebalances() -> None:
    """A ranking that reversed mid-quarter must not move the book."""
    instruments, funding = world({"AAA": "0.003", "BBB": "0.002", "CCC": "0.001"})
    cadenced = CadencedCarry(inner=variant(funding, positions=1), rebalance_months=3)
    at = ts("2022-04-01T00:00:00")
    decided = cadenced.allocate(instruments, at, view_at(instruments, at))
    chosen = {key.symbol for key, weight in decided.weights if weight > 0}
    assert chosen == {"AAAUSDT"}

    reversed_instruments, reversed_funding = world({"AAA": "0.001", "BBB": "0.002", "CCC": "0.003"})
    del reversed_funding
    later = ts("2022-05-01T00:00:00")
    carried = cadenced.allocate(reversed_instruments, later, view_at(reversed_instruments, later))
    assert {key.symbol for key, weight in carried.weights if weight > 0} == chosen


# ---------------------------------------------------------------------------
# Before the first rebalance instant
# ---------------------------------------------------------------------------


def test_a_run_starting_off_cadence_starts_in_cash() -> None:
    """Only quarterly instants are registered, so no extra decision is granted."""
    instruments, funding = world({"AAA": "0.003", "BBB": "0.002"})
    cadenced = CadencedCarry(inner=variant(funding), rebalance_months=3)
    at = ts("2022-02-01T00:00:00")
    allocation = cadenced.allocate(instruments, at, view_at(instruments, at))
    assert allocation.weights == ()
    assert cadenced.idle_before_first == [at]
    assert cadenced.rebalance_count == 0
    assert "no rebalance instant has been reached yet" in allocation.note


def test_the_idle_months_are_recorded_so_the_report_can_state_them() -> None:
    """An asymmetry against its siblings that a reader must be able to see."""
    instruments, funding = world({"AAA": "0.003"})
    cadenced = CadencedCarry(inner=variant(funding), rebalance_months=3)
    for month in ("2022-02", "2022-03"):
        at = ts(f"{month}-01T00:00:00")
        cadenced.allocate(instruments, at, view_at(instruments, at))
    assert len(cadenced.idle_before_first) == 2
    at = ts("2022-04-01T00:00:00")
    cadenced.allocate(instruments, at, view_at(instruments, at))
    assert cadenced.rebalance_count == 1
    assert len(cadenced.idle_before_first) == 2


# ---------------------------------------------------------------------------
# The rebalance count, and what the nulls see
# ---------------------------------------------------------------------------


def test_the_rebalance_count_is_a_third_of_the_months_walked() -> None:
    """Roughly 23 against roughly 70 is the number section 28.3 requires printed."""
    instruments, funding = world({"AAA": "0.003", "BBB": "0.002"})
    cadenced = CadencedCarry(inner=variant(funding), rebalance_months=3)
    months = [ts(f"2022-{month:02d}-01T00:00:00") for month in range(1, 13)]
    for at in months:
        cadenced.allocate(instruments, at, view_at(instruments, at))
    assert cadenced.rebalance_count == 4
    assert len(cadenced.carried_at) == 8


def test_the_recorder_sees_the_carried_exposure_and_not_a_fresh_decision() -> None:
    """The exposure-matched null must match what was held, cadence included."""
    instruments, funding = world({"AAA": "0.003", "BBB": "0.002"})
    recorder = RecordingCarry(inner=CadencedCarry(inner=variant(funding), rebalance_months=3))
    for month in range(4, 7):
        at = ts(f"2022-{month:02d}-01T00:00:00")
        recorder.allocate(instruments, at, view_at(instruments, at))
    held = recorder.held_counts()
    assert set(held.values()) == {2}
    assert len(held) == 3


def test_the_cadence_is_in_the_parameter_set_id() -> None:
    """Two trials that differ only in cadence are two trials in the registry."""
    instruments, funding = world({"AAA": "0.003"})
    del instruments
    monthly = variant(funding)
    quarterly = CadencedCarry(inner=monthly, rebalance_months=3)
    assert quarterly.parameter_set_id != monthly.parameter_set_id
    assert quarterly.parameter_set_id.endswith("rebalance_months=3")
    assert quarterly.name == monthly.name
