"""Reducing F1's result file to the six criteria and the verdict's inputs.

Pure over the **saved JSON**, not over the live run. That is deliberate and it is
the one design decision in this module worth arguing for: the grid costs hours of
wall clock, and an analysis that could only run inside it would make every reporting
fix cost another night. Everything here is recomputable from
``research/spike-006-f1.json`` alone, which is also what makes the file a thing a
competent stranger can check rather than a thing they have to trust.

Nothing here decides anything. Every threshold arrives from
:mod:`sextant.app.spike_006_f1`; every criterion is section 11's, in section 11's
words; and a criterion that cannot be evaluated answers ``None`` rather than
``False``. The difference matters: ``False`` says the variant failed, ``None`` says
the data could not say, and collapsing them is how a (C) becomes a (B).

Why the statistics are recomputed rather than read
--------------------------------------------------

The Deflated Sharpe Ratio needs skewness, kurtosis and the per-observation Sharpe.
Those are recomputed here from the monthly return series the file carries, rather
than stored alongside it, so there is exactly one path from returns to statistics
and no chance of a stored summary describing a different series than the one printed
beside it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from math import sqrt

from sextant.app.spike_006_f1 import (
    ACCOUNT_EQUITY,
    CONVERSION_BPS,
    DEPTH_WINDOW_ENDS,
    DEPTH_WINDOW_STARTS,
    EXECUTION_FEE_OF_EQUITY_BPS,
    FX_CROSSINGS_PER_RUN,
    MINIMUM_DEPTH_MONTHS,
    MINIMUM_MONTHS_TO_PROCEED,
    RECENT_WINDOW_MONTHS,
    REGISTERED_CELLS,
    REGISTERED_VARIANTS,
    RESEARCH_FEE_OF_EQUITY_BPS,
    SPREAD_LEVEL_BOUND,
    SPREAD_LEVEL_HEADLINE,
    SPREAD_SAMPLE_SYMBOL_DAYS,
    SPREAD_TRIGGER_RULE,
    SPREAD_TRIGGER_THRESHOLD,
    execution_sensitivity,
)
from sextant.app.spike_006_f1_engine import recent_window, statistics_of
from sextant.domain.time import Timestamp
from sextant.engine.execution.breakeven import BreakEven, BreakEvenUndefined
from sextant.engine.regime.segmentation import Regime, conclusive
from sextant.engine.statistics.dsr import (
    DeflatedSharpeResult,
    corrected_sharpe_standard_error,
    deflated_sharpe_ratio,
)
from sextant.engine.statistics.independence import (
    MINIMUM_EFFECTIVE_OBSERVATIONS,
    independence_of,
)
from sextant.engine.statistics.metrics import PerformanceStatistics

#: Amendment 10. The family from which rule S1 requires the counterfactual to clear
#: the null by at least one standard error of its own Sharpe estimate, rather than
#: merely to clear it. Stated as a principle rather than a euro figure, and applying
#: prospectively: F1 is judged by the bar that was registered when it ran.
FLOOR_APPLIES_FROM = "F2"

#: Amendment 11, section 33.1. Rule S1's floor is anchored to ZERO, not to the null. The
#: exposure-matched null over this window is itself losing, so its 95th percentile sits
#: at -1.26 to -1.58 and a bar anchored to it is a bar below zero that a variant merely
#: failing to lose will clear. Held as a named constant so a reported figure can say
#: which anchor produced it, and so amendment 10's form stays computable beside it.
FLOOR_ANCHOR = "zero"

#: Amendment 11, section 33.3. The family from which criterion 1's absolute clause is
#: "the mean return exceeds one standard error of itself" rather than "strictly
#: positive". Strictly narrowing, so it can only ever remove a pass. F1 was judged on the
#: weaker form and keeps it; the stronger one is reported beside it as supplementary.
CRITERION_ONE_STRENGTHENED_FROM = "F2"

#: What the strengthened clause compares the t-statistic against. One standard error, so
#: one. Named rather than inlined because a threshold that appears as a bare literal in a
#: comparison is a threshold nobody can find later.
T_STATISTIC_FLOOR = 1.0

#: Basis points in one unit, for turning a charge back into the rate that produced it.
BASIS_POINTS = Decimal(10_000)

#: Criterion 2's threshold, section 11.
DSR_THRESHOLD = 0.95

#: Criterion 3's threshold: the share of the combined excess the selection effect
#: must account for.
SELECTION_SHARE = Decimal("0.50")

#: Criterion 4's thresholds.
MINIMUM_REGIMES = 3
MINIMUM_MONTHS_PER_REGIME = 6

#: The percentile every null is read at for criteria 1 and 6.
NULL_PERCENTILE = "95.0"

#: The construct suffixes the decomposition reads.
SELECTION_ONLY = "selection-only"
TIMING_NULL = "timing-null"
EXPOSURE_MATCHED = "exposure-matched"
EQUAL_WEIGHT_PASSIVE = "equal-weight-passive"


class ResultsIncomplete(RuntimeError):
    """The result file does not carry what a criterion needs. Nothing is guessed."""


# ---------------------------------------------------------------------------
# Reading the file
# ---------------------------------------------------------------------------


def _rows(payload: Mapping[str, object], key: str) -> tuple[Mapping[str, object], ...]:
    """One list of records out of the result file, as typed mappings."""
    value = payload.get(key)
    if not isinstance(value, list):
        raise ResultsIncomplete(f"{key} is not a list in the result file.")
    out: list[Mapping[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ResultsIncomplete(f"an entry of {key} is not a record.")
        out.append({str(name): field for name, field in item.items()})
    return tuple(out)


def _text(value: object) -> str:
    """A scalar as text, so nothing depends on how JSON typed it."""
    return str(value)


def _decimal(value: object) -> Decimal:
    """A money or return figure, exactly as it was written."""
    return Decimal(_text(value))


def _optional_float(value: object) -> float | None:
    """A statistic that may legitimately be absent."""
    return None if value is None else float(_text(value))


def monthly_of(row: Mapping[str, object]) -> tuple[tuple[Timestamp, Decimal], ...]:
    """The monthly net return series of one run, in instant order."""
    raw = row.get("monthly_returns")
    if not isinstance(raw, dict):
        raise ResultsIncomplete(f"{row.get('construct')} carries no monthly returns.")
    series = [(Timestamp.parse(str(at)), Decimal(str(value))) for at, value in raw.items()]
    return tuple(sorted(series, key=lambda item: item[0]))


def opening_instants(
    monthly: Sequence[tuple[Timestamp, Decimal]],
) -> Mapping[Timestamp, Timestamp]:
    """For each closing instant, the instant its holding period opened.

    A month's return is keyed by the instant it closed; its regime is the label
    that was knowable when the position was opened. Keying the regime to the close
    would let a month be attributed to a state that only became visible after the
    decision, which is the look-ahead the cascade exists to avoid.
    """
    instants = [at for at, _ in monthly]
    return {instants[index]: instants[index - 1] for index in range(1, len(instants))}


def regime_labels(payload: Mapping[str, object]) -> Mapping[Timestamp, str]:
    """The cascade's label at every instant it classified."""
    return {
        Timestamp.parse(_text(row["at"])): _text(row["regime"]) for row in _rows(payload, "regimes")
    }


# ---------------------------------------------------------------------------
# One variant in one cell
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Decomposition:
    """Where a variant's return came from, section 10's four ways."""

    combined: Decimal
    timing_effect: Decimal | None
    selection_effect: Decimal | None
    funding: Decimal
    price: Decimal
    """The two legs' price move, funding aside. The ledger's own ``gross_pnl``."""
    gross: Decimal
    """The carry before every charge: the price move **plus** the funding stream.

    Deliberately not serialised as ``gross_pnl``. The ledger uses that name for the
    price move alone, and a file carrying one name for two quantities is a file whose
    two blocks contradict each other.
    """
    charges: Decimal
    """Fees, spread, slippage, conversion and delisting. Funding is not in here."""
    costs: Decimal
    """The ledger's own cost total, which *is* net of the funding receipt."""

    @property
    def net(self) -> Decimal:
        """Price plus funding, less charges. Equals the ledger's net by construction."""
        return self.price + self.funding - self.charges

    @property
    def funding_share(self) -> Decimal | None:
        """Funding as a share of the combined net return, or None at zero net."""
        if self.combined == 0:
            return None
        return self.funding / self.combined

    def as_json(self) -> dict[str, object]:
        return {
            "combined_net_return": str(self.combined),
            "timing_effect_net_return": None
            if self.timing_effect is None
            else str(self.timing_effect),
            "selection_effect_net_return": None
            if self.selection_effect is None
            else str(self.selection_effect),
            "funding_received_net": str(self.funding),
            "price_pnl": str(self.price),
            "carry_before_costs": str(self.gross),
            "charges_excluding_funding": str(self.charges),
            "net_pnl": str(self.net),
            "total_costs_net_of_funding": str(self.costs),
            "funding_share_of_combined": None
            if self.funding_share is None
            else str(self.funding_share),
            "note": (
                "Funding is the return, not a cost line. The identity is price_pnl plus "
                "funding_received_net less charges_excluding_funding equals net_pnl. "
                "carry_before_costs is the first two added, and total_costs_net_of_funding "
                "is the ledger's own total, which already nets the funding receipt: "
                "subtracting it from the carry would count funding twice. A funding share "
                "above one means the settlement stream earned more than the book kept, and "
                "the difference is the basis and the charges."
            ),
        }


@dataclass(frozen=True, slots=True)
class Criteria:
    """Section 11's six criteria for one variant in one cell, each answered."""

    beats_exposure_matched_null: bool | None
    survives_deflation: bool | None
    win_is_selection: bool | None
    regime_stability: bool | None
    sign_stable_across_cells: bool | None
    recent_window_holds: bool | None
    regimes_positive: int
    regimes_countable: tuple[str, ...]
    effective_observations: float
    return_t_statistic: float | None
    """The mean scored-month return over one standard error of that mean.

    Carried for the SUPPLEMENTARY reading of criterion 1 that amendment 11 registers from
    F2, and for nothing else in F1: :attr:`answered` does not include it and
    :attr:`all_hold` cannot read it. F1 was judged on the weaker clause and is not
    re-scored on a bar registered after its figures were read.
    """

    @property
    def answered(self) -> tuple[bool | None, ...]:
        return (
            self.beats_exposure_matched_null,
            self.survives_deflation,
            self.win_is_selection,
            self.regime_stability,
            self.sign_stable_across_cells,
            self.recent_window_holds,
        )

    @property
    def all_hold(self) -> bool:
        """True only when every one of the six is answered True.

        Reads :attr:`answered`, which is criterion 1 in the form F1 was judged on. The
        strengthened clause below is deliberately not in it.
        """
        return all(item is True for item in self.answered)

    @property
    def return_is_distinguishable_from_zero(self) -> bool | None:
        """Whether the mean return exceeds one standard error of itself."""
        if self.return_t_statistic is None:
            return None
        return self.return_t_statistic > T_STATISTIC_FLOOR

    @property
    def criterion_one_strengthened(self) -> bool | None:
        """Criterion 1 with amendment 11's absolute clause, applying from F2.

        The null comparison unchanged, and "strictly positive net return" replaced by "a
        mean return exceeding one standard error of itself". Strictly narrower than the
        clause it replaces, so on any data it can only ever turn a True into a False.
        Reported for F1 as a supplementary reading and read by no F1 verdict.
        """
        distinguishable = self.return_is_distinguishable_from_zero
        if self.beats_exposure_matched_null is None or distinguishable is None:
            return None
        return self.beats_exposure_matched_null and distinguishable

    @property
    def effective_observations_met(self) -> bool:
        return self.effective_observations >= MINIMUM_EFFECTIVE_OBSERVATIONS

    def as_json(self) -> dict[str, object]:
        return {
            "1_beats_exposure_matched_null_and_is_positive": self.beats_exposure_matched_null,
            "2_survives_deflation": self.survives_deflation,
            "3_win_is_selection": self.win_is_selection,
            "4_regime_stability": self.regime_stability,
            "4_regimes_positive": self.regimes_positive,
            "4_regimes_countable": list(self.regimes_countable),
            "5_sign_stable_across_cost_regimes": self.sign_stable_across_cells,
            "6_recent_window_holds": self.recent_window_holds,
            "all_six_hold": self.all_hold,
            "effective_observations": self.effective_observations,
            "effective_observations_floor": MINIMUM_EFFECTIVE_OBSERVATIONS,
            "effective_observations_met": self.effective_observations_met,
            "supplementary_1_strengthened": {
                "applies_from": CRITERION_ONE_STRENGTHENED_FROM,
                "mean_return_t_statistic": self.return_t_statistic,
                "t_statistic_floor": T_STATISTIC_FLOOR,
                "return_is_distinguishable_from_zero": (self.return_is_distinguishable_from_zero),
                "criterion_1_would_hold": self.criterion_one_strengthened,
                "note": (
                    "SUPPLEMENTARY. F1 was judged on criterion 1 as registered when it ran: "
                    "the null comparison AND a strictly positive net return. Amendment 11 "
                    "strengthens the absolute clause from F2 to require a mean return "
                    "exceeding one standard error of itself. The strengthened clause is "
                    "strictly narrower, so it can only ever remove a pass, and it is "
                    "reported here beside the original rather than in place of it. No F1 "
                    "verdict reads it."
                ),
            },
        }


@dataclass(frozen=True, slots=True)
class VariantRow:
    """Everything the report prints for one variant in one cell."""

    variant: str
    cell: str
    fill_mix: str
    rebalances: int | None
    idle_months: int | None
    net_return: Decimal
    statistics: PerformanceStatistics | None
    recent_statistics: PerformanceStatistics | None
    recent_months: int
    recent_net_return: Decimal
    null_p95: float | None
    recent_null_p95: float | None
    deflated: DeflatedSharpeResult | None
    decomposition: Decomposition
    regime_returns: Mapping[str, tuple[Decimal, int]]
    break_even: BreakEven | None
    criteria: Criteria

    @property
    def sharpe(self) -> float | None:
        return None if self.statistics is None else self.statistics.sharpe_annualised

    def as_json(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "variant": self.variant,
            "cell_id": self.cell,
            "fill_mix": self.fill_mix,
            "net_return": str(self.net_return),
            "sharpe_annualised": self.sharpe,
            "sharpe_standard_error": (
                None if self.statistics is None else self.statistics.sharpe_standard_error
            ),
            "observations": None if self.statistics is None else self.statistics.observations,
            "skewness": None if self.statistics is None else self.statistics.skewness,
            "kurtosis": None if self.statistics is None else self.statistics.kurtosis,
            "exposure_matched_null_p95": self.null_p95,
            "recent_window": {
                "months": self.recent_months,
                "net_return": str(self.recent_net_return),
                "sharpe_annualised": (
                    None
                    if self.recent_statistics is None
                    else self.recent_statistics.sharpe_annualised
                ),
                "null_p95_same_draws": self.recent_null_p95,
            },
            "deflated_sharpe": None if self.deflated is None else _dsr_json(self.deflated),
            "decomposition": self.decomposition.as_json(),
            "regime_returns": {
                label: {"net_return": str(value), "months": months}
                for label, (value, months) in sorted(self.regime_returns.items())
            },
            "break_even": None if self.break_even is None else self.break_even.as_json(),
            "criteria": self.criteria.as_json(),
        }
        if self.rebalances is not None:
            payload["rebalances"] = self.rebalances
            payload["rebalance_count_note"] = (
                f"{self.rebalances} rebalances, against roughly three times that for its "
                "monthly siblings on the same window. Section 28.3 requires this figure "
                "beside the result wherever the result appears."
            )
        if self.idle_months is not None:
            payload["idle_months_before_first_rebalance"] = self.idle_months
        return payload


def _dsr_json(result: DeflatedSharpeResult) -> dict[str, object]:
    """Every input to the DSR beside its output.

    The DSR is one number that four assumptions feed into - the trial count, the
    measured variance of the trial Sharpes, the observation count and the shape of
    the return distribution - and a report showing only the output invites exactly
    the argument this project exists to avoid.
    """
    return {
        "trials": result.trials,
        "trial_sharpe_variance": result.trial_sharpe_variance,
        "expected_maximum_sharpe_per_period": result.expected_maximum_sharpe_per_period,
        "probabilistic_sharpe_ratio": result.probabilistic_sharpe_ratio,
        "deflated_sharpe_ratio": result.deflated_sharpe_ratio,
        "threshold": DSR_THRESHOLD,
        "observations": result.observations,
        "skewness": result.skewness,
        "kurtosis": result.kurtosis,
        "autocorrelation_corrected": result.autocorrelation_corrected,
    }


# ---------------------------------------------------------------------------
# The reductions
# ---------------------------------------------------------------------------


def _percentile(row: Mapping[str, object] | None, block: str) -> float | None:
    """The registered percentile out of one null's summary, or None if absent."""
    if row is None:
        return None
    sharpe = row.get(block)
    if not isinstance(sharpe, dict):
        return None
    percentiles = sharpe.get("percentiles")
    if not isinstance(percentiles, dict):
        return None
    entry = percentiles.get(NULL_PERCENTILE)
    if not isinstance(entry, dict):
        return None
    return _optional_float(entry.get("value"))


def _variance(row: Mapping[str, object] | None) -> float | None:
    """The measured variance of the null's Sharpe distribution, the DSR's input."""
    if row is None:
        return None
    sharpe = row.get("sharpe")
    if not isinstance(sharpe, dict):
        return None
    return _optional_float(sharpe.get("variance"))


@dataclass(frozen=True, slots=True)
class CostLines:
    """One run's cost breakdown, as the break-even arithmetic needs to see it."""

    market_gain: Decimal
    """The ledger's gross PnL: the market move on what was held, funding aside."""
    funding_received: Decimal
    """The funding stream as a receipt. The ledger records a positive cost when the
    book paid and a negative one when it was paid, so the sign is flipped here and
    the field is named for what it actually is."""
    fees: Decimal
    """Exchange fees alone. What amendment 27's turnover figure inverts."""
    spread: Decimal
    slippage: Decimal
    conversion: Decimal
    delisting: Decimal
    total: Decimal

    @property
    def gross_before_costs(self) -> Decimal:
        """The carry, before every charge: the market move plus the funding stream.

        Funding belongs on this side of the line because it is the return this
        family exists to harvest, not a cost. Amendment 27's numerator.
        """
        return self.market_gain + self.funding_received

    @property
    def charges(self) -> Decimal:
        """Everything the book paid to hold and trade, funding excluded.

        ``total`` is not this figure. The ledger records funding as a cost line and a
        receipt is a negative one, so ``total`` is already net of the carry. Subtracting
        ``total`` from a carry that also contains the funding counts the funding twice,
        with opposite signs, and produces a number that is not any quantity at all.
        """
        return self.fees + self.spread + self.slippage + self.conversion + self.delisting

    @property
    def net(self) -> Decimal:
        """The identity the page prints: price plus funding, less what was charged."""
        return self.market_gain + self.funding_received - self.charges

    @property
    def assumed(self) -> Decimal:
        """Spread and slippage: the two charge lines that are configured, not observed.

        Invariant 12 names them assumptions and the report labels them everywhere. This
        property exists so the share of the toll that rests on an assumption can be
        stated as a number rather than left for a reader to add up.
        """
        return self.spread + self.slippage

    @property
    def contractual(self) -> Decimal:
        """Fees, conversion and the delisting haircut: everything not assumed.

        The conversion charge sits here rather than with the assumptions because its
        rate is a published one, even though it is applied under a policy. The haircut
        is a registered stress rather than a schedule, and is named where it is used.
        """
        return self.fees + self.conversion + self.delisting

    def net_at(self, multiplier: Decimal) -> Decimal:
        """Net PnL with spread and slippage scaled, the book held exactly as it ran.

        Exact in the charges and approximate in the path. No registered variant reads a
        cost when it decides, so a cheaper world would have traded the same pairs in the
        same weights; but it would have compounded a larger equity into every later
        position, so the realised figure at a lower assumption would be slightly better
        than this arithmetic. It is a sensitivity, and it is never a result.
        """
        return (
            self.market_gain + self.funding_received - self.contractual - multiplier * self.assumed
        )

    @property
    def flip_multiplier(self) -> Decimal | None:
        """The spread-and-slippage multiplier at which this run breaks even.

        Below 1 means a cheaper assumption would turn the sign; at or below 0 means the
        run loses even with spread and slippage deleted entirely, which is a stronger
        statement than any sensitivity. None where nothing was charged on either line.
        """
        if self.assumed == 0:
            return None
        return (self.market_gain + self.funding_received - self.contractual) / self.assumed


def _costs_of(row: Mapping[str, object]) -> CostLines:
    """One run's cost breakdown, itemised, never collapsed to a total."""
    costs = row.get("costs")
    if not isinstance(costs, dict):
        raise ResultsIncomplete(f"{row.get('construct')} carries no cost breakdown.")
    return CostLines(
        market_gain=_decimal(row.get("gross_pnl", "0")),
        funding_received=-_decimal(costs.get("funding", "0")),
        fees=_decimal(costs.get("fees", "0")),
        spread=_decimal(costs.get("spread", "0")),
        slippage=_decimal(costs.get("slippage", "0")),
        conversion=_decimal(costs.get("fx_conversion", "0")),
        delisting=_decimal(costs.get("delisting", "0")),
        total=_decimal(costs.get("total", "0")),
    )


def break_even_of(lines: CostLines, months: int) -> BreakEven | None:
    """Amendment 27's three numbers for one run, or None where they cannot form.

    **The numerator is compounded**, as 27.2 registers it: the gross carry as a
    fraction of starting equity, raised to the reciprocal of the years scored.
    **The fee line is annualised arithmetically**, because fees are an additive flow
    per round trip rather than a compounding return, and dividing them by the years
    is what makes the quotient a count of round trips.

    **Only the fee line is charged here.** Spread and slippage also scale with
    turnover and are excluded, which is exactly why the resulting figures are upper
    bounds; the conversion leg is excluded because it is charged twice for a whole
    run rather than per rebalance.
    """
    if months < 1:
        return None
    years = Decimal(months) / Decimal(12)
    equity = ACCOUNT_EQUITY.amount
    growth = Decimal(1) + lines.gross_before_costs / equity
    if growth <= 0:
        gross_bps = Decimal(-10000)
    else:
        gross_bps = (_root(growth, years) - Decimal(1)) * Decimal(10000)
    fees_bps = lines.fees / equity * Decimal(10000) / years
    try:
        return BreakEven(
            gross_return_bps_per_year=gross_bps,
            research_fee_of_equity_bps=RESEARCH_FEE_OF_EQUITY_BPS,
            execution_fee_of_equity_bps=EXECUTION_FEE_OF_EQUITY_BPS,
            realised_fees_bps_per_year=max(fees_bps, Decimal(0)),
        )
    except BreakEvenUndefined:
        return None


def _root(value: Decimal, years: Decimal) -> Decimal:
    """``value ** (1 / years)`` in Decimal, for a positive base.

    Through ``ln`` and ``exp`` rather than ``**`` because Decimal's power operator
    refuses a non-integral exponent, and because the money boundary is deliberate:
    this is a reporting conversion, not an accounting one, and it stays in Decimal
    rather than crossing to float for a fractional power.
    """
    if years <= 0:
        raise ResultsIncomplete("A compounded annual rate needs a positive span of years.")
    return (value.ln() / years).exp()


def _regime_returns(
    monthly: Sequence[tuple[Timestamp, Decimal]], labels: Mapping[Timestamp, str]
) -> dict[str, tuple[Decimal, int]]:
    """Compounded net return and month count per regime, keyed by the opening label."""
    openings = opening_instants(monthly)
    grouped: dict[str, tuple[Decimal, int]] = {}
    for closed_at, value in monthly:
        opened_at = openings.get(closed_at)
        if opened_at is None:
            continue
        label = labels.get(opened_at)
        if label is None:
            continue
        product, count = grouped.get(label, (Decimal(1), 0))
        grouped[label] = (product * (Decimal(1) + value), count + 1)
    return {label: (product - Decimal(1), count) for label, (product, count) in grouped.items()}


def _compound(monthly: Sequence[tuple[Timestamp, Decimal]]) -> Decimal:
    """The compounded net return of a slice of a monthly series."""
    product = Decimal(1)
    for _, value in monthly:
        product *= Decimal(1) + value
    return product - Decimal(1)


def _criteria(
    *,
    statistics: PerformanceStatistics | None,
    net_return: Decimal,
    null_p95: float | None,
    deflated: DeflatedSharpeResult | None,
    decomposition: Decomposition,
    regimes: Mapping[str, tuple[Decimal, int]],
    passive_regimes: Mapping[str, tuple[Decimal, int]],
    countable: tuple[str, ...],
    sign_stable: bool | None,
    recent_statistics: PerformanceStatistics | None,
    recent_net_return: Decimal,
    recent_null_p95: float | None,
    effective: float,
) -> Criteria:
    """Section 11's six, each answered True, False or None."""
    sharpe = None if statistics is None else statistics.sharpe_annualised
    one = None if sharpe is None or null_p95 is None else bool(sharpe > null_p95 and net_return > 0)
    two = None if deflated is None else deflated.deflated_sharpe_ratio >= DSR_THRESHOLD

    selection = decomposition.selection_effect
    three: bool | None = None
    if selection is not None:
        if decomposition.combined <= 0:
            three = False
        else:
            three = bool(selection > 0 and selection >= decomposition.combined * SELECTION_SHARE)

    positive = sum(
        1
        for label, (value, months) in regimes.items()
        if label in countable and months >= MINIMUM_MONTHS_PER_REGIME and value > 0
    )
    never_worse = all(
        value >= passive_regimes.get(label, (Decimal(0), 0))[0]
        for label, (value, months) in regimes.items()
        if label in countable and months >= MINIMUM_MONTHS_PER_REGIME
    )
    four = (positive >= MINIMUM_REGIMES and never_worse) if countable else None

    recent_sharpe = None if recent_statistics is None else recent_statistics.sharpe_annualised
    six = (
        None
        if recent_sharpe is None or recent_null_p95 is None
        else bool(recent_sharpe > recent_null_p95 and recent_net_return > 0)
    )
    return Criteria(
        return_t_statistic=None if statistics is None else statistics.mean_return_t_statistic,
        beats_exposure_matched_null=one,
        survives_deflation=two,
        win_is_selection=three,
        regime_stability=four,
        sign_stable_across_cells=sign_stable,
        recent_window_holds=six,
        regimes_positive=positive,
        regimes_countable=countable,
        effective_observations=effective,
    )


class ScoredAtTheWrongLevel(ResultsIncomplete):
    """A criterion was asked to read a row computed at the bound. Rule A12.6.

    The bound is a reported sensitivity and never a criterion: it is a measured figure
    this project cannot date, projected over a window it was not measured on. A verdict
    that read it would be a verdict about 2023 applied to 2021.

    Raised rather than filtered. A row at the wrong level reaching the criteria is a wiring
    defect, and silently dropping it would leave a grid that is quietly one cell short.
    """


def _at_the_headline(row: Mapping[str, object]) -> None:
    """Refuse a row that was not computed at the headline spread level.

    Rule A12.6's mechanical guard. A row that names no level is read as the headline, which
    is what F1's result file predates the field with: it was computed before two levels
    existed and there was only one for it to be at.
    """
    level = row.get("spread_level")
    if level is not None and _text(level) != SPREAD_LEVEL_HEADLINE:
        raise ScoredAtTheWrongLevel(
            f"{_text(row.get('construct'))} in cell {_text(row.get('cell_id'))} was computed "
            f"at the {_text(level)} spread level and a criterion was asked to read it. Only "
            f"{SPREAD_LEVEL_HEADLINE} is judged; {SPREAD_LEVEL_BOUND} is reported beside it "
            "and never scored."
        )


def analyse(payload: Mapping[str, object]) -> tuple[VariantRow, ...]:
    """Every variant in every cell, reduced to the numbers the verdict needs.

    Every row is checked against rule A12.6's guard before a criterion sees it.
    """
    deterministic = _rows(payload, "deterministic")
    nulls = _rows(payload, "nulls")
    labels = regime_labels(payload)
    trials = payload.get("trials")
    if not isinstance(trials, dict):
        raise ResultsIncomplete("the result file carries no trial count.")
    trial_count = int(_text(trials["including_nulls"]))

    by_key = {(_text(row["construct"]), _text(row["cell_id"])): row for row in deterministic}
    nulls_by_key = {(_text(row["construct"]), _text(row["cell_id"])): row for row in nulls}
    scored_counts = _scored_counts(payload)
    countable = tuple(sorted(regime.value for regime in conclusive(scored_counts)))

    signs = _signs_by_variant(deterministic)
    rows: list[VariantRow] = []
    for row in deterministic:
        _at_the_headline(row)
        if _text(row.get("kind")) != "variant":
            continue
        variant, cell = _text(row["construct"]), _text(row["cell_id"])
        monthly = monthly_of(row)
        statistics = statistics_of(monthly)
        trailing = recent_window(monthly, RECENT_WINDOW_MONTHS)
        null = nulls_by_key.get((f"{variant}/{EXPOSURE_MATCHED}", cell))
        variance = _variance(null)
        deflated = (
            deflated_sharpe_ratio(statistics, trials=trial_count, trial_sharpe_variance=variance)
            if statistics is not None and variance is not None
            else None
        )
        lines = _costs_of(row)
        selection = by_key.get((f"{variant}/{SELECTION_ONLY}", cell))
        timing = by_key.get((f"{variant}/{TIMING_NULL}", cell))
        decomposition = Decomposition(
            combined=_decimal(row["terminal_return"]),
            timing_effect=None if timing is None else _decimal(timing["terminal_return"]),
            selection_effect=None if selection is None else _decimal(selection["terminal_return"]),
            funding=lines.funding_received,
            price=lines.market_gain,
            gross=lines.gross_before_costs,
            charges=lines.charges,
            costs=lines.total,
        )
        passive = by_key.get((EQUAL_WEIGHT_PASSIVE, cell))
        regimes = _regime_returns(monthly, labels)
        passive_regimes = {} if passive is None else _regime_returns(monthly_of(passive), labels)
        rows.append(
            VariantRow(
                variant=variant,
                cell=cell,
                fill_mix=_text(row["fill_mix"]),
                rebalances=None if row.get("rebalances") is None else int(_text(row["rebalances"])),
                idle_months=(
                    None
                    if row.get("idle_months_before_first_rebalance") is None
                    else int(_text(row["idle_months_before_first_rebalance"]))
                ),
                net_return=_decimal(row["terminal_return"]),
                statistics=statistics,
                recent_statistics=statistics_of(trailing),
                recent_months=len(trailing),
                recent_net_return=_compound(trailing),
                null_p95=_percentile(null, "sharpe"),
                recent_null_p95=_percentile(null, "recent_window_sharpe"),
                deflated=deflated,
                decomposition=decomposition,
                regime_returns=regimes,
                break_even=break_even_of(
                    lines, 0 if statistics is None else statistics.observations
                ),
                criteria=_criteria(
                    statistics=statistics,
                    net_return=_decimal(row["terminal_return"]),
                    null_p95=_percentile(null, "sharpe"),
                    deflated=deflated,
                    decomposition=decomposition,
                    regimes=regimes,
                    passive_regimes=passive_regimes,
                    countable=countable,
                    sign_stable=signs.get(variant),
                    recent_statistics=statistics_of(trailing),
                    recent_net_return=_compound(trailing),
                    recent_null_p95=_percentile(null, "recent_window_sharpe"),
                    effective=independence_of(
                        [float(value) for _, value in monthly]
                    ).effective_observations,
                ),
            )
        )
    return tuple(rows)


def _scored_counts(payload: Mapping[str, object]) -> Mapping[Regime, int]:
    """Month counts per regime over the scored window, as the enum."""
    raw = payload.get("regime_month_counts_scored")
    if not isinstance(raw, dict):
        raise ResultsIncomplete("the result file carries no scored regime month counts.")
    return {Regime(str(label)): int(str(count)) for label, count in raw.items()}


def _signs_by_variant(
    deterministic: Sequence[Mapping[str, object]],
) -> Mapping[str, bool | None]:
    """Criterion 5: whether a variant's net return keeps its sign in all four cells.

    ``None`` when the variant was not run in all four, because "the sign held in the
    three cells we have" is not the criterion that was registered.
    """
    expected = {spec.label for spec in REGISTERED_VARIANTS}
    grouped: dict[str, list[Decimal]] = {}
    cells: dict[str, set[str]] = {}
    for row in deterministic:
        if _text(row.get("kind")) != "variant":
            continue
        variant = _text(row["construct"])
        if variant not in expected:
            continue
        grouped.setdefault(variant, []).append(_decimal(row["terminal_return"]))
        cells.setdefault(variant, set()).add(_text(row["cell_id"]))
    out: dict[str, bool | None] = {}
    for variant, returns in grouped.items():
        if len(cells[variant]) < 4:
            out[variant] = None
            continue
        out[variant] = all(value > 0 for value in returns) or all(value < 0 for value in returns)
    return out


# ---------------------------------------------------------------------------
# What the toll is made of
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Toll:
    """One run's charges, itemised, with each part as a share of the whole.

    Built because the composition decides what a negative verdict claims. A toll made
    of published fees is a statement about this account's cost structure, which moves
    with venue and tier. A toll made of spread and slippage is a statement resting on
    two configured numbers, and a verdict resting on those has to say so.
    """

    variant: str
    cell: str
    lines: CostLines

    def share(self, part: Decimal) -> Decimal | None:
        """One part as a share of the total charged, or None over nothing."""
        if self.lines.charges == 0:
            return None
        return part / self.lines.charges

    def as_json(self) -> dict[str, object]:
        parts = {
            "fees": self.lines.fees,
            "spread": self.lines.spread,
            "slippage": self.lines.slippage,
            "fx_conversion": self.lines.conversion,
            "delisting_haircut": self.lines.delisting,
        }
        return {
            "variant": self.variant,
            "cell_id": self.cell,
            "charges_total": str(self.lines.charges),
            "parts": {name: str(value) for name, value in parts.items()},
            "shares": {
                name: None if self.share(value) is None else str(self.share(value))
                for name, value in parts.items()
            },
            "assumed_share": None
            if self.share(self.lines.assumed) is None
            else str(self.share(self.lines.assumed)),
            "flip_multiplier": None
            if self.lines.flip_multiplier is None
            else str(self.lines.flip_multiplier),
            "note": (
                "Funding is not a part of this total. The ledger books a receipt as a "
                "negative cost line; charges here are what the book paid to trade and to "
                "hold, and the funding stream sits on the return side of the identity."
            ),
        }


def tolls(payload: Mapping[str, object], *, cell: str) -> tuple[Toll, ...]:
    """Every variant's charges in one cell, itemised."""
    return tuple(
        Toll(variant=_text(row["construct"]), cell=cell, lines=_costs_of(row))
        for row in _rows(payload, "deterministic")
        if _text(row.get("kind")) == "variant" and _text(row["cell_id"]) == cell
    )


def combined_toll(items: Sequence[Toll]) -> CostLines:
    """The nine added together, so the composition can be stated for the family.

    A family-level statement needs a family-level total. One variant's composition is
    one variant's, and the spread of turnover across the nine is wide enough that the
    largest and the smallest do not have the same shape.
    """

    def total(pick: Callable[[CostLines], Decimal]) -> Decimal:
        return sum((pick(item.lines) for item in items), Decimal(0))

    return CostLines(
        market_gain=total(lambda line: line.market_gain),
        funding_received=total(lambda line: line.funding_received),
        fees=total(lambda line: line.fees),
        spread=total(lambda line: line.spread),
        slippage=total(lambda line: line.slippage),
        conversion=total(lambda line: line.conversion),
        delisting=total(lambda line: line.delisting),
        total=total(lambda line: line.total),
    )


# ---------------------------------------------------------------------------
# Amendment 26.1: the headline with and without the contraction month
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WithoutAMonth:
    """One variant's headline with a single month kept and then dropped.

    Reported because amendment 26.1 requires it when the surviving slice differs
    systematically from the excluded one. It decides nothing: every criterion is
    judged on the full series exactly as registered, and this sits beside it.
    """

    variant: str
    cell: str
    months: int
    net_return: Decimal
    sharpe: float | None
    net_return_without: Decimal
    sharpe_without: float | None

    @property
    def difference(self) -> Decimal:
        """How much of the headline that one month accounted for."""
        return self.net_return - self.net_return_without

    def as_json(self) -> dict[str, object]:
        return {
            "variant": self.variant,
            "cell_id": self.cell,
            "months": self.months,
            "compounded_net_return": str(self.net_return),
            "compounded_net_return_excluding_the_month": str(self.net_return_without),
            "difference": str(self.difference),
            "sharpe_annualised": self.sharpe,
            "sharpe_annualised_excluding_the_month": self.sharpe_without,
        }


def excluding_month(
    payload: Mapping[str, object], at: Timestamp, *, cell: str
) -> tuple[WithoutAMonth, ...]:
    """Every variant in one cell, with and without the month ``at`` falls in.

    The month is matched on calendar year and month rather than on an exact instant,
    because a monthly series is stamped at whichever boundary its own convention uses.
    """
    target = (at.value.year, at.value.month)
    rows: list[WithoutAMonth] = []
    for item in _rows(payload, "deterministic"):
        if _text(item.get("kind")) != "variant" or _text(item["cell_id"]) != cell:
            continue
        monthly = monthly_of(item)
        kept = tuple(
            entry for entry in monthly if (entry[0].value.year, entry[0].value.month) != target
        )
        if len(kept) == len(monthly):
            continue
        with_it, without_it = statistics_of(monthly), statistics_of(kept)
        rows.append(
            WithoutAMonth(
                variant=_text(item["construct"]),
                cell=cell,
                months=len(monthly),
                net_return=_compound(monthly),
                sharpe=None if with_it is None else with_it.sharpe_annualised,
                net_return_without=_compound(kept),
                sharpe_without=None if without_it is None else without_it.sharpe_annualised,
            )
        )
    return tuple(sorted(rows, key=lambda row: row.variant))


# ---------------------------------------------------------------------------
# Rule S1, generalised: could the assumption be carrying the verdict?
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Rescue:
    """What one variant would look like with its assumed costs set to zero.

    The question rule S1 asks from amendment 9 onward. A sign change is not enough:
    a variant that crosses zero and lands below its own null was rescued by rounding
    rather than by the assumption, and measuring the assumption would change nothing.
    So the bar is criterion 1 itself, applied to the counterfactual.
    """

    variant: str
    cell: str
    net_return: Decimal
    sharpe: float | None
    net_return_at_zero: Decimal
    sharpe_at_zero: float | None
    null_p95: float | None
    assumed_cost: Decimal
    deflated_at_zero: DeflatedSharpeResult | None
    standard_error_at_zero: float | None
    """The counterfactual Sharpe's own standard error, annualised and corrected.

    Skew- and kurtosis-corrected rather than the normal approximation, because
    amendment 10 states the floor in units of this number and a floor is only as
    honest as the uncertainty it is measured in. On a fat-tailed, negatively skewed
    series the corrected figure is the larger of the two, which makes the bar higher.
    """
    """The counterfactual's Deflated Sharpe, at the same honest trial count.

    Carried because criterion 1 is one of six. A variant that clears criterion 1 at
    zero assumed cost has not thereby cleared the verdict, and the cheapest way to see
    whether the letter would move is to ask criterion 2 of the same counterfactual.
    """

    @property
    def survives_deflation_at_zero(self) -> bool | None:
        """Criterion 2, applied to the counterfactual."""
        if self.deflated_at_zero is None:
            return None
        return self.deflated_at_zero.deflated_sharpe_ratio > DSR_THRESHOLD

    @property
    def beats_its_null(self) -> bool | None:
        """Whether the counterfactual Sharpe clears the null's 95th percentile."""
        if self.sharpe_at_zero is None or self.null_p95 is None:
            return None
        return self.sharpe_at_zero > self.null_p95

    @property
    def clears_criterion_one(self) -> bool | None:
        """Criterion 1, applied to the counterfactual: beats its null *and* earns.

        The bar rule S1 carried for F1. Amendment 10 raises it from F2 by the floor
        below, and both are reported for every family so the two are comparable.
        """
        beats = self.beats_its_null
        if beats is None:
            return None
        return beats and self.net_return_at_zero > 0

    @property
    def clears_zero_by_a_standard_error(self) -> bool | None:
        """Whether the counterfactual Sharpe exceeds ZERO by one standard error of itself.

        Amendment 11's absolute clause. An assumption is worth measuring when removing it
        could produce a result *distinguishable from noise*, and noise here means zero
        rather than whatever the null happened to lose over this particular window.
        """
        if self.sharpe_at_zero is None or self.standard_error_at_zero is None:
            return None
        return self.sharpe_at_zero > self.standard_error_at_zero

    @property
    def clears_by_a_standard_error(self) -> bool | None:
        """Rule S1's floor as settled by amendment 11, applying from F2. Both clauses.

        The counterfactual Sharpe must exceed **zero** by one standard error of its own
        estimate *and* clear the 95th percentile of its own exposure-matched null. Never
        either: the null comparison says a no-edge process could not have produced this,
        the absolute clause says the result is distinguishable from nothing, and over a
        window where the null loses money the two answers come apart by the whole size of
        that loss.
        """
        absolute = self.clears_zero_by_a_standard_error
        relative = self.beats_its_null
        if absolute is None or relative is None:
            return None
        return absolute and relative

    @property
    def clears_the_null_by_a_standard_error(self) -> bool | None:
        """Amendment 10's floor as first written, kept computable and never applied.

        Superseded by the property above. It stays here because section 33.1 supersedes
        amendment 10 rather than deleting it, and a superseded bar that cannot be computed
        is a bar a later reader has to take on trust. On F1's figures this one fires and
        the settled one does not, which is exactly why both are in the record.
        """
        if (
            self.sharpe_at_zero is None
            or self.null_p95 is None
            or self.standard_error_at_zero is None
        ):
            return None
        return (
            self.sharpe_at_zero > self.null_p95 + self.standard_error_at_zero
            and self.net_return_at_zero > 0
        )

    def as_json(self) -> dict[str, object]:
        return {
            "variant": self.variant,
            "cell_id": self.cell,
            "assumed_cost_removed": str(self.assumed_cost),
            "net_return": str(self.net_return),
            "net_return_at_zero_assumed_cost": str(self.net_return_at_zero),
            "sharpe_annualised": self.sharpe,
            "sharpe_annualised_at_zero_assumed_cost": self.sharpe_at_zero,
            "exposure_matched_null_p95": self.null_p95,
            "beats_its_null_at_zero_assumed_cost": self.beats_its_null,
            "would_clear_criterion_one": self.clears_criterion_one,
            "standard_error_of_the_sharpe_at_zero": self.standard_error_at_zero,
            "would_clear_zero_by_one_standard_error": self.clears_zero_by_a_standard_error,
            "would_clear_rule_s1s_settled_floor": self.clears_by_a_standard_error,
            "would_clear_amendment_10s_null_anchored_floor": (
                self.clears_the_null_by_a_standard_error
            ),
            "floor_applies_from": FLOOR_APPLIES_FROM,
            "floor_anchor": FLOOR_ANCHOR,
            "deflated_sharpe_at_zero_assumed_cost": None
            if self.deflated_at_zero is None
            else self.deflated_at_zero.deflated_sharpe_ratio,
            "would_survive_deflation_at_zero": self.survives_deflation_at_zero,
            "model": (
                "The removed cost is added back in equal instalments across the scored "
                "months, each converted to a return on that month's opening equity along "
                "the realised path. Exact in the total and in the sign of the net return; "
                "approximate in the volatility, because the true charge follows each "
                "month's turnover and is lumpier than a constant. From the family that "
                "records per-month assumed costs, the same test is computed exactly."
            ),
        }


def _lifted(
    monthly: Sequence[tuple[Timestamp, Decimal]], removed: Decimal, equity: Decimal
) -> tuple[tuple[Timestamp, Decimal], ...]:
    """The monthly series with a total cost added back in equal instalments."""
    if not monthly:
        return ()
    instalment = removed / Decimal(len(monthly))
    out: list[tuple[Timestamp, Decimal]] = []
    running = equity
    for at, value in monthly:
        out.append((at, value + instalment / running) if running != 0 else (at, value))
        running = running * (Decimal(1) + value)
    return tuple(out)


def rescues(payload: Mapping[str, object], *, cell: str) -> tuple[Rescue, ...]:
    """Amendment 9's test for every variant in one cell."""
    trials = payload.get("trials")
    trial_count = int(_text(trials["including_nulls"])) if isinstance(trials, dict) else 0
    nulls = {
        _text(row["construct"]).split("/")[0]: row
        for row in _rows(payload, "nulls")
        if _text(row["cell_id"]) == cell and _text(row["construct"]).endswith(EXPOSURE_MATCHED)
    }
    out: list[Rescue] = []
    for row in _rows(payload, "deterministic"):
        if _text(row.get("kind")) != "variant" or _text(row["cell_id"]) != cell:
            continue
        name = _text(row["construct"])
        lines = _costs_of(row)
        monthly = monthly_of(row)
        equity = _decimal(row.get("initial_equity", ACCOUNT_EQUITY.amount))
        lifted = _lifted(monthly, lines.assumed, equity)
        before, after = statistics_of(monthly), statistics_of(lifted)
        null = nulls.get(name)
        variance = _variance(null)
        deflated = (
            deflated_sharpe_ratio(after, trials=trial_count, trial_sharpe_variance=variance)
            if after is not None and variance is not None and trial_count > 0
            else None
        )
        error = (
            corrected_sharpe_standard_error(
                sharpe_per_period=after.sharpe_per_period,
                observations=after.observations,
                skewness=after.skewness,
                kurtosis=after.kurtosis,
            )
            * sqrt(after.annualisation)
            if after is not None
            else None
        )
        out.append(
            Rescue(
                variant=name,
                cell=cell,
                net_return=_compound(monthly),
                sharpe=None if before is None else before.sharpe_annualised,
                net_return_at_zero=_compound(lifted),
                sharpe_at_zero=None if after is None else after.sharpe_annualised,
                null_p95=_percentile(null, "sharpe"),
                assumed_cost=lines.assumed,
                deflated_at_zero=deflated,
                standard_error_at_zero=error,
            )
        )
    return tuple(sorted(out, key=lambda item: item.variant))


def assumption_could_be_carrying_the_verdict(items: Sequence[Rescue]) -> bool:
    """Amendment 9's rule S1: acquire when some variant would clear criterion 1.

    True means the assumed cost is load-bearing for the verdict and has to be
    measured. False means no measurement of it could change the answer, which is a
    stronger statement than "the sample was not acquired" and is what the report says.
    """
    return any(item.clears_criterion_one for item in items)


# ---------------------------------------------------------------------------
# Rule A12.9: what the currency line should have been
# ---------------------------------------------------------------------------


class CurrencyLineUnreadable(RuntimeError):
    """The charged currency line cannot be reconciled, so nothing is corrected.

    Raised rather than correcting anyway. The correction below rests on one claim about a
    committed result file - that its currency line is a flat rate on turnover - and that
    claim is checked per variant rather than assumed. A file whose line does not reconcile
    is a file this correction has no business rewriting.
    """


@dataclass(frozen=True, slots=True)
class CurrencyCorrection:
    """One variant's currency line as charged, and as rule A12.9 says it should be.

    The defect: the conversion was charged inside every trade, on that trade's notional,
    which made the line scale with turnover. Rule A12.9 says it must scale with the number
    of times capital crosses a currency boundary, which for a run that stays invested is
    twice - once in and once out.

    **Nothing here edits a committed result.** The charged figures stay as they are and
    this is reported beside them, in the amendment section rather than in the original.
    """

    variant: str
    cell: str
    turnover: Decimal
    as_charged: Decimal
    """The currency line the run actually charged."""
    initial_equity: Decimal
    terminal_equity: Decimal
    net_pnl: Decimal

    @property
    def implied_bps_on_turnover(self) -> Decimal:
        """What rate on turnover the charged line works out to.

        Computed rather than assumed, because "it scales with turnover" is the finding and
        a finding asserted from a docstring is not a finding.
        """
        if self.turnover == 0:
            return Decimal(0)
        return self.as_charged / self.turnover * BASIS_POINTS

    @property
    def scales_with_turnover(self) -> bool:
        """Whether the charged line is the registered rate applied to turnover."""
        return abs(self.implied_bps_on_turnover - CONVERSION_BPS) < Decimal("0.0001")

    @property
    def corrected(self) -> Decimal:
        """Two crossings, each on the capital that crossed at that instant.

        The entry converts the opening equity and the exit converts the closing equity.
        Held to the registered crossing count rather than to a literal two, so the number
        and the rule cannot drift apart.
        """
        capital = self.initial_equity + self.terminal_equity
        if FX_CROSSINGS_PER_RUN != 2:  # pragma: no cover - the registered value is two
            capital = self.initial_equity * Decimal(FX_CROSSINGS_PER_RUN)
        return capital * CONVERSION_BPS / BASIS_POINTS

    @property
    def removed(self) -> Decimal:
        """How much of the charged line was double-counted."""
        return self.as_charged - self.corrected

    @property
    def multiple(self) -> Decimal | None:
        """How many times the correct charge the charged line was."""
        if self.corrected == 0:
            return None
        return self.as_charged / self.corrected

    @property
    def corrected_net_pnl(self) -> Decimal:
        """Net PnL with the double-counted part of the currency line given back.

        **First order, and labelled as such.** Returning the charge also returns the
        compounding it cost along the way, and the second crossing would then convert a
        slightly larger closing equity. Both effects are smaller than a euro on these
        figures and neither is large enough to change a sign.
        """
        return self.net_pnl + self.removed

    @property
    def still_loses(self) -> bool:
        """Whether the variant loses even with the whole double-count returned."""
        return self.corrected_net_pnl < 0

    def as_json(self) -> dict[str, object]:
        return {
            "variant": self.variant,
            "cell_id": self.cell,
            "turnover": str(self.turnover),
            "currency_line_as_charged": str(self.as_charged),
            "implied_rate_on_turnover_bps": str(self.implied_bps_on_turnover),
            "registered_conversion_bps": str(CONVERSION_BPS),
            "scales_with_turnover": self.scales_with_turnover,
            "crossings_per_run": FX_CROSSINGS_PER_RUN,
            "currency_line_corrected": str(self.corrected),
            "double_counted": str(self.removed),
            "as_charged_over_corrected": None if self.multiple is None else str(self.multiple),
            "net_pnl_as_run": str(self.net_pnl),
            "net_pnl_with_the_double_count_returned": str(self.corrected_net_pnl),
            "still_loses": self.still_loses,
            "order": (
                "FIRST ORDER. Returning the charge also returns the compounding it cost, "
                "and the exit crossing would then convert a slightly larger closing equity. "
                "Both are under a euro here and neither changes a sign."
            ),
            "changes_no_committed_figure": (
                "The result file is not edited. This is reported beside it, under rule "
                "A12.7, in a section added rather than substituted."
            ),
        }


def currency_corrections(
    payload: Mapping[str, object], *, cell: str
) -> tuple[CurrencyCorrection, ...]:
    """Rule A12.9's correction for every variant in one cell."""
    out: list[CurrencyCorrection] = []
    for row in _rows(payload, "deterministic"):
        if _text(row.get("kind")) != "variant" or _text(row["cell_id"]) != cell:
            continue
        out.append(
            CurrencyCorrection(
                variant=_text(row["construct"]),
                cell=cell,
                turnover=_decimal(row["turnover"]),
                as_charged=_costs_of(row).conversion,
                initial_equity=_decimal(row.get("initial_equity", ACCOUNT_EQUITY.amount)),
                terminal_equity=_decimal(row["terminal_equity"]),
                net_pnl=_decimal(row["net_pnl"]),
            )
        )
    return tuple(sorted(out, key=lambda item: item.variant))


def the_currency_line_scaled_with_turnover(items: Sequence[CurrencyCorrection]) -> bool:
    """Whether every variant's charged currency line is the registered rate on turnover.

    The finding, stated as a computed condition over the whole cell rather than as a claim
    about one number. False means the reconciliation failed somewhere and the correction
    below it must not be reported as though it had been established.
    """
    return bool(items) and all(item.scales_with_turnover for item in items)


# ---------------------------------------------------------------------------
# Capacity: rule C3's decision, from series that already exist
# ---------------------------------------------------------------------------


class CapacityVerdict(StrEnum):
    """What rule C3 decides for one variant. Four outcomes and no judgement call."""

    UNESTABLISHED_TOO_FEW_MONTHS = "unestablished: too few scored months inside the depth window"
    UNESTABLISHED_NO_EDGE_INSIDE = "unestablished: the variant did not earn inside the window"
    MEASURED_WITH_A_CAVEAT = "measured, over a period in which the variant earned less than overall"
    MEASURED = "measured, with its window"

    @property
    def needs_depth_data(self) -> bool:
        """Whether reporting this outcome requires the order-book depth sample.

        The point of asking. Two of the four outcomes are decided entirely by the
        monthly return series and the depth window's calendar dates, and reporting
        them needs no order-book data at all. Acquiring a depth sample to print the
        word "unestablished" would be acquiring data the registered rule does not use.
        """
        return self in {CapacityVerdict.MEASURED, CapacityVerdict.MEASURED_WITH_A_CAVEAT}


@dataclass(frozen=True, slots=True)
class Capacity:
    """Rule C3 for one variant: the two quantities and the outcome they force."""

    variant: str
    cell: str
    depth_months: int
    months_outside: int
    mean_inside: Decimal | None
    mean_outside: Decimal | None
    verdict: CapacityVerdict

    def as_json(self) -> dict[str, object]:
        return {
            "variant": self.variant,
            "cell_id": self.cell,
            "depth_window": f"{DEPTH_WINDOW_STARTS}/{DEPTH_WINDOW_ENDS}",
            "depth_months": self.depth_months,
            "minimum_depth_months": MINIMUM_DEPTH_MONTHS,
            "months_outside_the_depth_window": self.months_outside,
            "mean_monthly_net_return_inside": None
            if self.mean_inside is None
            else str(self.mean_inside),
            "mean_monthly_net_return_outside": None
            if self.mean_outside is None
            else str(self.mean_outside),
            "verdict": self.verdict.value,
            "requires_the_depth_sample": self.verdict.needs_depth_data,
            "note": (
                "Decided by rule C3's table from series that already exist. "
                "UNESTABLISHED does not mean the strategy has no capacity; it means this "
                "dataset cannot say what it is, which is the same class of statement as "
                "invariant 9's not evaluable, and it is not softened."
            ),
        }


def _inside_depth_window(at: Timestamp) -> bool:
    """Whether a scored month's whole holding period lies inside the depth window.

    Keyed on the month's OPENING instant and requiring the close to land inside too,
    which is what "whole holding period" means. A month straddling either edge counts
    as outside: a partially measurable month is not a measured one.
    """
    starts = Timestamp.parse(f"{DEPTH_WINDOW_STARTS}T00:00:00+00:00")
    ends = Timestamp.parse(f"{DEPTH_WINDOW_ENDS}T00:00:00+00:00")
    return starts <= at <= ends


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    """The arithmetic mean, or None over nothing."""
    if not values:
        return None
    return sum(values, Decimal(0)) / Decimal(len(values))


def capacity_of(row: VariantRow, monthly: Sequence[tuple[Timestamp, Decimal]]) -> Capacity:
    """Rule C3 applied to one variant, in the order the registered table states it."""
    openings = opening_instants(monthly)
    inside: list[Decimal] = []
    outside: list[Decimal] = []
    for closed_at, value in monthly:
        opened_at = openings.get(closed_at)
        if opened_at is None:
            continue
        target = (
            inside
            if _inside_depth_window(opened_at) and _inside_depth_window(closed_at)
            else outside
        )
        target.append(value)
    mean_inside, mean_outside = _mean(inside), _mean(outside)
    if len(inside) < MINIMUM_DEPTH_MONTHS:
        verdict = CapacityVerdict.UNESTABLISHED_TOO_FEW_MONTHS
    elif mean_inside is None or mean_inside <= 0:
        verdict = CapacityVerdict.UNESTABLISHED_NO_EDGE_INSIDE
    elif mean_outside is not None and mean_outside > mean_inside:
        verdict = CapacityVerdict.MEASURED_WITH_A_CAVEAT
    else:
        verdict = CapacityVerdict.MEASURED
    return Capacity(
        variant=row.variant,
        cell=row.cell,
        depth_months=len(inside),
        months_outside=len(outside),
        mean_inside=mean_inside,
        mean_outside=mean_outside,
        verdict=verdict,
    )


def capacity_report(
    payload: Mapping[str, object], rows: Sequence[VariantRow]
) -> tuple[Capacity, ...]:
    """Rule C3 for every variant in the headline cell."""
    deterministic = _rows(payload, "deterministic")
    series = {
        (_text(item["construct"]), _text(item["cell_id"])): monthly_of(item)
        for item in deterministic
        if _text(item.get("kind")) == "variant"
    }
    return tuple(
        capacity_of(row, series[(row.variant, row.cell)])
        for row in rows
        if (row.variant, row.cell) in series
    )


def depth_sample_is_needed(report: Sequence[Capacity]) -> bool:
    """Whether any variant's C3 outcome requires the order-book depth sample.

    The question the rule answers on its own. Acquiring a depth sample in order to
    print "unestablished" would be acquiring data the registered rule does not read,
    which is the opposite of the instruction to take the minimum the rule needs.
    """
    return any(item.verdict.needs_depth_data for item in report)


# ---------------------------------------------------------------------------
# Rule S1: whether the spread sample is acquired at all
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SpreadAcquisition:
    """Amendment 8's rule S1, decided from the result file and nothing else.

    Spread is a configured assumption and the measurement can only ever make a variant
    look worse. So the sample is worth its 1.8 to 3.2 GB exactly when some variant
    earns at research fees, because that is the only case where a larger spread could
    change a conclusion. Where nothing earns, refining a cost line on a strategy that
    does not earn refines nothing.
    """

    best_variant: str | None
    best_cell: str | None
    best_net_return: Decimal | None
    positive_count: int
    considered: int

    @property
    def acquire(self) -> bool:
        """Rule S1's condition: strictly positive net return, at research fees."""
        return self.positive_count > 0

    def as_json(self) -> dict[str, object]:
        return {
            "rule": SPREAD_TRIGGER_RULE,
            "condition": (
                "at least one registered variant, in at least one registered cost cell, "
                "earns a net return over the scored out-of-sample window strictly "
                "greater than "
                f"{SPREAD_TRIGGER_THRESHOLD}"
            ),
            "runs_considered": self.considered,
            "runs_with_a_positive_net_return": self.positive_count,
            "best_run": None
            if self.best_variant is None
            else f"{self.best_variant} in {self.best_cell}",
            "best_net_return": None if self.best_net_return is None else str(self.best_net_return),
            "acquire_the_spread_sample": self.acquire,
            "symbol_days_if_acquired": SPREAD_SAMPLE_SYMBOL_DAYS,
            "cells_excluded": [execution_sensitivity().label],
            "note": (
                "Registered as a computed condition before any figure of the execution "
                "it reads had been looked at, in the same shape as rule C3. Not "
                "acquiring is reported, and the cost assumption stays labelled an "
                "assumption under invariant 12 either way."
            ),
        }


def spread_acquisition(rows: Sequence[VariantRow]) -> SpreadAcquisition:
    """Rule S1 over every registered variant in every registered cell.

    The execution-venue sensitivity cell is excluded because rule S1 is a condition at
    *research* fees, and a different fee schedule is a different condition. Every one of
    the four registered cells prices the research venue, so all four count.
    """
    research = {cell.label for cell in REGISTERED_CELLS}
    considered = [row for row in rows if row.cell in research]
    positive = [row for row in considered if row.net_return > SPREAD_TRIGGER_THRESHOLD]
    best = max(considered, key=lambda row: row.net_return, default=None)
    return SpreadAcquisition(
        best_variant=None if best is None else best.variant,
        best_cell=None if best is None else best.cell,
        best_net_return=None if best is None else best.net_return,
        positive_count=len(positive),
        considered=len(considered),
    )


# ---------------------------------------------------------------------------
# The verdict
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Verdict:
    """Which of (A), (B) or (C) the numbers support, and why."""

    letter: str
    reason: str
    clearing_criterion_one: tuple[str, ...]
    clearing_all_six: tuple[str, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "letter": self.letter,
            "reason": self.reason,
            "variants_clearing_criterion_1": list(self.clearing_criterion_one),
            "variants_clearing_all_six": list(self.clearing_all_six),
        }


def verdict(rows: Sequence[VariantRow], *, headline_cell: str, scored_months: int) -> Verdict:
    """Section 11's verdict rule, applied without discretion.

    (B) when nothing clears criterion 1. (C) when something clears criterion 1 but
    not all six, or the window is too short, or every variant that cleared criterion
    1 has too few effective observations. (A) only when a variant clears all six and
    none of the (C) conditions bites. A (B) or a (C) is never presented as a
    softened (A), and this function is the only place the letter is chosen.
    """
    headline = [row for row in rows if row.cell == headline_cell]
    ones = tuple(
        sorted(row.variant for row in headline if row.criteria.beats_exposure_matched_null)
    )
    alls = tuple(sorted(row.variant for row in headline if row.criteria.all_hold))
    if scored_months < MINIMUM_MONTHS_TO_PROCEED:
        return Verdict(
            letter="C",
            reason=(
                f"The resolved window carries {scored_months} scored months against the "
                f"registered floor of {MINIMUM_MONTHS_TO_PROCEED}. Section 7 makes that a (C) "
                "whatever the returns say."
            ),
            clearing_criterion_one=ones,
            clearing_all_six=alls,
        )
    if not ones:
        return Verdict(
            letter="B",
            reason=(
                "No variant's out-of-sample net Sharpe exceeded the 95th percentile of its "
                "own exposure-matched null while also earning a positive net return, in the "
                "headline cell. Criterion 1 is the floor and nothing reached it."
            ),
            clearing_criterion_one=ones,
            clearing_all_six=alls,
        )
    thin = [
        row
        for row in headline
        if row.criteria.beats_exposure_matched_null and not row.criteria.effective_observations_met
    ]
    if len(thin) == len(ones):
        return Verdict(
            letter="C",
            reason=(
                "Every variant that cleared criterion 1 has fewer effective observations than "
                f"the registered floor of {MINIMUM_EFFECTIVE_OBSERVATIONS}, so the data cannot "
                "distinguish the edge from noise however the other criteria read."
            ),
            clearing_criterion_one=ones,
            clearing_all_six=alls,
        )
    if not alls:
        return Verdict(
            letter="C",
            reason=(
                f"{len(ones)} variant(s) cleared criterion 1 but none cleared all six. The "
                "result is a partial signal the registered criteria do not accept, which is "
                "(C) and not a weaker (A)."
            ),
            clearing_criterion_one=ones,
            clearing_all_six=alls,
        )
    return Verdict(
        letter="A",
        reason=(
            f"{len(alls)} variant(s) cleared all six registered criteria in the headline cell "
            "with sufficient effective observations."
        ),
        clearing_criterion_one=ones,
        clearing_all_six=alls,
    )


__all__ = [
    "DSR_THRESHOLD",
    "MINIMUM_MONTHS_PER_REGIME",
    "MINIMUM_REGIMES",
    "SELECTION_SHARE",
    "Capacity",
    "CapacityVerdict",
    "CostLines",
    "Criteria",
    "Decomposition",
    "Rescue",
    "ResultsIncomplete",
    "ScoredAtTheWrongLevel",
    "SpreadAcquisition",
    "Toll",
    "VariantRow",
    "Verdict",
    "WithoutAMonth",
    "analyse",
    "assumption_could_be_carrying_the_verdict",
    "break_even_of",
    "capacity_of",
    "capacity_report",
    "combined_toll",
    "currency_corrections",
    "depth_sample_is_needed",
    "excluding_month",
    "monthly_of",
    "opening_instants",
    "regime_labels",
    "rescues",
    "spread_acquisition",
    "the_currency_line_scaled_with_turnover",
    "tolls",
    "verdict",
]
