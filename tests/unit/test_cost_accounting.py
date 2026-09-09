"""Exact Decimal cost accounting, item 3 of Phase 0 section 8.

The property: for any generated sequence of trades,

    gross_pnl - fees - spread - slippage - funding - fx_conversion - delisting
        == net_pnl

exactly, in ``Decimal``, to the last digit. Not approximately. A tolerance here
would hide precisely the errors worth finding, and it did once already - an
earlier version of the engine double-counted the entry charge and the failure
was a discrepancy of ten to the minus twenty-six, which any sane tolerance would
have swallowed.

Generated rather than enumerated, and seeded rather than random. ``hypothesis``
would be the natural tool and is not a dependency of this project; a seeded
``random.Random`` over a wide parameter space gives the same coverage for this
particular property, at the cost of not shrinking a failure automatically. The
seed is printed in the failure message so any failure is reproducible.

Funding gets its own test, because Phase 0 asks specifically that it be tied to
holding time rather than to trade count - a funding figure that scales with the
number of trades is a fee wearing the wrong name.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from decimal import Decimal

import pytest

from sextant.adapters.clocks import SimulatedClock
from sextant.domain.instrument import Instrument, InstrumentKey
from sextant.domain.money import Notional
from sextant.domain.time import Timeframe
from sextant.engine.backtest.allocation import ParameterFreeStrategy
from sextant.engine.backtest.baselines import EqualWeightPassive, RandomSelection
from sextant.engine.backtest.engine import BacktestEngine
from sextant.engine.backtest.ledger import CostLines, money
from sextant.engine.backtest.window import WalkForwardPlan, anchored_plan
from sextant.engine.execution.costs import (
    ALL_MAKER,
    ALL_TAKER,
    HALF_AND_HALF,
    CostAssumption,
    CostModelError,
    FeeSchedule,
    FillMix,
    ItemisedCostModel,
    LiquidityBand,
)
from sextant.engine.execution.fx import CurrencyRouting
from sextant.engine.execution.markout import SeriesEnd
from tests.harness import (
    VENUE,
    FixedBand,
    InMemoryBarRepository,
    StaticSeriesEnd,
    StaticUniverse,
    assumption,
    cost_model,
    daily_bars,
    haircut,
    instrument,
    no_fx,
    ts,
)

SYMBOLS = ("AAAEUR", "BBBEUR", "CCCEUR", "DDDEUR", "EEEEUR", "FFFEUR")
DATA_START = "2023-09-01T00:00:00"
DATA_DAYS = 400
FIRST_MONTH = ts("2024-01-01T00:00:00")


def random_walk(generator: random.Random, days: int, start: Decimal) -> tuple[str, ...]:
    """A jagged, exact-decimal price path. Never touches zero."""
    closes: list[str] = []
    price = start
    for _ in range(days):
        move = Decimal(generator.randint(-900, 1000)) / Decimal(10_000)
        price = max(price * (Decimal(1) + move), Decimal("0.0001"))
        closes.append(str(price.quantize(Decimal("0.00000001"))))
    return tuple(closes)


def build_case(
    seed: int,
    *,
    stop_after: dict[str, int] | None = None,
    fill_mix: FillMix = ALL_MAKER,
    funding_bps_per_day: str = "0",
) -> tuple[BacktestEngine, dict[InstrumentKey, Instrument]]:
    """One generated scenario: prices, costs and a universe, all from ``seed``."""
    generator = random.Random(seed)
    instruments = {item.key: item for item in (instrument(symbol) for symbol in SYMBOLS)}
    repository = InMemoryBarRepository()
    cut = stop_after or {}
    for key, item in instruments.items():
        repository.add(
            daily_bars(
                item,
                first_day=DATA_START,
                closes=random_walk(
                    generator,
                    cut.get(key.symbol, DATA_DAYS),
                    Decimal(generator.randint(1, 5000)),
                ),
            )
        )
    model = ItemisedCostModel(
        schedule=FeeSchedule(
            maker_bps=Decimal(generator.randint(0, 90)),
            taker_bps=Decimal(generator.randint(0, 150)),
            tier="generated",
            source="generated",
        ),
        fill_mix=fill_mix,
        spread=assumption("spread", Decimal(generator.randint(0, 80))),
        slippage=assumption("slippage", Decimal(generator.randint(0, 40))),
        liquidity=FixedBand(),
        funding_bps_per_day=Decimal(funding_bps_per_day),
    )
    engine = BacktestEngine(
        repository=repository,
        clock=SimulatedClock(current=ts("2000-01-01T00:00:00")),
        timeframe=Timeframe.D1,
        instruments=instruments,
        universe=StaticUniverse(members={}, default=tuple(sorted(instruments))),
        cost_model=model,
        fx=no_fx(),
        routing=CurrencyRouting(
            account_currency="EUR",
            quote_by_symbol={key.symbol: "EUR" for key in instruments},
        ),
        haircut=haircut(),
        series_end=StaticSeriesEnd(
            reasons={InstrumentKey(VENUE, symbol): SeriesEnd.DELISTED for symbol in (cut or {})}
        ),
        initial_equity=Notional(Decimal(1500)),
        account_currency="EUR",
    )
    return engine, instruments


def plan() -> WalkForwardPlan:
    """Six months, two held back for fitting, two out-of-sample folds."""
    return anchored_plan(first_month=FIRST_MONTH, total_months=8, in_sample_months=2, fold_count=3)


@pytest.mark.parametrize("seed", list(range(40)))
def test_gross_minus_every_cost_line_equals_net_exactly(seed: int) -> None:
    """The identity, over forty generated scenarios."""
    engine, _ = build_case(seed)
    summary = engine.run(
        plan(),
        ParameterFreeStrategy(RandomSelection.for_seed(seed=seed, positions=3)),
        f"seed-{seed}",
    )
    ledger = summary.ledger
    residual = ledger.net_pnl.amount - (ledger.gross_pnl.amount - ledger.costs.total.amount)
    assert residual == 0, (
        f"seed {seed}: gross {ledger.gross_pnl.amount} minus costs "
        f"{ledger.costs.total.amount} is not net {ledger.net_pnl.amount}; "
        f"residual {residual}"
    )
    assert ledger.reconciles()


@pytest.mark.parametrize("seed", [101, 202, 303])
def test_the_identity_survives_delistings(seed: int) -> None:
    """The haircut is a line in the same identity, not an adjustment outside it."""
    engine, _ = build_case(seed, stop_after={"CCCEUR": 210, "EEEEUR": 170})
    summary = engine.run(
        plan(),
        ParameterFreeStrategy(EqualWeightPassive()),
        f"delist-{seed}",
    )
    assert summary.ledger.reconciles()
    assert summary.ledger.costs.delisting.amount > 0


@pytest.mark.parametrize("mix", [ALL_MAKER, HALF_AND_HALF, ALL_TAKER])
def test_the_identity_holds_at_every_reported_fill_mix(mix: FillMix) -> None:
    """The three mixes every headline result is reported at."""
    engine, _ = build_case(7, fill_mix=mix)
    summary = engine.run(
        plan(),
        ParameterFreeStrategy(EqualWeightPassive()),
        f"mix-{mix.maker_fraction}",
    )
    assert summary.ledger.reconciles()


def test_a_richer_fill_mix_costs_more_and_the_ordering_is_strict() -> None:
    """More taker fills means more fees, with everything else identical."""
    fees = []
    for mix in (ALL_MAKER, HALF_AND_HALF, ALL_TAKER):
        engine, _ = build_case(7, fill_mix=mix)
        summary = engine.run(
            plan(),
            ParameterFreeStrategy(EqualWeightPassive()),
            "mix",
        )
        fees.append(summary.ledger.costs.fees.amount)
    assert fees[0] < fees[1] < fees[2]


# -- funding accrues with time, not with trades -------------------------------


def test_funding_scales_with_days_held_and_not_with_trade_count() -> None:
    """Phase 0's specific requirement, asserted on the cost model directly."""
    model = cost_model()
    charged = ItemisedCostModel(
        schedule=model.schedule,
        fill_mix=model.fill_mix,
        spread=model.spread,
        slippage=model.slippage,
        liquidity=model.liquidity,
        funding_bps_per_day=Decimal(2),
    )
    notional = Notional(Decimal(1000))
    assert charged.funding_over(notional, 0).amount == 0
    one_day = charged.funding_over(notional, 1).amount
    assert charged.funding_over(notional, 30).amount == one_day * 30
    per_trade = charged.cost_of(InstrumentKey(VENUE, "AAAEUR"), notional, ts("2024-01-01T00:00:00"))
    assert per_trade.total.amount == (
        model.schedule.maker_bps
        + model.spread.bps(LiquidityBand.DEEP)
        + model.slippage.bps(LiquidityBand.DEEP)
    ) * Decimal(1000) / Decimal(10_000)


def test_funding_is_charged_over_the_holding_period_in_a_full_run() -> None:
    """A run with a funding rate must charge more than one with none, on time."""
    without, _ = build_case(9, funding_bps_per_day="0")
    with_funding, _ = build_case(9, funding_bps_per_day="1")
    free = without.run(
        plan(),
        ParameterFreeStrategy(EqualWeightPassive()),
        "free",
    )
    charged = with_funding.run(
        plan(),
        ParameterFreeStrategy(EqualWeightPassive()),
        "charged",
    )
    assert free.ledger.costs.funding.amount == 0
    assert charged.ledger.costs.funding.amount > 0
    assert charged.ledger.reconciles()

    for outcome in charged.ledger.rebalances:
        days = (outcome.closed_at.value - outcome.opened_at.value).days
        assert days >= 28
        assert outcome.costs.funding.amount > 0


def test_negative_days_held_is_refused() -> None:
    """A holding period that runs backwards is a defect, not a rebate."""
    with pytest.raises(CostModelError):
        cost_model().funding_over(Notional(Decimal(100)), -1)


# -- the cost model's own refusals --------------------------------------------


def test_an_assumption_without_a_stated_basis_cannot_be_constructed() -> None:
    """A configured number travelling without its justification becomes a fact."""
    with pytest.raises(CostModelError):
        CostAssumption(
            by_band=dict.fromkeys(LiquidityBand, Decimal(10)), basis="   ", label="spread"
        )


def test_every_band_must_be_priced_including_the_unknown_one() -> None:
    """An instrument we know nothing about is not an average instrument."""
    with pytest.raises(CostModelError):
        CostAssumption(
            by_band={LiquidityBand.DEEP: Decimal(10)},
            basis="incomplete on purpose",
            label="spread",
        )


def test_a_fill_mix_is_always_an_assumption_and_says_so() -> None:
    """It can never be constructed as a measurement."""
    for mix in (ALL_MAKER, HALF_AND_HALF, ALL_TAKER):
        assert mix.is_assumption is True
        metadata = mix.as_metadata()
        assert "ASSUMPTION" in metadata["fill_mix_kind"]
        assert "paper" in metadata["fill_mix_kind"]


def test_a_fill_mix_outside_zero_to_one_is_refused() -> None:
    """A fraction of fills, not a rate."""
    with pytest.raises(CostModelError):
        FillMix(maker_fraction=Decimal("1.5"), label="impossible")


def test_the_cost_breakdown_always_carries_every_line() -> None:
    """Reporting net alone is not acceptable output, so the lines always exist."""
    lines = CostLines(fees=money(Decimal(1)))
    payload = lines.as_json()
    assert set(payload) == {
        "fees",
        "spread",
        "slippage",
        "funding",
        "fx_conversion",
        "delisting",
        "total",
    }


def summed(lines: Sequence[CostLines]) -> Decimal:
    """Helper kept for readability in future additions to this module."""
    total = CostLines()
    for item in lines:
        total = total + item
    return total.total.amount
