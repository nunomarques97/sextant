"""Executing F1's 36-trial grid and reducing it to one JSON result file.

Order is enforced by the shape of this module rather than by a comment.

**The budget is charged before the engine runs.** ``ledger.charge(variant, cell)``
comes first, so a trial that would exceed the declared allowance is refused before
it can produce a number somebody has seen. Charging afterwards would make the guard
a report rather than a gate.

**The provenance gate comes before the budget.** The configuration must be tracked
and clean, because the report cites its commit as existing before these numbers did
and a dirty file makes that citation a false statement.

**The nulls are built from what the recorder saw.** They cannot exist before the
variant has taken its path, which is what makes the exposure-matched null matched
rather than merely random. A cadenced variant is recorded the same way, so its null
draws against the pair count it was really holding that month - between rebalances a
carried count, not a fresh decision.

Every null seed yields two Sharpes
----------------------------------

Criterion 6 asks whether the edge survives in the most recent 24 scored months, and
it percentiles *the same null draws* restricted to *the same months*. A null that
kept only a full-window figure could not answer it, and re-running the nulls on a
shorter window would be a different set of draws. So each seed's monthly series is
reduced twice before it is discarded, and both figures are kept.

What is kept and what is thrown away
------------------------------------

Thirteen thousand null runs produce thirteen thousand ledgers and holding them would
cost more memory than the machine has. Five figures survive each seed - the two
Sharpes, terminal net return, Sortino and maximum drawdown - and the ledger is
released immediately. The deterministic runs keep everything, because their cost
lines are reported in full and a net figure without its cost breakdown is what
invariant 8 exists to prevent.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from sextant.adapters.clocks import SystemClock
from sextant.adapters.storage.trials import REGISTRY_PATH, TrialRegistry
from sextant.app.futures_archive import FETCH_NAME as PERP_FETCH_NAME
from sextant.app.futures_archive import STORE_ROOT as PERP_ROOT
from sextant.app.futures_archive import FuturesFetchReport
from sextant.app.spike_006_f1 import (
    ACCOUNT_CURRENCY,
    CADENCE_PAIR,
    ENGINE_VERSION,
    MARGIN_FRACTION,
    RECENT_WINDOW_MONTHS,
    REGISTERED_VARIANTS,
    RESULTS_PATH,
    SEED_START,
    SEEDS_EXPOSURE_MATCHED,
    SEEDS_FULLY_INVESTED,
    THINNER_EVIDENCE_VARIANT,
    VariantSpec,
    assert_no_drift,
    budget,
    registration_provenance,
)
from sextant.app.spike_006_f1_analysis import analyse, verdict
from sextant.app.spike_006_f1_engine import (
    CostCell,
    assumption_metadata,
    band_floors,
    build_engine,
    cost_cells,
    monthly_series,
    recent_window,
    run_allocator,
    seeds_within_budget,
    sensitivity_cell,
    statistics_for,
    statistics_of,
    walk_forward_plan,
    write_json,
)
from sextant.app.spike_006_f1_world import (
    PERP_VENUE,
    SPOT_ROOT,
    SPOT_VENUE,
    World,
    build_world,
    carry_pairs_at,
)
from sextant.domain.instrument import InstrumentKey
from sextant.domain.time import Timestamp
from sextant.engine.backtest.allocation import Allocator
from sextant.engine.backtest.baselines import SingleAssetBuyAndHold
from sextant.engine.backtest.budget import BudgetLedger, consumption_from, ledger_for
from sextant.engine.backtest.engine import BacktestEngine
from sextant.engine.backtest.ledger import Ledger
from sextant.engine.backtest.trials import dataset_fingerprint
from sextant.engine.backtest.window import WalkForwardPlan
from sextant.engine.regime.segmentation import conclusive, month_counts
from sextant.engine.statistics.bootstrap import (
    PercentileEstimate,
    distribution_summary,
    generator_for,
    percentile_with_interval,
    quantiles,
    variance_of,
)
from sextant.engine.strategies.carry import (
    CadencedCarry,
    CarryAllocator,
    CashAndCarry,
    LongSpotUniverse,
    RandomCarry,
    RecordingCarry,
    SelectionOnlyCarry,
    WholeCarryUniverse,
)

#: Benchmark identifiers, fixed here rather than spelled at each use.
EUR_CASH = "eur-cash"
BTC_HOLD = "btc-buy-and-hold"
EQUAL_WEIGHT_PASSIVE = "equal-weight-passive"
FULLY_INVESTED_NULL = "random-selection-fully-invested"

#: The single-asset benchmark's instrument, on the spot leg's venue.
BTC_SYMBOL = "BTCUSDT"

#: Percentiles every null distribution is read at. Section 9.
PERCENTILES = (50.0, 90.0, 95.0, 99.0)

#: Wall clock the seeded nulls are allowed before the exposure-matched count steps
#: down its registered ladder. Generous on purpose: the brief's deadline is research
#: time, not machine time, and a run left unattended overnight costs nothing that
#: matters while a reduced seed count widens every interval in the report.
DEFAULT_NULL_BUDGET_SECONDS = 12 * 3600.0

#: The bootstrap generator's seed. Explicit and fixed, so every interval in the
#: report is reproducible from the result file alone.
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
    cell_id: str
    fill_mix: str
    ledger: Ledger
    monthly: tuple[tuple[Timestamp, Decimal], ...]
    rebalances: int | None = None
    """How many times the construct's signal was consulted. ``None`` where the
    question does not apply. Printed beside the result for the quarterly variant,
    per section 28.3, because a third as many decisions is thinner evidence."""
    idle_months_before_first_rebalance: int | None = None
    """Months held in cash because no rebalance instant had been reached, section
    28.6. ``None`` for every monthly construct, which has none by construction."""

    @property
    def sharpe(self) -> float | None:
        """Annualised net Sharpe in the account's currency, or None if too short."""
        statistics = statistics_for(self.ledger)
        return None if statistics is None else statistics.sharpe_annualised

    @property
    def recent(self) -> tuple[tuple[Timestamp, Decimal], ...]:
        """The trailing months criterion 6 reads."""
        return recent_window(self.monthly, RECENT_WINDOW_MONTHS)

    def as_json(self) -> dict[str, object]:
        """Serialisable form: never a net figure without its cost breakdown."""
        statistics = statistics_for(self.ledger)
        payload: dict[str, object] = {
            "construct": self.construct,
            "kind": self.kind,
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
        if self.rebalances is not None:
            payload["rebalances"] = self.rebalances
            payload["rebalance_count_note"] = (
                f"The signal was consulted {self.rebalances} times. Section 28.3 requires "
                "this beside the result wherever it appears: a variant's evidence rests on "
                "how many decisions it took, not on how many months it was scored over."
            )
        if self.idle_months_before_first_rebalance is not None:
            payload["idle_months_before_first_rebalance"] = self.idle_months_before_first_rebalance
        return payload


@dataclass(frozen=True, slots=True)
class NullDistribution:
    """A seeded null, reduced to the figures it is ever read for.

    Two Sharpe distributions rather than one: the full window, and the same draws
    restricted to the trailing months criterion 6 judges. Both are needed and
    neither can be recovered from the other.
    """

    construct: str
    cell_id: str
    fill_mix: str
    seed_start: int
    seed_count: int
    sharpes: tuple[float, ...]
    recent_sharpes: tuple[float, ...]
    recent_months: int
    terminal_returns: tuple[float, ...]
    sortinos: tuple[float, ...]
    drawdowns: tuple[float, ...]
    seconds: float

    def estimate(self, value: float) -> PercentileEstimate:
        """One percentile of the full-window Sharpe distribution, with its interval."""
        return percentile_with_interval(
            self.sharpes, value, generator=generator_for(BOOTSTRAP_SEED)
        )

    def percentile(self, value: float) -> float:
        """One percentile of the full-window Sharpe distribution."""
        return self.estimate(value).value

    def recent_estimate(self, value: float) -> PercentileEstimate:
        """The same percentile of the same draws over the trailing months."""
        return percentile_with_interval(
            self.recent_sharpes, value, generator=generator_for(BOOTSTRAP_SEED)
        )

    def recent_percentile(self, value: float) -> float:
        """One percentile of the recent-window Sharpe distribution."""
        return self.recent_estimate(value).value

    def _sharpe_json(
        self, values: tuple[float, ...], estimates: Mapping[str, PercentileEstimate]
    ) -> dict[str, object]:
        low, mean, deviation, high = distribution_summary(values)
        return {
            "minimum": low,
            "mean": mean,
            "standard_deviation": deviation,
            "maximum": high,
            "variance": variance_of(values),
            "percentiles": {
                key: {
                    "value": estimate.value,
                    "ci_low": estimate.low,
                    "ci_high": estimate.high,
                    "ci_width": estimate.width,
                }
                for key, estimate in estimates.items()
            },
        }

    def as_json(self) -> dict[str, object]:
        """Serialisable form, both windows, every percentile with its interval."""
        full = {str(item): self.estimate(item) for item in PERCENTILES}
        recent = {str(item): self.recent_estimate(item) for item in PERCENTILES}
        return {
            "construct": self.construct,
            "cell_id": self.cell_id,
            "fill_mix": self.fill_mix,
            "denomination": ACCOUNT_CURRENCY,
            "seed_start": self.seed_start,
            "seed_count": self.seed_count,
            "seconds": self.seconds,
            "sharpe": self._sharpe_json(self.sharpes, full),
            "recent_window_sharpe": self._sharpe_json(self.recent_sharpes, recent),
            "recent_window_months": self.recent_months,
            "recent_window_note": (
                "The same draws as the full-window figures, restricted to the same "
                "trailing months criterion 6 judges. Not a separate set of draws."
            ),
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
    rebalance_counts: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Building one variant
# ---------------------------------------------------------------------------


def build_variant(spec: VariantSpec, world: World) -> CarryAllocator:
    """One registered variant, cadence and all.

    The single place cadence is applied. A variant registered at one month is the
    bare construction, so the eight registered before amendment 6 are byte-for-byte
    the strategies they would have been without it.
    """
    inner = CashAndCarry(
        label=spec.label,
        signal=spec.signal,
        positions=spec.positions,
        funding=world.funding,
        premium=world.premium,
        perpetual_venue=PERP_VENUE.name,
        margin_fraction=MARGIN_FRACTION,
        require_positive=spec.require_positive,
    )
    if spec.rebalance_months == 1:
        return inner
    return CadencedCarry(inner=inner, rebalance_months=spec.rebalance_months)


def _cadence_facts(allocator: object) -> tuple[int | None, int | None]:
    """The rebalance count and idle months, where the construct has them."""
    if isinstance(allocator, CadencedCarry):
        return allocator.rebalance_count, len(allocator.idle_before_first)
    return None, None


# ---------------------------------------------------------------------------
# Running one thing
# ---------------------------------------------------------------------------


class RegisterTrial(Protocol):
    """How a run announces itself to the append-only registry.

    A protocol rather than a closure type, because the registry is the single
    number the Deflated Sharpe rests on and every caller that produces a result
    goes through the same door.
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
    cell: CostCell,
    construct: str,
    kind: str,
    rebalances: int | None = None,
    idle_months: int | None = None,
) -> Deterministic:
    """One deterministic run, kept whole.

    ``rebalances`` is passed in rather than inferred from the allocator's type. A
    reporting figure that depends on an ``isinstance`` check is a figure that goes
    missing the moment a construct is wrapped differently, and this one is required
    beside every result the quarterly variant appears in.
    """
    summary = run_allocator(engine, allocator, plan, f"{construct}-{cell.label}")
    return Deterministic(
        construct=construct,
        kind=kind,
        cell_id=cell.label,
        fill_mix=cell.fill_mix.label,
        ledger=summary.ledger,
        monthly=monthly_series(summary.ledger),
        rebalances=rebalances,
        idle_months_before_first_rebalance=idle_months,
    )


def _seeded(
    engine: BacktestEngine,
    plan: WalkForwardPlan,
    build: Callable[[int], Allocator],
    *,
    cell: CostCell,
    construct: str,
    seeds: int,
) -> NullDistribution:
    """A seeded null, reduced per seed and never held whole.

    ``build`` returns a *fresh* allocator per seed. One seed describes one whole
    path through the window, so reusing an instance would make the second run
    depend on the first.
    """
    started = time.monotonic()
    sharpes: list[float] = []
    recent_sharpes: list[float] = []
    terminals: list[float] = []
    sortinos: list[float] = []
    drawdowns: list[float] = []
    recent_months = 0
    for offset in range(seeds):
        seed = SEED_START + offset
        summary = run_allocator(engine, build(seed), plan, f"{construct}-{cell.label}-{seed}")
        ledger = summary.ledger
        statistics = statistics_for(ledger)
        sharpes.append(0.0 if statistics is None else statistics.sharpe_annualised)
        sortinos.append(0.0 if statistics is None else statistics.sortino_annualised)
        terminals.append(float(ledger.terminal_return))
        drawdowns.append(float(ledger.max_drawdown))
        trailing = recent_window(monthly_series(ledger), RECENT_WINDOW_MONTHS)
        recent_months = len(trailing)
        recent_sharpes.append(_recent_sharpe(trailing))
    return NullDistribution(
        construct=construct,
        cell_id=cell.label,
        fill_mix=cell.fill_mix.label,
        seed_start=SEED_START,
        seed_count=seeds,
        sharpes=tuple(sharpes),
        recent_sharpes=tuple(recent_sharpes),
        recent_months=recent_months,
        terminal_returns=tuple(terminals),
        sortinos=tuple(sortinos),
        drawdowns=tuple(drawdowns),
        seconds=time.monotonic() - started,
    )


def _recent_sharpe(monthly: Sequence[tuple[Timestamp, Decimal]]) -> float:
    """Annualised Sharpe of a monthly slice, or zero where it cannot be formed.

    Zero rather than dropped, because a percentile taken over a set whose size
    depends on how many seeds happened to be computable is not the percentile the
    criterion names, and a shorter distribution would silently be a different one.
    """
    statistics = statistics_of(monthly)
    return 0.0 if statistics is None else statistics.sharpe_annualised


# ---------------------------------------------------------------------------
# One cell
# ---------------------------------------------------------------------------


def _run_cell(
    world: World,
    cell: CostCell,
    plan: WalkForwardPlan,
    results: Results,
    register: RegisterTrial,
    ledger: BudgetLedger,
    seeds: int,
    fully_invested_seeds: int,
    recordings: dict[str, RecordingCarry],
) -> None:
    """Every run for one cost cell, in the order the constructs depend on.

    Cell-major rather than variant-major: the point-in-time views are what make a
    run cheap and they live on the engine, so one engine per cell pays the cache
    cost four times instead of thirteen thousand.
    """
    engine = build_engine(world, cell)

    benchmarks: tuple[tuple[Allocator, str], ...] = (
        (
            LongSpotUniverse(label=EQUAL_WEIGHT_PASSIVE, perpetual_venue=PERP_VENUE.name),
            EQUAL_WEIGHT_PASSIVE,
        ),
        (SingleAssetBuyAndHold(key=InstrumentKey(SPOT_VENUE, BTC_SYMBOL)), BTC_HOLD),
    )
    for allocator, name in benchmarks:
        results.deterministic.append(
            _deterministic(engine, plan, allocator, cell, name, "benchmark")
        )
        register(name, f"cell={cell.label}", null=True, seeds=1, note="benchmark")
    print(f"[f1] {cell.label}: {len(benchmarks)} benchmarks done")

    for spec in REGISTERED_VARIANTS:
        ledger.charge(spec.label, cell.label)
        allocator = build_variant(spec, world)
        recorder = RecordingCarry(inner=allocator)
        summary = _deterministic(engine, plan, recorder, cell, spec.label, "variant")
        cadence, idle = _cadence_facts(allocator)
        decided = cadence if cadence is not None else len(recorder.held_counts())
        results.deterministic.append(
            replace(summary, rebalances=decided, idle_months_before_first_rebalance=idle)
        )
        register(
            spec.label,
            f"{allocator.parameter_set_id};cell={cell.label}",
            null=False,
            seeds=1,
            note="pre-registered variant",
        )
        results.rebalance_counts[spec.label] = decided
        if cell.is_headline:
            recordings[spec.label] = recorder
    print(
        f"[f1] {cell.label}: {len(REGISTERED_VARIANTS)} variants done "
        f"({ledger.spent} of {ledger.budget.maximum_trials} charged)"
    )

    if not cell.runs_nulls:
        return

    for spec in REGISTERED_VARIANTS:
        recorder = recordings[spec.label]
        variant_allocator = recorder.inner
        constructs: tuple[tuple[Allocator, str], ...] = (
            (SelectionOnlyCarry(inner=variant_allocator), "selection-only"),
            (
                WholeCarryUniverse(
                    label=f"{spec.label}/timing-null",
                    positions=spec.positions,
                    invested=dict(recorder.held_counts()),
                    perpetual_venue=PERP_VENUE.name,
                    margin_fraction=MARGIN_FRACTION,
                ),
                "timing-null",
            ),
        )
        for construct, suffix in constructs:
            results.deterministic.append(
                _deterministic(
                    engine,
                    plan,
                    construct,
                    cell,
                    f"{spec.label}/{suffix}",
                    suffix.replace("-", "_"),
                )
            )
            register(
                f"{spec.label}/{suffix}",
                f"{construct.parameter_set_id};cell={cell.label}",
                null=True,
                seeds=1,
                note="decomposition construct",
            )
    print(f"[f1] {cell.label}: {len(REGISTERED_VARIANTS)} variants decomposed")

    results.nulls.append(
        _seeded(
            engine,
            plan,
            _fully_invested(world),
            cell=cell,
            construct=FULLY_INVESTED_NULL,
            seeds=fully_invested_seeds,
        )
    )
    register(
        FULLY_INVESTED_NULL,
        f"positions={MAX_NULL_POSITIONS};cell={cell.label};seeds={fully_invested_seeds}",
        null=True,
        seeds=fully_invested_seeds,
        note="fully-invested random null",
    )
    print(f"[f1] {cell.label}: fully-invested null done ({fully_invested_seeds} seeds)")

    for spec in REGISTERED_VARIANTS:
        held = dict(recordings[spec.label].held_counts())
        results.nulls.append(
            _seeded(
                engine,
                plan,
                _matched(spec, held),
                cell=cell,
                construct=f"{spec.label}/exposure-matched",
                seeds=seeds,
            )
        )
        register(
            f"{spec.label}/exposure-matched",
            f"positions={spec.positions};cell={cell.label};seeds={seeds}",
            null=True,
            seeds=seeds,
            note="exposure-matched selection null",
        )
    print(f"[f1] {cell.label}: {len(REGISTERED_VARIANTS)} exposure-matched nulls ({seeds} seeds)")


#: The fully-invested null's position count: the largest any variant registers, so
#: the outer sanity check is not quietly the most concentrated construct in the run.
MAX_NULL_POSITIONS = max(spec.positions for spec in REGISTERED_VARIANTS)


def _fully_invested(world: World) -> Callable[[int], Allocator]:
    """The outer null: N pairs drawn at every rebalance, always fully invested.

    "Always" is spelled out as the registered count at every rebalance instant the
    world knows about, rather than as a mapping that answers a constant to any
    question. A mapping with no keys could not be checked against the instants the
    engine actually walks, and an instant the null had no answer for would silently
    become a month in cash.
    """
    always = dict.fromkeys(world.instants, MAX_NULL_POSITIONS)

    def build(seed: int) -> Allocator:
        return RandomCarry(
            label=FULLY_INVESTED_NULL,
            seed=seed,
            positions=MAX_NULL_POSITIONS,
            held=always,
            perpetual_venue=PERP_VENUE.name,
            margin_fraction=MARGIN_FRACTION,
        )

    return build


def _matched(spec: VariantSpec, held: Mapping[Timestamp, int]) -> Callable[[int], Allocator]:
    """The exposure-matched null for one variant: its exposure, none of its choice."""

    def build(seed: int) -> Allocator:
        return RandomCarry(
            label=f"{spec.label}/exposure-matched",
            seed=seed,
            positions=spec.positions,
            held=held,
            perpetual_venue=PERP_VENUE.name,
            margin_fraction=MARGIN_FRACTION,
        )

    return build


# ---------------------------------------------------------------------------
# The whole grid
# ---------------------------------------------------------------------------


def execute(
    *,
    spot_root: Path = SPOT_ROOT,
    perp_root: Path = PERP_ROOT,
    registry_path: Path = REGISTRY_PATH,
    results_path: Path = RESULTS_PATH,
    repository_root: Path | None = None,
    null_budget_seconds: float = DEFAULT_NULL_BUDGET_SECONDS,
    seed_override: int | None = None,
    note_suffix: str = "",
) -> Mapping[str, object]:
    """Run the registered grid and write the result file.

    ``seed_override`` exists for a smoke run and for nothing else. A published
    result uses the registered count or a rung of its registered ladder, and the
    file records which, so an override cannot pass unnoticed.

    ``note_suffix`` is appended to every registry row this run writes. The registry
    is append-only, so a grid executed twice leaves two sets of rows and the Deflated
    Sharpe Ratio counts both. That is the conservative direction and it is the honest
    one, but a reader needs to know *why* there are two, and a note in the rows
    themselves is a better place to say it than a paragraph somewhere else.
    """
    started = time.monotonic()
    registered = assert_no_drift()
    provenance = registration_provenance(root=repository_root or Path())
    print(
        f"[f1] specification committed at {provenance.config_commit.sha[:12]} - "
        f"{provenance.config_commit.subject}"
    )

    declared = budget()
    registry = TrialRegistry(path=registry_path)
    consumption = consumption_from(declared, registry.read())
    ledger = ledger_for(declared, consumption)
    print(f"[f1] budget: {declared.maximum_trials} trials, {consumption.spent} already recorded")

    world = build_world(spot_root=spot_root, perp_root=perp_root)
    plan = walk_forward_plan(world)
    span = plan.out_of_sample_span
    print(
        f"[f1] walk-forward: {plan.fold_count} folds, out-of-sample "
        f"{span.start.isoformat()[:10]} to {span.end.isoformat()[:10]}"
    )

    results = Results()
    clock = SystemClock()
    version = provenance.head_sha[:12]
    checksums = _dataset_checksums(spot_root, perp_root)
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
            quote_policy="USDT",
            is_null_construct=null,
            seeds=seeds,
            note=f"{note}{note_suffix}",
        )

    cells = cost_cells()
    null_cells = sum(1 for cell in cells if cell.runs_nulls)
    per_run = _measure_run_cost(world, cells, plan)
    projected = per_run * (
        SEEDS_FULLY_INVESTED * null_cells
        + SEEDS_EXPOSURE_MATCHED * len(REGISTERED_VARIANTS) * null_cells
    )
    seeds = SEEDS_EXPOSURE_MATCHED
    if seed_override is not None:
        seeds = seed_override
        note = (
            f"SMOKE RUN: the exposure-matched seed count was overridden to {seeds} from the "
            f"registered {SEEDS_EXPOSURE_MATCHED}. This result is not publishable."
        )
        results.notes.append(note)
        print(f"[f1] {note}")
    elif projected > null_budget_seconds:
        seeds = seeds_within_budget(
            per_run, null_budget_seconds * 0.75, len(REGISTERED_VARIANTS) * null_cells
        )
        note = (
            f"A null run measured {per_run:.3f}s, projecting {projected / 3600:.1f}h for the "
            f"registered seed counts against a {null_budget_seconds / 3600:.1f}h budget. The "
            f"exposure-matched count stepped down its registered ladder to {seeds}. It was "
            "never stepped up; a smaller count only widens the interval on the threshold."
        )
        results.notes.append(note)
        print(f"[f1] {note}")
    else:
        print(
            f"[f1] a null run costs {per_run:.3f}s, projecting {projected / 3600:.1f}h; "
            "the registered seed counts fit and no reduction was applied"
        )

    recordings: dict[str, RecordingCarry] = {}
    for cell in cells:
        print(f"[f1] === cell {cell.label} ({cell.fill_mix.label}) ===")
        _run_cell(
            world,
            cell,
            plan,
            results,
            register,
            ledger,
            seeds,
            SEEDS_FULLY_INVESTED if seed_override is None else seed_override,
            recordings,
        )

    sensitivity = _run_sensitivity(world, plan, results, register, ledger)

    including = registry.count(include_null_constructs=True)
    excluding = registry.count(include_null_constructs=False)
    final = consumption_from(declared, registry.read())

    payload: dict[str, object] = {
        "engine_version": ENGINE_VERSION,
        "code_version": version,
        "registered_version": str(registered["version"]),
        "family": declared.family,
        "denomination": ACCOUNT_CURRENCY,
        "registration_provenance": provenance.as_json(),
        "budget": {
            "declared": declared.maximum_trials,
            "variants": list(declared.variants),
            "cost_cells": list(declared.cost_cells),
            "charged_this_run": ledger.spent,
            "charged_pairs": [list(pair) for pair in ledger.charged],
            "from_the_committed_registry": {
                "registered": final.spent,
                "remaining": final.remaining,
                "post_hoc": [list(pair) for pair in final.post_hoc],
                "nulls_not_charged": final.nulls,
                "is_overspent": final.is_overspent,
            },
            "summary": final.summary(),
        },
        "window": {
            **world.window.as_json(),
            "out_of_sample": span.as_json(),
            "folds": plan.as_json(),
            "scored_months": max(
                (len(item.monthly) for item in results.deterministic if item.kind == "variant"),
                default=0,
            ),
            "recent_window_months": RECENT_WINDOW_MONTHS,
        },
        "universe": {
            "pairs_at_each_rebalance": [
                {"at": at.isoformat(), "pairs": carry_pairs_at(world, at)} for at in world.instants
            ],
            "census": world.census.as_json(),
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
        "conclusive_regimes": [
            regime.value
            for regime in conclusive(
                month_counts(
                    tuple(item for item in world.regimes if span.start <= item.at <= span.end)
                )
            )
        ],
        "cells": [cell.as_json() for cell in cells],
        "cost_assumptions": {
            cell.label: assumption_metadata(cell) for cell in (*cells, sensitivity_cell())
        },
        "liquidity_bands": band_floors(),
        "cadence": {
            "pair": list(CADENCE_PAIR),
            "rebalance_counts": dict(sorted(results.rebalance_counts.items())),
            "thinner_evidence_variant": THINNER_EVIDENCE_VARIANT,
            "note": (
                "The pair differs in rebalance_months and in nothing else, so whatever "
                "separates the two results is cadence. Section 28.3."
            ),
        },
        "trials": {"including_nulls": including, "excluding_nulls": excluding},
        "seeds": {
            "smoke_override": seed_override,
            "fully_invested": SEEDS_FULLY_INVESTED if seed_override is None else seed_override,
            "exposure_matched": seeds,
            "registered_exposure_matched": SEEDS_EXPOSURE_MATCHED,
            "seed_start": SEED_START,
            "measured_seconds_per_null_run": per_run,
            "projected_hours_at_registered_counts": projected / 3600,
        },
        "deterministic": [item.as_json() for item in results.deterministic],
        "nulls": [item.as_json() for item in results.nulls],
        "execution_sensitivity": sensitivity,
        "notes": results.notes,
        "seconds": time.monotonic() - started,
    }
    _refuse_a_carry_run_without_funding(results)
    rows = analyse(payload)
    scored = payload["window"]
    months = int(str(scored["scored_months"])) if isinstance(scored, dict) else 0
    outcome = verdict(rows, headline_cell=_headline_label(), scored_months=months)
    payload["variants"] = [row.as_json() for row in rows]
    payload["verdict"] = outcome.as_json()
    payload["seconds"] = time.monotonic() - started
    write_json(payload, results_path)
    print(f"[f1] verdict ({outcome.letter}): {outcome.reason}")
    print(f"[f1] wrote {results_path} in {(time.monotonic() - started) / 60:.1f} min")
    return payload


class CarryWithoutFunding(RuntimeError):
    """Every variant's funding line was zero. For this family that is a defect."""


def _refuse_a_carry_run_without_funding(results: Results) -> None:
    """Refuse to write a carry result whose funding stream was never applied.

    A cash-and-carry book holds the basis plus the funding stream and has no term in
    the asset's own price, so funding is not one cost line among several: it is the
    return. An engine built without a funding schedule falls back to a flat rate and
    silently produces a book that never receives its carry, and every figure computed
    from it is wrong in the same direction.

    This exists because it happened. The first execution of the registered grid ran
    to completion, wrote a plausible-looking result file and reached a verdict, with
    every funding line at exactly zero, because the runner never passed the schedule
    to the engine. Nothing in the result file said so; the tell was a column of
    zeroes in a table nobody had to read.
    """
    variants = [item for item in results.deterministic if item.kind == "variant"]
    if not variants:
        return
    if any(item.ledger.costs.funding.amount != 0 for item in variants):
        return
    raise CarryWithoutFunding(
        f"All {len(variants)} variant runs charged exactly zero funding. A carry book's "
        "return IS the funding stream, so this is an engine that was never given the "
        "published settlements rather than a market in which funding was flat. Check "
        "that build_engine passes funding=world.funding. Nothing was written."
    )


def _run_sensitivity(
    world: World,
    plan: WalkForwardPlan,
    results: Results,
    register: RegisterTrial,
    ledger: BudgetLedger,
) -> dict[str, object]:
    """Amendments 4 and 5: the best variant, re-costed at the execution venue.

    One variant, one cell, read by no criterion and charged against no variant
    budget. "Best" is the highest out-of-sample net Sharpe in the headline cell,
    chosen by a rule fixed before the grid ran rather than by looking.
    """
    del ledger
    headline = [
        item
        for item in results.deterministic
        if item.kind == "variant" and item.cell_id == _headline_label()
    ]
    ranked = [item for item in headline if item.sharpe is not None]
    if not ranked:
        return {
            "ran": False,
            "reason": (
                "No variant produced a Sharpe in the headline cell, so there is no best "
                "variant to re-cost. Reported as not run rather than run on an arbitrary pick."
            ),
        }
    best = max(ranked, key=lambda item: (item.sharpe or 0.0, item.construct))
    spec = next(item for item in REGISTERED_VARIANTS if item.label == best.construct)
    cell = sensitivity_cell()
    engine = build_engine(world, cell)
    allocator = build_variant(spec, world)
    recorder = RecordingCarry(inner=allocator)
    outcome = _deterministic(engine, plan, recorder, cell, spec.label, "execution_sensitivity")
    cadence, idle = _cadence_facts(allocator)
    results.deterministic.append(
        replace(
            outcome,
            rebalances=cadence if cadence is not None else len(recorder.held_counts()),
            idle_months_before_first_rebalance=idle,
        )
    )
    register(
        spec.label,
        f"{allocator.parameter_set_id};cell={cell.label}",
        null=True,
        seeds=1,
        note="execution-venue re-cost, one-directional, read by no criterion",
    )
    print(f"[f1] execution re-cost: {spec.label} at {cell.label}")
    return {
        "ran": True,
        "variant": spec.label,
        "chosen_by": "highest out-of-sample net Sharpe in the headline cell",
        "cell": cell.as_json(),
        "research_cell": _headline_label(),
        "read_by_any_criterion": False,
        "charged_against_variant_budget": False,
    }


def _headline_label() -> str:
    """The one cell every criterion is judged in."""
    return next(cell.label for cell in cost_cells() if cell.is_headline)


def _measure_run_cost(world: World, cells: Sequence[CostCell], plan: WalkForwardPlan) -> float:
    """What one null seed costs, measured rather than assumed.

    Three runs in the headline cell, discarding the first: the first pays for the
    point-in-time cache the rest read, so including it would project a run cost the
    grid never pays again.
    """
    cell = next(item for item in cells if item.is_headline)
    engine = build_engine(world, cell)
    build = _fully_invested(world)
    samples: list[float] = []
    for seed in range(3):
        began = time.monotonic()
        run_allocator(engine, build(SEED_START + seed), plan, f"measure-{seed}")
        samples.append(time.monotonic() - began)
    return sum(samples[1:]) / max(len(samples) - 1, 1)


def _dataset_checksums(spot_root: Path, perp_root: Path) -> str:
    """One fingerprint over every object both archives were read from."""
    from sextant.app.binance_archive import FETCH_NAME as SPOT_FETCH_NAME
    from sextant.app.binance_archive import FetchReport as SpotFetchReport

    spot = SpotFetchReport.read_json(spot_root / SPOT_FETCH_NAME)
    perp = FuturesFetchReport.read_json(perp_root / PERP_FETCH_NAME)
    return dataset_fingerprint({**dict(spot.digests), **dict(perp.digests)})


__all__ = [
    "BTC_HOLD",
    "BTC_SYMBOL",
    "DEFAULT_NULL_BUDGET_SECONDS",
    "EQUAL_WEIGHT_PASSIVE",
    "EUR_CASH",
    "FULLY_INVESTED_NULL",
    "MAX_NULL_POSITIONS",
    "PERCENTILES",
    "CarryWithoutFunding",
    "Deterministic",
    "NullDistribution",
    "Results",
    "build_variant",
    "execute",
]
