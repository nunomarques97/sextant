"""Calibrating the null: what no edge looks like, net of real costs.

This is the deliverable of SEXTANT-004 that matters, and it is not a strategy.
Three benchmark constructs run through the same walk-forward engine any strategy
will run through, over the same window, with the same costs:

* **equal-weight passive** - hold the whole point-in-time executable universe;
* **random selection** - eight names drawn uniformly, without replacement, at
  every rebalance, over ten thousand seeds;
* **single-asset buy and hold** - the thing anyone can do with no system.

Everything is reported three ways (EUR only, EUR+USD with the currency leg
ignored, EUR+USD with it applied) and at three fill mixes (all maker, half and
half, all taker). Nine combinations per construct, because a result whose sign
moves across any of them depends on something nobody has demonstrated.

What the numbers are for
-------------------------

The random distribution is a **rejection filter**. A strategy below its 95th
percentile is rejected for insufficient evidence, finally and without further
analysis. A strategy above it has passed one filter and has demonstrated
nothing: the threshold is multiple-comparison naive, so testing twenty
strategies gets one through by chance alone. That is what the Deflated Sharpe
Ratio exists to correct, and it is why passing here buys entry to the real
analysis rather than a verdict.

Nothing here is tuned. There is nothing to tune. The configuration was
pre-registered in ``config/benchmarks.yaml`` before any of this ran, and the
runner refuses to start if the code has drifted from it.
"""

from __future__ import annotations

import json
import platform
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path

from sextant.adapters.clocks import SimulatedClock, SystemClock
from sextant.adapters.exchanges.kraken.archive import ArchiveManifest
from sextant.adapters.exchanges.kraken.capabilities import VENUE
from sextant.adapters.exchanges.kraken.costs import (
    DEEP_BAND_FLOOR,
    FX_PAIR_FEE_BPS,
    MID_BAND_FLOOR,
    SLIPPAGE_ASSUMPTION,
    SPOT_FEES,
    SPREAD_ASSUMPTION,
    fx_leg,
)
from sextant.adapters.exchanges.kraken.listing_calendar import KrakenListingCalendar
from sextant.adapters.storage.bars import ParquetBarStore
from sextant.adapters.storage.panel import PanelRow, load_daily_panel
from sextant.adapters.storage.repository import ParquetBarRepository
from sextant.adapters.storage.trials import REGISTRY_PATH, TrialRegistry
from sextant.app import benchmark_config
from sextant.app.archive_ingest import STORE_ROOT, load_calendar, load_manifest
from sextant.app.archive_measure import policies
from sextant.app.benchmark_config import BenchmarkConfig, VariantSpec
from sextant.domain.decision import DecisionRecord
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
from sextant.engine.backtest.manifest import RunManifest
from sextant.engine.backtest.trials import dataset_fingerprint
from sextant.engine.backtest.window import WalkForwardPlan, anchored_plan
from sextant.engine.execution.costs import (
    FillMix,
    ItemisedCostModel,
    MedianTurnoverBands,
)
from sextant.engine.execution.fx import CurrencyRouting, FxPolicy, FxRates
from sextant.engine.execution.markout import DelistingHaircut, SeriesEnd
from sextant.engine.statistics.bootstrap import (
    PercentileEstimate,
    distribution_summary,
    generator_for,
    percentile_with_interval,
    quantiles,
    variance_of,
)
from sextant.engine.statistics.boundary import returns_as_series
from sextant.engine.statistics.dsr import VARIANT as DSR_VARIANT
from sextant.engine.statistics.dsr import deflated_sharpe_ratio
from sextant.engine.statistics.metrics import PerformanceStatistics, summarise
from sextant.engine.universe.rules import AccountParameters
from sextant.engine.universe.statistics import DailyObservation, InstrumentHistory
from sextant.ports.clock import Clock

ENGINE_VERSION = "sextant-004"

EQUAL_WEIGHT = "equal-weight-passive"
SINGLE_ASSET = "single-asset-buy-and-hold"
RANDOM_SELECTION = "random-selection"

RESEARCH_ROOT = Path("research")
REPORT_PATH = Path("docs") / "NULL-BASELINE.md"
RESULTS_PATH = RESEARCH_ROOT / "null-baseline.json"
DECISIONS_PATH = RESEARCH_ROOT / "decision-stream-sample.jsonl"

NEWLINE = chr(10)


# ---------------------------------------------------------------------------
# The world the engine runs in
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PrecomputedUniverse:
    """The executable universe at every rebalance, resolved once.

    Resolving the point-in-time rules is expensive and the answer does not
    depend on the strategy, so it is computed once per quote policy and reused
    across every seed. Nothing about the resolution changes: the same rules run
    at the same instants over the same candidates.
    """

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
    because the archive stops is not a delisting, and an instant falling inside
    a membership bracket is undetermined - neither of those takes the haircut.
    """

    calendar: KrakenListingCalendar

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
    calendar: KrakenListingCalendar
    manifest: ArchiveManifest
    fx_rates: FxRates
    routing: CurrencyRouting
    universes: Mapping[str, PrecomputedUniverse]


def build_world(
    *,
    config: BenchmarkConfig,
    store_root: Path = STORE_ROOT,
    calendar: KrakenListingCalendar | None = None,
    manifest: ArchiveManifest | None = None,
) -> World:
    """Load the archive once and resolve every universe the report needs."""
    resolved_calendar = calendar if calendar is not None else load_calendar(store_root)
    resolved_manifest = manifest if manifest is not None else load_manifest(store_root)
    store = ParquetBarStore(store_root)

    print("[null] loading the daily panel")
    panel = load_daily_panel(store_root, VENUE, Timeframe.D1)
    metadata = _load_venue_constraints()

    instruments: dict[InstrumentKey, Instrument] = {}
    histories: dict[InstrumentKey, InstrumentHistory] = {}
    for symbol in sorted(resolved_calendar.entries):
        entry = resolved_calendar.entries[symbol]
        listed_by = entry.spells[0].certainly_listed_by
        rows = panel.get(symbol)
        if listed_by is None or rows is None:
            continue
        constraints = metadata.get(symbol, {})
        key = InstrumentKey(VENUE, symbol)
        instruments[key] = Instrument(
            venue=VENUE,
            symbol=symbol,
            base=constraints.get("base", _split_base(symbol)),
            quote=constraints.get("quote", _split_quote(symbol)),
            listed_at=listed_by,
            tick_size=Price(Decimal(constraints.get("tick_size", "0"))),
            lot_size=Quantity(Decimal(constraints.get("lot_size", "0"))),
            min_notional=Notional(Decimal(constraints.get("min_notional", "0"))),
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
                for row in rows
            ),
        )
    print(f"[null] {len(instruments)} candidates with a priced daily series")

    account = AccountParameters(
        equity_quote=config.account_equity,
        max_positions=config.max_positions,
        min_notional_fraction=config.min_notional_fraction,
        lot_rounding_fraction=config.lot_rounding_fraction,
    )
    candidates = tuple(instruments.values())
    instants = _all_instants(config)
    universes: dict[str, PrecomputedUniverse] = {}
    for name in sorted(config.quote_policies):
        _, _, executable = policies(
            resolved_calendar, config.quote_policies[name], histories, account
        )
        members = {
            at: tuple(sorted(executable.evaluate(candidates, at).members)) for at in instants
        }
        sizes = [len(value) for value in members.values()]
        print(
            f"[null] universe {name}: {min(sizes)} to {max(sizes)} instruments "
            f"across {len(instants)} rebalances"
        )
        universes[name] = PrecomputedUniverse(label=f"executable/{name}", members=members)

    return World(
        instruments=instruments,
        histories=histories,
        repository=ParquetBarRepository(store=store),
        calendar=resolved_calendar,
        manifest=resolved_manifest,
        fx_rates=_fx_rates(panel, config),
        routing=CurrencyRouting(
            account_currency=config.account_currency,
            quote_by_symbol={key.symbol: item.quote for key, item in instruments.items()},
        ),
        universes=universes,
    )


def _all_instants(config: BenchmarkConfig) -> tuple[Timestamp, ...]:
    """Every rebalance instant either plan visits, including the terminal one."""
    from sextant.engine.backtest.window import Window, add_months, monthly_instants

    span = Window(
        start=config.first_usable_month,
        end=add_months(config.first_usable_month, config.usable_months),
    )
    return (*monthly_instants(span), span.end)


def _fx_rates(
    panel: Mapping[str, tuple[PanelRow, ...]],
    config: BenchmarkConfig,
) -> FxRates:
    """Account-currency units per foreign unit, from the venue's own FX pair.

    The pair is quoted as USD per EUR, so the rate the engine wants - EUR per
    USD - is its reciprocal. Each observation is dated by the bar's *close*
    time, never its open time: a rate is knowable when the bar that carries it
    has finished forming.
    """
    rows = panel.get(config.fx_pair_symbol)
    if not rows:
        raise ValueError(
            f"No {config.fx_pair_symbol} series in the store. The currency leg cannot be "
            "priced, and running the EUR+USD policy without it would be the counterfactual "
            "wearing the label of the measurement."
        )
    observations: dict[Timestamp, Decimal] = {}
    for row in rows:
        close = Decimal(row.close)
        if close <= 0:
            continue
        opened = Timestamp.from_epoch_millis(row.open_time_ms)
        observations[opened.plus(Timeframe.D1.duration)] = Decimal(1) / close
    return FxRates.of(
        foreign_currency="USD",
        account_currency=config.account_currency,
        observations=observations,
        source=(
            f"{config.fx_pair_symbol} daily closes from the venue's quarterly archive, "
            "inverted to give account-currency units per foreign unit, dated by bar close"
        ),
    )


def _split_base(symbol: str) -> str:
    """The base leg of a venue-native symbol, by suffix."""
    for quote in ("EUR", "USD", "USDT", "USDC", "GBP", "XBT", "ETH", "AUD", "CHF", "JPY"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[: -len(quote)]
    return symbol


def _split_quote(symbol: str) -> str:
    """The quote leg of a venue-native symbol, by suffix."""
    for quote in ("USDT", "USDC", "EUR", "USD", "GBP", "XBT", "ETH", "AUD", "CHF", "JPY"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return quote
    return ""


def _load_venue_constraints() -> Mapping[str, Mapping[str, str]]:
    """Current tick, lot and minimum-notional values from the SEXTANT-002 fetch."""
    path = Path("data") / "spike" / "kraken" / "pair_metadata.json"
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    pairs = raw.get("pairs", raw)
    if not isinstance(pairs, dict):
        return {}
    return {
        str(symbol): {str(field): str(value) for field, value in entry.items()}
        for symbol, entry in pairs.items()
        if isinstance(entry, dict)
    }


# ---------------------------------------------------------------------------
# Building an engine for one reporting variant at one fill mix
# ---------------------------------------------------------------------------


def build_engine(
    world: World,
    variant: VariantSpec,
    mix: FillMix,
    config: BenchmarkConfig,
    *,
    record_decisions: bool = False,
) -> BacktestEngine:
    """One engine, fully configured, for one cell of the reporting grid."""
    cost_model = ItemisedCostModel(
        schedule=SPOT_FEES,
        fill_mix=mix,
        spread=SPREAD_ASSUMPTION,
        slippage=SLIPPAGE_ASSUMPTION,
        liquidity=MedianTurnoverBands(
            histories=world.histories,
            deep_floor=DEEP_BAND_FLOOR,
            mid_floor=MID_BAND_FLOOR,
        ),
        funding_bps_per_day=config.funding_bps_per_day,
    )
    rates = world.fx_rates if variant.fx_policy is FxPolicy.APPLIED else None
    return BacktestEngine(
        repository=world.repository,
        clock=SimulatedClock(current=Timestamp.from_epoch_millis(0)),
        timeframe=Timeframe.D1,
        instruments=world.instruments,
        universe=world.universes[variant.quote_policy],
        cost_model=cost_model,
        fx=fx_leg(variant.fx_policy, rates),
        routing=world.routing,
        haircut=DelistingHaircut(fraction=config.haircut_fraction),
        series_end=CalendarSeriesEnd(calendar=world.calendar),
        initial_equity=config.account_equity,
        account_currency=config.account_currency,
        record_decisions=record_decisions,
    )


def build_plan(config: BenchmarkConfig, plan_name: str) -> WalkForwardPlan:
    """The registered walk-forward shape, by name."""
    spec = config.plans[plan_name]
    return anchored_plan(
        first_month=config.first_usable_month,
        total_months=config.usable_months,
        in_sample_months=spec.in_sample_months,
        fold_count=spec.fold_count,
    )


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConstructResult:
    """One deterministic construct, in one cell of the reporting grid."""

    construct: str
    plan: str
    variant: str
    fill_mix: str
    is_counterfactual: bool
    ledger: Ledger
    statistics: PerformanceStatistics | None
    universe_sizes: Mapping[str, int]

    def as_json(self) -> dict[str, object]:
        """Serialisable form: the cost breakdown, never a net figure alone."""
        return {
            "construct": self.construct,
            "plan": self.plan,
            "variant": self.variant,
            "fill_mix": self.fill_mix,
            "is_counterfactual": self.is_counterfactual,
            "denomination": self.ledger.account_currency,
            **self.ledger.as_json(),
            "statistics": _statistics_json(self.statistics),
            "universe_sizes": dict(sorted(self.universe_sizes.items())),
        }


@dataclass(frozen=True, slots=True)
class NullDistribution:
    """The random-selection distribution in one cell of the reporting grid."""

    variant: str
    fill_mix: str
    is_counterfactual: bool
    seed_start: int
    seed_count: int
    terminal_returns: tuple[float, ...]
    sharpes: tuple[float, ...]
    sortinos: tuple[float, ...]
    drawdowns: tuple[float, ...]
    percentile_estimates: Mapping[float, PercentileEstimate]
    seconds: float

    def as_json(self) -> dict[str, object]:
        """Serialisable form, including the whole distribution's shape."""
        low, mean, deviation, high = distribution_summary(self.sharpes)
        return {
            "variant": self.variant,
            "fill_mix": self.fill_mix,
            "is_counterfactual": self.is_counterfactual,
            "denomination": "EUR",
            "seed_start": self.seed_start,
            "seed_count": self.seed_count,
            "sharpe": {
                "minimum": low,
                "mean": mean,
                "standard_deviation": deviation,
                "maximum": high,
                "percentiles": {
                    str(percentile): {
                        "value": estimate.value,
                        "ci_low": estimate.low,
                        "ci_high": estimate.high,
                        "ci_width": estimate.width,
                        "confidence": estimate.confidence,
                        "resamples": estimate.resamples,
                    }
                    for percentile, estimate in sorted(self.percentile_estimates.items())
                },
            },
            "terminal_return": quantiles(self.terminal_returns),
            "sortino": quantiles(self.sortinos),
            "max_drawdown": quantiles(self.drawdowns),
            "seconds": self.seconds,
        }


def _statistics_json(statistics: PerformanceStatistics | None) -> dict[str, object] | None:
    """A summary, or an explicit null when the series was too short to summarise."""
    if statistics is None:
        return None
    return {
        "observations": statistics.observations,
        "sharpe_annualised": statistics.sharpe_annualised,
        "sharpe_standard_error": statistics.sharpe_standard_error,
        "sharpe_per_period": statistics.sharpe_per_period,
        "sortino_annualised": statistics.sortino_annualised,
        "skewness": statistics.skewness,
        "kurtosis_raw": statistics.kurtosis,
    }


def statistics_for(ledger: Ledger) -> PerformanceStatistics | None:
    """The performance summary of one ledger, across the numeric boundary."""
    returns = monthly_returns(ledger)
    ordered = [returns[instant] for instant in sorted(returns)]
    if len(ordered) < 2:
        return None
    return summarise(returns_as_series(ordered))


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


def run_construct(
    engine: BacktestEngine,
    config: BenchmarkConfig,
    variant: VariantSpec,
    mix: FillMix,
    plan: WalkForwardPlan,
    construct: str,
    plan_name: str,
    *,
    record_decisions: bool = False,
) -> tuple[ConstructResult, RunSummary]:
    """Run one deterministic construct in one cell of the grid."""
    engine = engine.recording(record_decisions=record_decisions).with_clock(_fresh_clock())
    allocator: Allocator = (
        EqualWeightPassive()
        if construct == EQUAL_WEIGHT
        else SingleAssetBuyAndHold(key=InstrumentKey(VENUE, config.single_asset_symbol))
    )
    summary = engine.run(
        plan,
        ParameterFreeStrategy(allocator),
        f"{construct}:{variant.name}:{mix.maker_fraction}",
    )
    return (
        ConstructResult(
            construct=construct,
            plan=plan_name,
            variant=variant.name,
            fill_mix=mix.label,
            is_counterfactual=variant.is_counterfactual,
            ledger=summary.ledger,
            statistics=statistics_for(summary.ledger),
            universe_sizes=summary.universe_sizes,
        ),
        summary,
    )


def _fresh_clock() -> SimulatedClock:
    """A simulated clock at the beginning of time.

    One per run. A simulated clock refuses to move backwards, and a second run
    over the same window would ask exactly that of a clock that has already
    finished the first.
    """
    return SimulatedClock(current=Timestamp.from_epoch_millis(0))


def run_null(
    engine: BacktestEngine,
    config: BenchmarkConfig,
    variant: VariantSpec,
    mix: FillMix,
    plan: WalkForwardPlan,
    *,
    seed_count: int | None = None,
) -> NullDistribution:
    """The random-selection distribution in one cell of the grid.

    One engine, reused across every seed. Reuse is safe and is the reason this
    is minutes rather than hours: the engine holds no per-run state, and its
    caches are read-through views of the port at fixed instants.
    """
    prepared = engine.recording(record_decisions=False)
    count = seed_count if seed_count is not None else config.seed_count
    seeds = range(config.seed_start, config.seed_start + count)

    started = time.monotonic()
    terminal: list[float] = []
    sharpes: list[float] = []
    sortinos: list[float] = []
    drawdowns: list[float] = []
    for index, seed in enumerate(seeds, start=1):
        allocator: Allocator = RandomSelection.for_seed(seed=seed, positions=config.null_positions)
        summary = prepared.with_clock(_fresh_clock()).run(
            plan,
            ParameterFreeStrategy(allocator),
            f"null:{variant.name}:{mix.maker_fraction}:{seed}",
        )
        statistics = statistics_for(summary.ledger)
        if statistics is None:
            continue
        terminal.append(float(summary.ledger.terminal_return))
        sharpes.append(statistics.sharpe_annualised)
        sortinos.append(statistics.sortino_annualised)
        drawdowns.append(float(summary.ledger.max_drawdown))
        if index % 500 == 0:
            elapsed = time.monotonic() - started
            print(f"[null] {variant.name} {mix.label}: {index}/{count} seeds ({elapsed:.0f}s)")

    generator = generator_for(config.bootstrap_generator_seed)
    estimates = {
        percentile: percentile_with_interval(
            sharpes,
            percentile,
            generator=generator,
            resamples=config.bootstrap_resamples,
            confidence=config.bootstrap_confidence,
        )
        for percentile in config.percentiles
    }
    return NullDistribution(
        variant=variant.name,
        fill_mix=mix.label,
        is_counterfactual=variant.is_counterfactual,
        seed_start=config.seed_start,
        seed_count=len(sharpes),
        terminal_returns=tuple(terminal),
        sharpes=tuple(sharpes),
        sortinos=tuple(sortinos),
        drawdowns=tuple(drawdowns),
        percentile_estimates=estimates,
        seconds=time.monotonic() - started,
    )


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def code_version() -> str:
    """The current git revision, or an explicit statement that there is none."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607 - git is on PATH by contract
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown: git could not be executed"
    if result.returncode != 0:
        return "unknown: not a git working tree"
    return result.stdout.strip()


def library_versions() -> Mapping[str, str]:
    """The arithmetic a result was produced under.

    Read from installed package metadata rather than by importing the packages.
    The wiring layer is not allowed to import polars at all, and it is not
    allowed to import numpy in the same module as a monetary type - both are
    enforced, by ``.importlinter`` and by ``tests/unit/test_numeric_boundary.py``
    respectively. A version string is not worth breaking either rule for.
    """
    versions: dict[str, str] = {"python": platform.python_version()}
    for package in ("numpy", "scipy", "polars", "pyarrow", "duckdb"):
        try:
            versions[package] = package_version(package)
        except PackageNotFoundError:  # pragma: no cover - present by construction
            versions[package] = "not installed"
    return versions


def build_manifest(
    engine: BacktestEngine,
    world: World,
    config: BenchmarkConfig,
    variant: VariantSpec,
    plan: WalkForwardPlan,
    *,
    run_id: str,
    seed: int | None,
) -> RunManifest:
    """The manifest every run emits alongside its decision stream."""
    return RunManifest(
        run_id=run_id,
        engine_version=ENGINE_VERSION,
        code_version=code_version(),
        dataset_checksums={
            quarter.label: world.manifest.files[quarter.label].sha256
            for quarter in world.manifest.quarters
        },
        plan=plan,
        universe_policy=world.universes[variant.quote_policy].policy_name,
        quote_policy=variant.quote_policy,
        account_currency=config.account_currency,
        initial_equity=str(config.account_equity.amount),
        positions=config.null_positions,
        seed=seed,
        cost_assumptions=dict(engine.cost_model.as_metadata()),
        fx_assumptions=dict(engine.fx.as_metadata()),
        delisting_assumptions=dict(engine.haircut.as_metadata()),
        library_versions=library_versions(),
        notes=[
            f"benchmark configuration {config.version}, registered {config.registered_on}",
            (
                "the maker fee is an ASSUMPTION about execution, not a measured fee; "
                "the real fill ratio is an obligation of the paper phase"
            ),
            (
                "spread and slippage are ASSUMPTIONS; no historical quote data exists "
                "for this venue and none is recoverable"
            ),
        ],
    )


# ---------------------------------------------------------------------------
# The whole experiment
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BaselineReport:
    """Everything one full run of the experiment produced."""

    config_version: str
    code_version: str
    plans: Mapping[str, WalkForwardPlan]
    constructs: tuple[ConstructResult, ...]
    nulls: tuple[tuple[str, NullDistribution], ...]
    """Paired with the plan they were computed under, because the gate is
    calibrated on one plan and the whole usable window is reported alongside."""
    deflated: Mapping[str, object]
    trial_count: int
    trial_count_including_nulls: int
    trial_audit: tuple[str, ...]
    universe_sizes: Mapping[str, Mapping[str, int]]
    manifests: Mapping[str, object]
    seconds: float

    def as_json(self) -> dict[str, object]:
        """Serialisable form. Every cost line, every assumption, every seed count."""
        return {
            "benchmark_config_version": self.config_version,
            "code_version": self.code_version,
            "denomination": "EUR",
            "plans": {name: plan.as_json() for name, plan in sorted(self.plans.items())},
            "constructs": [item.as_json() for item in self.constructs],
            "null_distributions": [
                {"plan": plan_name, **distribution.as_json()}
                for plan_name, distribution in self.nulls
            ],
            "deflated_sharpe": dict(self.deflated),
            "trial_count_excluding_null_constructs": self.trial_count,
            "trial_count_including_null_constructs": self.trial_count_including_nulls,
            "trial_audit": list(self.trial_audit),
            "universe_sizes": {
                name: dict(sorted(sizes.items())) for name, sizes in self.universe_sizes.items()
            },
            "manifests": dict(self.manifests),
            "seconds": self.seconds,
        }


def run_all(
    *,
    config: BenchmarkConfig | None = None,
    store_root: Path = STORE_ROOT,
    seed_count: int | None = None,
    plan_names: Sequence[str] = ("walk_forward", "full_window"),
    report_path: Path = REPORT_PATH,
    results_path: Path = RESULTS_PATH,
    decisions_path: Path = DECISIONS_PATH,
    registry_path: Path = REGISTRY_PATH,
    world: World | None = None,
    clock: Clock | None = None,
) -> BaselineReport:
    """Run every construct in every cell of the reporting grid, and write it up.

    Every path is a parameter. ``registry_path`` in particular: a test that
    could append to the committed trial registry would corrupt the one number
    the Deflated Sharpe Ratio rests on.
    """
    started = time.monotonic()
    settings = config if config is not None else benchmark_config.load()
    benchmark_config.assert_costs_match_the_registration(
        settings,
        schedule=SPOT_FEES,
        spread=SPREAD_ASSUMPTION,
        slippage=SLIPPAGE_ASSUMPTION,
        deep_floor=DEEP_BAND_FLOOR,
        mid_floor=MID_BAND_FLOOR,
        conversion_bps=FX_PAIR_FEE_BPS,
    )
    resolved = world if world is not None else build_world(config=settings, store_root=store_root)
    plans = {name: build_plan(settings, name) for name in plan_names}

    constructs: list[ConstructResult] = []
    nulls: list[tuple[str, NullDistribution]] = []
    universe_sizes: dict[str, Mapping[str, int]] = {}
    manifests: dict[str, object] = {}
    decisions: list[dict[str, str]] = []

    engines = {
        (variant.name, mix.label): build_engine(resolved, variant, mix, settings)
        for variant in settings.variants
        for mix in settings.fill_mixes
    }

    for plan_name in plan_names:
        plan = plans[plan_name]
        for variant in settings.variants:
            for mix in settings.fill_mixes:
                cell = f"{plan_name}/{variant.name}/{mix.label}"
                engine = engines[(variant.name, mix.label)]
                # ``fill_mixes`` rebuilds its tuple on every access, so identity
                # against ``fill_mixes[0]`` is never true. Compare the label.
                keep_decisions = (
                    plan_name == plan_names[0] and mix.label == settings.fill_mixes[0].label
                )
                for construct in (EQUAL_WEIGHT, SINGLE_ASSET):
                    result, summary = run_construct(
                        engine,
                        settings,
                        variant,
                        mix,
                        plan,
                        construct,
                        plan_name,
                        record_decisions=keep_decisions,
                    )
                    constructs.append(result)
                    if construct == EQUAL_WEIGHT:
                        universe_sizes[f"{plan_name}/{variant.quote_policy}"] = (
                            summary.universe_sizes
                        )
                        if keep_decisions:
                            decisions.extend(
                                decision_as_json(record) for record in summary.decisions[:200]
                            )

                print(f"[null] {cell}: {seed_count or settings.seed_count} random seeds")
                distribution = run_null(engine, settings, variant, mix, plan, seed_count=seed_count)
                nulls.append((plan_name, distribution))
                manifests[cell] = build_manifest(
                    engine, resolved, settings, variant, plan, run_id=cell, seed=None
                ).as_json()

    registry = TrialRegistry(path=registry_path)
    checksum = dataset_fingerprint(
        {
            quarter.label: resolved.manifest.files[quarter.label].sha256
            for quarter in resolved.manifest.quarters
        }
    )
    register_trials(
        registry,
        settings,
        plans,
        checksum,
        seed_count or settings.seed_count,
        recorded_at=(clock or SystemClock()).now(),
    )

    report = BaselineReport(
        config_version=settings.version,
        code_version=code_version(),
        plans=plans,
        constructs=tuple(constructs),
        nulls=tuple(nulls),
        deflated=deflate(constructs, nulls, registry.count()),
        trial_count=registry.count(),
        trial_count_including_nulls=registry.count(include_null_constructs=True),
        trial_audit=tuple(registry.audit()),
        universe_sizes=universe_sizes,
        manifests=manifests,
        seconds=time.monotonic() - started,
    )

    results_path.parent.mkdir(parents=True, exist_ok=True)
    with results_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(report.as_json(), handle, indent=1, sort_keys=True, default=str)
        handle.write("\n")
    if decisions:
        decisions_path.parent.mkdir(parents=True, exist_ok=True)
        with decisions_path.open("w", encoding="utf-8", newline="\n") as handle:
            for entry in decisions:
                handle.write(json.dumps(entry, sort_keys=True))
                handle.write("\n")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render(report, settings), encoding="utf-8", newline="\n")
    print(f"[null] wrote {report_path} and {results_path} in {report.seconds:.0f}s")
    return report


def decision_as_json(record: DecisionRecord) -> dict[str, str]:
    """One audit record, flattened to strings for the sample stream."""
    return {
        "timestamp": record.timestamp.isoformat(),
        "run_id": record.run_id,
        "symbol": record.symbol,
        "venue": record.venue.name,
        "strategy": record.strategy,
        "signal": record.signal,
        "entry": str(record.entry.amount) if record.entry is not None else "",
        "size": str(record.size.amount) if record.size is not None else "",
        "expected_costs_bps": (
            str(record.expected_costs_bps) if record.expected_costs_bps is not None else ""
        ),
        "risk_verdict": record.risk_verdict.value,
        "risk_reason": record.risk_reason,
        "outcome": record.outcome or "",
    }


def register_trials(
    registry: TrialRegistry,
    config: BenchmarkConfig,
    plans: Mapping[str, WalkForwardPlan],
    checksum: str,
    seeds: int,
    *,
    recorded_at: Timestamp,
) -> None:
    """Record every construct evaluated, once per plan and reporting variant.

    The benchmark constructs are evaluations and they are recorded, flagged as
    null constructs: they are not searches for edge. The report states the trial
    count both ways so that the choice is visible rather than convenient.
    """
    version = code_version()
    for plan_name in sorted(plans):
        plan = plans[plan_name]
        window = (
            f"{plan.out_of_sample_span.start.isoformat()[:10]}/"
            f"{plan.out_of_sample_span.end.isoformat()[:10]}"
        )
        for variant in config.variants:
            entries = (
                (EQUAL_WEIGHT, f"plan={plan_name};fx={variant.fx_policy.value}", 1),
                (SINGLE_ASSET, f"plan={plan_name};symbol={config.single_asset_symbol}", 1),
                (
                    RANDOM_SELECTION,
                    (
                        f"plan={plan_name};positions={config.null_positions};"
                        f"fx={variant.fx_policy.value};"
                        f"seeds={config.seed_start}..{config.seed_start + seeds - 1}"
                    ),
                    seeds,
                ),
            )
            for construct, parameters, count in entries:
                registry.record(
                    recorded_at=recorded_at,
                    code_version=version,
                    engine_version=ENGINE_VERSION,
                    strategy_id=construct,
                    parameter_set_id=parameters,
                    dataset_checksum=checksum,
                    evaluation_window=window,
                    quote_policy=variant.quote_policy,
                    is_null_construct=True,
                    seeds=count,
                    note=(
                        "SEXTANT-004 benchmark construct. Not a search for edge; recorded "
                        "so that the count is complete from the start."
                    ),
                )


def deflate(
    constructs: Sequence[ConstructResult],
    nulls: Sequence[tuple[str, NullDistribution]],
    trials: int,
) -> Mapping[str, object]:
    """Deflate each construct's Sharpe against the null it sits beside.

    ``V`` - the variance of the trial Sharpe ratios - is *measured* from the
    random distribution in the same cell rather than assumed. This is the one
    place the project has a real distribution of trial Sharpes to use, and using
    an assumed one where a measured one exists would be the sort of shortcut this
    whole task is against.

    With no strategy yet written, the trial count excluding null constructs is
    zero and the DSR is reported at ``K = 1`` - the no-selection case. The report
    says so rather than quietly using the null-construct count.
    """
    by_cell = {
        (plan_name, distribution.variant, distribution.fill_mix): distribution
        for plan_name, distribution in nulls
    }
    deflated: dict[str, object] = {}
    for construct in constructs:
        distribution = by_cell.get((construct.plan, construct.variant, construct.fill_mix))
        if distribution is None or construct.statistics is None:
            continue
        if len(distribution.sharpes) < 2:
            continue
        root = Decimal(12).sqrt()
        per_period = [value / float(root) for value in distribution.sharpes]
        variance = variance_of(per_period)
        result = deflated_sharpe_ratio(
            construct.statistics, trials=max(trials, 1), trial_sharpe_variance=variance
        )
        key = f"{construct.plan}|{construct.construct}|{construct.variant}|{construct.fill_mix}"
        deflated[key] = {
            "formula_variant": DSR_VARIANT,
            "trials_used": max(trials, 1),
            "trial_sharpe_variance_per_period": variance,
            "trial_sharpe_variance_source": (
                "measured from the random-selection distribution in the same cell"
            ),
            "expected_maximum_sharpe_per_period": result.expected_maximum_sharpe_per_period,
            "probabilistic_sharpe_ratio": result.probabilistic_sharpe_ratio,
            "deflated_sharpe_ratio": result.deflated_sharpe_ratio,
            "autocorrelation_corrected": result.autocorrelation_corrected,
        }
    return deflated


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

DENOMINATION_NOTE = (
    "**Every figure below is in EUR.** That includes the single-asset benchmark, "
    "the EUR+USD policy and every percentile of the null distribution. A "
    "USD-quoted position is converted using exactly the FX methodology stated in "
    "the assumptions section and never a different rate. No table here mixes "
    "currencies. The `eur_usd_fx_ignored` rows are a **counterfactual**, not a "
    "currency: they are the wide universe with the currency leg deleted, which is "
    "what a backtester produces when nobody has thought about FX. They are "
    "intermediate figures and are labelled as such wherever they appear."
)

ASSUMPTION_NOTE = (
    "**The maker fee is an assumption, not a measurement.** The engine assumes "
    "post-only limit orders and therefore maker fees. That is a hypothesis about "
    "execution, not a property of it. Every result is therefore reported at three "
    "fill mixes, and where a conclusion moves across them it depends on something "
    "nobody has demonstrated. Measuring the real maker/taker fill ratio is an "
    "obligation of the paper-trading phase, recorded in docs/LIVE-GATES.md.\n\n"
    "**Spread and slippage are assumptions too.** No historical quote data exists "
    "for this venue and none is recoverable, so these are configured, deliberately "
    "pessimistic, liquidity-banded values. They are never presented as measured, "
    "and they are reported on their own lines so their contribution can be read "
    "off rather than argued about."
)


def _money(value: Decimal, places: int = 2) -> str:
    """A monetary amount, rounded for reading only."""
    return f"{value.quantize(Decimal(10) ** -places):,}"


def _ratio(value: float, places: int = 3) -> str:
    """A dimensionless statistic, rounded for reading only."""
    return f"{value:.{places}f}"


def _percent(value: Decimal) -> str:
    """A fraction, as a percentage, for reading only."""
    return f"{value * 100:.2f}%"


def render_constructs(constructs: Sequence[ConstructResult], plan_name: str) -> str:
    """The cost breakdown for every deterministic construct, in one table."""
    header = (
        "| construct | variant | fill mix | gross PnL | fees | spread | slippage | "
        "funding | FX conv. | delisting | net PnL | net return | max DD | Sharpe | "
        "SE(Sharpe) | Sortino |\n"
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n"
    )
    rows: list[str] = []
    for item in sorted(
        (item for item in constructs if item.plan == plan_name),
        key=lambda item: (item.construct, item.variant, item.fill_mix),
    ):
        costs = item.ledger.costs
        statistics = item.statistics
        label = item.variant + (" *(counterfactual)*" if item.is_counterfactual else "")
        rows.append(
            f"| {item.construct} | {label} | {item.fill_mix} | "
            f"{_money(item.ledger.gross_pnl.amount)} | {_money(costs.fees.amount)} | "
            f"{_money(costs.spread.amount)} | {_money(costs.slippage.amount)} | "
            f"{_money(costs.funding.amount)} | {_money(costs.fx_conversion.amount)} | "
            f"{_money(costs.delisting.amount)} | {_money(item.ledger.net_pnl.amount)} | "
            f"{_percent(item.ledger.terminal_return)} | "
            f"{_percent(item.ledger.max_drawdown)} | "
            f"{_ratio(statistics.sharpe_annualised) if statistics else 'n/a'} | "
            f"{_ratio(statistics.sharpe_standard_error) if statistics else 'n/a'} | "
            f"{_ratio(statistics.sortino_annualised) if statistics else 'n/a'} |"
        )
    return header + NEWLINE.join(rows) + NEWLINE


def render_null(nulls: Sequence[tuple[str, NullDistribution]], plan_name: str) -> str:
    """The null distribution's percentiles, each with its bootstrap interval."""
    header = (
        "| variant | fill mix | seeds | mean | sd | p50 | p90 | p95 | 95% CI on p95 | "
        "p99 | max |\n|---|---|---:|---:|---:|---:|---:|---:|---|---:|---:|\n"
    )
    rows: list[str] = []
    for name, distribution in nulls:
        if name != plan_name:
            continue
        low, mean, deviation, high = distribution_summary(distribution.sharpes)
        estimates = distribution.percentile_estimates
        ninety_five = estimates[95.0]
        label = distribution.variant + (
            " *(counterfactual)*" if distribution.is_counterfactual else ""
        )
        rows.append(
            f"| {label} | {distribution.fill_mix} | {distribution.seed_count} | "
            f"{_ratio(mean)} | {_ratio(deviation)} | {_ratio(estimates[50.0].value)} | "
            f"{_ratio(estimates[90.0].value)} | {_ratio(ninety_five.value)} | "
            f"[{_ratio(ninety_five.low)}, {_ratio(ninety_five.high)}] | "
            f"{_ratio(estimates[99.0].value)} | {_ratio(high)} |"
        )
        del low
    return header + NEWLINE.join(rows) + NEWLINE


def render_null_intervals(nulls: Sequence[tuple[str, NullDistribution]], plan_name: str) -> str:
    """Every reported percentile with its interval, since the gate rests on them."""
    header = (
        "| variant | fill mix | percentile | Sharpe | 95% CI | CI width |\n"
        "|---|---|---:|---:|---|---:|\n"
    )
    rows: list[str] = []
    for name, distribution in nulls:
        if name != plan_name:
            continue
        for percentile, estimate in sorted(distribution.percentile_estimates.items()):
            rows.append(
                f"| {distribution.variant} | {distribution.fill_mix} | {percentile:.0f} | "
                f"{_ratio(estimate.value)} | "
                f"[{_ratio(estimate.low)}, {_ratio(estimate.high)}] | "
                f"{_ratio(estimate.width)} |"
            )
    return header + NEWLINE.join(rows) + NEWLINE


def render_null_shape(nulls: Sequence[tuple[str, NullDistribution]], plan_name: str) -> str:
    """Terminal return, Sortino and drawdown across the whole distribution."""
    header = (
        "| variant | fill mix | metric | p05 | p25 | p50 | p75 | p95 | p99 | mean |\n"
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|\n"
    )
    rows: list[str] = []
    for name, distribution in nulls:
        if name != plan_name:
            continue
        for metric, values in (
            ("terminal net return", distribution.terminal_returns),
            ("Sortino", distribution.sortinos),
            ("max drawdown", distribution.drawdowns),
        ):
            shape = quantiles(values)
            rows.append(
                f"| {distribution.variant} | {distribution.fill_mix} | {metric} | "
                + " | ".join(
                    _ratio(shape[key]) for key in ("p05", "p25", "p50", "p75", "p95", "p99")
                )
                + f" | {_ratio(shape['mean'])} |"
            )
    return header + NEWLINE.join(rows) + NEWLINE


def render_fx_gap(constructs: Sequence[ConstructResult], plan_name: str) -> str:
    """The price of the breadth: the gap between ignoring FX and pricing it."""
    header = (
        "| construct | fill mix | net return, FX ignored *(counterfactual)* | "
        "net return, FX applied | gap | FX conversion charged |\n"
        "|---|---|---:|---:|---:|---:|\n"
    )
    by_key = {
        (item.construct, item.fill_mix, item.variant): item
        for item in constructs
        if item.plan == plan_name
    }
    rows: list[str] = []
    for (construct, mix, variant), item in sorted(by_key.items()):
        if variant != "eur_usd_fx_ignored":
            continue
        applied = by_key.get((construct, mix, "eur_usd_fx_applied"))
        if applied is None:
            continue
        gap = applied.ledger.terminal_return - item.ledger.terminal_return
        rows.append(
            f"| {construct} | {mix} | {_percent(item.ledger.terminal_return)} | "
            f"{_percent(applied.ledger.terminal_return)} | {_percent(gap)} | "
            f"{_money(applied.ledger.costs.fx_conversion.amount)} |"
        )
    return header + NEWLINE.join(rows) + NEWLINE


def render_fx_gap_null(nulls: Sequence[tuple[str, NullDistribution]], plan_name: str) -> str:
    """The same gap, measured on the null's median rather than on one construct."""
    header = (
        "| fill mix | median Sharpe, FX ignored *(counterfactual)* | "
        "median Sharpe, FX applied | gap | median net return, ignored | "
        "median net return, applied | gap |\n|---|---:|---:|---:|---:|---:|---:|\n"
    )
    by_key = {
        (distribution.variant, distribution.fill_mix): distribution
        for name, distribution in nulls
        if name == plan_name
    }
    rows: list[str] = []
    mixes = sorted({mix for _, mix in by_key})
    for mix in mixes:
        ignored = by_key.get(("eur_usd_fx_ignored", mix))
        applied = by_key.get(("eur_usd_fx_applied", mix))
        if ignored is None or applied is None:
            continue
        ignored_sharpe = ignored.percentile_estimates[50.0].value
        applied_sharpe = applied.percentile_estimates[50.0].value
        ignored_return = quantiles(ignored.terminal_returns)["p50"]
        applied_return = quantiles(applied.terminal_returns)["p50"]
        rows.append(
            f"| {mix} | {_ratio(ignored_sharpe)} | {_ratio(applied_sharpe)} | "
            f"{_ratio(applied_sharpe - ignored_sharpe)} | "
            f"{_ratio(ignored_return * 100, 2)}% | {_ratio(applied_return * 100, 2)}% | "
            f"{_ratio((applied_return - ignored_return) * 100, 2)}pp |"
        )
    return header + NEWLINE.join(rows) + NEWLINE


def render_deflated(deflated: Mapping[str, object]) -> str:
    """The Deflated Sharpe Ratio for each construct, with its inputs."""
    header = (
        "| cell | trials | V(trial Sharpe) | E[max Sharpe] | PSR | DSR |\n"
        "|---|---:|---:|---:|---:|---:|\n"
    )
    rows: list[str] = []
    for key in sorted(deflated):
        entry = deflated[key]
        if not isinstance(entry, dict):
            continue
        rows.append(
            f"| {key} | {entry['trials_used']} | "
            f"{float(entry['trial_sharpe_variance_per_period']):.5f} | "
            f"{float(entry['expected_maximum_sharpe_per_period']):.4f} | "
            f"{float(entry['probabilistic_sharpe_ratio']):.4f} | "
            f"{float(entry['deflated_sharpe_ratio']):.4f} |"
        )
    return header + NEWLINE.join(rows) + NEWLINE


def render_universe_sizes(sizes: Mapping[str, Mapping[str, int]]) -> str:
    """How many instruments the cross-section actually offered, per rebalance."""
    header = "| plan / quote policy | rebalances | minimum | median | maximum |\n"
    header += "|---|---:|---:|---:|---:|\n"
    rows: list[str] = []
    for name in sorted(sizes):
        values = sorted(sizes[name].values())
        if not values:
            continue
        middle = values[len(values) // 2]
        rows.append(f"| {name} | {len(values)} | {values[0]} | {middle} | {values[-1]} |")
    return header + NEWLINE.join(rows) + NEWLINE


def _window_end(report: BaselineReport) -> str:
    """The last instant any plan in this report covers."""
    return max(plan.out_of_sample_span.end.isoformat() for plan in report.plans.values())


def render(report: BaselineReport, config: BenchmarkConfig) -> str:
    """The whole write-up, as markdown."""
    walk_forward = report.plans.get("walk_forward")
    sections: list[str] = [
        "# The calibrated null: what no edge looks like, net of costs",
        "",
        "<!-- generated by `uv run sextant benchmark null`; do not hand-edit -->",
        "",
        f"Benchmark configuration `{config.version}`, registered {config.registered_on}, "
        f"committed before any of these numbers existed. Code version "
        f"`{report.code_version[:12]}`.",
        "",
        DENOMINATION_NOTE,
        "",
        ASSUMPTION_NOTE,
        "",
        "## What these numbers are, and what they are not",
        "",
        "The random-selection distribution is a **rejection filter**. A strategy "
        "scoring below its 95th percentile is rejected for insufficient evidence; "
        "that rejection is final and needs no further analysis. A strategy scoring "
        "above it has passed one filter and **has not demonstrated edge**. The "
        "threshold is multiple-comparison naive: test twenty strategies against it "
        "and roughly one clears it by chance alone. Everything in "
        "docs/LIVE-GATES.md still applies in full.",
        "",
        "## The window",
        "",
        f"Usable window: {config.first_usable_month.isoformat()[:10]} to "
        f"{_window_end(report):10.10}, "
        f"{config.usable_months} months. Nine months are lost to cold start and the "
        "April-to-September 2026 tail has no delisting record and is excluded.",
        "",
    ]
    if walk_forward is not None:
        sections.extend(
            [
                f"Walk-forward plan: {walk_forward.fold_count} non-overlapping "
                f"out-of-sample windows covering "
                f"{walk_forward.out_of_sample_span.start.isoformat()[:10]} to "
                f"{walk_forward.out_of_sample_span.end.isoformat()[:10]}. Each fold "
                "fits on everything before its own window and is scored on nothing "
                "else. There is no code path that scores a strategy on its fitting "
                "data.",
                "",
            ]
        )
    sections.extend(
        [
            "### How much cross-section there was to choose from",
            "",
            render_universe_sizes(report.universe_sizes),
            "",
        ]
    )

    for plan_name in sorted(report.plans):
        sections.extend(
            [
                f"## Plan `{plan_name}`",
                "",
                "### The deterministic constructs, with every cost line",
                "",
                render_constructs(report.constructs, plan_name),
                "",
                "### The random-selection null",
                "",
                f"Seeds: {config.seed_start} to "
                f"{config.seed_start + config.seed_count - 1} inclusive, sampled "
                "uniformly **without replacement** within each rebalance. Without "
                "replacement because the null has to mirror the constraint a real "
                "strategy operates under: eight positions means eight distinct "
                "names, and no strategy this project will build can concentrate two "
                "slots in one instrument.",
                "",
                render_null(report.nulls, plan_name),
                "",
                "#### Every reported percentile, with its bootstrap interval",
                "",
                f"Intervals are {int(config.bootstrap_confidence * 100)}% two-sided, "
                f"from {config.bootstrap_resamples:,} resamples, generator seeded at "
                f"{config.bootstrap_generator_seed}. They describe **sampling** "
                "error - how far the threshold would move if the experiment were "
                "re-run with different seeds. They say nothing about whether the "
                "cost model is right.",
                "",
                render_null_intervals(report.nulls, plan_name),
                "",
                "#### The distribution's shape, not only its mean",
                "",
                render_null_shape(report.nulls, plan_name),
                "",
                "### The FX question, measured",
                "",
                render_fx_gap(report.constructs, plan_name),
                "",
                render_fx_gap_null(report.nulls, plan_name),
                "",
            ]
        )

    sections.extend(
        [
            "## Deflated Sharpe Ratio",
            "",
            f"Formula: `{DSR_VARIANT}`. The trial variance is **measured** from the "
            "random-selection distribution in the same cell rather than assumed. "
            f"Trials excluding null constructs: {report.trial_count}. Trials "
            f"including them: {report.trial_count_including_nulls}. With no strategy "
            "yet written the first count is zero, so the DSR below is reported at "
            "K = 1, which is the no-selection case and is the PSR against a zero "
            "benchmark. It is not a gate on anything yet; it exists so that the "
            "count is true from the start.",
            "",
            render_deflated(report.deflated),
            "",
            "## The trial registry",
            "",
            "Append-only and hash-chained at `research/trial-registry.jsonl`. Every "
            "evaluation ever run against this dataset is in it, and a later run "
            "cannot silently rewrite, reset or reduce it.",
            "",
            "```",
            *report.trial_audit,
            "```",
            "",
            f"Total wall time: {report.seconds:.0f} seconds.",
            "",
        ]
    )
    return NEWLINE.join(sections)
