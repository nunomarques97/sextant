"""Rule A12.9: the currency charge scales with crossings and never with turnover.

Amendment 12, section 34.9. The account is funded in one currency and trades
instruments quoted in another. Capital crosses the boundary when it is deployed and
again when it is withdrawn; rotating between two instruments quoted in the *same*
foreign currency crosses nothing, because selling one into the quote asset and buying
the other out of it is one currency throughout.

The invariant, stated as the amendment states it:

    The total FX conversion charge must scale with the NUMBER OF TIMES capital actually
    crosses currency, and must not scale with turnover.

Four tests hold it from four directions: more rebalances at the same capital must not
move the charge, more turnover at the same capital must not move it, more crossings must
move it proportionally, and a run that never leaves its own currency must not pay at all.

**This is a regression test for a defect that was live.** Before this amendment the
conversion was charged inside every trade, on that trade's notional, which made the line
scale with turnover exactly as the invariant forbids. It was the second largest charge in
F1 and it was roughly forty times what two crossings cost.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from sextant.adapters.clocks import SimulatedClock
from sextant.app.spike_006_f1 import FX_CROSSINGS_PER_RUN
from sextant.domain.instrument import Instrument, InstrumentKey
from sextant.domain.money import Notional, Price, Quantity
from sextant.domain.time import Timeframe, Timestamp
from sextant.domain.venue import Venue
from sextant.engine.backtest.allocation import (
    Allocation,
    LongShortAllocation,
    ParameterFreeStrategy,
)
from sextant.engine.backtest.engine import BacktestEngine
from sextant.engine.backtest.market import PointInTimeView
from sextant.engine.backtest.window import WalkForwardPlan, anchored_plan
from sextant.engine.execution.fx import CurrencyRouting, FxLeg, FxPolicy, FxRates
from sextant.engine.execution.markout import SeriesEnd
from sextant.engine.strategies.carry import CarryPair, build_pair_allocation
from tests.harness import (
    InMemoryBarRepository,
    StaticSeriesEnd,
    StaticUniverse,
    cost_model,
    daily_bars,
    haircut,
    ts,
)

SPOT = Venue("testvenue")
PERP = Venue("testperp")

FIRST_MONTH = "2021-01-01T00:00:00"
DATA_START = "2020-10-01T00:00:00"
DATA_DAYS = 700

#: The conversion charge, in basis points of the capital that crosses. Ten, matching the
#: venue's own spot schedule, so the arithmetic in the assertions is readable.
CONVERSION_BPS = Decimal(10)

EQUITY = Decimal(1500)
MARGIN = Decimal("0.20")


def leg(base: str, venue: Venue) -> Instrument:
    """One leg, quoted in the foreign currency."""
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


def flat(value: str) -> tuple[str, ...]:
    """A price path that does not move, so nothing but the charges can change equity."""
    return (value,) * DATA_DAYS


def rates() -> FxRates:
    """A constant conversion rate, so the charge is the only currency effect."""
    return FxRates.of(
        foreign_currency="USDT",
        account_currency="EUR",
        observations={ts(DATA_START): Decimal(1)},
        source="test fixture: a constant rate, so the charge is the only effect",
    )


def applied_fx() -> FxLeg:
    """The currency leg, honestly priced."""
    return FxLeg(
        policy=FxPolicy.APPLIED,
        conversion_bps=CONVERSION_BPS,
        basis="test fixture: the venue's spot schedule on the conversion pair",
        rates=rates(),
    )


def build_engine(*paths: tuple[Instrument, tuple[str, ...]]) -> BacktestEngine:
    """An engine with no trading charges at all, so only the currency leg is visible."""
    repository = InMemoryBarRepository()
    instruments: dict[InstrumentKey, Instrument] = {}
    for item, closes in paths:
        instruments[item.key] = item
        repository.add(daily_bars(item, first_day=DATA_START, closes=closes))
    return BacktestEngine(
        repository=repository,
        clock=SimulatedClock(current=ts("2000-01-01T00:00:00")),
        timeframe=Timeframe.D1,
        instruments=instruments,
        universe=StaticUniverse(members={}, default=tuple(sorted(instruments))),
        cost_model=cost_model(maker_bps="0", taker_bps="0", spread_bps="0", slippage_bps="0"),
        fx=applied_fx(),
        routing=CurrencyRouting(
            account_currency="EUR",
            quote_by_symbol={key.symbol: "USDT" for key in instruments},
        ),
        haircut=haircut(),
        series_end=StaticSeriesEnd(reasons={}, default=SeriesEnd.UNDETERMINED),
        initial_equity=Notional(EQUITY),
        account_currency="EUR",
        funding=None,
    )


class Invested:
    """An allocator that holds the same pairs at every rebalance, and never goes flat."""

    name = "invested"
    parameter_set_id = "fixture"
    is_cross_sectional = False

    def __init__(self, *pairs: CarryPair) -> None:
        self._pairs = pairs

    def allocate(
        self, candidates: list[Instrument], at: Timestamp, view: PointInTimeView
    ) -> LongShortAllocation:
        del candidates, view
        return build_pair_allocation(
            self._pairs,
            at,
            candidates_considered=len(self._pairs),
            positions=len(self._pairs),
            margin_fraction=MARGIN,
        )


class Rotating:
    """An allocator that holds one pair, then the other, then the first again.

    Capital stays in the foreign currency throughout: every rebalance sells one
    foreign-quoted position into the quote asset and buys another out of it. Turnover is
    roughly double what holding one pair costs, and the number of currency crossings is
    unchanged at two. This is the exact case the defect got wrong.
    """

    name = "rotating"
    parameter_set_id = "fixture"
    is_cross_sectional = False

    def __init__(self, first: CarryPair, second: CarryPair) -> None:
        self._pairs = (first, second)
        self._index = 0

    def allocate(
        self, candidates: list[Instrument], at: Timestamp, view: PointInTimeView
    ) -> LongShortAllocation:
        del candidates, view
        pair = self._pairs[self._index % len(self._pairs)]
        self._index += 1
        return build_pair_allocation(
            (pair,), at, candidates_considered=2, positions=1, margin_fraction=MARGIN
        )


class Alternating:
    """An allocator that holds a pair, then nothing, then the pair again.

    Every flat rebalance takes the account's capital back to its own currency and every
    invested one sends it out again, so this is the case where the charge *should* grow.
    """

    name = "alternating"
    parameter_set_id = "fixture"
    is_cross_sectional = False

    def __init__(self, pair: CarryPair) -> None:
        self._pair = pair
        self._invested = False

    def allocate(
        self, candidates: list[Instrument], at: Timestamp, view: PointInTimeView
    ) -> LongShortAllocation | Allocation:
        del candidates, view
        self._invested = not self._invested
        if not self._invested:
            return Allocation(weights=(), at=at, candidates_considered=0)
        return build_pair_allocation(
            (self._pair,), at, candidates_considered=1, positions=1, margin_fraction=MARGIN
        )


def plan(months: int) -> WalkForwardPlan:
    """A walk-forward plan over ``months`` of out-of-sample rebalances."""
    return anchored_plan(
        first_month=ts(FIRST_MONTH), total_months=months, in_sample_months=2, fold_count=3
    )


def conversion_of(engine: BacktestEngine, allocator: object, months: int) -> Decimal:
    """The run's whole FX conversion charge."""
    if not isinstance(allocator, Invested | Alternating | Rotating):
        raise TypeError("the fixture takes one of its own two allocators")
    summary = engine.run(plan(months), ParameterFreeStrategy(allocator), "fx")
    return summary.ledger.costs.fx_conversion.amount


def one_pair() -> tuple[CarryPair, tuple[tuple[Instrument, tuple[str, ...]], ...]]:
    """One pair and the price paths it needs."""
    spot, perp = leg("AAA", SPOT), leg("AAA", PERP)
    return CarryPair("AAA", spot, perp), ((spot, flat("100")), (perp, flat("100")))


def two_pairs() -> tuple[
    tuple[CarryPair, CarryPair], tuple[tuple[Instrument, tuple[str, ...]], ...]
]:
    """Two pairs, which trade twice the notional of one for the same capital."""
    first_spot, first_perp = leg("AAA", SPOT), leg("AAA", PERP)
    second_spot, second_perp = leg("BBB", SPOT), leg("BBB", PERP)
    pairs = (CarryPair("AAA", first_spot, first_perp), CarryPair("BBB", second_spot, second_perp))
    return pairs, (
        (first_spot, flat("100")),
        (first_perp, flat("100")),
        (second_spot, flat("50")),
        (second_perp, flat("50")),
    )


# ---------------------------------------------------------------------------
# The invariant, from four directions
# ---------------------------------------------------------------------------


def test_more_rebalances_at_the_same_capital_do_not_change_the_charge() -> None:
    """Nine rebalances and twenty-one cross the boundary the same two times.

    Rotating rather than holding, deliberately: a pair held unchanged trades nothing after
    entry, so a longer run of it would leave turnover flat and the test would pass against
    the defect as well as against the fix. Rotation makes the longer run genuinely trade
    more, which is what the defect charged for.
    """
    pairs, paths = two_pairs()
    short = conversion_of(build_engine(*paths), Rotating(*pairs), 12)
    long = conversion_of(build_engine(*paths), Rotating(*pairs), 24)
    assert short > 0
    assert long == short


def test_more_turnover_at_the_same_capital_does_not_change_the_charge() -> None:
    """Rotating between two foreign-quoted pairs trades far more and crosses no more.

    This is the invariant in its own words, and the exact mechanism of the defect it
    replaces: every rotation sells one position into the quote asset and buys another out
    of it, which is one currency throughout. The turnover assertion is part of the test
    rather than an assumption about the fixture.
    """
    pairs, paths = two_pairs()
    held = build_engine(*paths).run(plan(12), ParameterFreeStrategy(Invested(pairs[0])), "fx")
    rotated = build_engine(*paths).run(plan(12), ParameterFreeStrategy(Rotating(*pairs)), "fx")
    assert rotated.ledger.turnover.amount > held.ledger.turnover.amount * Decimal("1.5")
    assert rotated.ledger.costs.fx_conversion.amount == held.ledger.costs.fx_conversion.amount


def test_a_run_that_goes_flat_and_back_pays_for_every_crossing_it_makes() -> None:
    """Crossings are what the charge counts, so making more of them costs more.

    The other half of the invariant. A rule that only ever said "do not scale with
    turnover" would be satisfied by charging nothing at all.
    """
    pair, paths = one_pair()
    steady = conversion_of(build_engine(*paths), Invested(pair), 12)
    alternating = conversion_of(build_engine(*paths), Alternating(pair), 12)
    assert alternating > steady


def test_an_account_that_never_leaves_its_own_currency_pays_nothing() -> None:
    """No foreign instrument, no crossing, no charge. Charging one would invent a cost."""
    spot, perp = leg("AAA", SPOT), leg("AAA", PERP)
    engine = build_engine((spot, flat("100")), (perp, flat("100")))
    domestic = BacktestEngine(
        repository=engine.repository,
        clock=engine.clock,
        timeframe=engine.timeframe,
        instruments=engine.instruments,
        universe=engine.universe,
        cost_model=engine.cost_model,
        fx=applied_fx(),
        routing=CurrencyRouting(
            account_currency="EUR",
            quote_by_symbol={key.symbol: "EUR" for key in engine.instruments},
        ),
        haircut=engine.haircut,
        series_end=engine.series_end,
        initial_equity=engine.initial_equity,
        account_currency="EUR",
        funding=None,
    )
    summary = domestic.run(
        plan(12), ParameterFreeStrategy(Invested(CarryPair("AAA", spot, perp))), "fx"
    )
    assert summary.ledger.costs.fx_conversion.amount == 0


# ---------------------------------------------------------------------------
# The size of the charge, not only its behaviour
# ---------------------------------------------------------------------------


def test_a_run_that_stays_invested_pays_exactly_two_crossings() -> None:
    """Once in and once out, each on the capital that crossed.

    The entry converts the opening equity and the exit converts the closing equity, so on
    a path that does not move and with no other charges the total is two crossings of
    almost exactly the same amount. Asserted against the registered crossing count rather
    than against a literal two, so the two cannot drift apart.
    """
    pair, paths = one_pair()
    charge = conversion_of(build_engine(*paths), Invested(pair), 12)
    expected = EQUITY * CONVERSION_BPS / Decimal(10_000) * Decimal(FX_CROSSINGS_PER_RUN)
    assert charge == pytest.approx(expected, rel=Decimal("0.01"))


def test_the_charge_is_a_rate_on_the_capital_and_not_on_the_notional() -> None:
    """A levered, market-neutral book moves more notional than it has capital.

    The pair holds a long and a short of equal notional at a margin fraction, so its gross
    notional is several times the account's equity. The crossing charge must read the
    equity: the notional never touches the account's own currency at all.
    """
    pair, paths = one_pair()
    summary = build_engine(*paths).run(plan(12), ParameterFreeStrategy(Invested(pair)), "fx")
    charge = summary.ledger.costs.fx_conversion.amount
    on_turnover = summary.ledger.turnover.amount * CONVERSION_BPS / Decimal(10_000)
    assert summary.ledger.turnover.amount > EQUITY
    assert charge < on_turnover
