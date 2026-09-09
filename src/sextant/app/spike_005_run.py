"""Executing the SEXTANT-005 grid and reducing it to one JSON result file.

Order matters and is enforced by the shape of this module rather than by a
comment. The variants run first and are recorded; the two matched nulls are
built from what the recorder saw, so they cannot exist before the variant has
taken its path. Nothing here chooses a parameter, a threshold or a seed count:
all of those arrive from :mod:`sextant.app.spike_005`, which reads them from a
specification committed before the dataset existed.

What is kept, and what is thrown away
--------------------------------------

Twenty thousand null runs produce twenty thousand ledgers, and holding them
would cost more memory than the machine has. Only the four figures a null
distribution is read for survive each seed - Sharpe, terminal net return,
Sortino and maximum drawdown - and the ledger is discarded as soon as they are
taken. The deterministic runs keep everything, because their cost lines are
reported in full and a net figure without its cost breakdown is exactly what
invariant 8 exists to prevent.

Attribution of a month to a regime
-----------------------------------

A holding period's return is keyed by the instant it *closed*. Its regime is the
label the cascade assigned at the instant it *opened*, because that is the
market state that was knowable when the position was taken. Keying it to the
close would let a month be assigned to a regime that only became visible after
the decision, which is the same look-ahead the cascade exists to avoid.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from sextant.adapters.clocks import SystemClock
from sextant.adapters.exchanges.binance.capabilities import VENUE
from sextant.adapters.storage.trials import REGISTRY_PATH, TrialRegistry
from sextant.app.binance_archive import FETCH_NAME, STORE_ROOT, FetchReport
from sextant.app.spike_005 import (
    ACCOUNT_CURRENCY,
    ENGINE_VERSION,
    MAX_POSITIONS,
    RESULTS_PATH,
    SEED_START,
    SEEDS_EXPOSURE_MATCHED,
    SEEDS_FULLY_INVESTED,
    CostCell,
    World,
    assert_no_drift,
    build_engine,
    build_world,
    cost_cells,
    measure_run_cost,
    monthly_series,
    regime_by_instant,
    run_allocator,
    seeds_within_budget,
    statistics_for,
    universe_sizes,
    walk_forward_plan,
    write_json,
)
from sextant.domain.instrument import InstrumentKey
from sextant.domain.time import Timestamp
from sextant.engine.backtest.allocation import Allocator
from sextant.engine.backtest.baselines import (
    EqualWeightPassive,
    RandomSelection,
    SingleAssetBuyAndHold,
)
from sextant.engine.backtest.engine import BacktestEngine
from sextant.engine.backtest.ledger import Ledger
from sextant.engine.backtest.trials import dataset_fingerprint
from sextant.engine.backtest.window import WalkForwardPlan
from sextant.engine.regime.segmentation import Regime, conclusive, month_counts
from sextant.engine.statistics.bootstrap import (
    PercentileEstimate,
    distribution_summary,
    generator_for,
    percentile_with_interval,
    quantiles,
    variance_of,
)
from sextant.engine.statistics.boundary import returns_panel
from sextant.engine.statistics.dsr import deflated_sharpe_ratio
from sextant.engine.statistics.independence import (
    MINIMUM_EFFECTIVE_OBSERVATIONS,
    MINIMUM_OVERLAP_DAYS,
    breadth_of,
    independence_of,
    mean_pairwise_correlation,
    measurable_pairs,
)
from sextant.engine.strategies.momentum import (
    ExposureMatched,
    FullyInvested,
    Recording,
    ScaledUniverse,
    positions_of,
    registered_variants,
)

#: The regime reference and the equal-weight baseline are compared against every
#: variant, so their labels are fixed here rather than spelled twice.
EQUAL_WEIGHT = "equal-weight-passive"
BTC_HOLD = "btc-buy-and-hold"
EUR_CASH = "eur-cash"
FULLY_INVESTED_NULL = "random-selection-fully-invested"

BTC_SYMBOL = "BTCUSDT"

#: Percentiles every null distribution is read at. Part 1 section 9.
PERCENTILES = (50.0, 90.0, 95.0, 99.0)

#: Wall clock the seeded nulls are allowed. If the measured per-run cost
#: projects beyond it, the exposure-matched seed count steps DOWN its registered
#: ladder and the reduction is reported with the projection that caused it.
DEFAULT_NULL_BUDGET_SECONDS = 7200.0

#: The bootstrap generator seed, from part 1 section 10. Explicit and fixed,
#: so every interval in the report is reproducible from the file alone.
BOOTSTRAP_SEED = 987654321

NEWLINE = chr(10)


# ---------------------------------------------------------------------------
# What one run is reduced to
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Deterministic:
    """One deterministic run, kept whole: cost lines and all."""

    construct: str
    kind: str
    policy: str
    cell_id: str
    fill_mix: str
    ledger: Ledger
    monthly: tuple[tuple[Timestamp, Decimal], ...]

    @property
    def sharpe(self) -> float | None:
        """Annualised net Sharpe in the account's currency, or None if too short."""
        statistics = statistics_for(self.ledger)
        return None if statistics is None else statistics.sharpe_annualised

    def as_json(self) -> dict[str, object]:
        """Serialisable form: never a net figure without its cost breakdown."""
        statistics = statistics_for(self.ledger)
        return {
            "construct": self.construct,
            "kind": self.kind,
            "quote_policy": self.policy,
            "cell_id": self.cell_id,
            "fill_mix": self.fill_mix,
            "denomination": ACCOUNT_CURRENCY,
            **self.ledger.as_json(),
            "sharpe_annualised": None if statistics is None else statistics.sharpe_annualised,
            "sharpe_standard_error": (
                None if statistics is None else statistics.sharpe_standard_error
            ),
            "sortino_annualised": None if statistics is None else statistics.sortino_annualised,
            "observations": None if statistics is None else statistics.observations,
            "monthly_returns": {at.isoformat(): str(value) for at, value in self.monthly},
        }


@dataclass(frozen=True, slots=True)
class NullDistribution:
    """A seeded null, reduced to the four figures it is ever read for."""

    construct: str
    policy: str
    cell_id: str
    fill_mix: str
    seed_start: int
    seed_count: int
    sharpes: tuple[float, ...]
    terminal_returns: tuple[float, ...]
    sortinos: tuple[float, ...]
    drawdowns: tuple[float, ...]
    seconds: float

    def estimate(self, value: float) -> PercentileEstimate:
        """One percentile of the Sharpe distribution, with its interval."""
        return percentile_with_interval(
            self.sharpes, value, generator=generator_for(BOOTSTRAP_SEED)
        )

    def percentile(self, value: float) -> float:
        """One percentile of the Sharpe distribution."""
        return self.estimate(value).value

    def as_json(self) -> dict[str, object]:
        """Serialisable form, including every percentile's bootstrap interval."""
        low, mean, deviation, high = distribution_summary(self.sharpes)
        estimates = {str(percentile): self.estimate(percentile) for percentile in PERCENTILES}
        return {
            "construct": self.construct,
            "quote_policy": self.policy,
            "cell_id": self.cell_id,
            "fill_mix": self.fill_mix,
            "denomination": ACCOUNT_CURRENCY,
            "seed_start": self.seed_start,
            "seed_count": self.seed_count,
            "seconds": self.seconds,
            "sharpe": {
                "minimum": low,
                "mean": mean,
                "standard_deviation": deviation,
                "maximum": high,
                "variance": variance_of(self.sharpes),
                "percentiles": {
                    key: {
                        "value": estimate.value,
                        "ci_low": estimate.low,
                        "ci_high": estimate.high,
                        "ci_width": estimate.width,
                    }
                    for key, estimate in estimates.items()
                },
            },
            "terminal_return": quantiles(self.terminal_returns),
            "sortino": quantiles(self.sortinos),
            "max_drawdown": quantiles(self.drawdowns),
        }


@dataclass(slots=True)
class Results:
    """Everything the report needs, accumulated as the grid runs."""

    deterministic: list[Deterministic] = field(default_factory=list)
    nulls: list[NullDistribution] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Running one thing
# ---------------------------------------------------------------------------


class RegisterTrial(Protocol):
    """How a run announces itself to the append-only registry.

    A protocol rather than a closure type because the registry is the single
    number the Deflated Sharpe rests on, and every caller that produces a result
    must go through the same door.
    """

    def __call__(
        self, strategy_id: str, parameters: str, *, null: bool, seeds: int, note: str
    ) -> None:
        """Append one trial."""
        ...


def _deterministic(
    engine: BacktestEngine,
    plan: WalkForwardPlan,
    allocator: Allocator,
    policy: str,
    cell: CostCell,
    construct: str,
    kind: str,
) -> Deterministic:
    """One deterministic run, kept whole: cost lines and all."""
    run_id = f"{construct}-{policy}-{cell.cell_id}-{cell.fill_mix.maker_fraction}"
    summary = run_allocator(engine, allocator, plan, run_id)
    return Deterministic(
        construct=construct,
        kind=kind,
        policy=policy,
        cell_id=cell.cell_id,
        fill_mix=cell.fill_mix.label,
        ledger=summary.ledger,
        monthly=monthly_series(summary.ledger),
    )


def _seeded(
    engine: BacktestEngine,
    plan: WalkForwardPlan,
    build: Callable[[int], Allocator],
    *,
    policy: str,
    cell: CostCell,
    construct: str,
    seeds: int,
) -> NullDistribution:
    """A seeded null, reduced per seed and never held whole.

    ``build`` returns a *fresh* allocator for each seed. These allocators are
    stateful by design - one seed describes one whole path through the window -
    so reusing an instance would make the second run depend on the first.
    """
    started = time.monotonic()
    sharpes: list[float] = []
    terminals: list[float] = []
    sortinos: list[float] = []
    drawdowns: list[float] = []
    for offset in range(seeds):
        seed = SEED_START + offset
        summary = run_allocator(engine, build(seed), plan, f"{construct}-{policy}-{seed}")
        ledger = summary.ledger
        statistics = statistics_for(ledger)
        sharpes.append(0.0 if statistics is None else statistics.sharpe_annualised)
        sortinos.append(0.0 if statistics is None else statistics.sortino_annualised)
        terminals.append(float(ledger.terminal_return))
        drawdowns.append(float(ledger.max_drawdown))
    return NullDistribution(
        construct=construct,
        policy=policy,
        cell_id=cell.cell_id,
        fill_mix=cell.fill_mix.label,
        seed_start=SEED_START,
        seed_count=seeds,
        sharpes=tuple(sharpes),
        terminal_returns=tuple(terminals),
        sortinos=tuple(sortinos),
        drawdowns=tuple(drawdowns),
        seconds=time.monotonic() - started,
    )


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def regime_returns(
    monthly: Sequence[tuple[Timestamp, Decimal]],
    labels: Mapping[Timestamp, str],
    opening: Mapping[Timestamp, Timestamp],
) -> Mapping[str, tuple[Decimal, int]]:
    """Compounded net return and month count per regime.

    A month is attributed to the regime that was knowable when its position was
    opened, never to the one visible when it closed.
    """
    totals: dict[str, tuple[Decimal, int]] = {}
    for closed_at, value in monthly:
        opened_at = opening.get(closed_at)
        label = labels.get(opened_at) if opened_at is not None else None
        if label is None:
            label = Regime.NOT_EVALUABLE.value
        running, count = totals.get(label, (Decimal(1), 0))
        totals[label] = (running * (Decimal(1) + value), count + 1)
    return {label: (value - Decimal(1), count) for label, (value, count) in totals.items()}


def opening_instants(monthly: Sequence[tuple[Timestamp, Decimal]]) -> Mapping[Timestamp, Timestamp]:
    """For each closing instant, the instant the holding period opened."""
    instants = [at for at, _ in monthly]
    return {instants[index]: instants[index - 1] for index in range(1, len(instants))}


def _no_breadth() -> Mapping[str, float | int | bool]:
    """Breadth when the variant held nothing at all. Labelled, never zero-by-accident."""
    return dict(breadth_of(0.0, 0.0, measured_pairs=0, names=0).as_json())


def breadth_for(
    world: World,
    recording: Recording,
    window_start: Timestamp,
    window_end: Timestamp,
) -> Mapping[str, float | int | bool]:
    """Effective breadth of the names a variant held, over the scored window.

    Daily returns rather than monthly, because breadth is a statement about how
    much of the same thing was being held and twelve monthly observations would
    leave far too few to estimate a correlation from.

    Every name the variant held anywhere in the window is included, and a day on
    which a name did not trade is carried as *missing* rather than as a zero
    return. That is the whole difficulty: a variant holding a hundred names
    across four years holds almost none of them throughout, so a panel
    restricted to days where every name has a price would be empty. Each pair is
    therefore correlated over its own overlap, and a pair without enough shared
    days is left out rather than counted as uncorrelated.
    """
    held: set[InstrumentKey] = set()
    counts: list[int] = []
    for at, keys in recording.chosen.items():
        if window_start <= at <= window_end:
            held.update(keys)
            counts.append(len(keys))
    if not held or not counts:
        return _no_breadth()

    names = sorted(held, key=lambda item: item.symbol)
    by_day: dict[Timestamp, dict[InstrumentKey, Decimal]] = {}
    for key in names:
        history = world.histories.get(key)
        if history is None:
            continue
        previous: Decimal | None = None
        for observation in history.observations:
            closed_at = observation.open_time
            if not (window_start <= closed_at <= window_end):
                previous = None
                continue
            current = observation.close.amount
            if previous is not None and previous > 0 and current > 0:
                by_day.setdefault(closed_at, {})[key] = current / previous - Decimal(1)
            previous = current

    median = float(sorted(counts)[len(counts) // 2])
    days = sorted(by_day)
    if len(days) < MINIMUM_OVERLAP_DAYS or len(names) < 2:
        return dict(breadth_of(median, 0.0, measured_pairs=0, names=len(names)).as_json())
    panel = returns_panel([[by_day[day].get(key) for key in names] for day in days])
    correlation = mean_pairwise_correlation(panel)
    pairs = measurable_pairs(panel)
    return dict(breadth_of(median, correlation, measured_pairs=pairs, names=len(names)).as_json())


def analyse(
    world: World,
    results: Results,
    trials_including_nulls: int,
    trials_excluding_nulls: int,
    recordings: Mapping[str, Recording],
    plan: WalkForwardPlan,
) -> list[dict[str, object]]:
    """Reduce every variant, in every cell, to the numbers the verdict needs."""
    labels = regime_by_instant(world)
    # Criterion 4 counts months in the SCORED window. Counting them over the
    # whole usable window would let the twelve fitting months decide whether a
    # regime is conclusive for a result they were never part of.
    scored = tuple(
        item
        for item in world.regimes
        if plan.out_of_sample_span.start <= item.at <= plan.out_of_sample_span.end
    )
    scored_counts = month_counts(scored)
    by_key = {
        (item.construct, item.policy, item.cell_id, item.fill_mix): item
        for item in results.deterministic
    }
    nulls_by_key = {
        (item.construct, item.policy, item.cell_id, item.fill_mix): item for item in results.nulls
    }
    span = plan.out_of_sample_span
    rows: list[dict[str, object]] = []

    for item in results.deterministic:
        if item.kind != "variant":
            continue
        opening = opening_instants(item.monthly)
        statistics = statistics_for(item.ledger)
        sharpe = None if statistics is None else statistics.sharpe_annualised
        where = (item.policy, item.cell_id, item.fill_mix)
        selection = by_key.get((f"{item.construct}/selection-only", *where))
        timing = by_key.get((f"{item.construct}/timing-null", *where))
        null = nulls_by_key.get((f"{item.construct}/exposure-matched", *where))
        equal_weight = by_key.get((EQUAL_WEIGHT, *where))

        monthly_floats = [float(value) for _, value in item.monthly]
        independence = independence_of(monthly_floats)
        recording = recordings.get(item.construct) if item.policy == "USDT" else None
        # Breadth is measured for the headline panel only. The secondary panel
        # holds a thirteen-name universe, where "how many independent bets was
        # this" is answered by the universe rather than by the strategy. There is
        # exactly one shape for the absent case, so the two paths cannot drift.
        breadth = (
            breadth_for(world, recording, span.start, span.end)
            if recording is not None
            else _no_breadth()
        )

        combined_return = item.ledger.terminal_return
        selection_return = None if selection is None else selection.ledger.terminal_return
        timing_return = None if timing is None else timing.ledger.terminal_return

        deflated = None
        if sharpe is not None and null is not None and statistics is not None:
            deflated = deflated_sharpe_ratio(
                statistics,
                trials=max(trials_including_nulls, 1),
                trial_sharpe_variance=variance_of(null.sharpes),
            )

        regimes = regime_returns(item.monthly, labels, opening)
        equal_regimes = (
            regime_returns(equal_weight.monthly, labels, opening_instants(equal_weight.monthly))
            if equal_weight is not None
            else {}
        )

        rows.append(
            {
                "variant": item.construct,
                "quote_policy": item.policy,
                "cell_id": item.cell_id,
                "fill_mix": item.fill_mix,
                "denomination": ACCOUNT_CURRENCY,
                "net_return": str(combined_return),
                "max_drawdown": str(item.ledger.max_drawdown),
                "sharpe_annualised": sharpe,
                "turnover": str(item.ledger.turnover),
                "costs": item.ledger.costs.as_json(),
                "decomposition": {
                    "combined_net_return": str(combined_return),
                    "selection_net_return": None
                    if selection_return is None
                    else str(selection_return),
                    "timing_net_return": None if timing_return is None else str(timing_return),
                    "combined_sharpe": sharpe,
                    "selection_sharpe": None if selection is None else selection.sharpe,
                    "timing_sharpe": None if timing is None else timing.sharpe,
                },
                "exposure_matched_null": (
                    None
                    if null is None
                    else {
                        "seed_count": null.seed_count,
                        "p50": null.percentile(50.0),
                        "p90": null.percentile(90.0),
                        "p95": null.percentile(95.0),
                        "p99": null.percentile(99.0),
                        "p95_ci": [null.estimate(95.0).low, null.estimate(95.0).high],
                    }
                ),
                "deflated_sharpe": (
                    None
                    if deflated is None
                    else {
                        "trials": max(trials_including_nulls, 1),
                        "trials_excluding_nulls": trials_excluding_nulls,
                        "probabilistic_sharpe": deflated.probabilistic_sharpe_ratio,
                        "deflated_sharpe": deflated.deflated_sharpe_ratio,
                        "expected_maximum": deflated.expected_maximum_sharpe_per_period,
                        "trial_sharpe_variance": deflated.trial_sharpe_variance,
                        "autocorrelation_corrected": deflated.autocorrelation_corrected,
                    }
                ),
                "independence": independence.as_json(),
                "breadth": breadth,
                "regimes": {
                    label: {"net_return": str(value), "months": count}
                    for label, (value, count) in sorted(regimes.items())
                },
                "equal_weight_regimes": {
                    label: {"net_return": str(value), "months": count}
                    for label, (value, count) in sorted(equal_regimes.items())
                },
                "criteria": _criteria(
                    sharpe=sharpe,
                    null=null,
                    deflated_value=(None if deflated is None else deflated.deflated_sharpe_ratio),
                    combined_return=combined_return,
                    selection_return=selection_return,
                    regimes=regimes,
                    equal_regimes=equal_regimes,
                    label_counts=scored_counts,
                    effective=independence.effective_observations,
                ),
            }
        )
    return rows


def _criteria(
    *,
    sharpe: float | None,
    null: NullDistribution | None,
    deflated_value: float | None,
    combined_return: Decimal,
    selection_return: Decimal | None,
    regimes: Mapping[str, tuple[Decimal, int]],
    equal_regimes: Mapping[str, tuple[Decimal, int]],
    label_counts: Mapping[Regime, int],
    effective: float,
) -> dict[str, object]:
    """The five pre-registered criteria, each answered True, False or unknown."""
    one = None if sharpe is None or null is None else sharpe > null.percentile(95.0)
    two = None if deflated_value is None else deflated_value >= 0.95
    three: bool | None = None
    if selection_return is not None:
        if combined_return <= 0:
            three = False
        else:
            three = selection_return > 0 and selection_return >= combined_return / Decimal(2)
    countable = {regime.value for regime in conclusive(label_counts)}
    positive = sum(1 for label, (value, _) in regimes.items() if label in countable and value > 0)
    never_worse = all(
        value >= equal_regimes.get(label, (Decimal(0), 0))[0]
        for label, (value, _) in regimes.items()
        if label in countable and label in equal_regimes
    )
    four = positive >= 3 and never_worse if countable else None
    return {
        "1_beats_exposure_matched_null": one,
        "2_survives_deflation": two,
        "3_win_is_selection": three,
        "4_regime_stability": four,
        "4_regimes_positive": positive,
        "4_regimes_countable": sorted(countable),
        "effective_observations": effective,
        "effective_observations_floor": MINIMUM_EFFECTIVE_OBSERVATIONS,
        "effective_observations_met": effective >= MINIMUM_EFFECTIVE_OBSERVATIONS,
    }


# ---------------------------------------------------------------------------
# The whole grid
# ---------------------------------------------------------------------------


def _fully_invested(positions: int) -> Callable[[int], Allocator]:
    """A builder making one fully-invested random null per seed."""

    def build(seed: int) -> Allocator:
        return RandomSelection.for_seed(seed, positions)

    return build


def _matched(held: Mapping[Timestamp, int], positions: int) -> Callable[[int], Allocator]:
    """A builder making one exposure-matched null per seed.

    ``held`` is the variant's own position count at each rebalance, taken from
    the recorder. The null therefore inherits the variant's exposure path
    exactly and differs from it only in which names it draws.
    """

    def build(seed: int) -> Allocator:
        return ExposureMatched.for_seed(seed, positions, held, "exposure-matched-null")

    return build


@dataclass(frozen=True, slots=True)
class Panel:
    """One quote policy and the cost cells it is reported in.

    The headline policy is reported in all four cells. The secondary one is
    reported in the headline cell only: its cross-section is a twentieth the
    width, and running the cost sensitivity over it would add four more rows
    saying what the headline panel already says about the fee schedule. That is
    a narrowing of the registered grid and it is recorded as a deviation rather
    than left to be noticed.
    """

    policy: str
    cells: tuple[CostCell, ...]
    single_asset: str


def panels() -> tuple[Panel, ...]:
    """The two quote policies of part 1 section 4, in reporting order."""
    cells = cost_cells()
    return (
        Panel(policy="USDT", cells=cells, single_asset="BTCUSDT"),
        Panel(policy="EUR", cells=(cells[0],), single_asset="BTCEUR"),
    )


def _run_panel(
    world: World,
    panel: Panel,
    plan: WalkForwardPlan,
    results: Results,
    register: RegisterTrial,
    seeds: int,
    fully_invested_seeds: int,
) -> Mapping[str, Recording]:
    """Every run for one quote policy, cell by cell.

    Cell-major rather than variant-major because the engine's point-in-time
    views are what make a run cheap, and they are per engine. Building one
    engine per run would pay the cache cost - about forty seconds over this
    universe - twenty thousand times. Cell-major also bounds peak memory to one
    cache, because the previous cell's engine is released before the next is
    built.
    """
    recordings: dict[str, Recording] = {}
    variants = registered_variants()
    for cell in panel.cells:
        engine = build_engine(world, cell, panel.policy)
        label = f"{panel.policy}/{cell.label}"

        benchmarks: tuple[tuple[Allocator, str], ...] = (
            (EqualWeightPassive(), EQUAL_WEIGHT),
            (
                SingleAssetBuyAndHold(key=InstrumentKey(VENUE, panel.single_asset)),
                BTC_HOLD,
            ),
        )
        for allocator, name in benchmarks:
            results.deterministic.append(
                _deterministic(engine, plan, allocator, panel.policy, cell, name, "benchmark")
            )
            register(name, f"cell={label}", null=True, seeds=1, note="benchmark")
        print(f"[spike] {label}: benchmarks done")

        for variant in variants:
            recorder = Recording(inner=variant, positions=positions_of(variant))
            results.deterministic.append(
                _deterministic(engine, plan, recorder, panel.policy, cell, variant.name, "variant")
            )
            register(
                variant.name,
                f"{variant.parameter_set_id};cell={label}",
                null=False,
                seeds=1,
                note="pre-registered variant",
            )
            if cell.is_headline:
                recordings[variant.name] = recorder
        print(f"[spike] {label}: {len(variants)} variants done")

        if not cell.runs_nulls:
            continue

        for variant in variants:
            recorder = recordings[variant.name]
            constructs: tuple[tuple[Allocator, str], ...] = (
                (FullyInvested(inner=variant), "selection-only"),
                (ScaledUniverse(invested=dict(recorder.invested)), "timing-null"),
            )
            for allocator, suffix in constructs:
                results.deterministic.append(
                    _deterministic(
                        engine,
                        plan,
                        allocator,
                        panel.policy,
                        cell,
                        f"{variant.name}/{suffix}",
                        suffix.replace("-", "_"),
                    )
                )
                register(
                    f"{variant.name}/{suffix}",
                    f"{variant.parameter_set_id};cell={label}",
                    null=True,
                    seeds=1,
                    note="decomposition construct",
                )
        print(f"[spike] {label}: {len(variants)} variants decomposed")

        results.nulls.append(
            _seeded(
                engine,
                plan,
                _fully_invested(MAX_POSITIONS),
                policy=panel.policy,
                cell=cell,
                construct=FULLY_INVESTED_NULL,
                seeds=fully_invested_seeds,
            )
        )
        register(
            FULLY_INVESTED_NULL,
            f"positions={MAX_POSITIONS};cell={label};seeds={fully_invested_seeds}",
            null=True,
            seeds=fully_invested_seeds,
            note="fully-invested random null",
        )
        print(f"[spike] {label}: fully-invested null done ({fully_invested_seeds} seeds)")

        for variant in variants:
            held = dict(recordings[variant.name].held_counts())
            positions = positions_of(variant)
            results.nulls.append(
                _seeded(
                    engine,
                    plan,
                    _matched(held, positions),
                    policy=panel.policy,
                    cell=cell,
                    construct=f"{variant.name}/exposure-matched",
                    seeds=seeds,
                )
            )
            register(
                f"{variant.name}/exposure-matched",
                f"{variant.parameter_set_id};cell={label};seeds={seeds}",
                null=True,
                seeds=seeds,
                note="exposure-matched selection null",
            )
        print(f"[spike] {label}: {len(variants)} exposure-matched nulls done ({seeds} seeds)")
    return recordings


def execute(
    *,
    store_root: Path = STORE_ROOT,
    registry_path: Path = REGISTRY_PATH,
    results_path: Path = RESULTS_PATH,
    null_budget_seconds: float = DEFAULT_NULL_BUDGET_SECONDS,
    seed_override: int | None = None,
) -> Mapping[str, object]:
    """Run every pre-registered cell and write the result file.

    ``seed_override`` exists for a smoke run and for nothing else. A published
    result uses the registered count or a rung of its registered ladder, and the
    result file records which, so an override cannot pass unnoticed.
    """
    started = time.monotonic()
    registered = assert_no_drift()
    world = build_world(store_root=store_root)
    plan = walk_forward_plan(world)
    results = Results()
    span = plan.out_of_sample_span
    print(
        f"[spike] walk-forward: {plan.fold_count} folds, out-of-sample "
        f"{span.start.isoformat()[:10]} to {span.end.isoformat()[:10]}"
    )

    checksums = _dataset_checksums(store_root)
    registry = TrialRegistry(path=registry_path)
    clock = SystemClock()
    version = _code_version()
    window_label = f"{span.start.isoformat()[:10]}/{span.end.isoformat()[:10]}"

    def register(strategy_id: str, parameters: str, *, null: bool, seeds: int, note: str) -> None:
        registry.record(
            recorded_at=clock.now(),
            code_version=version,
            engine_version=ENGINE_VERSION,
            strategy_id=strategy_id,
            parameter_set_id=parameters,
            dataset_checksum=checksums,
            evaluation_window=window_label,
            quote_policy=parameters.split("cell=")[-1].split("/")[0],
            is_null_construct=null,
            seeds=seeds,
            note=note,
        )

    per_run = measure_run_cost(world, plan)
    all_panels = panels()
    null_cells = sum(1 for panel in all_panels for cell in panel.cells if cell.runs_nulls)
    variants = registered_variants()
    projected = per_run * (
        SEEDS_FULLY_INVESTED * null_cells + SEEDS_EXPOSURE_MATCHED * len(variants) * null_cells
    )
    seeds = SEEDS_EXPOSURE_MATCHED
    if seed_override is not None:
        seeds = seed_override
        results.notes.append(
            f"SMOKE RUN: the exposure-matched seed count was overridden to {seeds} from the "
            f"registered {SEEDS_EXPOSURE_MATCHED}. This result is not publishable."
        )
        print(f"[spike] SMOKE RUN with {seeds} seeds; not a publishable result")
    elif projected > null_budget_seconds:
        seeds = seeds_within_budget(per_run, null_budget_seconds * 0.75, len(variants) * null_cells)
        note = (
            f"A null run measured {per_run:.3f}s, projecting {projected / 3600:.1f}h for the "
            f"registered seed counts against a {null_budget_seconds / 3600:.1f}h budget. The "
            f"exposure-matched count stepped down its registered ladder to {seeds}. It was "
            "never stepped up; a smaller count only widens the interval on the threshold."
        )
        results.notes.append(note)
        print(f"[spike] {note}")
    else:
        print(f"[spike] a null run costs {per_run:.3f}s; the registered seed counts fit")

    recordings: dict[str, Recording] = {}
    for panel in all_panels:
        print(f"[spike] === quote policy {panel.policy} ===")
        found = _run_panel(
            world,
            panel,
            plan,
            results,
            register,
            seeds,
            SEEDS_FULLY_INVESTED if seed_override is None else seed_override,
        )
        if panel.policy == "USDT":
            recordings.update(found)

    including = registry.count(include_null_constructs=True)
    excluding = registry.count(include_null_constructs=False)
    rows = analyse(world, results, including, excluding, recordings, plan)
    scored_months = max(
        (len(item.monthly) for item in results.deterministic if item.kind == "variant"),
        default=0,
    )

    payload: dict[str, object] = {
        "engine_version": ENGINE_VERSION,
        "code_version": version,
        "registered_version": str(registered["version"]),
        "denomination": ACCOUNT_CURRENCY,
        "window": {
            "first_usable_month": world.plan.first_usable_month.label,
            "last_usable_month_end": world.plan.last_usable_month_end.label,
            "usable_months": world.plan.usable_months,
            "out_of_sample": span.as_json(),
            "folds": plan.as_json(),
            "scored_months": scored_months,
        },
        "universe": {
            "USDT": list(universe_sizes(world, "USDT", world.instants)),
            "EUR": list(universe_sizes(world, "EUR", world.instants)),
            "membership_undetermined": dict(world.not_evaluable),
        },
        "regimes": [item.as_json() for item in world.regimes],
        "regime_month_counts": {
            regime.value: count for regime, count in month_counts(world.regimes).items()
        },
        "regime_month_counts_scored": {
            regime.value: count
            for regime, count in month_counts(
                tuple(item for item in world.regimes if span.start <= item.at <= span.end)
            ).items()
        },
        "trials": {"including_nulls": including, "excluding_nulls": excluding},
        "seeds": {
            "smoke_override": seed_override,
            "fully_invested": SEEDS_FULLY_INVESTED if seed_override is None else seed_override,
            "exposure_matched": seeds,
            "registered_exposure_matched": SEEDS_EXPOSURE_MATCHED,
            "measured_seconds_per_null_run": per_run,
        },
        "deterministic": [item.as_json() for item in results.deterministic],
        "nulls": [item.as_json() for item in results.nulls],
        "variants": rows,
        "notes": results.notes,
        "seconds": time.monotonic() - started,
    }
    write_json(payload, results_path)
    print(f"[spike] wrote {results_path} in {(time.monotonic() - started) / 60:.1f} min")
    return payload


def _dataset_checksums(store_root: Path) -> str:
    """One fingerprint over every object the results were computed from."""
    report = FetchReport.read_json(store_root / FETCH_NAME)
    return dataset_fingerprint(dict(report.digests))


def _code_version() -> str:
    """The commit the run was made from, or a marker when git cannot answer."""
    import subprocess

    try:
        output = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607 - git is on PATH by contract
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return output.stdout.strip()[:12] or "unknown"
