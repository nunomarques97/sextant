"""Running SEXTANT-005: the pre-registered variants, their nulls, their regimes.

Everything this module does is fixed in `docs/PRE-REGISTRATION-005.md` and
`config/spike-005.yaml`, both committed before the dataset existed. The runner
refuses to start if the code has drifted from the registered numbers, because a
pre-registration that can silently disagree with the code it pre-registers is
not a pre-registration.

What runs, and in what order
-----------------------------

1. the world: one load of the archive, one resolution of the point-in-time
   universe per quote policy, reused by every run that follows;
2. the four benchmarks that do not depend on a variant - cash, the single asset,
   the equal-weight universe, and the fully-invested random null;
3. the sixteen variants, each wrapped in a recorder so its selection and its
   exposure path are kept;
4. per variant, the three constructs that decompose it: selection with the
   timing removed, the exposure-matched random null, and the deterministic
   timing null.

Steps 3 and 4 are separated because the nulls in 4 are *matched to the path the
variant actually took*, so they cannot be built until it has taken it.

Cost cells
-----------

Four, and every variant runs in all of them. One is this venue's own published
schedule, where maker and taker are equal and the fill mix cannot move the fee
line. Three are the other venue's schedule at three fill mixes, carried as a
sensitivity so that a result which exists only because this venue is cheap is
visible as one. Neither is a claim about executability anywhere.

Nothing is tuned. There is nothing here to tune.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

from sextant.adapters.clocks import SimulatedClock
from sextant.adapters.exchanges.binance.capabilities import VENUE
from sextant.adapters.exchanges.binance.costs import (
    DEEP_BAND_FLOOR,
    MID_BAND_FLOOR,
    SLIPPAGE_ASSUMPTION,
    SPOT_FEES,
    SPREAD_ASSUMPTION,
    fx_leg,
)
from sextant.adapters.exchanges.binance.listing_calendar import BinanceListingCalendar
from sextant.adapters.storage.bars import ParquetBarStore
from sextant.adapters.storage.panel import PanelRow, load_daily_panel
from sextant.adapters.storage.repository import ParquetBarRepository
from sextant.app.binance_archive import (
    CALENDAR_NAME,
    INDEX_NAME,
    PLAN_NAME,
    STORE_ROOT,
    AcquisitionPlan,
    MonthIndex,
    split_symbol,
)
from sextant.app.panels import fx_rates_from, reference_closes_from
from sextant.app.spike import EXCLUDED_BASES
from sextant.domain.instrument import Instrument, InstrumentKey
from sextant.domain.listing import MembershipState
from sextant.domain.money import Notional, Price, Quantity
from sextant.domain.time import Timeframe, Timestamp
from sextant.engine.backtest.allocation import Allocator, ParameterFreeStrategy
from sextant.engine.backtest.baselines import (
    EqualWeightPassive,
    RandomSelection,
    SingleAssetBuyAndHold,
)
from sextant.engine.backtest.engine import BacktestEngine, RunSummary
from sextant.engine.backtest.ledger import Ledger, monthly_returns
from sextant.engine.backtest.window import WalkForwardPlan, Window, anchored_plan, monthly_instants
from sextant.engine.execution.costs import (
    FeeSchedule,
    FillMix,
    ItemisedCostModel,
    LiquidityBand,
    MedianTurnoverBands,
)
from sextant.engine.execution.fx import (
    CurrencyRouting,
    FxPolicy,
    FxRates,
)
from sextant.engine.execution.markout import DelistingHaircut, SeriesEnd
from sextant.engine.regime.segmentation import RegimeInputs, segment
from sextant.engine.statistics.boundary import returns_as_series
from sextant.engine.statistics.metrics import PerformanceStatistics, summarise
from sextant.engine.universe.policy import UniversePolicy, executable_from
from sextant.engine.universe.rules import (
    AccountParameters,
    ExcludedAssetClassRule,
    ListingAgeRule,
    LotSizeFeasibilityRule,
    MedianQuoteVolumeRule,
    MinNotionalFeasibilityRule,
    QuoteCurrencyRule,
    SourcedMembershipRule,
)
from sextant.engine.universe.statistics import DailyObservation, InstrumentHistory

ENGINE_VERSION = "sextant-005"
CONFIG_PATH = Path("config") / "spike-005.yaml"
RESEARCH_ROOT = Path("research")
RESULTS_PATH = RESEARCH_ROOT / "spike-005.json"

#: The account, from part 1 section 3.
ACCOUNT_CURRENCY = "EUR"
ACCOUNT_EQUITY = Notional(Decimal(1500))
MAX_POSITIONS = 10
MIN_NOTIONAL_FRACTION = Decimal("0.25")
LOT_ROUNDING_FRACTION = Decimal("0.01")

#: Universe thresholds, from part 1 section 4. Unchanged from Phase 0.
MIN_LISTING_AGE_DAYS = 180
MIN_MEDIAN_QUOTE_VOLUME = Notional(Decimal(250_000))

#: The walk-forward shapes, from part 1 section 5.
IN_SAMPLE_MONTHS = 12
FOLD_COUNT = 4

#: Part 1 section 6.
HAIRCUT_FRACTION = Decimal("0.20")
FUNDING_BPS_PER_DAY = Decimal(0)

#: Part 1 section 8: the longest lookback is 360 days, and the view must reach
#: past it far enough to find a close on the far side of a weekend or a gap.
ENGINE_LOOKBACK_DAYS = 420

#: Part 1 section 6, the FX leg.
FX_SYMBOL = "EURUSDT"
FOREIGN_CURRENCY = "USDT"

#: Part 1 section 7, the regime reference series.
REGIME_SYMBOL = "BTCUSDT"

#: Part 1 section 9.
SEEDS_EXPOSURE_MATCHED = 500
SEEDS_FULLY_INVESTED = 2000
SEED_START = 1
SEED_REDUCTION_LADDER = (500, 250, 100)

NEWLINE = chr(10)


# ---------------------------------------------------------------------------
# The pre-registration, and the drift check it exists for
# ---------------------------------------------------------------------------


class DriftedFromPreRegistration(Exception):
    """The code and the registered specification disagree. Nothing may run."""


def _mapping(value: object, name: str) -> Mapping[str, object]:
    """One registered block, as a typed mapping, or a refusal naming the block."""
    if not isinstance(value, dict):
        raise DriftedFromPreRegistration(
            f"{name} is not a block in the registered specification; it is {type(value).__name__}."
        )
    return {str(key): item for key, item in value.items()}


def _text(value: object) -> str:
    """A registered scalar as text, so a comparison never depends on YAML typing."""
    return str(value)


def assert_no_drift(config_path: Path = CONFIG_PATH) -> Mapping[str, object]:
    """Compare every registered number against the constant the code will use.

    Deliberately explicit rather than reflective. A loop that walked the YAML
    and looked for a matching constant would pass just as happily if a key were
    renamed out of existence, which is the failure this is here to catch.
    """
    with config_path.open(encoding="utf-8") as handle:
        raw = _mapping(yaml.safe_load(handle), "the registered specification")
    account = _mapping(raw["account"], "account")
    window = _mapping(raw["window"], "window")
    costs = _mapping(raw["costs"], "costs")
    nulls = _mapping(raw["null_experiment"], "null_experiment")
    plans = _mapping(window["plans"], "window.plans")
    walk = _mapping(plans["walk_forward"], "window.plans.walk_forward")
    regimes = _mapping(costs["regimes"], "costs.regimes")
    headline = _mapping(regimes["headline"], "costs.regimes.headline")
    bands = _mapping(costs["liquidity_bands"], "costs.liquidity_bands")
    seed_counts = _mapping(nulls["seed_counts"], "null_experiment.seed_counts")
    spreads = _mapping(costs["spread_bps"], "costs.spread_bps")
    slippages = _mapping(costs["slippage_bps"], "costs.slippage_bps")
    checks: list[tuple[str, object, object]] = [
        ("account.currency", _text(account["currency"]), ACCOUNT_CURRENCY),
        ("account.equity", Decimal(_text(account["equity"])), ACCOUNT_EQUITY.amount),
        ("account.max_positions", _text(account["max_positions"]), str(MAX_POSITIONS)),
        (
            "account.min_notional_fraction",
            Decimal(_text(account["min_notional_fraction"])),
            MIN_NOTIONAL_FRACTION,
        ),
        (
            "account.lot_rounding_fraction",
            Decimal(_text(account["lot_rounding_fraction"])),
            LOT_ROUNDING_FRACTION,
        ),
        (
            "window.plans.walk_forward.in_sample_months",
            _text(walk["in_sample_months"]),
            str(IN_SAMPLE_MONTHS),
        ),
        ("window.plans.walk_forward.fold_count", _text(walk["fold_count"]), str(FOLD_COUNT)),
        (
            "costs.regimes.headline.maker_bps",
            Decimal(_text(headline["maker_bps"])),
            SPOT_FEES.maker_bps,
        ),
        (
            "costs.regimes.headline.taker_bps",
            Decimal(_text(headline["taker_bps"])),
            SPOT_FEES.taker_bps,
        ),
        (
            "costs.liquidity_bands.deep_floor",
            Decimal(_text(bands["deep_floor"])),
            DEEP_BAND_FLOOR.amount,
        ),
        (
            "costs.liquidity_bands.mid_floor",
            Decimal(_text(bands["mid_floor"])),
            MID_BAND_FLOOR.amount,
        ),
        (
            "costs.delisting_haircut_fraction",
            Decimal(_text(costs["delisting_haircut_fraction"])),
            HAIRCUT_FRACTION,
        ),
        (
            "costs.funding_bps_per_day",
            Decimal(_text(costs["funding_bps_per_day"])),
            FUNDING_BPS_PER_DAY,
        ),
        (
            "null_experiment.seed_counts.exposure_matched_per_cell",
            _text(seed_counts["exposure_matched_per_cell"]),
            str(SEEDS_EXPOSURE_MATCHED),
        ),
        (
            "null_experiment.seed_counts.fully_invested_per_cell",
            _text(seed_counts["fully_invested_per_cell"]),
            str(SEEDS_FULLY_INVESTED),
        ),
        ("null_experiment.seed_start", _text(nulls["seed_start"]), str(SEED_START)),
        (
            "null_experiment.seed_reduction_ladder",
            _text(nulls["seed_reduction_ladder"]),
            _text(list(SEED_REDUCTION_LADDER)),
        ),
    ]
    for band in ("deep", "mid", "thin", "unknown"):
        checks.append(
            (
                f"costs.spread_bps.{band}",
                Decimal(_text(spreads[band])),
                SPREAD_ASSUMPTION.by_band[_band(band)],
            )
        )
        checks.append(
            (
                f"costs.slippage_bps.{band}",
                Decimal(_text(slippages[band])),
                SLIPPAGE_ASSUMPTION.by_band[_band(band)],
            )
        )

    drifted = [
        f"{name}: registered {registered!r}, code has {in_code!r}"
        for name, registered, in_code in checks
        if registered != in_code
    ]
    if drifted:
        raise DriftedFromPreRegistration(
            "The code no longer matches the registered specification. Nothing may run "
            "until this is resolved by a new pre-registration version, never by editing "
            "the old one:" + NEWLINE + NEWLINE.join(f"  - {item}" for item in drifted)
        )
    version = _text(raw["version"])
    print(f"[spike] pre-registration {version} verified against the code")
    return raw


def _band(name: str) -> LiquidityBand:
    """The band enum one registered key names."""
    return {
        "deep": LiquidityBand.DEEP,
        "mid": LiquidityBand.MID,
        "thin": LiquidityBand.THIN,
        "unknown": LiquidityBand.UNKNOWN,
    }[name]


# ---------------------------------------------------------------------------
# The world
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PrecomputedUniverse:
    """The executable universe at every rebalance, resolved once per policy."""

    label: str
    members: Mapping[Timestamp, tuple[InstrumentKey, ...]]

    @property
    def policy_name(self) -> str:
        """Recorded in the run manifest."""
        return self.label

    def executable_at(self, at: Timestamp) -> tuple[InstrumentKey, ...]:
        """Members at ``at``, sorted. Empty where the instant was not resolved."""
        return self.members.get(at, ())


@dataclass(frozen=True, slots=True)
class CalendarSeriesEnd:
    """Why a series stopped, answered by the sourced listing calendar.

    The haircut applies to a delisting and to nothing else. A series that stops
    because the archive stops is not a delisting, and an instant inside a
    membership bracket is undetermined - neither of those takes the haircut.
    """

    calendar: BinanceListingCalendar

    def series_end_at(self, key: InstrumentKey, at: Timestamp) -> SeriesEnd:
        """Whether ``key`` had stopped trading by ``at``, allowing for ignorance."""
        state = self.calendar.membership_at(key.symbol, at)
        if state is MembershipState.LISTED:
            return SeriesEnd.STILL_LISTED
        if state is MembershipState.NOT_LISTED:
            return SeriesEnd.DELISTED
        return SeriesEnd.UNDETERMINED


@dataclass(frozen=True, slots=True)
class World:
    """Everything the engine needs that is the same across every run."""

    instruments: Mapping[InstrumentKey, Instrument]
    histories: Mapping[InstrumentKey, InstrumentHistory]
    repository: ParquetBarRepository
    calendar: BinanceListingCalendar
    plan: AcquisitionPlan
    fx_rates: FxRates
    routing: CurrencyRouting
    universes: Mapping[str, PrecomputedUniverse]
    instants: tuple[Timestamp, ...]
    regimes: tuple[RegimeInputs, ...]
    eur_closes: Mapping[Timestamp, Decimal]
    not_evaluable: Mapping[str, int]


def build_world(*, store_root: Path = STORE_ROOT) -> World:
    """Load the archive once and resolve every universe the report needs."""
    index = MonthIndex.read_json(store_root / INDEX_NAME)
    plan = AcquisitionPlan.read_json(store_root / PLAN_NAME, index)
    calendar = BinanceListingCalendar.read_json(store_root / CALENDAR_NAME)

    print("[spike] loading the daily panel")
    panel = load_daily_panel(store_root, VENUE, Timeframe.D1)
    print(f"[spike] {len(panel)} stored series")

    instruments: dict[InstrumentKey, Instrument] = {}
    histories: dict[InstrumentKey, InstrumentHistory] = {}
    for symbol in sorted(panel):
        entry = calendar.entry_for(symbol)
        parts = split_symbol(symbol)
        if entry is None or parts is None:
            continue
        listed_by = entry.spells[0].certainly_listed_by
        if listed_by is None:
            continue
        base, quote = parts
        key = InstrumentKey(VENUE, symbol)
        instruments[key] = Instrument(
            venue=VENUE,
            symbol=symbol,
            base=base,
            quote=quote,
            listed_at=listed_by,
            # No historical instrument-constraint record exists for a delisted
            # symbol on this venue, so a tick, lot and minimum recorded from
            # today's endpoint would apply to survivors and to nothing else.
            # Zero is carried for every symbol instead, which makes the two
            # feasibility rules inert here rather than selectively strict. The
            # report says so; the practical effect is nil, because this venue's
            # minimums are single-digit quote units against a 150 EUR target.
            tick_size=Price(Decimal(0)),
            lot_size=Quantity(Decimal(0)),
            min_notional=Notional(Decimal(0)),
            delisted_at=None,
            provenance=entry.provenance,
        )
        histories[key] = InstrumentHistory.of(
            key,
            (
                DailyObservation(
                    open_time=Timestamp.from_epoch_millis(row.open_time_ms),
                    close=Price(Decimal(row.close)),
                    quote_volume=Notional(Decimal(row.close) * Decimal(row.volume)),
                )
                for row in panel[symbol]
                if Decimal(row.close) > 0
            ),
        )
    print(f"[spike] {len(instruments)} candidates with a priced daily series")

    fx_rates = _fx_rates(panel)
    account = AccountParameters(
        equity_quote=ACCOUNT_EQUITY,
        max_positions=MAX_POSITIONS,
        min_notional_fraction=MIN_NOTIONAL_FRACTION,
        lot_rounding_fraction=LOT_ROUNDING_FRACTION,
    )
    span = Window(
        start=plan.first_usable_month.starts_at,
        end=plan.last_usable_month_end.starts_at,
    )
    instants = (*monthly_instants(span), span.end)
    candidates = tuple(instruments.values())

    universes: dict[str, PrecomputedUniverse] = {}
    not_evaluable: dict[str, int] = {}
    for name, quotes in (("USDT", frozenset({"USDT"})), ("EUR", frozenset({"EUR"}))):
        policy = _executable_policy(calendar, quotes, histories, account)
        members: dict[Timestamp, tuple[InstrumentKey, ...]] = {}
        undetermined = 0
        for at in instants:
            evaluation = policy.evaluate(candidates, at)
            members[at] = tuple(sorted(evaluation.members))
            undetermined += len(calendar.undetermined_at(at))
        sizes = [len(value) for value in members.values()]
        not_evaluable[name] = undetermined
        print(
            f"[spike] universe {name}: {min(sizes)} to {max(sizes)} instruments "
            f"across {len(instants)} rebalances (median {sorted(sizes)[len(sizes) // 2]})"
        )
        universes[name] = PrecomputedUniverse(label=f"executable/{name}", members=members)

    eur_closes = _reference_closes(panel, fx_rates)
    regimes = segment(eur_closes, instants)

    return World(
        instruments=instruments,
        histories=histories,
        repository=ParquetBarRepository(store=ParquetBarStore(store_root)),
        calendar=calendar,
        plan=plan,
        fx_rates=fx_rates,
        routing=CurrencyRouting(
            account_currency=ACCOUNT_CURRENCY,
            quote_by_symbol={key.symbol: item.quote for key, item in instruments.items()},
        ),
        universes=universes,
        instants=instants,
        regimes=regimes,
        eur_closes=eur_closes,
        not_evaluable=not_evaluable,
    )


def _executable_policy(
    calendar: BinanceListingCalendar,
    quotes: frozenset[str],
    histories: Mapping[InstrumentKey, InstrumentHistory],
    account: AccountParameters,
) -> UniversePolicy:
    """The seven admission rules of part 1 section 4, in the order stated.

    The spread rule is absent, not relaxed. No historical quote data exists for
    this venue either, so at every past instant it returns *not evaluable* and
    the whole policy would collapse to empty. The rule is unchanged and simply
    has no data to run on here, exactly as in SEXTANT-004.
    """
    research = UniversePolicy.of(
        "research",
        (
            QuoteCurrencyRule(allowed=quotes),
            SourcedMembershipRule(oracle=calendar),
            ListingAgeRule(minimum_days=MIN_LISTING_AGE_DAYS),
            MedianQuoteVolumeRule(minimum=MIN_MEDIAN_QUOTE_VOLUME, histories=histories),
            ExcludedAssetClassRule(excluded_bases=EXCLUDED_BASES),
        ),
    )
    return executable_from(
        research,
        (
            MinNotionalFeasibilityRule(account=account),
            LotSizeFeasibilityRule(account=account, histories=histories),
        ),
    )


def _fx_rates(panel: Mapping[str, tuple[PanelRow, ...]]) -> FxRates:
    """The currency leg, from the shared reading in `app.panels`.

    Extracted so SEXTANT-006 takes it identically. A return in EUR in this task
    and a return in EUR in that one must be the same quantity, and two copies of
    a conversion are two chances to invert one of them.
    """
    return fx_rates_from(
        panel,
        symbol=FX_SYMBOL,
        foreign_currency=FOREIGN_CURRENCY,
        account_currency=ACCOUNT_CURRENCY,
    )


def _reference_closes(
    panel: Mapping[str, tuple[PanelRow, ...]],
    rates: FxRates,
) -> Mapping[Timestamp, Decimal]:
    """The regime reference series, from the same shared reading."""
    return reference_closes_from(panel, rates, symbol=REGIME_SYMBOL)


# ---------------------------------------------------------------------------
# Cost cells
# ---------------------------------------------------------------------------

#: The other venue's published tier-1 spot schedule, carried here as a
#: sensitivity and never as a claim about executability. It is a literal rather
#: than an import because the two venue packages are peers and neither may
#: import the other; the duplication is three numbers and is checked against the
#: registered specification like everything else.
COMPARISON_FEES = FeeSchedule(
    maker_bps=Decimal(40),
    taker_bps=Decimal(80),
    tier="the other venue's tier 1, carried as an execution-reality sensitivity",
    source=(
        "The schedule SEXTANT-004 evaluated everything at: 0.40% maker, 0.80% taker. "
        "Reported for every variant here so that a result which exists only because the "
        "research venue is cheap is visible as one. It is not evidence that anything is "
        "executable at that venue; that question is separate and remains open."
    ),
)

MAKER_ONLY = FillMix(maker_fraction=Decimal(1), label="100% maker (assumed)")
HALF_AND_HALF = FillMix(maker_fraction=Decimal("0.5"), label="50/50 maker/taker (assumed)")
TAKER_ONLY = FillMix(maker_fraction=Decimal(0), label="100% taker (assumed)")


@dataclass(frozen=True, slots=True)
class CostCell:
    """One cell of the reporting grid: a fee schedule and a fill-mix assumption."""

    cell_id: str
    schedule: FeeSchedule
    fill_mix: FillMix
    is_headline: bool
    runs_nulls: bool

    @property
    def label(self) -> str:
        """What appears in a report row."""
        return f"{self.cell_id} / {self.fill_mix.label}"


def cost_cells() -> tuple[CostCell, ...]:
    """The four cells of part 1 section 6, in a fixed order.

    The venue's own schedule charges maker and taker alike, so the fill mix
    cannot move its fee line and one cell is reported rather than three. It is
    still labelled with a mix because the spread assumption is charged on every
    fill whatever the mix is, and dropping the label would suggest otherwise.
    """
    return (
        CostCell(
            cell_id="binance_vip0",
            schedule=SPOT_FEES,
            fill_mix=HALF_AND_HALF,
            is_headline=True,
            runs_nulls=True,
        ),
        CostCell(
            cell_id="kraken_reality",
            schedule=COMPARISON_FEES,
            fill_mix=MAKER_ONLY,
            is_headline=False,
            runs_nulls=False,
        ),
        CostCell(
            cell_id="kraken_reality",
            schedule=COMPARISON_FEES,
            fill_mix=HALF_AND_HALF,
            is_headline=False,
            runs_nulls=True,
        ),
        CostCell(
            cell_id="kraken_reality",
            schedule=COMPARISON_FEES,
            fill_mix=TAKER_ONLY,
            is_headline=False,
            runs_nulls=False,
        ),
    )


def build_engine(
    world: World,
    cell: CostCell,
    quote_policy: str,
    *,
    fx_policy: FxPolicy = FxPolicy.APPLIED,
) -> BacktestEngine:
    """One engine, fully configured, for one cell of the reporting grid."""
    cost_model = ItemisedCostModel(
        schedule=cell.schedule,
        fill_mix=cell.fill_mix,
        spread=SPREAD_ASSUMPTION,
        slippage=SLIPPAGE_ASSUMPTION,
        liquidity=MedianTurnoverBands(
            histories=world.histories,
            deep_floor=DEEP_BAND_FLOOR,
            mid_floor=MID_BAND_FLOOR,
        ),
        funding_bps_per_day=FUNDING_BPS_PER_DAY,
    )
    rates = world.fx_rates if fx_policy is FxPolicy.APPLIED else None
    return BacktestEngine(
        repository=world.repository,
        clock=SimulatedClock(current=Timestamp.from_epoch_millis(0)),
        timeframe=Timeframe.D1,
        instruments=world.instruments,
        universe=world.universes[quote_policy],
        cost_model=cost_model,
        fx=fx_leg(fx_policy, rates),
        routing=world.routing,
        haircut=DelistingHaircut(fraction=HAIRCUT_FRACTION),
        series_end=CalendarSeriesEnd(calendar=world.calendar),
        initial_equity=ACCOUNT_EQUITY,
        account_currency=ACCOUNT_CURRENCY,
        lookback_days=ENGINE_LOOKBACK_DAYS,
        record_decisions=False,
    )


def walk_forward_plan(world: World, *, fold_count: int = FOLD_COUNT) -> WalkForwardPlan:
    """The registered walk-forward shape over the resolved window."""
    return anchored_plan(
        first_month=world.plan.first_usable_month.starts_at,
        total_months=world.plan.usable_months,
        in_sample_months=IN_SAMPLE_MONTHS,
        fold_count=fold_count,
    )


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


def fresh_clock() -> SimulatedClock:
    """A clock that has not yet reached the beginning of anything.

    A run is one simulation and a simulation starts at its own beginning, so a
    second run over the same engine needs a clock that has not already walked to
    the end of the first. The engine's data caches are shared across runs
    because they are read-through views of the port and cannot differ between
    them; only the clock is new.
    """
    return SimulatedClock(current=Timestamp.from_epoch_millis(0))


def run_allocator(
    engine: BacktestEngine,
    allocator: Allocator,
    plan: WalkForwardPlan,
    run_id: str,
) -> RunSummary:
    """One allocator through the walk-forward engine, out-of-sample only."""
    return engine.with_clock(fresh_clock()).run(
        plan, ParameterFreeStrategy(allocator=allocator), run_id
    )


def statistics_for(ledger: Ledger) -> PerformanceStatistics | None:
    """The performance summary of one ledger, across the numeric boundary."""
    returns = monthly_returns(ledger)
    ordered = [returns[instant] for instant in sorted(returns)]
    if len(ordered) < 2:
        return None
    return summarise(returns_as_series(ordered))


def monthly_series(ledger: Ledger) -> tuple[tuple[Timestamp, Decimal], ...]:
    """Monthly net returns in instant order, for the regime and null analysis."""
    returns = monthly_returns(ledger)
    return tuple((instant, returns[instant]) for instant in sorted(returns))


def measure_run_cost(
    world: World,
    plan: WalkForwardPlan,
    *,
    samples: int = 3,
) -> float:
    """Seconds one null run costs on a WARM engine, to project the seed budget.

    The first run over a fresh engine builds its point-in-time views and costs
    tens of seconds; every run after it reads them. Timing the first would
    project a budget forty times too large and step the seed count needlessly
    down its ladder, so a warm-up run is made and discarded before the clock
    starts. Measured rather than guessed, and used only to decide whether the
    registered seed count needs to step DOWN. It can never raise one.
    """
    engine = build_engine(world, cost_cells()[0], "USDT")
    # The warm-up holds the WHOLE universe, not ten names of it. A random draw
    # touches a tenth of the cross-section, so warming with one would leave most
    # of the views unbuilt and the measurement would still be timing the cache
    # rather than the run - projecting a budget an order of magnitude too large
    # and stepping the seed count needlessly down its ladder.
    run_allocator(engine, EqualWeightPassive(), plan, "calibration-warm")
    started = time.monotonic()
    for seed in range(samples):
        run_allocator(
            engine,
            RandomSelection.for_seed(seed + 1, MAX_POSITIONS),
            plan,
            run_id=f"calibration-{seed}",
        )
    return (time.monotonic() - started) / samples


def seeds_within_budget(per_run_seconds: float, budget_seconds: float, runs_per_seed: int) -> int:
    """The registered seed count, or the first rung of the ladder that fits.

    Steps down only. A smaller seed count widens the confidence interval on a
    threshold; it cannot move the threshold in a flattering direction.
    """
    for rung in SEED_REDUCTION_LADDER:
        if per_run_seconds * rung * runs_per_seed <= budget_seconds:
            return rung
    return SEED_REDUCTION_LADDER[-1]


def write_json(payload: Mapping[str, object], path: Path) -> None:
    """Persist a result payload, sorted so two runs diff cleanly."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=1, sort_keys=True, default=str)
        handle.write("\n")


def instants_in(plan: WalkForwardPlan) -> tuple[Timestamp, ...]:
    """Every scored rebalance instant, in order."""
    span = plan.out_of_sample_span
    return (*monthly_instants(span), span.end)


def regime_by_instant(world: World) -> Mapping[Timestamp, str]:
    """The regime label at each rebalance instant, by the pre-registered cascade."""
    return {item.at: item.regime.value for item in world.regimes}


def universe_sizes(world: World, policy: str, instants: Sequence[Timestamp]) -> tuple[int, ...]:
    """How many instruments were executable at each instant."""
    universe = world.universes[policy]
    return tuple(len(universe.executable_at(at)) for at in instants)


__all__ = [
    "ACCOUNT_CURRENCY",
    "ACCOUNT_EQUITY",
    "MAX_POSITIONS",
    "SEEDS_EXPOSURE_MATCHED",
    "SEEDS_FULLY_INVESTED",
    "SEED_START",
    "CostCell",
    "EqualWeightPassive",
    "RandomSelection",
    "SingleAssetBuyAndHold",
    "World",
    "assert_no_drift",
    "build_engine",
    "build_world",
    "cost_cells",
    "instants_in",
    "measure_run_cost",
    "monthly_series",
    "regime_by_instant",
    "run_allocator",
    "seeds_within_budget",
    "statistics_for",
    "universe_sizes",
    "walk_forward_plan",
    "write_json",
]
