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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from sextant.app.spike_006_f1 import (
    ACCOUNT_EQUITY,
    DEPTH_WINDOW_ENDS,
    DEPTH_WINDOW_STARTS,
    EXECUTION_FEE_OF_EQUITY_BPS,
    MINIMUM_DEPTH_MONTHS,
    MINIMUM_MONTHS_TO_PROCEED,
    RECENT_WINDOW_MONTHS,
    REGISTERED_CELLS,
    REGISTERED_VARIANTS,
    RESEARCH_FEE_OF_EQUITY_BPS,
    SPREAD_SAMPLE_SYMBOL_DAYS,
    SPREAD_TRIGGER_RULE,
    SPREAD_TRIGGER_THRESHOLD,
    execution_sensitivity,
)
from sextant.app.spike_006_f1_engine import recent_window, statistics_of
from sextant.domain.time import Timestamp
from sextant.engine.execution.breakeven import BreakEven, BreakEvenUndefined
from sextant.engine.regime.segmentation import Regime, conclusive
from sextant.engine.statistics.dsr import DeflatedSharpeResult, deflated_sharpe_ratio
from sextant.engine.statistics.independence import (
    MINIMUM_EFFECTIVE_OBSERVATIONS,
    independence_of,
)
from sextant.engine.statistics.metrics import PerformanceStatistics

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
        """True only when every one of the six is answered True."""
        return all(item is True for item in self.answered)

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


def analyse(payload: Mapping[str, object]) -> tuple[VariantRow, ...]:
    """Every variant in every cell, reduced to the numbers the verdict needs."""
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


def _compounded(series: Sequence[tuple[Timestamp, Decimal]]) -> Decimal:
    """The terminal return of a monthly series, compounded rather than summed."""
    total = Decimal(1)
    for _, value in series:
        total *= Decimal(1) + value
    return total - Decimal(1)


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
                net_return=_compounded(monthly),
                sharpe=None if with_it is None else with_it.sharpe_annualised,
                net_return_without=_compounded(kept),
                sharpe_without=None if without_it is None else without_it.sharpe_annualised,
            )
        )
    return tuple(sorted(rows, key=lambda row: row.variant))


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
    "ResultsIncomplete",
    "SpreadAcquisition",
    "VariantRow",
    "Verdict",
    "WithoutAMonth",
    "analyse",
    "break_even_of",
    "capacity_of",
    "capacity_report",
    "depth_sample_is_needed",
    "excluding_month",
    "monthly_of",
    "opening_instants",
    "regime_labels",
    "spread_acquisition",
    "verdict",
]
