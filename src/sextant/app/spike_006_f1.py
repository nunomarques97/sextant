"""SEXTANT-006 family F1, cash-and-carry: the registered specification and its guards.

Every number here is fixed in `docs/PRE-REGISTRATION-006-F1.md` and
`config/spike-006-f1.yaml`, both committed before the grid ran. This module holds
the constants the run will use and the three things that stop the registration
from being decorative:

1. :func:`assert_no_drift` compares every registered number against the constant
   the code actually uses, and refuses to start if they disagree. Explicit rather
   than reflective, for the reason SEXTANT-005's guard is: a loop that walked the
   YAML looking for a matching constant would pass just as happily if a key were
   renamed out of existence, which is the failure it exists to catch.

2. :func:`budget` builds the enforced trial budget from the registered variant and
   cell lists. The runner charges one trial per variant per cell *before* the
   engine runs. An unregistered variant, an unregistered cell, or a charge past
   the declared allowance all raise. F1 is closed when its 32 are spent.

3. :func:`registration_provenance` refuses to start unless the configuration is
   committed and clean, and returns the commit a reader can cite. The drift guard
   proves code and config agree now; only the history can show they were not
   edited together after a result was seen. :func:`ordering_audit` produces the
   lines the report quotes, after the results have been committed.

The world builder, the grid and the report live in their own modules. This one is
the specification, and it is deliberately runnable with no dataset at all so the
guards can be tested without one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

from sextant.adapters.exchanges.binance.costs import (
    DEEP_BAND_FLOOR,
    FUTURES_FEES,
    FX_PAIR_FEE_BPS,
    MID_BAND_FLOOR,
    SLIPPAGE_ASSUMPTION,
    SPOT_FEES,
    SPREAD_ASSUMPTION,
)
from sextant.adapters.exchanges.kraken.costs import (
    FUTURES_FEES as KRAKEN_FUTURES_FEES,
)
from sextant.adapters.exchanges.kraken.costs import (
    SPOT_FEES as KRAKEN_SPOT_FEES,
)
from sextant.adapters.vcs import Commit, GitRepository
from sextant.domain.money import Notional
from sextant.domain.time import Timestamp
from sextant.engine.backtest.budget import TrialBudget, budget_from
from sextant.engine.execution.breakeven import MONTHLY_ROUND_TRIPS, fee_of_equity_bps
from sextant.engine.execution.costs import LiquidityBand
from sextant.engine.execution.funding import DEFAULT_INTERVAL_HOURS
from sextant.engine.statistics.persistence import (
    BOOTSTRAP_SEED,
    MINIMUM_MONTHS_FOR_A_YEAR,
    RECENT_WINDOW_MONTHS,
    RESAMPLES,
)
from sextant.engine.strategies.carry import CarrySignal

FAMILY = "F1"
ENGINE_VERSION = "sextant-006-f1"
CONFIG_PATH = Path("config") / "spike-006-f1.yaml"
REGISTRATION_PATH = Path("docs") / "PRE-REGISTRATION-006-F1.md"
RESEARCH_ROOT = Path("research")
RESULTS_PATH = RESEARCH_ROOT / "spike-006-f1.json"

NEWLINE = chr(10)

#: The account, from section 4. Equity is unchanged from SEXTANT-004 and
#: SEXTANT-005 so that a return here and a return there are returns on the same
#: account.
ACCOUNT_CURRENCY = "EUR"
ACCOUNT_EQUITY = Notional(Decimal(1500))
MAX_POSITIONS = 10
MIN_NOTIONAL_FRACTION = Decimal("0.25")
LOT_ROUNDING_FRACTION = Decimal("0.01")

#: The futures leg's initial margin fraction. An assumption, labelled as one
#: everywhere: five times leverage on that leg, far inside what the venue permits
#: on a major, chosen to be conservative rather than representative.
MARGIN_FRACTION = Decimal("0.20")

#: Universe thresholds, section 6. Rules 3, 5 and 7 are carried from SEXTANT-005
#: unchanged and at the same numbers, so no result here can be attributed to a
#: screen retuned for a new dataset.
MIN_LISTING_AGE_DAYS = 180
MIN_MEDIAN_QUOTE_VOLUME = Notional(Decimal(250_000))
FUNDING_TRAILING_DAYS = 30
MIN_BAR_COVERAGE_FRACTION = Decimal("0.90")

#: Section 6. The quote asset both legs are priced in, and the observation floor
#: the turnover statistic needs before it is allowed to answer.
QUOTE_ASSET = "USDT"
TURNOVER_MIN_OBSERVATIONS = 20

#: The longest lookback any registered variant reads: the 90-day funding sum of
#: `carry-rank90-*`. Section 6 rule 6's bar-coverage window is measured over it.
LONGEST_LOOKBACK_DAYS = 90

#: The walk-forward shapes, section 7.
IN_SAMPLE_MONTHS = 12
FOLD_COUNT = 4
MINIMUM_MONTHS_TO_PROCEED = 36
FX_FIRST_MONTH_LOOKBACK_DAYS = 300

#: Section 8. The longest lookback is 90 days for the funding signal; the view
#: must reach past it far enough to find a close on the far side of a gap, and
#: past the 180-day listing age the universe rule reads.
ENGINE_LOOKBACK_DAYS = 420
HAIRCUT_FRACTION = Decimal("0.20")

#: Section 8. A delisting is a loss whichever side of it the book was on. A short
#: leg that delists is not a windfall: the venue settles it and the position cannot
#: be bought back at the last observed close, which predates the news the haircut
#: stands in for. Registered as a named constant rather than a literal because the
#: previous shape - a drift check comparing the configuration against ``True`` while
#: the engine ran on its own default of ``False`` - verified the number and wired
#: none of it.
HAIRCUT_ON_EITHER_SIDE = True

#: Section 29. How many times a family may be re-executed after a void run before it
#: is suspended and reported as (C). Two, so three executions in total. Held here
#: because a ceiling nobody counts against is not a ceiling.
MAXIMUM_RE_EXECUTIONS = 2

#: The version of the pre-registration this code implements. Held here as well as in
#: the configuration so a report can cite the specification's current version beside
#: the version the run it describes actually read, without either being retyped.
REGISTERED_VERSION = "v2.3"

#: Section 30, amendment 8. Who may declare a run void, and who may not. Two roles
#: rather than one sentence of prose, because the prose outlives the conversation it
#: was written in and the whole point is that a reader cannot get this wrong. The
#: reason is conflict of interest: the party that produced a run must not be the party
#: that decides it did not happen.
VOID_DECLARED_BY_ROLE = "product-owner"
VOID_NEVER_DECLARED_BY_ROLE = "developer"

#: Section 30, amendment 8, generalised by section 31, amendment 9. Rule S1: the
#: section 12 spread sample is acquired only if the assumed cost could plausibly be
#: carrying the verdict, and that is decided by computation rather than by argument.
#: Set the assumed cost to zero, re-evaluate, and acquire when some variant would then
#: clear criterion 1. A sign change alone is not a rescue: a variant that crosses zero
#: and lands below its own null was rescued by rounding.
SPREAD_TRIGGER_RULE = "S1"
SPREAD_TRIGGER_THRESHOLD = Decimal(0)

#: Amendment 9's bar, named so the condition cannot be read as "positive net return".
#: The counterfactual must clear criterion 1 itself, which is the null comparison and
#: the positive return together.
SPREAD_TRIGGER_BAR = "criterion-1"

#: The family from which the runner records per-month assumed costs, making amendment
#: 9's test exact rather than modelled. F1's result file carries totals only.
EXACT_RESCUE_TEST_FROM = "F2"

#: Section 32, amendment 10. The family from which rule S1 carries a floor, and from
#: which the measured spread is the default cost. Both are prospective: F1 is judged
#: by the bar that was registered when it ran, and stays costed at the assumption.
FLOOR_FROM = "F2"

#: Section 33, amendment 11. Rule S1's floor is anchored to ZERO, not to the null.
#: The exposure-matched null over this window loses, so its 95th percentile sits at
#: -1.26 to -1.58 and any bar anchored to it is a bar below zero that a variant merely
#: failing to lose will clear. Both clauses hold together: the counterfactual Sharpe
#: must exceed zero by one standard error of its own estimate AND clear the null.
FLOOR_ANCHOR = "zero"

#: Section 33, amendment 11. Rule P1: every criterion that compares a variant to a null
#: is paired with an absolute test against zero. Registered as a standing rule rather
#: than as a third patch, because the same confusion has now appeared in three places -
#: SEXTANT-004's exposure-matched null, criterion 1 as written, and both attempts at a
#: floor for rule S1 - and a patch repairs one site while the defect is in the shape.
PAIRING_RULE = "P1"

#: The family from which criterion 1's absolute clause becomes "distinguishable from
#: zero by its own standard error" rather than "strictly positive". Strictly narrowing,
#: so it can only ever remove a pass; F1 was judged on the weaker form and keeps it.
CRITERION_ONE_STRENGTHENED_FROM = "F2"

#: Section 33.4, amendment 11. Rule E1: the banded spread assumption is replaced from
#: F2 by a low-frequency estimate per instrument and per period, adopted only if it
#: passes an acceptance test written and committed before the calibration was run.
ESTIMATOR_RULE = "E1"
ESTIMATOR_ID = "abdi-ranaldo-2017"

#: Computed beside the registered estimator and never substituted for it. Adopting
#: whichever of two estimators passes, after seeing which one passed, is selection.
ESTIMATOR_COMPARISON_ID = "corwin-schultz-2012"

#: Rule E1's three clauses, all of which must hold. The rank floor is the one-tailed
#: 5 per cent critical value of Spearman's rho at n = 6; the magnitude clause is a
#: factor of two, expressed as one base-2 logarithm; the positivity share is what a
#: figure has to be before it can be charged as a cost at all.
ESTIMATOR_RANK_FLOOR = Decimal("0.771")
ESTIMATOR_FACTOR_LOG2 = Decimal(1)
ESTIMATOR_POSITIVE_SHARE = Decimal("0.90")

#: How the estimate is applied from F2: a trailing window ending strictly before the
#: decision instant, which is the window the liquidity bands are already cut on, and a
#: minimum number of usable two-day pairs below which the banded assumption is charged
#: instead, labelled as one.
ESTIMATOR_TRAILING_DAYS = 30
ESTIMATOR_MINIMUM_PAIRS = 20

#: Section 34, amendment 12, rule A12.1. Two spread levels, and only one of them
#: decides anything. The HEADLINE is the registered assumption and it is what every
#: criterion, every verdict letter and every Deflated Sharpe Ratio is evaluated against.
#: The BOUND is the measured deep-band half-spread and it is reported beside the
#: headline on every cost-bearing table, never read by a criterion.
SPREAD_HEADLINE_BPS = Decimal(10)
SPREAD_BOUND_BPS = Decimal("0.53")

#: What the headline is a multiple of the bound. Registered because A12.7 requires the
#: 10 bps figure to be labelled with it wherever it appears, so that a reader cannot
#: mistake an upper bound for a measurement. Quantised to two places, which is the
#: precision the measured figure itself carries.
SPREAD_BOUND_RATIO = Decimal("18.87")

#: The two level names a scored row records. A row that does not name its level is read
#: as the headline, which is what F1's result file predates the field with; a row that
#: names the bound is refused by the criteria evaluation outright.
SPREAD_LEVEL_HEADLINE = "registered-upper-bound"
SPREAD_LEVEL_BOUND = "measured-lower-bound"

#: Section 34, rule A12.2. The family-level outcome registered before any family could
#: produce it: fails at the headline, clears at the bound. Not a failure and not a
#: promotion - a deferral, which is the only honest answer when the number that decides
#: the question is one this project invented.
SPREAD_CONTINGENT = "spread-contingent"

#: Section 34, rule B1. The bands are re-cut on whichever candidate quantity actually
#: correlates with measured spread, decided by a permutation test rather than by
#: inspection, and the number of bands is whatever separates rather than whatever there
#: happens to be now.
BAND_RECUT_RULE = "B1"
BAND_RECUT_PERMUTATIONS = 10_000
BAND_RECUT_SEED = 20260911
BAND_RECUT_ALPHA = Decimal("0.05")
BAND_MINIMUM_SYMBOLS = 3
BAND_SEPARATION_FACTOR = Decimal(2)

#: Section 34, rule M1. At least four symbols in each of the mid and thin bands, over
#: rule S1's own protocol and at least its own six days - unless the band turns out to be
#: empty in practice, which is an answer rather than a gap.
EXTENDED_SAMPLE_RULE = "M1"
EXTENDED_SAMPLE_PER_BAND = 4
EXTENDED_SAMPLE_DAYS = 6

#: Section 34, rule H1. Historical quoted spread from the venue's own archive, which
#: fires only if a family is recorded spread-contingent. The response to a failed
#: inference is not a better inference; it is to check whether the quantity was
#: published.
HISTORICAL_QUOTES_RULE = "H1"

#: The venue's charge for one crossing of the currency boundary, re-exported so that the
#: analysis can compare it against what a committed result file actually charged without
#: reaching into the venue adapter itself.
CONVERSION_BPS = FX_PAIR_FEE_BPS

#: Section 34, rule A12.9. How many times capital crosses a currency boundary in one
#: run: once in and once out. The FX charge must scale with this number and never with
#: turnover, and a test holds it.
FX_CROSSINGS_PER_RUN = 2

#: The size rule S1 implies when it fires: six symbols on six days, which is section
#: 12's own registered sample and already the minimum. No subsampling rule is added,
#: because a subsample of a rule-fixed sample would be a second rule with nothing to
#: constrain it.
SPREAD_SAMPLE_SYMBOL_DAYS = 36
FX_SYMBOL = "EURUSDT"
FOREIGN_CURRENCY = "USDT"
REGIME_SYMBOL = "BTCUSDT"

#: Section 10, unchanged from SEXTANT-005.
SEEDS_EXPOSURE_MATCHED = 500
SEEDS_FULLY_INVESTED = 2000
SEED_START = 1
SEED_REDUCTION_LADDER = (500, 250, 100)

#: Section 13 and amendment 17.2. Three settings, and the third is a stress level
#: rather than a candidate rate: at this capital the largest single-leg notional is
#: some thirty-two times below the first margin bracket's ceiling, so a variant
#: failing only at 0.025 is not rejected on that basis.
MAINTENANCE_MARGIN_HEADLINE = Decimal("0.005")
MAINTENANCE_MARGIN_COMPANION = Decimal("0.010")
MAINTENANCE_MARGIN_STRESS = Decimal("0.025")
MAINTENANCE_MARGINS = (
    MAINTENANCE_MARGIN_HEADLINE,
    MAINTENANCE_MARGIN_COMPANION,
    MAINTENANCE_MARGIN_STRESS,
)
MARGIN_BUFFER_SWEEP = (Decimal("0.50"), Decimal("0.20"), Decimal("0.10"), Decimal("0.05"))

#: Amendment 16.4 and 17.1. The depth tree's coverage, and rule C3's floor: fewer
#: than twelve of a variant's scored months inside this window and capacity is
#: reported as unestablished rather than estimated.
DEPTH_WINDOW_STARTS = "2023-01-01"
DEPTH_WINDOW_ENDS = "2024-05-17"
MINIMUM_DEPTH_MONTHS = 12


# ---------------------------------------------------------------------------
# The eight variants, as specifications rather than as constructed strategies
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VariantSpec:
    """One registered variant's parameters, section 9.2.

    Separate from :class:`~sextant.engine.strategies.carry.CashAndCarry` because a
    constructed strategy needs a funding schedule and a premium index, which need
    a dataset. The specification needs neither, which is what lets the drift guard
    and the budget be verified before a single byte is read.
    """

    label: str
    signal: CarrySignal
    positions: int
    require_positive: bool = False
    rebalance_months: int = 1
    """Months between rebalance instants, amendment 6. One for every variant
    registered before it; three for ``carry-rank90-10-quarterly``. Explicit rather
    than implied by the label, because a cadence a reader has to infer from a name
    is a cadence a reimplementation can get wrong."""


REGISTERED_VARIANTS: tuple[VariantSpec, ...] = (
    VariantSpec(label="carry-basket-5", signal=CarrySignal.TURNOVER, positions=5),
    VariantSpec(label="carry-basket-10", signal=CarrySignal.TURNOVER, positions=10),
    VariantSpec(label="carry-rank30-5", signal=CarrySignal.FUNDING_30, positions=5),
    VariantSpec(label="carry-rank30-10", signal=CarrySignal.FUNDING_30, positions=10),
    VariantSpec(label="carry-rank90-5", signal=CarrySignal.FUNDING_90, positions=5),
    VariantSpec(label="carry-rank90-10", signal=CarrySignal.FUNDING_90, positions=10),
    VariantSpec(label="carry-premium-10", signal=CarrySignal.PREMIUM, positions=10),
    VariantSpec(
        label="carry-positive-10",
        signal=CarrySignal.FUNDING_30,
        positions=10,
        require_positive=True,
    ),
    VariantSpec(
        label="carry-rank90-10-quarterly",
        signal=CarrySignal.FUNDING_90,
        positions=10,
        rebalance_months=3,
    ),
)

#: Amendment 28.3's one-factor comparison: same signal, same lookback, same position
#: count, cadence the only difference. Held here so the reporter cannot pair the
#: wrong two rows, and checked against the registered pair by the drift guard.
CADENCE_PAIR: tuple[str, str] = ("carry-rank90-10", "carry-rank90-10-quarterly")

#: The variant whose rebalance count is printed beside its result everywhere it
#: appears, because roughly 23 rebalances against roughly 70 is thinner evidence and
#: a reader must see that where the number is.
THINNER_EVIDENCE_VARIANT = "carry-rank90-10-quarterly"


@dataclass(frozen=True, slots=True)
class CellSpec:
    """One cell of the reporting grid, section 8.

    Fees differ by leg here, which is new: the venue charges spot and futures on
    different schedules, so a cell carries both.
    """

    label: str
    spot_maker_bps: Decimal
    spot_taker_bps: Decimal
    futures_maker_bps: Decimal
    futures_taker_bps: Decimal
    maker_fraction: Decimal
    is_headline: bool
    runs_nulls: bool
    spread_and_slippage_multiplier: Decimal = Decimal(1)


REGISTERED_CELLS: tuple[CellSpec, ...] = (
    CellSpec(
        label="vip0_maker",
        spot_maker_bps=SPOT_FEES.maker_bps,
        spot_taker_bps=SPOT_FEES.taker_bps,
        futures_maker_bps=FUTURES_FEES.maker_bps,
        futures_taker_bps=FUTURES_FEES.taker_bps,
        maker_fraction=Decimal("1.00"),
        is_headline=False,
        runs_nulls=False,
    ),
    CellSpec(
        label="vip0_even",
        spot_maker_bps=SPOT_FEES.maker_bps,
        spot_taker_bps=SPOT_FEES.taker_bps,
        futures_maker_bps=FUTURES_FEES.maker_bps,
        futures_taker_bps=FUTURES_FEES.taker_bps,
        maker_fraction=Decimal("0.50"),
        is_headline=True,
        runs_nulls=True,
    ),
    CellSpec(
        label="vip0_taker",
        spot_maker_bps=SPOT_FEES.maker_bps,
        spot_taker_bps=SPOT_FEES.taker_bps,
        futures_maker_bps=FUTURES_FEES.maker_bps,
        futures_taker_bps=FUTURES_FEES.taker_bps,
        maker_fraction=Decimal("0.00"),
        is_headline=False,
        runs_nulls=False,
    ),
    CellSpec(
        label="stress",
        spot_maker_bps=SPOT_FEES.maker_bps,
        spot_taker_bps=SPOT_FEES.taker_bps,
        futures_maker_bps=FUTURES_FEES.maker_bps,
        futures_taker_bps=FUTURES_FEES.taker_bps,
        maker_fraction=Decimal("0.00"),
        is_headline=False,
        runs_nulls=True,
        spread_and_slippage_multiplier=Decimal(2),
    ),
)


#: Amendment 26.1. The contraction check compares the admitted and excluded groups
#: on three attributes at the rebalance with the largest month-on-month fall in pair
#: count, and every difference is bootstrapped by the same method so nothing is
#: chosen per attribute.
CONTRACTION_ATTRIBUTES = ("median_funding_rate", "contract_age_days", "liquidity_band")

#: Amendment 26.2. The execution venue's published schedule, on the one variant that
#: scored best. Not a grid cell, not read by any criterion, and it consumes no
#: variant budget because it can only make a variant look worse - it cannot produce a
#: winner that was not already one, so it cannot widen the search.
EXECUTION_SENSITIVITY = CellSpec(
    label="kraken_execution",
    spot_maker_bps=KRAKEN_SPOT_FEES.maker_bps,
    spot_taker_bps=KRAKEN_SPOT_FEES.taker_bps,
    futures_maker_bps=KRAKEN_FUTURES_FEES.maker_bps,
    futures_taker_bps=KRAKEN_FUTURES_FEES.taker_bps,
    maker_fraction=Decimal("0.50"),
    is_headline=False,
    runs_nulls=False,
)


def execution_sensitivity() -> CellSpec:
    """The one re-cost amendment 26.2 registers, and there is exactly one.

    A function rather than a bare constant so the two callers - the runner and the
    drift guard - cannot disagree about which cell it is.
    """
    return EXECUTION_SENSITIVITY


def round_trip_fee_bps(cell: CellSpec) -> Decimal:
    """Fees alone for opening and closing both legs of one pair, in basis points.

    The arithmetic amendment 26.2 states in advance: two legs opened and two closed
    at the cell's own fill mix. Spread and slippage are excluded deliberately, since
    they are assumptions rather than published rates and are identical across the two
    venues here. What a variant actually pays depends on its realised turnover, which
    the run measures.
    """
    taker_fraction = Decimal(1) - cell.maker_fraction
    spot = cell.maker_fraction * cell.spot_maker_bps + taker_fraction * cell.spot_taker_bps
    futures = cell.maker_fraction * cell.futures_maker_bps + taker_fraction * cell.futures_taker_bps
    return 2 * (spot + futures)


def headline_cell() -> CellSpec:
    """The one cell the criteria are judged in, and there is exactly one."""
    headline = [cell for cell in REGISTERED_CELLS if cell.is_headline]
    if len(headline) != 1:
        raise DriftedFromPreRegistration(
            f"{FAMILY}: exactly one cell is the headline; the code declares {len(headline)}."
        )
    return headline[0]


#: Amendment 27. One full-book round trip's fees in basis points of equity, at each
#: schedule. Derived from the per-leg figures rather than restated, so a change to a
#: fee schedule cannot leave these behind.
RESEARCH_FEE_OF_EQUITY_BPS = fee_of_equity_bps(round_trip_fee_bps(headline_cell()), MARGIN_FRACTION)
EXECUTION_FEE_OF_EQUITY_BPS = fee_of_equity_bps(
    round_trip_fee_bps(execution_sensitivity()), MARGIN_FRACTION
)

#: D2b's threshold: a full monthly rebalance. Held in one place, in the engine.
D2B_THRESHOLD_ROUND_TRIPS = MONTHLY_ROUND_TRIPS


# ---------------------------------------------------------------------------
# The trial budget, built from the registered lists and enforced by the runner
# ---------------------------------------------------------------------------


def budget() -> TrialBudget:
    """F1's declared allowance: the nine variants across the four cells.

    Built from the same tuples the runner iterates, so the budget cannot describe
    a grid different from the one that runs. The type refuses a maximum that
    exceeds the product, which is what keeps this a registration rather than a
    ceiling with a reserve behind it.
    """
    return budget_from(
        family=FAMILY,
        engine_version=ENGINE_VERSION,
        variants=(variant.label for variant in REGISTERED_VARIANTS),
        cost_cells=(cell.label for cell in REGISTERED_CELLS),
        maximum_trials=len(REGISTERED_VARIANTS) * len(REGISTERED_CELLS),
    )


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


def _sequence(value: object, name: str) -> Sequence[object]:
    """One registered list, or a refusal naming it."""
    if not isinstance(value, list):
        raise DriftedFromPreRegistration(
            f"{name} is not a list in the registered specification; it is {type(value).__name__}."
        )
    return value


def _text(value: object) -> str:
    """A registered scalar as text, so a comparison never depends on YAML typing."""
    return str(value)


def _band(name: str) -> LiquidityBand:
    """The band enum one registered key names."""
    return {
        "deep": LiquidityBand.DEEP,
        "mid": LiquidityBand.MID,
        "thin": LiquidityBand.THIN,
        "unknown": LiquidityBand.UNKNOWN,
    }[name]


def _rule(rules: Sequence[object], name: str) -> Mapping[str, object]:
    """One named universe rule out of the registered ordered list."""
    for entry in rules:
        block = _mapping(entry, "universe.rules_in_order entry")
        if _text(block.get("name")) == name:
            return block
    raise DriftedFromPreRegistration(
        f"universe.rules_in_order does not contain a rule named {name!r}. A rule renamed out "
        "of existence is exactly what an explicit guard is for."
    )


def _setting(settings: Sequence[object], rate: str) -> Mapping[str, object]:
    """One maintenance-margin setting out of the registered list."""
    for entry in settings:
        block = _mapping(entry, "maintenance_margin.settings entry")
        if _text(block.get("rate")) == rate:
            return block
    raise DriftedFromPreRegistration(
        f"maintenance_margin.settings does not carry the rate {rate!r}."
    )


def _variant_checks(registered: Sequence[object]) -> list[tuple[str, object, object]]:
    """Every variant's parameters, compared one field at a time.

    Compared positionally as well as by name, because the order the variants run
    in is the order the trial registry records them in, and a reordering would
    make a reimplementation's numbers line up against the wrong rows.
    """
    if len(registered) != len(REGISTERED_VARIANTS):
        raise DriftedFromPreRegistration(
            f"variants.registered lists {len(registered)} variants and the code declares "
            f"{len(REGISTERED_VARIANTS)}. The trial budget is the product of this count and "
            "the cell count, so a mismatch here is a mismatch in the allowance."
        )
    checks: list[tuple[str, object, object]] = []
    for position, (entry, spec) in enumerate(zip(registered, REGISTERED_VARIANTS, strict=True)):
        block = _mapping(entry, f"variants.registered[{position}]")
        checks.append((f"variants.registered[{position}].id", _text(block["id"]), spec.label))
        checks.append(
            (
                f"variants.registered[{position}].signal",
                _text(block["signal"]),
                spec.signal.value,
            )
        )
        checks.append(
            (
                f"variants.registered[{position}].positions",
                _text(block["positions"]),
                str(spec.positions),
            )
        )
        checks.append(
            (
                f"variants.registered[{position}].require_positive",
                bool(block["require_positive"]),
                spec.require_positive,
            )
        )
        checks.append(
            (
                f"variants.registered[{position}].rebalance_months",
                _text(block["rebalance_months"]),
                str(spec.rebalance_months),
            )
        )
    return checks


def _void_run_checks(raw: Mapping[str, object]) -> list[tuple[str, object, object]]:
    """Section 29's counting rule, guarded like every other registered value.

    Checked because this block is the one that decides how many searches the
    Deflated Sharpe Ratio deflates against. A rule that could be edited between a
    result and a report would be worth less than no rule.
    """
    block = _mapping(raw["void_runs"], "void_runs")
    return [
        (
            "void_runs.maximum_re_executions",
            _text(block["maximum_re_executions"]),
            str(MAXIMUM_RE_EXECUTIONS),
        ),
        ("void_runs.rows_are_deleted", bool(block["rows_are_deleted"]), False),
        ("void_runs.rows_are_counted_by_the_dsr", bool(block["rows_are_counted_by_the_dsr"]), True),
        ("void_runs.grants_no_trial", bool(block["grants_no_trial"]), True),
        ("void_runs.relaxes_no_threshold", bool(block["relaxes_no_threshold"]), True),
        ("void_runs.declared_by_role", _text(block["declared_by_role"]), VOID_DECLARED_BY_ROLE),
        (
            "void_runs.never_declared_by_role",
            _text(block["never_declared_by_role"]),
            VOID_NEVER_DECLARED_BY_ROLE,
        ),
        (
            "void_runs.declared_by names the Product Owner",
            "the Product Owner" in _text(block["declared_by"]),
            True,
        ),
        (
            "void_runs.declared_by excludes the Developer",
            "NEVER the Developer" in _text(block["declared_by"]),
            True,
        ),
    ]


def _spread_trigger_checks(raw: Mapping[str, object]) -> list[tuple[str, object, object]]:
    """Rule S1, guarded like rule C3 and for the same reason.

    A condition on the result file is only a pre-registered condition if it cannot be
    edited once the result file exists. The threshold and the sample size are both
    compared, because a trigger with a movable threshold is a decision deferred rather
    than a decision made.
    """
    block = _mapping(
        _mapping(raw["spread_sample"], "spread_sample")["acquisition"],
        "spread_sample.acquisition",
    )
    return [
        ("spread_sample.acquisition.rule_id", _text(block["rule_id"]), SPREAD_TRIGGER_RULE),
        (
            "spread_sample.acquisition.threshold",
            Decimal(_text(block["threshold"])),
            SPREAD_TRIGGER_THRESHOLD,
        ),
        (
            "spread_sample.acquisition.comparison",
            _text(block["comparison"]),
            "strictly greater than",
        ),
        ("spread_sample.acquisition.acquire_if_true", bool(block["acquire_if_true"]), True),
        ("spread_sample.acquisition.acquire_if_false", bool(block["acquire_if_false"]), False),
        (
            "spread_sample.acquisition.size_if_acquired.symbol_days",
            _text(_mapping(block["size_if_acquired"], "size_if_acquired")["symbol_days"]),
            str(SPREAD_SAMPLE_SYMBOL_DAYS),
        ),
        (
            "spread_sample.acquisition.generalised_by",
            _text(block["generalised_by"]),
            "amendment-9",
        ),
        (
            "spread_sample.acquisition.per_month_assumed_costs_required_from",
            _text(block["per_month_assumed_costs_required_from"]),
            EXACT_RESCUE_TEST_FROM,
        ),
        (
            "spread_sample.acquisition.condition names criterion 1",
            "criterion 1" in _text(block["condition"]),
            True,
        ),
        (
            "spread_sample.acquisition.condition sets the assumed cost to zero",
            "ZERO" in _text(block["condition"]),
            True,
        ),
        ("spread_sample.acquisition.floor_from", _text(block["floor_from"]), FLOOR_FROM),
        ("spread_sample.acquisition.floor_anchor", _text(block["floor_anchor"]), FLOOR_ANCHOR),
        (
            "spread_sample.acquisition.floor_as_settled names both clauses",
            "BOTH clauses" in _text(block["floor_as_settled"]),
            True,
        ),
        (
            "spread_sample.acquisition.floor_as_settled is anchored to zero",
            "exceeding ZERO by at least one standard error" in _text(block["floor_as_settled"]),
            True,
        ),
        (
            "spread_sample.acquisition.floor names one standard error",
            "one standard error" in _text(block["floor"]),
            True,
        ),
        (
            "spread_sample.acquisition.floor is skew and kurtosis corrected",
            "kurtosis correction" in _text(block["floor"]),
            True,
        ),
        (
            "spread_sample.acquisition.cells_excluded",
            tuple(
                _text(item)
                for item in _sequence(block["cells_excluded"], "acquisition.cells_excluded")
            ),
            (EXECUTION_SENSITIVITY.label,),
        ),
    ]


def _every_family_checks(raw: Mapping[str, object]) -> list[tuple[str, object, object]]:
    """Section 31's two reporting rules, which outlive this family.

    Guarded because they are the two things F1 measured that F2 onward would otherwise
    rediscover: a currency leg larger than the venue's own fees, and a family-level
    result that reads differently from its best variant's.
    """
    block = _mapping(raw["every_family_reports"], "every_family_reports")
    currency = _mapping(block["currency_leg_as_its_own_line"], "currency_leg_as_its_own_line")
    headline = _mapping(
        block["family_level_result_is_the_headline"], "family_level_result_is_the_headline"
    )
    return [
        (
            "every_family_reports.currency_leg_as_its_own_line.rule",
            "NEVER folded into fees" in _text(currency["rule"]),
            True,
        ),
        (
            "every_family_reports.measured_spread_is_the_default_from_f2.rule",
            "DEFAULT spread cost from F2"
            in _text(
                _mapping(
                    block["measured_spread_is_the_default_from_f2"],
                    "measured_spread_is_the_default_from_f2",
                )["rule"]
            ),
            True,
        ),
        (
            "every_family_reports.family_level_result_is_the_headline.rule",
            "not its best variant" in _text(headline["rule"]),
            True,
        ),
    ]


def _pairing_checks(raw: Mapping[str, object]) -> list[tuple[str, object, object]]:
    """Rule P1 and criterion 1's strengthened clause, guarded as strictly as a cell.

    The narrowing property is checked as text as well as the family it applies from,
    because "can only ever remove a pass" is the entire justification for registering a
    criterion change after this family's figures were read. If that sentence ever leaves
    the configuration, the justification has left with it.
    """
    block = _mapping(raw["absolute_pairing"], "absolute_pairing")
    strengthened = _mapping(block["criterion_1_strengthened"], "criterion_1_strengthened")
    return [
        ("absolute_pairing.rule_id", _text(block["rule_id"]), PAIRING_RULE),
        ("absolute_pairing.applies_from", _text(block["applies_from"]), "F2"),
        ("absolute_pairing.is_a_trial", bool(block["is_a_trial"]), False),
        (
            "absolute_pairing.rule pairs every null comparison with an absolute test",
            "ABSOLUTE test" in _text(block["rule"]),
            True,
        ),
        (
            "absolute_pairing.criterion_1_strengthened.applies_from",
            _text(strengthened["applies_from"]),
            CRITERION_ONE_STRENGTHENED_FROM,
        ),
        (
            "absolute_pairing.criterion_1_strengthened names one standard error",
            "ONE STANDARD ERROR" in _text(strengthened["as_strengthened"]),
            True,
        ),
        (
            "absolute_pairing.criterion_1_strengthened can only ever remove a pass",
            "can never add" in _text(strengthened["can_only_ever_remove_a_pass"]),
            True,
        ),
        (
            "absolute_pairing.criterion_1_strengthened.not_applied_to_f1",
            "SUPPLEMENTARY" in _text(strengthened["not_applied_to_f1"]),
            True,
        ),
    ]


def _estimator_checks(raw: Mapping[str, object]) -> list[tuple[str, object, object]]:
    """Rule E1: the estimator, the three clauses, and how the estimate is applied.

    Every threshold is compared, for the reason rule C3's and rule S1's are: a computed
    condition with a movable threshold is a decision deferred rather than a decision
    made, and this one was committed before the calibration it decides was run.
    """
    block = _mapping(raw["spread_estimator"], "spread_estimator")
    acceptance = _mapping(block["acceptance"], "spread_estimator.acceptance")
    clauses = _mapping(acceptance["clauses_all_of_which_must_hold"], "acceptance.clauses")
    application = _mapping(block["application_from_f2"], "application_from_f2")
    return [
        ("spread_estimator.rule_id", _text(block["rule_id"]), ESTIMATOR_RULE),
        ("spread_estimator.applies_from", _text(block["applies_from"]), "F2"),
        ("spread_estimator.is_a_trial", bool(block["is_a_trial"]), False),
        (
            "spread_estimator.registered_estimator.id",
            _text(_mapping(block["registered_estimator"], "registered_estimator")["id"]),
            ESTIMATOR_ID,
        ),
        (
            "spread_estimator.comparison_estimator.id",
            _text(_mapping(block["comparison_estimator"], "comparison_estimator")["id"]),
            ESTIMATOR_COMPARISON_ID,
        ),
        (
            "spread_estimator.comparison_estimator.never_substituted",
            "NEVER adopted"
            in _text(_mapping(block["comparison_estimator"], "c")["never_substituted"]),
            True,
        ),
        (
            "spread_estimator.acceptance.clauses.ordering floor",
            _rank_floor(_text(clauses["ordering"])),
            ESTIMATOR_RANK_FLOOR,
        ),
        (
            "spread_estimator.acceptance.clauses.magnitude factor",
            _factor(_text(clauses["magnitude"])),
            ESTIMATOR_FACTOR_LOG2,
        ),
        (
            "spread_estimator.acceptance.clauses.positivity share",
            _share(_text(clauses["positivity"])),
            ESTIMATOR_POSITIVE_SHARE,
        ),
        (
            "spread_estimator.acceptance.if_it_fails keeps the assumption",
            "ASSUMPTION is kept" in _text(acceptance["if_it_fails"]),
            True,
        ),
        (
            "spread_estimator.application_from_f2.trailing days",
            _trailing_days(_text(application["point_in_time"])),
            ESTIMATOR_TRAILING_DAYS,
        ),
        (
            "spread_estimator.application_from_f2.point_in_time ends before the decision",
            "STRICTLY BEFORE" in _text(application["point_in_time"]),
            True,
        ),
        (
            "spread_estimator.application_from_f2.minimum pairs",
            _minimum_pairs(_text(application["insufficient_history"])),
            ESTIMATOR_MINIMUM_PAIRS,
        ),
        (
            "spread_estimator.application_from_f2.halving",
            "HALF-spread per leg" in _text(application["halving"]),
            True,
        ),
        (
            "spread_estimator.application_from_f2.slippage_is_untouched",
            "remains an assumption" in _text(application["slippage_is_untouched"]),
            True,
        ),
    ]


def _rank_floor(prose: str) -> Decimal:
    """The rank-correlation floor, read out of the clause that states it."""
    return Decimal(_after(prose, "at least "))


def _factor(prose: str) -> Decimal:
    """The magnitude clause's logarithm bound, read out of the clause."""
    return Decimal(_after(prose, "over measured is at most "))


def _share(prose: str) -> Decimal:
    """The positivity share, read out of the clause, as a fraction rather than per cent."""
    return Decimal(_after(prose, "at least ")) / Decimal(100)


def _trailing_days(prose: str) -> int:
    """How many trailing daily bars price a decision, read out of the clause."""
    return int(_after(prose, "estimated from the "))


def _minimum_pairs(prose: str) -> int:
    """The usable-pair floor below which the assumption is charged instead."""
    return int(_after(prose, "fewer than "))


def _after(prose: str, marker: str) -> str:
    """The first whitespace-delimited token after a marker, or a refusal.

    Reading a threshold out of the prose that states it, rather than holding it twice,
    is what stops a registered number and its justification from drifting apart. A
    marker that no longer appears is a drift and is raised as one.
    """
    if marker not in prose:
        raise DriftedFromPreRegistration(
            f"The registered prose no longer contains {marker!r}, so the threshold it "
            "states cannot be read out of it. Resolve this with a new pre-registration "
            "version, never by editing the old one."
        )
    return prose.split(marker, 1)[1].split()[0].rstrip(".,;:")


def _spread_level_checks(raw: Mapping[str, object]) -> list[tuple[str, object, object]]:
    """Rules A12.1, A12.2, A12.6 and A12.7: two levels, one of which decides nothing.

    Both figures are compared, and so is the sentence that says which of the two a
    criterion reads. A configuration in which the bound could be read as a criterion is a
    configuration in which a measurement this project cannot date becomes a verdict.
    """
    levels = _mapping(raw["spread_levels"], "spread_levels")
    headline = _mapping(levels["headline"], "spread_levels.headline")
    bound = _mapping(levels["bound"], "spread_levels.bound")
    contingent = _mapping(raw["spread_contingent_verdict"], "spread_contingent_verdict")
    trial = _mapping(raw["bound_is_not_a_trial"], "bound_is_not_a_trial")
    relabel = _mapping(raw["relabel_the_assumption"], "relabel_the_assumption")
    return [
        ("spread_levels.rule_id", _text(levels["rule_id"]), "A12.1"),
        ("spread_levels.applies_from", _text(levels["applies_from"]), "F2"),
        ("spread_levels.is_a_trial", bool(levels["is_a_trial"]), False),
        (
            "spread_levels.headline.deep_bps",
            Decimal(_text(headline["deep_bps"])),
            SPREAD_HEADLINE_BPS,
        ),
        ("spread_levels.bound.deep_bps", Decimal(_text(bound["deep_bps"])), SPREAD_BOUND_BPS),
        (
            "spread_levels.headline is what every criterion reads",
            "only it" in _text(headline["what_it_is"]),
            True,
        ),
        (
            "spread_levels.headline is labelled an upper bound",
            "UPPER BOUND" in _text(headline["label_required_everywhere"]),
            True,
        ),
        (
            "spread_levels.headline label carries the ratio",
            _ratio(_text(headline["label_required_everywhere"])),
            SPREAD_BOUND_RATIO,
        ),
        (
            "spread_levels.bound.never_a_criterion",
            "No criterion" in _text(bound["never_a_criterion"]),
            True,
        ),
        (
            "spread_levels.both_or_neither",
            "No table may show one level without the other" in _text(levels["both_or_neither"]),
            True,
        ),
        (
            "spread_contingent_verdict.outcome",
            _text(contingent["outcome"]),
            SPREAD_CONTINGENT,
        ),
        ("spread_contingent_verdict.applies_from", _text(contingent["applies_from"]), "F2"),
        (
            "spread_contingent_verdict.definition fails at the headline and clears at the bound",
            "FAILS its criteria at the headline" in _text(contingent["definition"]),
            True,
        ),
        (
            "spread_contingent_verdict.definition is a deferral and not a promotion",
            "not promoted" in _text(contingent["definition"]),
            True,
        ),
        (
            "spread_contingent_verdict.prevents both directions",
            "BOTH directions" in _text(contingent["what_it_prevents"]),
            True,
        ),
        ("bound_is_not_a_trial.rule_id", _text(trial["rule_id"]), "A12.6"),
        (
            "bound_is_not_a_trial.rule does not inflate the deflation count",
            "not a new trial" in _text(trial["rule"]),
            True,
        ),
        (
            "bound_is_not_a_trial.conditions",
            tuple(
                _text(item)
                for item in _sequence(trial["conditions_under_which_that_holds"], "conditions")
            )[:1],
            ("the headline level is fixed before the run, for every variant",),
        ),
        (
            "bound_is_not_a_trial.drift_guard_assertion names the headline",
            "read the HEADLINE spread level and never the bound"
            in _text(trial["drift_guard_assertion"]),
            True,
        ),
        (
            "relabel_the_assumption.rule refuses the word estimate",
            "never an estimate" in _text(relabel["rule"]),
            True,
        ),
        (
            "relabel_the_assumption.rule carries the ratio",
            _ratio(_text(relabel["rule"])),
            SPREAD_BOUND_RATIO,
        ),
        (
            "relabel_the_assumption.f1_verdict_amended_not_edited",
            "without editing the original text" in _text(relabel["f1_verdict_amended_not_edited"]),
            True,
        ),
    ]


def _band_recut_checks(raw: Mapping[str, object]) -> list[tuple[str, object, object]]:
    """Rule B1: the cut quantity is decided by a test, and the band count by separation.

    Every number is compared because the whole rule is a procedure with four thresholds
    in it, and a procedure whose thresholds move after the data is seen is an inspection
    wearing a procedure's clothes.
    """
    block = _mapping(raw["band_recut"], "band_recut")
    return [
        ("band_recut.rule_id", _text(block["rule_id"]), BAND_RECUT_RULE),
        ("band_recut.is_a_trial", bool(block["is_a_trial"]), False),
        (
            "band_recut.procedure.permutations",
            _permutations(_text(block["procedure"])),
            BAND_RECUT_PERMUTATIONS,
        ),
        ("band_recut.procedure.seed", _seed(_text(block["procedure"])), BAND_RECUT_SEED),
        ("band_recut.procedure.alpha", _alpha(_text(block["procedure"])), BAND_RECUT_ALPHA),
        (
            "band_recut.how_many_bands.minimum_symbols",
            _minimum_symbols(_text(block["how_many_bands"])),
            BAND_MINIMUM_SYMBOLS,
        ),
        (
            "band_recut.how_many_bands.separation",
            _separation(_text(block["how_many_bands"])),
            BAND_SEPARATION_FACTOR,
        ),
        (
            "band_recut.how_many_bands takes the largest that separates",
            "adopt the LARGEST number" in _text(block["how_many_bands"]),
            True,
        ),
        (
            "band_recut.candidates",
            len(_sequence(block["candidates"], "band_recut.candidates")),
            4,
        ),
        (
            "band_recut.collapse_is_an_allowed_answer",
            "One band is a legitimate outcome" in _text(block["collapse_is_an_allowed_answer"]),
            True,
        ),
        (
            "band_recut.changes_no_f1_figure",
            "applies from F2" in _text(block["changes_no_f1_figure"]),
            True,
        ),
    ]


def _extended_sample_checks(raw: Mapping[str, object]) -> list[tuple[str, object, object]]:
    """Rules M1 and H1: what is measured unconditionally, and what only on a condition."""
    block = _mapping(raw["extended_spread_sample"], "extended_spread_sample")
    historical = _mapping(raw["historical_quoted_spread"], "historical_quoted_spread")
    return [
        ("extended_spread_sample.rule_id", _text(block["rule_id"]), EXTENDED_SAMPLE_RULE),
        ("extended_spread_sample.is_a_trial", bool(block["is_a_trial"]), False),
        (
            "extended_spread_sample.requirement.per_band",
            _per_band(_text(block["requirement"])),
            EXTENDED_SAMPLE_PER_BAND,
        ),
        (
            "extended_spread_sample.requirement.days",
            _sample_days(_text(block["requirement"])),
            EXTENDED_SAMPLE_DAYS,
        ),
        (
            "extended_spread_sample.occupancy_is_computed_first",
            "BEFORE anything is downloaded" in _text(block["occupancy_is_computed_first"]),
            True,
        ),
        (
            "extended_spread_sample.an_empty_band_is_the_answer",
            "EMPTY" in _text(block["an_empty_band_is_the_answer"]),
            True,
        ),
        (
            "extended_spread_sample.dispersion_is_the_finding",
            "Do not average away" in _text(block["dispersion_is_the_finding"]),
            True,
        ),
        ("historical_quoted_spread.rule_id", _text(historical["rule_id"]), HISTORICAL_QUOTES_RULE),
        ("historical_quoted_spread.is_a_trial", bool(historical["is_a_trial"]), False),
        (
            "historical_quoted_spread.fires_only_if names the contingent outcome",
            SPREAD_CONTINGENT in _text(historical["fires_only_if"]),
            True,
        ),
        (
            "historical_quoted_spread.is not done speculatively",
            "NOT done" in _text(historical["fires_only_if"]),
            True,
        ),
        (
            "historical_quoted_spread.if_it_does_not_exist_for_the_window",
            "Do not substitute" in _text(historical["if_it_does_not_exist_for_the_window"]),
            True,
        ),
    ]


def _fx_invariant_checks(raw: Mapping[str, object]) -> list[tuple[str, object, object]]:
    """Rule A12.9: the FX charge scales with crossings and never with turnover."""
    block = _mapping(raw["fx_crossing_invariant"], "fx_crossing_invariant")
    return [
        ("fx_crossing_invariant.rule_id", _text(block["rule_id"]), "A12.9"),
        ("fx_crossing_invariant.is_a_trial", bool(block["is_a_trial"]), False),
        (
            "fx_crossing_invariant.crossings_per_run",
            int(_text(block["crossings_per_run"])),
            FX_CROSSINGS_PER_RUN,
        ),
        (
            "fx_crossing_invariant.invariant forbids scaling with turnover",
            "must not scale with turnover" in _text(block["invariant"]),
            True,
        ),
        (
            "fx_crossing_invariant.invariant says reporting is not a charge",
            "reporting, not a charge" in _text(block["invariant"]),
            True,
        ),
        (
            "fx_crossing_invariant.asserted_as_a_test",
            "doubling the number of rebalances" in _text(block["asserted_as_a_test"]),
            True,
        ),
    ]


def _ratio(prose: str) -> Decimal:
    """The headline-to-bound multiple, read out of the label that states it."""
    return Decimal(_after(prose, "approximately "))


def _permutations(prose: str) -> int:
    """How many permutations the rank test draws, read out of the procedure."""
    return int(_after(prose, "p-value from "))


def _seed(prose: str) -> int:
    """The permutation seed, read out of the procedure that names it."""
    return int(_after(prose, "permutations at seed "))


def _alpha(prose: str) -> Decimal:
    """The significance level a candidate quantity has to clear."""
    return Decimal(_after(prose, "p-value is below "))


def _minimum_symbols(prose: str) -> int:
    """How few sampled symbols a band may hold and still be a band."""
    return int(_after(prose, "at least "))


def _separation(prose: str) -> Decimal:
    """How far apart adjacent bands' measured medians must sit."""
    return Decimal(_after(prose, "a factor of "))


def _per_band(prose: str) -> int:
    """How many symbols rule M1 measures in each unmeasured band."""
    return int(_after(prose, "At least "))


def _sample_days(prose: str) -> int:
    """How many days rule M1 measures each of them over."""
    return int(_after(prose, "over at least the same "))


def _cadence_checks(variants: Mapping[str, object]) -> list[tuple[str, object, object]]:
    """Amendment 28.3's pairing and its reporting requirement.

    The pair is compared because a one-factor comparison is only one-factor if both
    members are the ones registered, and the thinner-evidence variant is compared
    because a requirement to print a rebalance count everywhere is worth nothing if
    the name it applies to can drift.
    """
    pair = _mapping(variants["one_factor_comparison"], "variants.one_factor_comparison")
    reporting = _mapping(
        variants["rebalance_count_reporting"], "variants.rebalance_count_reporting"
    )
    between = _mapping(variants["between_rebalances"], "variants.between_rebalances")
    registered_pair = _sequence(pair["pair"], "variants.one_factor_comparison.pair")
    differs = _sequence(pair["differs_in"], "variants.one_factor_comparison.differs_in")
    return [
        (
            "variants.one_factor_comparison.pair",
            tuple(_text(item) for item in registered_pair),
            CADENCE_PAIR,
        ),
        (
            "variants.one_factor_comparison.differs_in",
            tuple(_text(item) for item in differs),
            ("rebalance_months",),
        ),
        (
            "variants.rebalance_count_reporting.applies_to",
            _text(reporting["applies_to"]),
            THINNER_EVIDENCE_VARIANT,
        ),
        (
            "variants.between_rebalances.applies_to",
            _text(between["applies_to"]),
            THINNER_EVIDENCE_VARIANT,
        ),
    ]


def _cell_checks(registered: Sequence[object]) -> list[tuple[str, object, object]]:
    """Every cost cell's fees, fill mix and role, compared one field at a time."""
    if len(registered) != len(REGISTERED_CELLS):
        raise DriftedFromPreRegistration(
            f"costs.cells lists {len(registered)} cells and the code declares "
            f"{len(REGISTERED_CELLS)}."
        )
    checks: list[tuple[str, object, object]] = []
    for position, (entry, spec) in enumerate(zip(registered, REGISTERED_CELLS, strict=True)):
        block = _mapping(entry, f"costs.cells[{position}]")
        where = f"costs.cells[{position}]"
        checks.append((f"{where}.label", _text(block["label"]), spec.label))
        checks.append(
            (
                f"{where}.spot_maker_bps",
                Decimal(_text(block["spot_maker_bps"])),
                spec.spot_maker_bps,
            )
        )
        checks.append(
            (
                f"{where}.spot_taker_bps",
                Decimal(_text(block["spot_taker_bps"])),
                spec.spot_taker_bps,
            )
        )
        checks.append(
            (
                f"{where}.futures_maker_bps",
                Decimal(_text(block["futures_maker_bps"])),
                spec.futures_maker_bps,
            )
        )
        checks.append(
            (
                f"{where}.futures_taker_bps",
                Decimal(_text(block["futures_taker_bps"])),
                spec.futures_taker_bps,
            )
        )
        checks.append(
            (
                f"{where}.maker_fraction",
                Decimal(_text(block["maker_fraction"])),
                spec.maker_fraction,
            )
        )
        checks.append((f"{where}.is_headline", bool(block["is_headline"]), spec.is_headline))
        checks.append((f"{where}.runs_nulls", bool(block["runs_nulls"]), spec.runs_nulls))
        checks.append(
            (
                f"{where}.spread_and_slippage_multiplier",
                Decimal(_text(block.get("spread_and_slippage_multiplier", "1"))),
                spec.spread_and_slippage_multiplier,
            )
        )
    return checks


def _budget_checks(raw: Mapping[str, object]) -> list[tuple[str, object, object]]:
    """The declared allowance against the budget the runner will actually enforce.

    The most load-bearing block in the guard. Everything else here changes what a
    number means; this changes how many numbers may be produced before the family
    is closed.
    """
    block = _mapping(raw["trial_budget"], "trial_budget")
    declared = budget()
    registered_variants = [_text(item) for item in _sequence(block["variants"], "variants")]
    registered_cells = [_text(item) for item in _sequence(block["cost_cells"], "cost_cells")]
    return [
        ("trial_budget.family", _text(block["family"]), declared.family),
        ("trial_budget.engine_version", _text(block["engine_version"]), declared.engine_version),
        (
            "trial_budget.maximum_trials",
            _text(block["maximum_trials"]),
            str(declared.maximum_trials),
        ),
        ("trial_budget.variants", tuple(registered_variants), declared.variants),
        ("trial_budget.cost_cells", tuple(registered_cells), declared.cost_cells),
        ("trial_budget.nulls_are_charged", bool(block["nulls_are_charged"]), False),
    ]


def assert_no_drift(config_path: Path = CONFIG_PATH) -> Mapping[str, object]:
    """Compare every registered number against the constant the code will use.

    Deliberately explicit rather than reflective, and deliberately long. A guard
    that is shorter than the specification it guards is guarding part of it.
    """
    with config_path.open(encoding="utf-8") as handle:
        raw = _mapping(yaml.safe_load(handle), "the registered specification")
    account = _mapping(raw["account"], "account")
    universe = _mapping(raw["universe"], "universe")
    rules = _sequence(universe["rules_in_order"], "universe.rules_in_order")
    window = _mapping(raw["window"], "window")
    walk = _mapping(
        _mapping(window["plans"], "window.plans")["walk_forward"], "window.plans.walk_forward"
    )
    costs = _mapping(raw["costs"], "costs")
    bands = _mapping(costs["liquidity_bands"], "costs.liquidity_bands")
    spreads = _mapping(costs["spread_bps"], "costs.spread_bps")
    slippages = _mapping(costs["slippage_bps"], "costs.slippage_bps")
    fx = _mapping(costs["fx"], "costs.fx")
    variants = _mapping(raw["variants"], "variants")
    fixed = _mapping(variants["fixed_for_every_variant"], "variants.fixed_for_every_variant")
    nulls = _mapping(raw["null_experiment"], "null_experiment")
    seed_counts = _mapping(nulls["seed_counts"], "null_experiment.seed_counts")
    decay = _mapping(raw["decay"], "decay")
    per_year = _mapping(decay["per_calendar_year_table"], "decay.per_calendar_year_table")
    trend = _mapping(decay["trend"], "decay.trend")
    recent = _mapping(decay["recent_window"], "decay.recent_window")
    leverage = _mapping(raw["leverage"], "leverage")
    margin = _mapping(raw["maintenance_margin"], "maintenance_margin")
    settings = _sequence(margin["settings"], "maintenance_margin.settings")
    capacity = _mapping(raw["capacity"], "capacity")
    depth = _mapping(capacity["depth_window"], "capacity.depth_window")
    statistics = _mapping(raw["statistics"], "statistics")
    bootstrap = _mapping(statistics["bootstrap"], "statistics.bootstrap")

    checks: list[tuple[str, object, object]] = [
        ("family", _text(raw["family"]), FAMILY),
        ("account.currency", _text(account["currency"]), ACCOUNT_CURRENCY),
        ("account.equity", Decimal(_text(account["equity"])), ACCOUNT_EQUITY.amount),
        ("account.max_positions", _text(account["max_positions"]), str(MAX_POSITIONS)),
        (
            "account.margin_fraction",
            Decimal(_text(account["margin_fraction"])),
            MARGIN_FRACTION,
        ),
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
            "universe.rules_in_order.listing_age.minimum_days",
            _text(_rule(rules, "listing_age")["minimum_days"]),
            str(MIN_LISTING_AGE_DAYS),
        ),
        (
            "universe.rules_in_order.funding_evaluability.trailing_days",
            _text(_rule(rules, "funding_evaluability")["trailing_days"]),
            str(FUNDING_TRAILING_DAYS),
        ),
        (
            "universe.rules_in_order.median_quote_volume.minimum",
            Decimal(_text(_rule(rules, "median_quote_volume")["minimum"])),
            MIN_MEDIAN_QUOTE_VOLUME.amount,
        ),
        (
            "universe.rules_in_order.bar_coverage.minimum_fraction",
            Decimal(_text(_rule(rules, "bar_coverage")["minimum_fraction"])),
            MIN_BAR_COVERAGE_FRACTION,
        ),
        (
            "window.first_usable_month_lookback_days",
            _text(window["first_usable_month_lookback_days"]),
            str(FX_FIRST_MONTH_LOOKBACK_DAYS),
        ),
        (
            "window.minimum_months_to_proceed",
            _text(window["minimum_months_to_proceed"]),
            str(MINIMUM_MONTHS_TO_PROCEED),
        ),
        (
            "window.plans.walk_forward.in_sample_months",
            _text(walk["in_sample_months"]),
            str(IN_SAMPLE_MONTHS),
        ),
        ("window.plans.walk_forward.fold_count", _text(walk["fold_count"]), str(FOLD_COUNT)),
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
            "costs.haircut_is_a_loss_on_either_side",
            bool(costs["haircut_is_a_loss_on_either_side"]),
            HAIRCUT_ON_EITHER_SIDE,
        ),
        (
            "costs.funding_default_interval_hours",
            _text(costs["funding_default_interval_hours"]),
            str(DEFAULT_INTERVAL_HOURS),
        ),
        ("costs.fx.symbol", _text(fx["symbol"]), FX_SYMBOL),
        ("costs.fx.foreign_currency", _text(fx["foreign_currency"]), FOREIGN_CURRENCY),
        (
            "variants.fixed_for_every_variant.turnover_days",
            _text(fixed["turnover_days"]),
            str(FUNDING_TRAILING_DAYS),
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
        (
            "null_experiment.cells_running_nulls",
            tuple(_text(item) for item in _sequence(nulls["cells_running_nulls"], "cells")),
            tuple(cell.label for cell in REGISTERED_CELLS if cell.runs_nulls),
        ),
        (
            "decay.per_calendar_year_table.minimum_months_for_a_year",
            _text(per_year["minimum_months_for_a_year"]),
            str(MINIMUM_MONTHS_FOR_A_YEAR),
        ),
        ("decay.trend.resamples", _text(trend["resamples"]), str(RESAMPLES)),
        ("decay.trend.seed", _text(trend["seed"]), str(BOOTSTRAP_SEED)),
        ("decay.recent_window.months", _text(recent["months"]), str(RECENT_WINDOW_MONTHS)),
        (
            "leverage.margin_buffer_sweep",
            tuple(
                Decimal(_text(item))
                for item in _sequence(leverage["margin_buffer_sweep"], "margin_buffer_sweep")
            ),
            MARGIN_BUFFER_SWEEP,
        ),
        (
            "maintenance_margin.point_in_time_available",
            bool(margin["point_in_time_available"]),
            False,
        ),
        (
            "maintenance_margin.settings[0.005].status",
            _text(_setting(settings, "0.005")["status"]),
            "headline",
        ),
        (
            "maintenance_margin.settings[0.010].status",
            _text(_setting(settings, "0.010")["status"]),
            "companion",
        ),
        (
            "maintenance_margin.settings[0.025].status",
            _text(_setting(settings, "0.025")["status"]),
            "stress",
        ),
        (
            "maintenance_margin.settings",
            tuple(Decimal(_text(_mapping(entry, "setting")["rate"])) for entry in settings),
            MAINTENANCE_MARGINS,
        ),
        ("capacity.depth_window.starts", _text(depth["starts"]), DEPTH_WINDOW_STARTS),
        ("capacity.depth_window.ends", _text(depth["ends"]), DEPTH_WINDOW_ENDS),
        ("statistics.bootstrap.resamples", _text(bootstrap["resamples"]), str(RESAMPLES)),
        ("statistics.bootstrap.seed", _text(bootstrap["seed"]), str(BOOTSTRAP_SEED)),
        ("statistics.periods_per_year", _text(statistics["periods_per_year"]), "12"),
    ]
    checks.extend(_capacity_rule_checks(capacity))
    checks.extend(_contraction_checks(raw))
    checks.extend(_break_even_checks(raw))
    checks.extend(_sensitivity_checks(raw))
    checks.extend(_budget_checks(raw))
    checks.extend(_variant_checks(_sequence(variants["registered"], "variants.registered")))
    checks.extend(_cadence_checks(variants))
    checks.append(("version", _text(raw["version"]), REGISTERED_VERSION))
    checks.extend(_void_run_checks(raw))
    checks.extend(_spread_trigger_checks(raw))
    checks.extend(_every_family_checks(raw))
    checks.extend(_pairing_checks(raw))
    checks.extend(_estimator_checks(raw))
    checks.extend(_spread_level_checks(raw))
    checks.extend(_band_recut_checks(raw))
    checks.extend(_extended_sample_checks(raw))
    checks.extend(_fx_invariant_checks(raw))
    checks.extend(_cell_checks(_sequence(costs["cells"], "costs.cells")))
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
            "The code no longer matches the registered specification. Nothing may run until "
            "this is resolved by a new pre-registration version, never by editing the old "
            "one:" + NEWLINE + NEWLINE.join(f"  - {item}" for item in drifted)
        )
    headline_cell()
    version = _text(raw["version"])
    print(f"[f1] pre-registration {version} verified against the code: {len(checks)} numbers")
    return raw


def _sensitivity_checks(raw: Mapping[str, object]) -> list[tuple[str, object, object]]:
    """Amendment 26.2's registered schedule against the constant the runner uses.

    Checked as strictly as a grid cell even though no criterion reads it. A
    sensitivity nobody verified is a sensitivity that can quietly become the
    flattering number instead of the honest one.
    """
    block = _mapping(raw["execution_sensitivity"], "execution_sensitivity")
    cell = execution_sensitivity()
    where = "execution_sensitivity"
    return [
        (f"{where}.label", _text(block["label"]), cell.label),
        (
            f"{where}.spot_maker_bps",
            Decimal(_text(block["spot_maker_bps"])),
            cell.spot_maker_bps,
        ),
        (
            f"{where}.spot_taker_bps",
            Decimal(_text(block["spot_taker_bps"])),
            cell.spot_taker_bps,
        ),
        (
            f"{where}.futures_maker_bps",
            Decimal(_text(block["futures_maker_bps"])),
            cell.futures_maker_bps,
        ),
        (
            f"{where}.futures_taker_bps",
            Decimal(_text(block["futures_taker_bps"])),
            cell.futures_taker_bps,
        ),
        (
            f"{where}.maker_fraction",
            Decimal(_text(block["maker_fraction"])),
            cell.maker_fraction,
        ),
        (f"{where}.is_a_grid_cell", bool(block["is_a_grid_cell"]), False),
        (f"{where}.consumes_variant_budget", bool(block["consumes_variant_budget"]), False),
        (f"{where}.recorded_in_the_registry", bool(block["recorded_in_the_registry"]), True),
        (f"{where}.read_by_any_criterion", bool(block["read_by_any_criterion"]), False),
        (
            f"{where}.round_trip_fee_arithmetic.execution_venue_bps",
            Decimal(
                _text(
                    _mapping(block["round_trip_fee_arithmetic"], f"{where}.round_trip")[
                        "execution_venue_bps"
                    ]
                )
            ),
            round_trip_fee_bps(cell),
        ),
        (
            f"{where}.round_trip_fee_arithmetic.research_venue_bps",
            Decimal(
                _text(
                    _mapping(block["round_trip_fee_arithmetic"], f"{where}.round_trip")[
                        "research_venue_bps"
                    ]
                )
            ),
            round_trip_fee_bps(headline_cell()),
        ),
        (
            f"{where}.is_not_in_the_grid",
            cell.label in cell_labels(),
            False,
        ),
    ]


def _contraction_checks(raw: Mapping[str, object]) -> list[tuple[str, object, object]]:
    """Amendment 26.1's registered method, which must be one method for all three."""
    block = _mapping(raw["contraction_check"], "contraction_check")
    attributes = tuple(
        _text(_mapping(entry, "contraction_check.attributes entry")["name"])
        for entry in _sequence(block["attributes"], "contraction_check.attributes")
    )
    return [
        ("contraction_check.attributes", attributes, CONTRACTION_ATTRIBUTES),
        ("contraction_check.resamples", _text(block["resamples"]), str(RESAMPLES)),
        ("contraction_check.seed", _text(block["seed"]), str(BOOTSTRAP_SEED)),
        ("contraction_check.difference_method", _text(block["difference_method"]), "bootstrap"),
    ]


def _break_even_checks(raw: Mapping[str, object]) -> list[tuple[str, object, object]]:
    """Amendment 27's registered arithmetic, against the constants the runner uses.

    The two fee-of-equity figures are checked because they are the denominators of
    every number this amendment prints. A denominator nobody verified is a number that
    can drift into flattering a conclusion without anybody editing a threshold.
    """
    block = _mapping(raw["break_even"], "break_even")
    expectation = _mapping(block["declared_expectation"], "break_even.declared_expectation")
    clauses = _sequence(expectation["clauses"], "break_even.declared_expectation.clauses")
    second = _mapping(clauses[1], "break_even.declared_expectation.clauses[1]")
    return [
        (
            "break_even.research_fee_of_equity_bps",
            Decimal(_text(block["research_fee_of_equity_bps"])),
            RESEARCH_FEE_OF_EQUITY_BPS.quantize(Decimal("0.01")),
        ),
        (
            "break_even.execution_fee_of_equity_bps",
            Decimal(_text(block["execution_fee_of_equity_bps"])),
            EXECUTION_FEE_OF_EQUITY_BPS,
        ),
        ("break_even.is_a_criterion", bool(block["is_a_criterion"]), False),
        ("break_even.consumes_variant_budget", bool(block["consumes_variant_budget"]), False),
        ("break_even.declared_expectation.id", _text(expectation["id"]), "D2"),
        (
            "break_even.declared_expectation.carries_verdict_weight",
            bool(expectation["carries_verdict_weight"]),
            False,
        ),
        (
            "break_even.declared_expectation.clauses[0].id",
            _text(_mapping(clauses[0], "D2a")["id"]),
            "D2a",
        ),
        ("break_even.declared_expectation.clauses[1].id", _text(second["id"]), "D2b"),
        (
            "break_even.declared_expectation.clauses[1].threshold",
            _text(second["threshold"]),
            str(int(D2B_THRESHOLD_ROUND_TRIPS)),
        ),
    ]


def _capacity_rule_checks(capacity: Mapping[str, object]) -> list[tuple[str, object, object]]:
    """Rule C3's floor, which decides when capacity is reported as unestablished."""
    for entry in _sequence(capacity["rules"], "capacity.rules"):
        block = _mapping(entry, "capacity.rules entry")
        if _text(block.get("id")) == "C3":
            return [
                (
                    "capacity.rules[C3].minimum_depth_months",
                    _text(block["minimum_depth_months"]),
                    str(MINIMUM_DEPTH_MONTHS),
                )
            ]
    raise DriftedFromPreRegistration("capacity.rules does not contain rule C3.")


# ---------------------------------------------------------------------------
# Provenance: the ordering a reader can verify without trusting the runner
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RegistrationProvenance:
    """What the run recorded about the specification it read.

    Written into the results so that the ordering claim in the report is not a
    sentence somebody typed but a SHA a reader can check out.
    """

    config_path: str
    config_commit: Commit
    head_sha: str
    head_committed_at: Timestamp

    def as_json(self) -> dict[str, object]:
        return {
            "config_path": self.config_path,
            "config_commit_sha": self.config_commit.sha,
            "config_committed_at": self.config_commit.committed_at.isoformat(),
            "config_commit_subject": self.config_commit.subject,
            "config_was_clean_at_run_time": True,
            "head_sha_at_run_time": self.head_sha,
            "head_committed_at": self.head_committed_at.isoformat(),
        }


def registration_provenance(
    *, root: Path, config_path: Path = CONFIG_PATH
) -> RegistrationProvenance:
    """Refuse to start unless the specification is committed, and record its commit.

    Called before the world is built and before anything is computed. An untracked
    or dirty configuration raises
    :class:`~sextant.adapters.vcs.NotCommitted`, because in either case there is no
    committed SHA that describes the bytes this run would read, and a report citing
    one anyway would be making a false statement about the ordering.
    """
    repository = GitRepository(root=root)
    commit = repository.require_committed(config_path)
    head = repository.head()
    return RegistrationProvenance(
        config_path=config_path.as_posix(),
        config_commit=commit,
        head_sha=head.sha,
        head_committed_at=head.committed_at,
    )


@dataclass(frozen=True, slots=True)
class OrderingAudit:
    """Whether the specification demonstrably predates the numbers.

    Produced after the results have been committed, because the SHA of the commit
    that introduces a results file does not exist while the file is being written.
    Running this too early answers "not yet verifiable" rather than pretending.
    """

    config_path: str
    config_commit: Commit
    results_path: str
    results_commit: Commit | None
    config_precedes_results: bool | None

    @property
    def is_verified(self) -> bool:
        """True only when both commits exist and the config's comes first."""
        return self.results_commit is not None and self.config_precedes_results is True

    def lines(self) -> tuple[str, ...]:
        """The rows the report quotes, one fact each."""
        rows = [
            f"registered specification: {self.config_path}",
            f"specification commit: {self.config_commit.cite()}",
            f"results: {self.results_path}",
        ]
        if self.results_commit is None:
            rows.append(
                "results commit: NOT YET COMMITTED. The ordering is not verifiable until the "
                "results are committed; this audit must be rerun afterwards and its output is "
                "what the report quotes."
            )
            rows.append("ordering verified: no, and the reason is stated above")
            return tuple(rows)
        rows.append(f"results commit: {self.results_commit.cite()}")
        rows.append(
            "specification commit is an ancestor of the results commit: "
            f"{'yes' if self.config_precedes_results else 'NO'}"
        )
        rows.append(
            "ordering verified: "
            + (
                "yes. The specification existed in the history before the numbers did, and "
                "two git commands confirm it without taking anyone's word for it."
                if self.is_verified
                else "NO. The specification's commit is not an ancestor of the results' commit, "
                "so this result may not be reported as pre-registered."
            )
        )
        return tuple(rows)


def ordering_audit(
    *,
    root: Path,
    config_path: Path = CONFIG_PATH,
    results_path: Path = RESULTS_PATH,
) -> OrderingAudit:
    """The two commits and the ancestry between them, for the report to quote."""
    repository = GitRepository(root=root)
    config_commit = repository.require_committed(config_path)
    results_commit = repository.last_commit_touching(results_path)
    precedes = (
        None
        if results_commit is None
        else repository.is_ancestor(config_commit.sha, results_commit.sha)
    )
    return OrderingAudit(
        config_path=config_path.as_posix(),
        config_commit=config_commit,
        results_path=results_path.as_posix(),
        results_commit=results_commit,
        config_precedes_results=precedes,
    )


def variant_labels() -> tuple[str, ...]:
    """The registered variant identifiers, in registered order."""
    return tuple(variant.label for variant in REGISTERED_VARIANTS)


def cell_labels() -> tuple[str, ...]:
    """The registered cost cell identifiers, in registered order."""
    return tuple(cell.label for cell in REGISTERED_CELLS)


def registered_grid() -> Iterable[tuple[str, str]]:
    """Every trial the budget covers, in the order the runner will charge them."""
    for cell in REGISTERED_CELLS:
        for variant in REGISTERED_VARIANTS:
            yield variant.label, cell.label


__all__ = [
    "ACCOUNT_CURRENCY",
    "ACCOUNT_EQUITY",
    "BAND_MINIMUM_SYMBOLS",
    "BAND_RECUT_ALPHA",
    "BAND_RECUT_PERMUTATIONS",
    "BAND_RECUT_RULE",
    "BAND_RECUT_SEED",
    "BAND_SEPARATION_FACTOR",
    "BOOTSTRAP_SEED",
    "CADENCE_PAIR",
    "CONFIG_PATH",
    "CONVERSION_BPS",
    "CRITERION_ONE_STRENGTHENED_FROM",
    "D2B_THRESHOLD_ROUND_TRIPS",
    "ENGINE_LOOKBACK_DAYS",
    "ENGINE_VERSION",
    "ESTIMATOR_COMPARISON_ID",
    "ESTIMATOR_FACTOR_LOG2",
    "ESTIMATOR_ID",
    "ESTIMATOR_MINIMUM_PAIRS",
    "ESTIMATOR_POSITIVE_SHARE",
    "ESTIMATOR_RANK_FLOOR",
    "ESTIMATOR_RULE",
    "ESTIMATOR_TRAILING_DAYS",
    "EXACT_RESCUE_TEST_FROM",
    "EXECUTION_FEE_OF_EQUITY_BPS",
    "EXTENDED_SAMPLE_DAYS",
    "EXTENDED_SAMPLE_PER_BAND",
    "EXTENDED_SAMPLE_RULE",
    "FAMILY",
    "FLOOR_ANCHOR",
    "FLOOR_FROM",
    "FOLD_COUNT",
    "FX_CROSSINGS_PER_RUN",
    "HAIRCUT_FRACTION",
    "HAIRCUT_ON_EITHER_SIDE",
    "HISTORICAL_QUOTES_RULE",
    "IN_SAMPLE_MONTHS",
    "MARGIN_FRACTION",
    "MAXIMUM_RE_EXECUTIONS",
    "MINIMUM_MONTHS_FOR_A_YEAR",
    "PAIRING_RULE",
    "RECENT_WINDOW_MONTHS",
    "REGISTERED_CELLS",
    "REGISTERED_VARIANTS",
    "REGISTERED_VERSION",
    "RESAMPLES",
    "RESEARCH_FEE_OF_EQUITY_BPS",
    "RESULTS_PATH",
    "SPREAD_BOUND_BPS",
    "SPREAD_BOUND_RATIO",
    "SPREAD_CONTINGENT",
    "SPREAD_HEADLINE_BPS",
    "SPREAD_LEVEL_BOUND",
    "SPREAD_LEVEL_HEADLINE",
    "SPREAD_SAMPLE_SYMBOL_DAYS",
    "SPREAD_TRIGGER_BAR",
    "SPREAD_TRIGGER_RULE",
    "SPREAD_TRIGGER_THRESHOLD",
    "THINNER_EVIDENCE_VARIANT",
    "VOID_DECLARED_BY_ROLE",
    "VOID_NEVER_DECLARED_BY_ROLE",
    "CellSpec",
    "DriftedFromPreRegistration",
    "OrderingAudit",
    "RegistrationProvenance",
    "VariantSpec",
    "assert_no_drift",
    "budget",
    "cell_labels",
    "execution_sensitivity",
    "headline_cell",
    "ordering_audit",
    "registered_grid",
    "registration_provenance",
    "round_trip_fee_bps",
    "variant_labels",
]
