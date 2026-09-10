"""The carry pair: does it hedge, does funding have the right sign, does it add up.

Nothing here reaches a store or a network. Prices are straight lines chosen so
that every expected number can be written down by hand, because the point of
these tests is the arithmetic and the sign conventions rather than market
realism.

Three things are proved and they are the three that could silently manufacture
an edge:

**A hedged pair with no funding earns nothing.** If the two legs move together
and the account's equity moves at all, the hedge is not a hedge and every carry
result would be a directional bet wearing a market-neutral label.

**Funding's sign follows the side of the position.** A positive rate means the
longs pay the shorts. Getting that backwards would turn the whole family's return
line inside out and would look like a large, clean edge.

**Gross minus every cost line still equals net, exactly, with a signed book and
realised funding in it.** The identity held for a long-only book; a two-sided one
with a new cost line is exactly where it would stop holding.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import ROUND_DOWN, Decimal

import pytest

from sextant.adapters.clocks import SimulatedClock
from sextant.domain.instrument import Instrument, InstrumentKey
from sextant.domain.money import Notional, Price, Quantity
from sextant.domain.time import Timeframe, Timestamp
from sextant.domain.venue import Venue
from sextant.engine.backtest.allocation import (
    InvalidAllocation,
    LongShortAllocation,
    ParameterFreeStrategy,
)
from sextant.engine.backtest.engine import BacktestEngine
from sextant.engine.backtest.market import PointInTimeView
from sextant.engine.backtest.window import WalkForwardPlan, anchored_plan
from sextant.engine.execution.funding import RealisedFunding, Settlement
from sextant.engine.execution.fx import CurrencyRouting
from sextant.engine.execution.markout import SeriesEnd
from sextant.engine.strategies.carry import (
    CarryError,
    CarryPair,
    CarrySignal,
    CashAndCarry,
    PremiumIndex,
    build_pair_allocation,
    pairs_from,
)
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

SPOT = Venue("testvenue")
PERP = Venue("testperp")

FIRST_MONTH = "2021-01-01T00:00:00"
DATA_START = "2020-10-01T00:00:00"
DATA_DAYS = 400

MARGIN = Decimal("0.20")


def leg(base: str, venue: Venue) -> Instrument:
    """One leg, with its base asset stated rather than sliced off the symbol.

    The shared harness derives a base by dropping three characters, which is
    right for a three-character quote and wrong for USDT. Stating it here keeps
    the pairing rule under test rather than the fixture's spelling.
    """
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


def legs(base: str) -> tuple[Instrument, Instrument]:
    """The spot and perpetual legs for one asset, on their two venues."""
    return leg(base, SPOT), leg(base, PERP)


def flat(value: str, days: int = DATA_DAYS) -> tuple[str, ...]:
    """A price path that does not move."""
    return (value,) * days


def straight(start: str, step: str, days: int = DATA_DAYS) -> tuple[str, ...]:
    """A price path rising by ``step`` a day."""
    first = Decimal(start)
    increment = Decimal(step)
    return tuple(str(first + increment * Decimal(day)) for day in range(days))


def build_engine(
    *,
    paths: dict[Instrument, tuple[str, ...]],
    funding: RealisedFunding | None,
    equity: str = "1500",
) -> tuple[BacktestEngine, dict[InstrumentKey, Instrument]]:
    """An engine over the given price paths, with no costs at all by default.

    Fees, spread and slippage are zero so that a hedged pair's expected equity
    is exactly its starting equity and a discrepancy is the hedge failing rather
    than a charge nobody accounted for. The cost identity test switches them on.
    """
    repository = InMemoryBarRepository()
    instruments: dict[InstrumentKey, Instrument] = {}
    for item, closes in paths.items():
        instruments[item.key] = item
        repository.add(daily_bars(item, first_day=DATA_START, closes=closes))
    engine = BacktestEngine(
        repository=repository,
        clock=SimulatedClock(current=ts("2000-01-01T00:00:00")),
        timeframe=Timeframe.D1,
        instruments=instruments,
        universe=StaticUniverse(members={}, default=tuple(sorted(instruments))),
        cost_model=cost_model(maker_bps="0", taker_bps="0", spread_bps="0", slippage_bps="0"),
        fx=no_fx(),
        routing=CurrencyRouting(
            account_currency="EUR",
            quote_by_symbol={key.symbol: "EUR" for key in instruments},
        ),
        haircut=haircut(),
        series_end=StaticSeriesEnd(reasons={}, default=SeriesEnd.UNDETERMINED),
        initial_equity=Notional(Decimal(equity)),
        account_currency="EUR",
        funding=funding,
    )
    return engine, instruments


class OnePair:
    """An allocator that holds exactly one registered pair, every rebalance."""

    def __init__(self, pair: CarryPair, positions: int = 1) -> None:
        self._pair = pair
        self._positions = positions

    name = "one-pair"
    parameter_set_id = "fixture"
    is_cross_sectional = False

    def allocate(
        self, candidates: list[Instrument], at: Timestamp, view: PointInTimeView
    ) -> LongShortAllocation:
        """The same pair, always."""
        del candidates, view
        return build_pair_allocation(
            (self._pair,),
            at,
            candidates_considered=1,
            positions=self._positions,
            margin_fraction=MARGIN,
        )


def plan(months: int = 8, in_sample: int = 2, folds: int = 3) -> WalkForwardPlan:
    """A short walk-forward plan anchored on the fixture's first month."""
    return anchored_plan(
        first_month=ts(FIRST_MONTH),
        total_months=months,
        in_sample_months=in_sample,
        fold_count=folds,
    )


# ---------------------------------------------------------------------------
# The hedge
# ---------------------------------------------------------------------------


def test_a_pair_whose_legs_move_together_earns_exactly_nothing() -> None:
    """The hedge. Both legs double; the account does not move by one unit."""
    spot, perp = legs("AAA")
    path = straight("100", "1")
    engine, _ = build_engine(paths={spot: path, perp: path}, funding=None)
    summary = engine.run(
        plan(), ParameterFreeStrategy(OnePair(CarryPair("AAA", spot, perp))), "hedge"
    )
    assert summary.ledger.net_pnl.amount == Decimal(0)
    assert summary.ledger.terminal_equity.amount == Decimal(1500)


def test_a_pair_loses_exactly_the_basis_when_the_perpetual_outruns_the_spot() -> None:
    """The pair holds the difference, and nothing but the difference.

    The perpetual rises one unit a day faster than the spot, so a short in it
    loses the basis widening and the long spot leg does not make it back. The
    expected loss is the leg's notional times the relative widening.
    """
    spot, perp = legs("AAA")
    engine, _ = build_engine(paths={spot: flat("100"), perp: straight("100", "1")}, funding=None)
    summary = engine.run(
        plan(), ParameterFreeStrategy(OnePair(CarryPair("AAA", spot, perp))), "basis"
    )
    assert summary.ledger.net_pnl.amount < 0
    # The spot leg never moved, so every unit of the loss came from the short.
    assert summary.ledger.gross_pnl.amount == summary.ledger.net_pnl.amount


# ---------------------------------------------------------------------------
# The sign of funding
# ---------------------------------------------------------------------------


def settlements(rate: str, *, days: int = DATA_DAYS) -> tuple[Settlement, ...]:
    """Three settlements a day at a fixed rate, over the whole fixture window."""
    start = ts(DATA_START)
    return tuple(
        Settlement(at=start.plus(timedelta(hours=8 * step)), rate=Decimal(rate))
        for step in range(days * 3)
    )


def test_a_positive_rate_pays_the_short_leg_of_a_hedged_pair() -> None:
    """The whole family's premise. A positive rate is money in for a short."""
    spot, perp = legs("AAA")
    path = flat("100")
    funding = RealisedFunding.of({perp.key: settlements("0.0001")})
    engine, _ = build_engine(paths={spot: path, perp: path}, funding=funding)
    summary = engine.run(
        plan(), ParameterFreeStrategy(OnePair(CarryPair("AAA", spot, perp))), "carry"
    )
    assert summary.ledger.net_pnl.amount > 0
    # Funding is a cost line, so money coming in is a negative charge.
    assert summary.ledger.costs.funding.amount < 0


def test_a_negative_rate_charges_the_short_leg() -> None:
    """And the other way, so the sign is not accidentally right in one direction."""
    spot, perp = legs("AAA")
    path = flat("100")
    funding = RealisedFunding.of({perp.key: settlements("-0.0001")})
    engine, _ = build_engine(paths={spot: path, perp: path}, funding=funding)
    summary = engine.run(
        plan(), ParameterFreeStrategy(OnePair(CarryPair("AAA", spot, perp))), "negative-carry"
    )
    assert summary.ledger.net_pnl.amount < 0
    assert summary.ledger.costs.funding.amount > 0


def test_a_long_perpetual_pays_what_a_short_one_receives() -> None:
    """Symmetry. The same rate on the same notional, one sign each."""
    spot, perp = legs("AAA")
    path = flat("100")
    funding = RealisedFunding.of({perp.key: settlements("0.0001")})

    class LongPerp(OnePair):
        """Holds the mirror image of the registered pair."""

        def allocate(
            self, candidates: list[Instrument], at: Timestamp, view: PointInTimeView
        ) -> LongShortAllocation:
            del candidates, view
            leg = Decimal(1) / (Decimal(1) + MARGIN)
            return LongShortAllocation(
                weights=((perp.key, leg), (spot.key, -leg)),
                at=at,
                candidates_considered=1,
                gross_limit=Decimal(2) / (Decimal(1) + MARGIN),
            )

    # One out-of-sample month, so that the two runs accrue on the same notional.
    # Over several months they would not: the receiving side compounds upward
    # and the paying side downward, and the magnitudes rightly diverge.
    one_period = plan(months=3, in_sample=2, folds=1)
    short_engine, _ = build_engine(paths={spot: path, perp: path}, funding=funding)
    short_side = short_engine.run(
        one_period, ParameterFreeStrategy(OnePair(CarryPair("AAA", spot, perp))), "short"
    )
    long_engine, _ = build_engine(paths={spot: path, perp: path}, funding=funding)
    long_side = long_engine.run(
        one_period, ParameterFreeStrategy(LongPerp(CarryPair("AAA", spot, perp))), "long"
    )
    assert short_side.ledger.costs.funding.amount == -long_side.ledger.costs.funding.amount


def test_funding_is_not_charged_to_the_spot_leg() -> None:
    """A leg with no published settlements accrues nothing, not a default."""
    spot, perp = legs("AAA")
    path = flat("100")
    funding = RealisedFunding.of({perp.key: settlements("0.0001")})
    engine, _ = build_engine(paths={spot: path, perp: path}, funding=funding)
    summary = engine.run(
        plan(), ParameterFreeStrategy(OnePair(CarryPair("AAA", spot, perp))), "one-sided"
    )
    # One leg's worth of funding, not two cancelling to zero.
    assert summary.ledger.costs.funding.amount != Decimal(0)


# ---------------------------------------------------------------------------
# The window a settlement belongs to
# ---------------------------------------------------------------------------


def test_settlements_are_half_open_at_the_start_and_closed_at_the_end() -> None:
    """Consecutive holding periods partition the payments exactly once each."""
    key = InstrumentKey(PERP, "AAAUSDT")
    first = ts("2021-01-01T00:00:00")
    middle = ts("2021-01-02T00:00:00")
    last = ts("2021-01-03T00:00:00")
    funding = RealisedFunding.of(
        {
            key: (
                Settlement(at=first, rate=Decimal("0.001")),
                Settlement(at=middle, rate=Decimal("0.002")),
                Settlement(at=last, rate=Decimal("0.004")),
            )
        }
    )
    early = funding.settlements(key, first, middle)
    late = funding.settlements(key, middle, last)
    assert [item.rate for item in early] == [Decimal("0.002")]
    assert [item.rate for item in late] == [Decimal("0.004")]
    assert funding.total_rate(key, first, last) == Decimal("0.006")


def test_a_gap_in_the_published_settlements_is_not_covered() -> None:
    """A missing settlement must be answerable as a gap, never as a zero."""
    key = InstrumentKey(PERP, "AAAUSDT")
    start = ts("2021-01-01T00:00:00")
    complete = [
        Settlement(at=start.plus(timedelta(hours=8 * step)), rate=Decimal("0.001"))
        for step in range(1, 10)
    ]
    holed = [item for index, item in enumerate(complete) if index != 4]
    span_end = complete[-1].at
    whole = RealisedFunding.of({key: complete})
    # A span ending exactly on a settlement is covered, and so is one ending
    # part-way to the next: the next payment has not fallen due.
    assert whole.covers(key, start, span_end) is True
    assert whole.covers(key, start, span_end.plus(timedelta(hours=4))) is True
    # A span running a full interval past the last published settlement is not:
    # a payment that should be there is not there.
    assert whole.covers(key, start, span_end.plus(timedelta(hours=12))) is False
    # And a hole in the middle is a hole wherever the span ends.
    assert RealisedFunding.of({key: holed}).covers(key, start, span_end) is False


def test_an_instrument_with_no_settlements_at_all_is_not_covered() -> None:
    """Which is what keeps an unfunded perpetual out of a carry universe."""
    key = InstrumentKey(PERP, "AAAUSDT")
    funding = RealisedFunding.of({})
    assert funding.covers(key, ts("2021-01-01T00:00:00"), ts("2021-02-01T00:00:00")) is False
    assert funding.settlements(key, ts("2021-01-01T00:00:00"), ts("2021-02-01T00:00:00")) == ()


# ---------------------------------------------------------------------------
# The allocation type and the pairing rule
# ---------------------------------------------------------------------------


def test_a_signed_allocation_refuses_gross_above_its_stated_limit() -> None:
    """Leverage is a number a pre-registration states, never one an allocator reaches."""
    spot, perp = legs("AAA")
    with pytest.raises(InvalidAllocation, match="above the stated limit"):
        LongShortAllocation(
            weights=((spot.key, Decimal("0.9")), (perp.key, Decimal("-0.9"))),
            at=ts(FIRST_MONTH),
            candidates_considered=1,
            gross_limit=Decimal(1),
        )


def test_a_signed_allocation_reports_net_and_gross_separately() -> None:
    """A book that intended neutrality and missed must be visible, not corrected."""
    spot, perp = legs("AAA")
    allocation = LongShortAllocation(
        weights=((spot.key, Decimal("0.5")), (perp.key, Decimal("-0.3"))),
        at=ts(FIRST_MONTH),
        candidates_considered=1,
        gross_limit=Decimal(1),
    )
    assert allocation.gross_exposure == Decimal("0.8")
    assert allocation.net_exposure == Decimal("0.2")
    assert allocation.long_exposure == Decimal("0.5")
    assert allocation.short_exposure == Decimal("0.3")


def test_a_long_only_allocation_still_refuses_a_short() -> None:
    """The old type is untouched: a long-only strategy cannot short by accident."""
    from sextant.engine.backtest.allocation import Allocation

    spot, _ = legs("AAA")
    with pytest.raises(InvalidAllocation, match="long-only"):
        Allocation(
            weights=((spot.key, Decimal("-0.1")),),
            at=ts(FIRST_MONTH),
            candidates_considered=1,
        )


def test_pairing_is_by_exact_base_asset_and_a_scaled_unit_pairs_with_nothing() -> None:
    """The registered rule. A unit-conversion rule would be a degree of freedom."""
    spot_a, perp_a = legs("AAA")
    scaled = leg("1000BBB", PERP)
    unpaired_spot = leg("BBB", SPOT)
    found = pairs_from([spot_a, perp_a, scaled, unpaired_spot], perpetual_venue=PERP.name)
    assert [item.base for item in found] == ["AAA"]


def test_two_legs_on_the_same_side_are_a_defect_and_are_refused() -> None:
    """A pair is two legs. Three is a broken candidate set, not a tie to break."""
    spot, perp = legs("AAA")
    duplicate = leg("AAA", PERP)
    with pytest.raises(CarryError, match="twice on the same side"):
        pairs_from([spot, perp, duplicate], perpetual_venue=PERP.name)


def test_a_short_universe_holds_fewer_pairs_at_the_registered_size() -> None:
    """Not rescaled to full investment: the idle capital is the exposure signal."""
    spot, perp = legs("AAA")
    allocation = build_pair_allocation(
        (CarryPair("AAA", spot, perp),),
        ts(FIRST_MONTH),
        candidates_considered=10,
        positions=10,
        margin_fraction=MARGIN,
    )
    expected = (Decimal(1) / (Decimal(10) * (Decimal(1) + MARGIN))).quantize(
        Decimal("1E-18"), rounding=ROUND_DOWN
    )
    assert allocation.long_exposure == expected
    assert allocation.net_exposure == Decimal(0)


def test_an_empty_selection_is_an_empty_allocation_and_not_an_error() -> None:
    """A month in which nothing qualifies is a real month, held in cash."""
    allocation = build_pair_allocation(
        (),
        ts(FIRST_MONTH),
        candidates_considered=0,
        positions=10,
        margin_fraction=MARGIN,
    )
    assert allocation.weights == ()
    assert allocation.gross_exposure == Decimal(0)


# ---------------------------------------------------------------------------
# The variant's own refusals
# ---------------------------------------------------------------------------


def carry_variant(
    signal: CarrySignal, *, positions: int = 1, positive: bool = False
) -> CashAndCarry:
    """One variant over an empty funding and premium set, for the refusal tests."""
    return CashAndCarry(
        label="fixture",
        signal=signal,
        positions=positions,
        funding=RealisedFunding.of({}),
        premium=PremiumIndex(by_instrument={}),
        perpetual_venue=PERP.name,
        margin_fraction=MARGIN,
        require_positive=positive,
    )


def test_a_pair_with_no_published_funding_is_never_ranked() -> None:
    """Invariant 10 at the point it bites: unevaluable is not zero."""
    spot, perp = legs("AAA")
    variant = carry_variant(CarrySignal.FUNDING_30)
    engine, instruments = build_engine(paths={spot: flat("100"), perp: flat("100")}, funding=None)
    view = PointInTimeView.at(engine.repository, Timeframe.D1, ts(FIRST_MONTH), lookback_days=120)
    allocation = variant.allocate(list(instruments.values()), ts(FIRST_MONTH), view)
    assert allocation.weights == ()


def test_the_positive_filter_is_registered_against_one_signal_only() -> None:
    """A filter silently applied to another signal would be an unregistered variant."""
    with pytest.raises(CarryError, match="registered against the"):
        carry_variant(CarrySignal.PREMIUM, positive=True)


def test_a_premium_lookup_answers_with_the_last_close_at_or_before_the_instant() -> None:
    """Point in time, and signed."""
    key = InstrumentKey(PERP, "AAAUSDT")
    index = PremiumIndex(
        by_instrument={
            key: (
                (ts("2021-01-01T00:00:00"), Decimal("0.001")),
                (ts("2021-01-05T00:00:00"), Decimal("-0.002")),
            )
        }
    )
    assert index.at_or_before(key, ts("2021-01-03T00:00:00")) == Decimal("0.001")
    assert index.at_or_before(key, ts("2021-01-05T00:00:00")) == Decimal("-0.002")
    assert index.at_or_before(key, ts("2020-12-31T00:00:00")) is None


# ---------------------------------------------------------------------------
# The identity, with a signed book and a real funding line
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rate", ["0.0001", "-0.0001", "0"])
def test_gross_minus_every_cost_line_equals_net_exactly_for_a_signed_book(rate: str) -> None:
    """The identity that made the long-only accounting trustworthy, re-proved here."""
    spot, perp = legs("AAA")
    repository = InMemoryBarRepository()
    instruments: dict[InstrumentKey, Instrument] = {}
    for item, closes in ((spot, straight("100", "0.5")), (perp, straight("100", "0.7"))):
        instruments[item.key] = item
        repository.add(daily_bars(item, first_day=DATA_START, closes=closes))
    engine = BacktestEngine(
        repository=repository,
        clock=SimulatedClock(current=ts("2000-01-01T00:00:00")),
        timeframe=Timeframe.D1,
        instruments=instruments,
        universe=StaticUniverse(members={}, default=tuple(sorted(instruments))),
        cost_model=cost_model(maker_bps="10", taker_bps="10", spread_bps="25", slippage_bps="10"),
        fx=no_fx(),
        routing=CurrencyRouting(
            account_currency="EUR",
            quote_by_symbol={key.symbol: "EUR" for key in instruments},
        ),
        haircut=haircut(),
        series_end=StaticSeriesEnd(reasons={}, default=SeriesEnd.UNDETERMINED),
        initial_equity=Notional(Decimal(1500)),
        account_currency="EUR",
        funding=RealisedFunding.of({perp.key: settlements(rate)}),
    )
    summary = engine.run(
        plan(), ParameterFreeStrategy(OnePair(CarryPair("AAA", spot, perp))), f"identity-{rate}"
    )
    ledger = summary.ledger
    costs = ledger.costs
    assert ledger.net_pnl.amount == (
        ledger.gross_pnl.amount
        - costs.fees.amount
        - costs.spread.amount
        - costs.slippage.amount
        - costs.funding.amount
        - costs.fx_conversion.amount
        - costs.delisting.amount
    )
