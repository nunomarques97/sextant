"""Turning F1's registered cost cells into engines, and one carry run into figures.

The wiring layer between :mod:`sextant.app.spike_006_f1_world`, which owns the
dataset, and :mod:`sextant.app.spike_006_f1_run`, which owns the order things happen
in. Nothing here chooses a number: every fee, fraction, multiplier and threshold
arrives from :mod:`sextant.app.spike_006_f1`, which reads them from a specification
committed before the archive had finished downloading.

Two registered values that the engine defaults away from
---------------------------------------------------------

The engine's ``funding`` defaults to ``None`` and its
``haircut_is_a_loss_on_either_side`` defaults to ``False``. Both defaults are right
for a long-only single-leg strategy and wrong for this family, and both are
registered the other way. A drift guard that compares the configuration against a
literal proves the specification says what it says; it proves nothing about whether
anything read it. Both are named constants now and both are passed here.

The funding schedule is not optional here
-----------------------------------------

The engine accepts ``funding=None`` and falls back to a flat rate, which is right
for every family whose return has no funding term. For this one the funding stream
**is** the return, so an engine built without it computes a carry book that never
receives its carry. It is passed here and asserted by a test, and the runner refuses
to write a result file in which every variant's funding line is exactly zero.

Two legs, two fee schedules
---------------------------

SEXTANT-005 charged one schedule because a single-leg strategy trades one venue.
A carry pair trades two, and the venue's spot and futures schedules are not the
same, so a cell carries both and the cost model routes by the instrument's venue.
The routing is per-venue rather than per-leg because the engine knows venues and
knows nothing about carry, which is what keeps the venue name out of the engine.

The stress cell
---------------

Section 8 registers ``stress`` as taker fills with spread and slippage at twice the
assumption on both legs. Doubling happens here, in ``app``, by building a second
:class:`CostAssumption` whose stated basis says it is doubled and why. The engine is
not taught about multipliers: a cost assumption is a value plus its justification,
and a multiplier applied inside the engine would be a value whose justification had
been left behind.

Reused rather than copied
------------------------

``fresh_clock``, ``run_allocator``, ``statistics_for``, ``monthly_series``,
``seeds_within_budget`` and ``write_json`` come from :mod:`sextant.app.spike_005`
unchanged. They are pure reducers over a ledger with no SEXTANT-005 parameter in
them, and one definition that both tasks read cannot drift between the two. Nothing
here modifies them, so SEXTANT-005's published numbers rest on the same code they
were computed with.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from sextant.adapters.exchanges.binance.costs import (
    DEEP_BAND_FLOOR,
    MID_BAND_FLOOR,
    SLIPPAGE_ASSUMPTION,
    SPREAD_ASSUMPTION,
    fx_leg,
)
from sextant.app.spike_005 import (
    fresh_clock,
    monthly_series,
    run_allocator,
    seeds_within_budget,
    statistics_for,
    write_json,
)
from sextant.app.spike_006_f1 import (
    ACCOUNT_CURRENCY,
    ACCOUNT_EQUITY,
    ENGINE_LOOKBACK_DAYS,
    FOLD_COUNT,
    HAIRCUT_FRACTION,
    HAIRCUT_ON_EITHER_SIDE,
    IN_SAMPLE_MONTHS,
    REGISTERED_CELLS,
    CellSpec,
    execution_sensitivity,
)
from sextant.app.spike_006_f1_world import PERP_VENUE, SPOT_VENUE, World
from sextant.domain.time import Timeframe, Timestamp
from sextant.domain.venue import Venue
from sextant.engine.backtest.engine import BacktestEngine
from sextant.engine.backtest.window import WalkForwardPlan, anchored_plan
from sextant.engine.execution.costs import (
    CostAssumption,
    FeeSchedule,
    FillMix,
    ItemisedCostModel,
    LiquidityBand,
    MedianTurnoverBands,
)
from sextant.engine.execution.fx import FxLeg, FxPolicy
from sextant.engine.execution.markout import DelistingHaircut
from sextant.engine.statistics.boundary import returns_as_series
from sextant.engine.statistics.metrics import PerformanceStatistics, summarise

#: How the fill mix is spelled in a report row. The mix is an assumption and the
#: label travels with every figure computed under it, per invariant 12.
FILL_MIX_LABELS: Mapping[str, str] = {
    "1.00": "maker-only",
    "0.50": "half-and-half",
    "0.00": "taker-only",
}


def _fill_mix(fraction: Decimal) -> FillMix:
    """The registered maker fraction as a labelled fill-mix assumption."""
    key = str(fraction)
    return FillMix(maker_fraction=fraction, label=FILL_MIX_LABELS.get(key, f"maker={key}"))


def scaled(assumption: CostAssumption, multiplier: Decimal) -> CostAssumption:
    """The same assumption at ``multiplier`` times its basis points.

    The basis is extended rather than replaced, so a doubled figure still carries
    the reasoning behind the number it was doubled from. A multiplier of one
    returns the assumption untouched, so the ordinary cells cannot acquire a
    "doubled by a factor of 1" note nobody asked for.
    """
    if multiplier == 1:
        return assumption
    return CostAssumption(
        by_band={band: value * multiplier for band, value in assumption.by_band.items()},
        basis=(
            f"{assumption.basis} Multiplied by {multiplier} for the registered stress cell "
            "of section 8, which asks what the result looks like if both legs are twice as "
            "expensive to cross as assumed. Still an assumption, and a harsher one."
        ),
        label=assumption.label,
    )


@dataclass(frozen=True, slots=True)
class CostCell:
    """One cell of the reporting grid, as the engine needs it.

    Distinct from :class:`~sextant.app.spike_006_f1.CellSpec`, which is the
    registered specification and is compared against the configuration by the drift
    guard. This is what that specification becomes once it has been given the
    venues it applies to.
    """

    spec: CellSpec
    spot_schedule: FeeSchedule
    futures_schedule: FeeSchedule

    @property
    def label(self) -> str:
        """What appears in a report row, and in a run identifier."""
        return self.spec.label

    @property
    def is_headline(self) -> bool:
        """True for ``vip0_even`` only: the cell every criterion is judged in."""
        return self.spec.is_headline

    @property
    def runs_nulls(self) -> bool:
        """Whether the seeded nulls run in this cell. Section 9's two cells."""
        return self.spec.runs_nulls

    @property
    def fill_mix(self) -> FillMix:
        """The maker fraction this cell assumes, labelled."""
        return _fill_mix(self.spec.maker_fraction)

    @property
    def multiplier(self) -> Decimal:
        """The stress cell's spread and slippage multiplier; one everywhere else."""
        return self.spec.spread_and_slippage_multiplier

    def as_json(self) -> dict[str, object]:
        """Every fee and assumption this cell charges, for the results file."""
        return {
            "label": self.label,
            "spot_maker_bps": str(self.spec.spot_maker_bps),
            "spot_taker_bps": str(self.spec.spot_taker_bps),
            "futures_maker_bps": str(self.spec.futures_maker_bps),
            "futures_taker_bps": str(self.spec.futures_taker_bps),
            "maker_fraction": str(self.spec.maker_fraction),
            "fill_mix": self.fill_mix.label,
            "spread_and_slippage_multiplier": str(self.multiplier),
            "is_headline": self.is_headline,
            "runs_nulls": self.runs_nulls,
            "spot_venue": SPOT_VENUE.name,
            "futures_venue": PERP_VENUE.name,
        }


def _schedule(maker: Decimal, taker: Decimal, *, tier: str, source: str) -> FeeSchedule:
    """One leg's published schedule at the fees the cell registers."""
    return FeeSchedule(maker_bps=maker, taker_bps=taker, tier=tier, source=source)


def cell_from(spec: CellSpec) -> CostCell:
    """One registered cell, given the two venues its two schedules apply to."""
    return CostCell(
        spec=spec,
        spot_schedule=_schedule(
            spec.spot_maker_bps,
            spec.spot_taker_bps,
            tier=f"{spec.label} spot leg",
            source=(
                "The registered cell of pre-registration section 8, whose figures are the "
                "venue's own published spot schedule."
            ),
        ),
        futures_schedule=_schedule(
            spec.futures_maker_bps,
            spec.futures_taker_bps,
            tier=f"{spec.label} futures leg",
            source=(
                "The registered cell of pre-registration section 8, whose figures are the "
                "venue's own published perpetual-futures schedule."
            ),
        ),
    )


def cost_cells() -> tuple[CostCell, ...]:
    """The four registered cells of section 8, in registration order."""
    return tuple(cell_from(spec) for spec in REGISTERED_CELLS)


def sensitivity_cell() -> CostCell:
    """The execution-venue re-cost of amendments 4 and 5.

    One cell, one variant, read by no criterion. It is built here beside the four
    so that the fee arithmetic cannot differ between the grid and the sensitivity,
    and it is returned separately so that no loop over ``cost_cells`` can pick it
    up by accident.
    """
    return cell_from(execution_sensitivity())


def cost_model(world: World, cell: CostCell) -> ItemisedCostModel:
    """The cost model for one cell: two schedules, and the assumptions labelled.

    ``schedule`` is the spot leg's and ``schedule_by_venue`` routes both, so a
    venue that somehow reached the model unrouted is charged the spot schedule
    rather than nothing. Charging nothing is the failure mode worth designing out.
    """
    schedules: Mapping[Venue, FeeSchedule] = {
        SPOT_VENUE: cell.spot_schedule,
        PERP_VENUE: cell.futures_schedule,
    }
    # funding_bps_per_day is deliberately left at its zero default. The engine reads
    # it only when it has no FundingSchedule, and this family always has one; setting
    # a flat rate here as well would charge the book twice for the same stream.
    return ItemisedCostModel(
        schedule=cell.spot_schedule,
        schedule_by_venue=schedules,
        fill_mix=cell.fill_mix,
        spread=scaled(SPREAD_ASSUMPTION, cell.multiplier),
        slippage=scaled(SLIPPAGE_ASSUMPTION, cell.multiplier),
        liquidity=MedianTurnoverBands(
            histories=world.histories,
            deep_floor=DEEP_BAND_FLOOR,
            mid_floor=MID_BAND_FLOOR,
        ),
    )


def conversion_leg(world: World) -> FxLeg:
    """The EUR to USDT conversion, charged once in and once out.

    The venue's own published figure, from the same adapter SEXTANT-005 read it
    from. Section 8 registers it unchanged, so it is imported rather than restated.
    """
    return fx_leg(FxPolicy.APPLIED, world.fx_rates)


def build_engine(world: World, cell: CostCell) -> BacktestEngine:
    """One engine, fully configured, for one cell of the reporting grid.

    Built per cell rather than per run: the point-in-time views are what make a
    run cheap and they live on the engine, so a fresh engine per null seed would
    pay the cache cost thousands of times. Only the clock is new per run.
    """
    return BacktestEngine(
        repository=world.repository,
        clock=fresh_clock(),
        timeframe=Timeframe.D1,
        instruments=world.instruments,
        universe=world.universe,
        cost_model=cost_model(world, cell),
        fx=conversion_leg(world),
        routing=world.routing,
        haircut=DelistingHaircut(fraction=HAIRCUT_FRACTION),
        haircut_is_a_loss_on_either_side=HAIRCUT_ON_EITHER_SIDE,
        series_end=world.series_end,
        initial_equity=ACCOUNT_EQUITY,
        account_currency=ACCOUNT_CURRENCY,
        lookback_days=ENGINE_LOOKBACK_DAYS,
        record_decisions=False,
        funding=world.funding,
    )


def walk_forward_plan(world: World, *, fold_count: int = FOLD_COUNT) -> WalkForwardPlan:
    """The registered walk-forward shape over the resolved window, section 7."""
    return anchored_plan(
        first_month=world.window.first_month.starts_at,
        total_months=world.window.usable_months,
        in_sample_months=IN_SAMPLE_MONTHS,
        fold_count=fold_count,
    )


def recent_window(
    monthly: Sequence[tuple[Timestamp, Decimal]], months: int
) -> tuple[tuple[Timestamp, Decimal], ...]:
    """The last ``months`` scored months of a monthly series, in instant order.

    Criterion 6 reads this, and so does every null seed: the criterion percentiles
    the *same* draws restricted to the same trailing months, so a null that kept
    only a full-window figure could not answer it. A series shorter than ``months``
    is returned whole rather than padded, and the caller reports the count.
    """
    ordered = sorted(monthly, key=lambda item: item[0])
    return tuple(ordered[-months:]) if months > 0 else ()


def statistics_of(
    monthly: Sequence[tuple[Timestamp, Decimal]],
) -> PerformanceStatistics | None:
    """The performance summary of a monthly series, across the numeric boundary.

    Separate from ``statistics_for``, which takes a whole ledger. This one exists
    because criterion 6 and every null seed need the statistics of a *slice* of a
    series, and slicing a ledger is not a thing a ledger allows.
    """
    ordered = [value for _, value in sorted(monthly, key=lambda item: item[0])]
    if len(ordered) < 2:
        return None
    return summarise(returns_as_series(ordered))


def band_floors() -> Mapping[str, str]:
    """The registered liquidity-band floors, for the results file."""
    return {
        "deep_floor": str(DEEP_BAND_FLOOR.amount),
        "mid_floor": str(MID_BAND_FLOOR.amount),
        "statistic": "trailing median daily quote turnover",
    }


def assumption_metadata(cell: CostCell) -> dict[str, str]:
    """Spread and slippage as the run manifest carries them, labelled assumptions."""
    spread = scaled(SPREAD_ASSUMPTION, cell.multiplier)
    slippage = scaled(SLIPPAGE_ASSUMPTION, cell.multiplier)
    return {**spread.as_metadata(), **slippage.as_metadata()}


__all__ = [
    "FILL_MIX_LABELS",
    "CostCell",
    "LiquidityBand",
    "assumption_metadata",
    "band_floors",
    "build_engine",
    "cell_from",
    "conversion_leg",
    "cost_cells",
    "cost_model",
    "monthly_series",
    "recent_window",
    "run_allocator",
    "scaled",
    "seeds_within_budget",
    "sensitivity_cell",
    "statistics_for",
    "statistics_of",
    "walk_forward_plan",
    "write_json",
]
