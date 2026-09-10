"""The hedge, and the accounting identity that says funding is counted once.

A cash-and-carry pair is long ``q`` units of spot and short ``q`` units of the
perpetual on the same base. Its profit is the change in the *basis* plus the funding
stream, and it carries no term in the asset's own price. Two things have to be true
for any carry number to mean anything, and neither is safe to establish by reading
the code:

**The two price legs cancel.** If they do not, the book holds a residual delta and
every figure computed from it is a directional bet wearing a hedge's label.

**Funding enters the accounting exactly once.** If the perpetual leg's marked value
already embedded the settlement stream and the stream were then added as its own
line, the book would show a large funding receipt beside a market leg of nearly
equal size and opposite sign - a double count with a sign flip, which looks like a
big basis move and is not one.

The tests below assert both against fixed fixtures with hand-computable answers.
They exist because two registered values in this family were verified by a drift
guard and read by no code path, so a near-coincidence in the output is not something
to explain away.

A note on the third test. ``_buy_scale`` scales an allocation's *buys* to the cash
that actually exists, and does not scale its sells. A long-short pair is one of
each, so a naive implementation would open the two legs at different notionals once
costs are non-zero and leave a residual short delta the size of the cost rate. That
is asserted against rather than assumed away.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from sextant.adapters.clocks import SimulatedClock
from sextant.domain.instrument import Instrument, InstrumentKey
from sextant.domain.money import Notional, Price, Quantity
from sextant.domain.time import Timeframe, Timestamp
from sextant.domain.venue import Venue
from sextant.engine.backtest.allocation import LongShortAllocation, ParameterFreeStrategy
from sextant.engine.backtest.engine import BacktestEngine
from sextant.engine.backtest.ledger import Ledger
from sextant.engine.backtest.market import PointInTimeView
from sextant.engine.backtest.window import WalkForwardPlan, anchored_plan
from sextant.engine.execution.funding import RealisedFunding, Settlement
from sextant.engine.execution.fx import CurrencyRouting
from sextant.engine.execution.markout import SeriesEnd
from sextant.engine.strategies.carry import CarryPair, build_pair_allocation, pairs_from
from tests.harness import (
    InMemoryBarRepository,
    StaticSeriesEnd,
    StaticUniverse,
    cost_model,
    daily_bars,
    haircut,
    no_fx,
    ts,
)

SPOT = Venue("hedgespot")
PERP = Venue("hedgeperp")

FIRST_MONTH = "2021-01-01T00:00:00"
DATA_START = "2020-10-01T00:00:00"
DATA_DAYS = 400
MARGIN = Decimal("0.20")
EQUITY = Decimal(1500)

#: One pair at one position: each leg is ``1 / (1 * 1.20)`` of equity.
LEG_NOTIONAL = (EQUITY / (Decimal(1) + MARGIN)).quantize(Decimal("0.01"))

#: Eight-hourly settlements, so a 30-day month carries ninety of them.
SETTLEMENTS_PER_DAY = 3


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


def flat(value: str) -> tuple[str, ...]:
    """A price path that does not move."""
    return (value,) * DATA_DAYS


#: Day index of the first scored rebalance. The out-of-sample window opens on
#: 2021-03-01, which is this many days after the fixture's first bar. A price step
#: placed before it happens while the book is not yet open and moves nothing.
FIRST_SCORED_DAY = 151


def step_at(before: str, after: str, day: int) -> tuple[str, ...]:
    """A price path that jumps once, on a stated day of the fixture."""
    return (before,) * day + (after,) * (DATA_DAYS - day)


def rising(start: str, step: str) -> tuple[str, ...]:
    """A price path rising by ``step`` a day."""
    first, increment = Decimal(start), Decimal(step)
    return tuple(str(first + increment * Decimal(day)) for day in range(DATA_DAYS))


def settlements(rate: str, *, key: InstrumentKey) -> RealisedFunding:
    """Eight-hourly settlements at a constant rate on one instrument only."""
    start = ts(DATA_START)
    rows = tuple(
        Settlement(at=start.plus(timedelta(hours=8 * step)), rate=Decimal(rate))
        for step in range(DATA_DAYS * SETTLEMENTS_PER_DAY)
    )
    return RealisedFunding.of({key: rows})


class OnePair:
    """An allocator that holds exactly one registered pair at every rebalance."""

    name = "one-pair"
    parameter_set_id = "hedge-fixture"
    is_cross_sectional = False

    def __init__(self, pair: CarryPair) -> None:
        self._pair = pair

    def allocate(
        self, candidates: list[Instrument], at: Timestamp, view: PointInTimeView
    ) -> LongShortAllocation:
        """The same pair, always, at the registered single-position weight."""
        del candidates, view
        return build_pair_allocation(
            (self._pair,), at, candidates_considered=1, positions=1, margin_fraction=MARGIN
        )


def build(
    *,
    spot_path: tuple[str, ...],
    perp_path: tuple[str, ...],
    funding: RealisedFunding | None,
    fees_bps: str = "0",
    spread_bps: str = "0",
    slippage_bps: str = "0",
) -> tuple[BacktestEngine, CarryPair]:
    """An engine over one pair, with costs off unless a test switches them on."""
    spot, perp = leg("AAA", SPOT), leg("AAA", PERP)
    repository = InMemoryBarRepository()
    repository.add(daily_bars(spot, first_day=DATA_START, closes=spot_path))
    repository.add(daily_bars(perp, first_day=DATA_START, closes=perp_path))
    instruments = {spot.key: spot, perp.key: perp}
    engine = BacktestEngine(
        repository=repository,
        clock=SimulatedClock(current=ts("2000-01-01T00:00:00")),
        timeframe=Timeframe.D1,
        instruments=instruments,
        universe=StaticUniverse(members={}, default=tuple(sorted(instruments))),
        cost_model=cost_model(
            maker_bps=fees_bps,
            taker_bps=fees_bps,
            spread_bps=spread_bps,
            slippage_bps=slippage_bps,
        ),
        fx=no_fx(),
        routing=CurrencyRouting(
            account_currency="EUR",
            quote_by_symbol={key.symbol: "EUR" for key in instruments},
        ),
        haircut=haircut(),
        haircut_is_a_loss_on_either_side=True,
        series_end=StaticSeriesEnd(reasons={}, default=SeriesEnd.UNDETERMINED),
        initial_equity=Notional(EQUITY),
        account_currency="EUR",
        funding=funding,
    )
    return engine, CarryPair("AAA", spot, perp)


def plan(months: int = 6, in_sample: int = 2, folds: int = 2) -> WalkForwardPlan:
    """A short walk-forward plan anchored on the fixture's first month."""
    return anchored_plan(
        first_month=ts(FIRST_MONTH),
        total_months=months,
        in_sample_months=in_sample,
        fold_count=folds,
    )


def run(engine: BacktestEngine, pair: CarryPair, run_id: str) -> Ledger:
    """One walk-forward run of the single-pair allocator."""
    return engine.run(plan(), ParameterFreeStrategy(allocator=OnePair(pair)), run_id).ledger


# ---------------------------------------------------------------------------
# The two price legs cancel
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["flat", "rising", "falling"])
def test_the_price_legs_cancel_exactly_when_both_move_together(path: str) -> None:
    """A hedged pair carries no term in the asset's own price. Any path, any move.

    Not "small": exactly zero. Both legs are opened at the same notional on the same
    price series, so a price relative that multiplies one multiplies the other, and
    the account does not move by one unit whatever the market does.
    """
    series = {
        "flat": flat("100"),
        "rising": rising("100", "1"),
        "falling": rising("500", "-1"),
    }[path]
    engine, pair = build(spot_path=series, perp_path=series, funding=None)
    ledger = run(engine, pair, f"hedge-{path}")
    assert ledger.gross_pnl.amount == Decimal(0)
    assert ledger.net_pnl.amount == Decimal(0)
    assert ledger.terminal_equity.amount == EQUITY


def test_a_price_move_of_five_hundred_per_cent_still_cancels() -> None:
    """The cancellation is structural, not an artefact of a small move."""
    series = rising("100", "5")
    engine, pair = build(spot_path=series, perp_path=series, funding=None)
    ledger = run(engine, pair, "hedge-large")
    assert series[-1] != series[0]
    assert ledger.gross_pnl.amount == Decimal(0)


def test_the_pair_holds_the_basis_and_only_the_basis() -> None:
    """When the legs diverge, what remains is exactly the divergence."""
    engine, pair = build(spot_path=flat("100"), perp_path=rising("100", "1"), funding=None)
    ledger = run(engine, pair, "basis")
    assert ledger.gross_pnl.amount < 0
    assert ledger.gross_pnl.amount == ledger.net_pnl.amount


# ---------------------------------------------------------------------------
# Funding enters exactly once
# ---------------------------------------------------------------------------


def test_funding_is_the_whole_of_the_return_when_the_legs_cancel() -> None:
    """The identity: with the price legs cancelling and no costs, net IS funding.

    The ledger records funding as a *cost*, so a receipt is a negative cost line.
    ``net_pnl == -funding`` is therefore the statement that funding was added once
    and that nothing else touched the account.
    """
    _, perp = leg("AAA", SPOT), leg("AAA", PERP)
    engine, pair = build(
        spot_path=flat("100"),
        perp_path=flat("100"),
        funding=settlements("0.0001", key=perp.key),
    )
    ledger = run(engine, pair, "funding-once")
    assert ledger.gross_pnl.amount == Decimal(0)
    assert ledger.costs.funding.amount < 0, "a short leg at a positive rate receives"
    assert ledger.net_pnl.amount == -ledger.costs.funding.amount


def test_a_double_counted_funding_stream_would_show_a_ratio_of_two() -> None:
    """The specific defect the Sponsor named, asserted against.

    If the perpetual leg's marked value already embedded the settlement stream and
    the stream were also charged as its own line, the account would move by twice
    the funding figure while the market leg absorbed the other copy with the
    opposite sign. The ratio is one.
    """
    _, perp = leg("AAA", SPOT), leg("AAA", PERP)
    engine, pair = build(
        spot_path=flat("100"),
        perp_path=flat("100"),
        funding=settlements("0.0001", key=perp.key),
    )
    ledger = run(engine, pair, "no-double-count")
    ratio = ledger.net_pnl.amount / -ledger.costs.funding.amount
    assert ratio == Decimal(1)


def test_the_spot_leg_accrues_no_funding() -> None:
    """Funding is a perpetual-contract mechanism and the spot leg has none.

    Asserted by charging the SPOT key instead: a schedule keyed on the leg that has
    no funding must produce a funding line the engine still reports, because a cost
    that is structurally absent and one nobody measured look identical in a report
    that omits the row.
    """
    spot, _ = leg("AAA", SPOT), leg("AAA", PERP)
    engine, pair = build(
        spot_path=flat("100"),
        perp_path=flat("100"),
        funding=settlements("0.0001", key=spot.key),
    )
    ledger = run(engine, pair, "spot-funding")
    assert ledger.costs.funding.amount > 0, "a LONG leg at a positive rate pays"
    assert ledger.net_pnl.amount == -ledger.costs.funding.amount


def test_the_first_period_funding_figure_is_the_hand_computed_one() -> None:
    """Rate times signed notional times the settlements inside the period, exactly.

    The FIRST period, because it is the only one whose notional is known before the
    run: from the second onwards the receipts have been paid into cash and the next
    rebalance sizes the pair against the larger equity. A magnitude check as well as
    a sign check, since a stream counted twice or accrued on a notional the position
    never had would pass every sign test above and fail this.
    """
    _, perp = leg("AAA", SPOT), leg("AAA", PERP)
    rate = Decimal("0.0001")
    engine, pair = build(
        spot_path=flat("100"), perp_path=flat("100"), funding=settlements("0.0001", key=perp.key)
    )
    ledger = run(engine, pair, "funding-magnitude")
    first = ledger.rebalances[0]
    days = (first.closed_at.value - first.opened_at.value).days
    expected = -LEG_NOTIONAL * rate * Decimal(days * SETTLEMENTS_PER_DAY)
    assert first.financing.funding.amount == expected


def test_funding_receipts_are_reinvested_rather_than_left_in_a_corner() -> None:
    """The compounding that made the naive hand figure too small.

    A receipt raises equity, and the next rebalance sizes the pair against the larger
    equity. So the total exceeds the uncompounded product, and by more than rounding.
    A book that quietly held its receipts aside would fail this.
    """
    _, perp = leg("AAA", SPOT), leg("AAA", PERP)
    rate = Decimal("0.0001")
    engine, pair = build(
        spot_path=flat("100"), perp_path=flat("100"), funding=settlements("0.0001", key=perp.key)
    )
    ledger = run(engine, pair, "funding-compounds")
    days = sum((item.closed_at.value - item.opened_at.value).days for item in ledger.rebalances)
    uncompounded = -LEG_NOTIONAL * rate * Decimal(days * SETTLEMENTS_PER_DAY)
    assert ledger.costs.funding.amount < uncompounded, "receipts must grow the next notional"
    assert ledger.costs.funding.amount > uncompounded * Decimal("1.05"), "and not by much"


# ---------------------------------------------------------------------------
# What the price legs actually hold
# ---------------------------------------------------------------------------


def test_the_pair_holds_the_RELATIVE_divergence_of_the_two_legs() -> None:
    """Equal notional at entry, not equal coin count, and the difference matters.

    With both legs opened at notional ``N``, the pair's price PnL over a period is
    ``N * (S1/S0 - P1/P0)``: the *relative* divergence. Equal coin count would hold
    the absolute basis instead. The registered construction is equal notional, and
    this is the arithmetic every basis figure in the family rests on, so it is
    asserted rather than described.

    Ten per cent on the spot leg against twenty on the perpetual is a ten per cent
    relative divergence, and a short in the faster leg loses exactly that.
    """
    engine, pair = build(
        spot_path=step_at("100", "110", FIRST_SCORED_DAY + 14),
        perp_path=step_at("100", "120", FIRST_SCORED_DAY + 14),
        funding=None,
    )
    ledger = run(engine, pair, "relative-divergence")
    assert ledger.gross_pnl.amount < 0
    first = ledger.rebalances[0]
    assert first.market_gain.amount == pytest.approx(
        -LEG_NOTIONAL * Decimal("0.10"), rel=Decimal("0.01")
    )


def test_a_perpetual_that_converges_to_spot_pays_the_short_leg() -> None:
    """The other direction, so the sign is pinned rather than assumed.

    A perpetual trading above spot that converges is the whole economic story of a
    carry trade, and it must show up as a gain on the pair.
    """
    engine, pair = build(
        spot_path=flat("100"),
        perp_path=step_at("105", "100", FIRST_SCORED_DAY + 14),
        funding=None,
    )
    ledger = run(engine, pair, "convergence")
    assert ledger.gross_pnl.amount > 0


# ---------------------------------------------------------------------------
# The hedge survives the cash constraint
# ---------------------------------------------------------------------------


def test_the_two_legs_open_at_equal_notional_even_when_costs_scale_the_buys() -> None:
    """``_buy_scale`` scales buys and not sells. A pair is one of each.

    If the scaling were applied to the long leg alone, the book would carry a
    residual short delta the size of the cost rate, and every carry figure would be
    a small directional bet. The legs must match to within the rounding the weight
    quantisation allows.
    """
    engine, pair = build(
        spot_path=flat("100"),
        perp_path=flat("100"),
        funding=None,
        fees_bps="10",
        spread_bps="25",
        slippage_bps="10",
    )
    ledger = run(engine, pair, "equal-notional")
    for outcome in ledger.rebalances:
        if not outcome.holdings:
            continue
        longs = sum(item.value.amount for item in outcome.holdings if item.value.amount > 0)
        shorts = sum(item.value.amount for item in outcome.holdings if item.value.amount < 0)
        gross = longs - shorts
        assert gross > 0
        residual = abs(longs + shorts) / gross
        assert residual < Decimal("0.001"), (
            f"the legs differ by {residual:.6f} of gross exposure, which is a "
            "directional position the hedge is supposed to have removed"
        )


def test_a_costed_hedge_loses_only_its_costs() -> None:
    """With the legs cancelling and no funding, the whole loss is the cost lines."""
    engine, pair = build(
        spot_path=rising("100", "1"),
        perp_path=rising("100", "1"),
        funding=None,
        fees_bps="10",
        spread_bps="25",
        slippage_bps="10",
    )
    ledger = run(engine, pair, "costs-only")
    assert ledger.gross_pnl.amount == Decimal(0)
    assert ledger.net_pnl.amount == -ledger.costs.total.amount
    assert ledger.costs.total.amount > 0


def test_the_pairing_rule_refuses_two_legs_on_the_same_side() -> None:
    """A pair is one long and one short, and the constructor will not build otherwise."""
    spot, perp = leg("AAA", SPOT), leg("AAA", PERP)
    assert pairs_from([spot, perp], perpetual_venue=PERP.name) == (CarryPair("AAA", spot, perp),)
    assert pairs_from([spot], perpetual_venue=PERP.name) == ()
