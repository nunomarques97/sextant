"""Rule E1: the spread estimator, calibrated against the 36 symbol-days of real quotes.

Section 33.4 registered the estimator, the comparison, the three acceptance clauses and
their thresholds, and it was committed before this module produced a number. Nothing
here chooses anything: it computes the three clauses and reports what they say.

What is being replaced
----------------------

One spread constant per liquidity band. The bands are cut on quote turnover, spread does
not respond to turnover, and section 7.5 measured 0.0357 bps on `BTCUSDT` against
4.2452 bps on `API3USDT` - two orders of magnitude, both inside the *deep* band. A
better constant per band cannot repair a partition cut on the wrong variable.

What it is replaced with, if rule E1 passes
-------------------------------------------

An estimate per instrument and per period, from daily high, low and close, which the
archive already holds for every instrument across the whole window. Abdi and Ranaldo
(2017) is the registered estimator; Corwin and Schultz (2012) is computed beside it and
**never substituted for it**, because adopting whichever of two estimators passes, after
seeing which one passed, is selection with one extra step in front of it.

The three clauses, all of which must hold
-----------------------------------------

**Ordering.** Spearman's rho between estimated and measured across the six symbols, at
least 0.771 - the one-tailed 5 per cent critical value at n = 6.

**Magnitude.** The median across the six symbols of the absolute base-2 logarithm of
estimated over measured, at most 1: within a factor of two for at least half of them.

**Positivity.** Over the F1 window, at every month end, for every perpetual with enough
stored history in its trailing window, a strictly positive estimate at least 90 per cent
of the time. A cost model cannot charge zero for a spread.

Two windows, and why they differ
--------------------------------

The **calibration** window is the calendar month containing each sampled day. It
contains the day it is compared against, which would be look-ahead in a trading decision
and is not one here, because nothing is traded on a calibration.

The **application** window is the 30 stored daily bars ending strictly before the
decision instant, which is what the positivity clause is computed over and what prices a
trade from F2. Estimating from the period a trade falls in would price the trade with its
own month.
"""

from __future__ import annotations

import json
import math
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sextant.adapters.storage.panel import RangeRow, load_daily_ranges
from sextant.app.futures_archive import PERP_VENUE
from sextant.app.futures_archive import STORE_ROOT as PERP_ROOT
from sextant.app.spike_006_f1 import (
    ESTIMATOR_FACTOR_LOG2,
    ESTIMATOR_MINIMUM_PAIRS,
    ESTIMATOR_POSITIVE_SHARE,
    ESTIMATOR_RANK_FLOOR,
    ESTIMATOR_RULE,
    ESTIMATOR_TRAILING_DAYS,
    REGISTERED_VERSION,
    RESEARCH_ROOT,
)
from sextant.app.spike_006_f1_spread import SPREAD_RESULTS
from sextant.domain.time import Timeframe
from sextant.engine.execution.spread_estimator import (
    ABDI_RANALDO,
    BASIS_POINTS,
    CORWIN_SCHULTZ,
    DailyRange,
    EstimatedSpread,
    UnusableDailyRange,
    abdi_ranaldo,
    corwin_schultz,
    two_day_terms,
)
from sextant.engine.statistics.rank import spearman_rank_correlation

#: Where the calibration is written. Committed, like every other result file.
ESTIMATOR_RESULTS = RESEARCH_ROOT / "spike-006-f1-estimator.json"

#: The F1 window, as the run itself resolved it. Held here rather than read from the
#: result file so that the positivity clause is computed over a stated window rather
#: than over whatever a file happens to contain.
WINDOW_FIRST_MONTH = "2020-11"
WINDOW_LAST_MONTH = "2026-07"

NEWLINE = chr(10)


class CalibrationIncomplete(RuntimeError):
    """The calibration cannot be assembled as registered, and nothing is substituted."""


@dataclass(frozen=True, slots=True)
class SymbolMonth:
    """One symbol's estimate over one calibration month, against the day measured in it."""

    symbol: str
    month: str
    sampled_day: str
    measured_median_bps: float
    usable_days: int
    unusable_days: int
    registered: EstimatedSpread | None
    comparison: EstimatedSpread | None
    term_standard_deviation: float | None
    """The scatter of the registered estimator's own two-day terms over this month.

    Carried because it, and not the mean, is what decides whether the estimator can
    resolve anything here. Each term estimates the squared spread plus noise whose scale
    is the daily variance; when the noise is orders of magnitude larger than the signal,
    the mean of thirty terms is a draw from the noise and its square root is a spread
    estimate in name only.
    """

    @property
    def ratio(self) -> float | None:
        """Estimated over measured, for the registered estimator."""
        if self.registered is None or self.measured_median_bps <= 0.0:
            return None
        return self.registered.basis_points / self.measured_median_bps

    def as_json(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "calibration_month": self.month,
            "sampled_day": self.sampled_day,
            "measured_median_quoted_spread_bps": self.measured_median_bps,
            "usable_daily_bars": self.usable_days,
            "unusable_daily_bars": self.unusable_days,
            "estimated_over_measured": self.ratio,
            "two_day_term_standard_deviation": self.term_standard_deviation,
            ABDI_RANALDO: None if self.registered is None else self.registered.as_json(),
            CORWIN_SCHULTZ: None if self.comparison is None else self.comparison.as_json(),
        }


@dataclass(frozen=True, slots=True)
class SymbolCalibration:
    """One symbol summarised across the six calibration months and the six sampled days.

    Both sides are medians: the estimate across its six months and the measurement across
    its six days. A median rather than a mean because `API3USDT` changed regime inside the
    sample - 7.6 to 10.3 bps before August 2023, 0.68 to 0.89 after October - and a mean
    of two regimes represents neither.
    """

    symbol: str
    chosen_as: str
    measured_median_bps: float
    estimated_median_bps: float | None
    comparison_median_bps: float | None
    term_standard_deviation: float | None
    """The median, across the six months, of the scatter of the two-day terms."""

    @property
    def measured_squared(self) -> float:
        """The squared proportional spread the estimator would have to resolve."""
        return (self.measured_median_bps / float(BASIS_POINTS)) ** 2

    @property
    def pairs_to_resolve(self) -> float | None:
        """How many two-day terms an average needs before the signal exceeds the noise.

        The standard error of a mean of ``n`` terms is the term scatter over the root of
        ``n``, so resolving a squared spread ``s2`` at one standard error needs
        ``n >= (scatter / s2) ** 2``. Reported per symbol because it forecloses the
        obvious next suggestion: a longer window does not fix this, and this says by how
        far it does not.
        """
        if self.term_standard_deviation is None or self.measured_squared <= 0.0:
            return None
        return (self.term_standard_deviation / self.measured_squared) ** 2

    @property
    def ratio(self) -> float | None:
        if self.estimated_median_bps is None or self.measured_median_bps <= 0.0:
            return None
        return self.estimated_median_bps / self.measured_median_bps

    @property
    def absolute_log2_ratio(self) -> float:
        """``|log2(estimated / measured)|``: how many factors of two the estimate is out.

        Infinite when the estimate is zero, which is the honest encoding: an estimate of
        zero is not within any factor of a positive measurement. It reaches the result
        file as ``null`` beside a flag, because JSON has no infinity.
        """
        ratio = self.ratio
        if ratio is None or ratio <= 0.0:
            return math.inf
        return abs(math.log2(ratio))

    def as_json(self) -> dict[str, object]:
        finite = math.isfinite(self.absolute_log2_ratio)
        return {
            "symbol": self.symbol,
            "chosen_as": self.chosen_as,
            "measured_median_quoted_spread_bps": self.measured_median_bps,
            "estimated_median_bps": self.estimated_median_bps,
            "comparison_estimated_median_bps": self.comparison_median_bps,
            "estimated_over_measured": self.ratio,
            "absolute_log2_ratio": self.absolute_log2_ratio if finite else None,
            "estimate_is_zero": not finite,
            "two_day_term_standard_deviation": self.term_standard_deviation,
            "measured_squared_proportional_spread": self.measured_squared,
            "two_day_pairs_needed_to_resolve_it": self.pairs_to_resolve,
        }


@dataclass(frozen=True, slots=True)
class Positivity:
    """Rule E1's third clause, over every instrument-period the application would ask for."""

    estimator: str
    instrument_periods: int
    positive: int
    floored: int
    too_few_pairs: int
    instruments_scanned: int
    unusable_bars: int

    @property
    def share(self) -> float | None:
        """The share of asked-for periods that came back strictly positive."""
        if self.instrument_periods == 0:
            return None
        return float(self.positive) / float(self.instrument_periods)

    def as_json(self) -> dict[str, object]:
        return {
            "estimator": self.estimator,
            "instruments_scanned": self.instruments_scanned,
            "instrument_periods_asked_for": self.instrument_periods,
            "strictly_positive": self.positive,
            "floored_at_zero": self.floored,
            "too_few_pairs_to_estimate": self.too_few_pairs,
            "unusable_daily_bars": self.unusable_bars,
            "share_strictly_positive": self.share,
            "trailing_days": ESTIMATOR_TRAILING_DAYS,
            "minimum_pairs": ESTIMATOR_MINIMUM_PAIRS,
            "note": (
                "A period with too few usable pairs is NOT counted as a failure of this "
                "clause: it is the case section 33.4 registered a substitution for, and "
                "the instrument is charged at the banded assumption, labelled as one. It "
                "is counted and reported so the size of that substitution is visible."
            ),
        }


@dataclass(frozen=True, slots=True)
class Calibration:
    """Rule E1's three clauses, computed, and the adoption they decide."""

    per_symbol_month: tuple[SymbolMonth, ...]
    per_symbol: tuple[SymbolCalibration, ...]
    positivity: Positivity
    comparison_positivity: Positivity

    @property
    def rank_correlation(self) -> float | None:
        """Clause 1, for the registered estimator."""
        return self._rank(lambda row: row.estimated_median_bps)

    @property
    def comparison_rank_correlation(self) -> float | None:
        """Clause 1 for the comparison, reported and never acted on."""
        return self._rank(lambda row: row.comparison_median_bps)

    def _rank(self, of: Callable[[SymbolCalibration], float | None]) -> float | None:
        """Spearman's rho between the measurement and whichever estimate ``of`` reads.

        ``None`` unless every symbol has an estimate. A rank correlation over a subset of
        the six would be a different statistic from the one rule E1 registered, and
        silently computing it on five is how a clause gets weakened without an edit.
        """
        pairs = [
            (row.measured_median_bps, value)
            for row in self.per_symbol
            if (value := of(row)) is not None
        ]
        if len(pairs) != len(self.per_symbol):
            return None
        return spearman_rank_correlation([x for x, _ in pairs], [y for _, y in pairs])

    @property
    def median_absolute_log2_ratio(self) -> float | None:
        """Clause 2: the median across the six symbols, infinity included."""
        return _median([row.absolute_log2_ratio for row in self.per_symbol])

    @property
    def ordering_holds(self) -> bool:
        statistic = self.rank_correlation
        return statistic is not None and statistic >= float(ESTIMATOR_RANK_FLOOR)

    @property
    def magnitude_holds(self) -> bool:
        statistic = self.median_absolute_log2_ratio
        return statistic is not None and statistic <= float(ESTIMATOR_FACTOR_LOG2)

    @property
    def positivity_holds(self) -> bool:
        share = self.positivity.share
        return share is not None and share >= float(ESTIMATOR_POSITIVE_SHARE)

    @property
    def adopted(self) -> bool:
        """All three clauses. Rule E1 has no partial pass and no relaxation."""
        return self.ordering_holds and self.magnitude_holds and self.positivity_holds

    def as_json(self) -> dict[str, object]:
        return {
            "rule": ESTIMATOR_RULE,
            "registered_version": REGISTERED_VERSION,
            "registered_estimator": ABDI_RANALDO,
            "comparison_estimator": CORWIN_SCHULTZ,
            "calibration": {
                "against": str(SPREAD_RESULTS).replace("\\", "/"),
                "per_symbol_month": [row.as_json() for row in self.per_symbol_month],
                "per_symbol": [row.as_json() for row in self.per_symbol],
                "both_sides_are_medians": (
                    "the estimate across the six calibration months, the measurement "
                    "across the six sampled days"
                ),
                "what_is_compared_is_not_identical": (
                    "The estimator estimates an EFFECTIVE spread and the measurement is a "
                    "QUOTED spread. Where trades print at the touch the two coincide; where "
                    "they do not, effective is the smaller. An estimate below the measurement "
                    "by a small factor is consistent with the estimator being right, and this "
                    "was registered before the numbers rather than offered after them."
                ),
            },
            "clauses": {
                "ordering": {
                    "statistic": self.rank_correlation,
                    "floor": str(ESTIMATOR_RANK_FLOOR),
                    "holds": self.ordering_holds,
                    "comparison_statistic": self.comparison_rank_correlation,
                },
                "magnitude": {
                    "statistic": _finite(self.median_absolute_log2_ratio),
                    "ceiling": str(ESTIMATOR_FACTOR_LOG2),
                    "holds": self.magnitude_holds,
                },
                "positivity": {
                    "floor": str(ESTIMATOR_POSITIVE_SHARE),
                    "holds": self.positivity_holds,
                    "registered_estimator": self.positivity.as_json(),
                    "comparison_estimator": self.comparison_positivity.as_json(),
                },
            },
            "resolution": {
                "question": (
                    "Can either estimator resolve a spread of this size against volatility "
                    "of this size? Each two-day term estimates the squared spread plus noise "
                    "whose scale is the daily variance, so the answer is a ratio rather than "
                    "an opinion."
                ),
                "per_symbol": [
                    {
                        "symbol": row.symbol,
                        "two_day_term_standard_deviation": row.term_standard_deviation,
                        "measured_squared_proportional_spread": row.measured_squared,
                        "two_day_pairs_needed_to_resolve_it": row.pairs_to_resolve,
                    }
                    for row in self.per_symbol
                ],
                "why_a_longer_window_does_not_fix_it": (
                    "The standard error of a mean of n terms falls as the root of n, so "
                    "resolving a squared spread s2 at one standard error needs n of at least "
                    "(scatter / s2) squared. The pair counts above are what that comes to. "
                    "Thirty days against thirty years is not the difference."
                ),
                "the_implementation_is_not_the_explanation": (
                    "tests/unit/test_spread_estimator.py simulates a quote-driven market with "
                    "a known spread and recovers it from both estimators at 100 and at 50 "
                    "basis points, then shows both break down as the spread falls relative to "
                    "the volatility. The estimators are implemented correctly and are being "
                    "asked a question this market cannot answer."
                ),
            },
            "all_three_hold": self.adopted,
            "decision": (
                "ADOPTED as the default spread cost from F2, per instrument and per period, "
                "with the banded assumption retained beside it as a labelled alternative."
                if self.adopted
                else "NOT ADOPTED. The banded assumption is kept, labelled an assumption "
                "exactly as it is now. No third estimator is tried, no clause is relaxed, "
                "and the comparison estimator is not promoted."
            ),
            "changes_no_f1_figure": (
                "Every F1 cell is costed at the registered assumption and stays that way. "
                "Section 7.4 shows F1's verdict survives deleting the assumed cost "
                "entirely, which is a stronger statement than any estimate of it could be."
            ),
            "what_it_cannot_say": [
                "Nothing about slippage. It is an intraday quantity and daily bars cannot "
                "calibrate it. It remains an assumption at its registered figures.",
                "Nothing about the spot leg. The 36 measured symbol-days are perpetual "
                "quotes, so the calibration speaks for the perpetual leg only.",
                "Nothing about the mid and thin bands. Every sampled symbol sits in the "
                "deep band, so the calibration is a deep-band calibration.",
                "Nothing from six symbols about the cross-section at large. Six is what "
                "section 12 registered and it is enough to rank and to scale, not enough "
                "to characterise a distribution.",
            ],
            "window": {
                "calibration_months": sorted({row.month for row in self.per_symbol_month}),
                "positivity_first_month": WINDOW_FIRST_MONTH,
                "positivity_last_month": WINDOW_LAST_MONTH,
            },
        }


def _median(values: Sequence[float]) -> float | None:
    """The median of a sequence, or ``None`` when it is empty. Infinity is a value."""
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _scatter(terms: Sequence[float]) -> float | None:
    """The sample standard deviation of the two-day terms, or ``None`` below two of them."""
    if len(terms) < 2:
        return None
    return statistics.stdev(terms)


def _finite(value: float | None) -> float | None:
    """A float for JSON, or ``None`` where it is not finite. JSON has no infinity."""
    if value is None or not math.isfinite(value):
        return None
    return value


def _millis(day: str) -> int:
    """Midnight UTC on a ``YYYY-MM-DD`` day, in milliseconds."""
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


def _month_of(day: str) -> str:
    """The ``YYYY-MM`` a ``YYYY-MM-DD`` day falls in."""
    return day[:7]


def _month_bounds(month: str) -> tuple[int, int]:
    """The half-open millisecond bounds of one ``YYYY-MM`` calendar month."""
    start = datetime.strptime(month + "-01", "%Y-%m-%d").replace(tzinfo=UTC)
    end = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def month_starts(first: str = WINDOW_FIRST_MONTH, last: str = WINDOW_LAST_MONTH) -> tuple[int, ...]:
    """Every month start in the window, in milliseconds. The instants a decision is made at."""
    out: list[int] = []
    at = datetime.strptime(first + "-01", "%Y-%m-%d").replace(tzinfo=UTC)
    stop = datetime.strptime(last + "-01", "%Y-%m-%d").replace(tzinfo=UTC)
    while at <= stop:
        out.append(int(at.timestamp() * 1000))
        at = (
            at.replace(year=at.year + 1, month=1)
            if at.month == 12
            else at.replace(month=at.month + 1)
        )
    return tuple(out)


def _ranges(
    rows: Sequence[RangeRow], *, starts_millis: int, ends_millis: int
) -> tuple[tuple[DailyRange, ...], int]:
    """The usable daily ranges in a half-open window, and how many were unusable.

    The float crossing for this rule, and the only one: the store holds prices as exact
    decimal text and this is where that text becomes a log-price. Nothing downstream of
    here holds money, and nothing upstream of here holds a float.
    """
    usable: list[DailyRange] = []
    unusable = 0
    for row in rows:
        if not starts_millis <= row.open_time_ms < ends_millis:
            continue
        try:
            usable.append(
                DailyRange(high=float(row.high), low=float(row.low), close=float(row.close))
            )
        except UnusableDailyRange:
            unusable += 1
    return tuple(usable), unusable


def _measured(
    payload: Mapping[str, object],
) -> tuple[
    Mapping[str, str],
    Mapping[tuple[str, str], float],
]:
    """The 36 measured symbol-days, keyed by symbol and day, with how each was chosen."""
    symbols = payload.get("symbols")
    days = payload.get("per_symbol_day")
    if not isinstance(symbols, list) or not isinstance(days, list):
        raise CalibrationIncomplete(
            "the measured spread file carries no symbols or no symbol-days. Rule E1 "
            "cannot be calibrated against a file that is not there, and nothing is "
            "estimated in its place."
        )
    chosen: dict[str, str] = {}
    for entry in symbols:
        if isinstance(entry, dict):
            chosen[str(entry["symbol"])] = str(entry["chosen_as"])
    measured: dict[tuple[str, str], float] = {}
    for entry in days:
        if not isinstance(entry, dict):
            continue
        whole = entry.get("whole_day")
        if not isinstance(whole, dict):
            continue
        median = whole.get("median_bps")
        if median is None:
            continue
        measured[(str(entry["symbol"]), str(entry["day"]))] = float(str(median))
    return chosen, measured


def calibrate(
    *,
    spread_path: Path = SPREAD_RESULTS,
    store_root: Path = PERP_ROOT,
) -> Calibration:
    """Rule E1's three clauses, computed over the measured sample and the whole window."""
    payload = json.loads(spread_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise CalibrationIncomplete(f"{spread_path} does not hold a mapping.")
    chosen, measured = _measured({str(k): v for k, v in payload.items()})
    sampled = sorted(chosen)
    if not sampled:
        raise CalibrationIncomplete("no sampled symbols to calibrate against.")

    print(f"[f1] rule {ESTIMATOR_RULE}: reading daily ranges for {len(sampled)} sampled symbols")
    panel = load_daily_ranges(store_root, PERP_VENUE, Timeframe.D1, symbols=sampled)
    rows: list[SymbolMonth] = []
    for (symbol, day), median_bps in sorted(measured.items()):
        month = _month_of(day)
        starts, ends = _month_bounds(month)
        ranges, unusable = _ranges(panel.get(symbol, ()), starts_millis=starts, ends_millis=ends)
        rows.append(
            SymbolMonth(
                symbol=symbol,
                month=month,
                sampled_day=day,
                measured_median_bps=median_bps,
                usable_days=len(ranges),
                unusable_days=unusable,
                registered=abdi_ranaldo(ranges),
                comparison=corwin_schultz(ranges),
                term_standard_deviation=_scatter(two_day_terms(ranges)),
            )
        )

    per_symbol: list[SymbolCalibration] = []
    for symbol in sampled:
        mine = [row for row in rows if row.symbol == symbol]
        per_symbol.append(
            SymbolCalibration(
                symbol=symbol,
                chosen_as=chosen[symbol],
                measured_median_bps=_require(
                    _median([row.measured_median_bps for row in mine]), symbol
                ),
                estimated_median_bps=_median(
                    [row.registered.basis_points for row in mine if row.registered is not None]
                ),
                comparison_median_bps=_median(
                    [row.comparison.basis_points for row in mine if row.comparison is not None]
                ),
                term_standard_deviation=_median(
                    [
                        row.term_standard_deviation
                        for row in mine
                        if row.term_standard_deviation is not None
                    ]
                ),
            )
        )

    registered, comparison = _positivity(store_root)
    return Calibration(
        per_symbol_month=tuple(rows),
        per_symbol=tuple(per_symbol),
        positivity=registered,
        comparison_positivity=comparison,
    )


def _require(value: float | None, symbol: str) -> float:
    """A measured median that must exist, because 36 of 36 symbol-days were measured."""
    if value is None:
        raise CalibrationIncomplete(
            f"{symbol} has no measured median spread in the sample. The calibration is "
            "not completed against a symbol that was not measured."
        )
    return value


def _positivity(store_root: Path) -> tuple[Positivity, Positivity]:
    """Rule E1's positivity clause, over every instrument-period the application asks for.

    Every perpetual in the store, at every month start in the F1 window, estimated from
    the trailing 30 days ending strictly before that instant - which is exactly the call
    the cost model would make from F2. The scan is the whole store rather than the carry
    universe, because a universe membership is a decision and this clause is about the
    estimator rather than about a strategy.
    """
    print(f"[f1] rule {ESTIMATOR_RULE}: reading daily ranges for every stored perpetual")
    panel = load_daily_ranges(store_root, PERP_VENUE, Timeframe.D1)
    instants = month_starts()
    window = ESTIMATOR_TRAILING_DAYS * 24 * 60 * 60 * 1000
    counts = {ABDI_RANALDO: [0, 0, 0], CORWIN_SCHULTZ: [0, 0, 0]}
    unusable_total = 0
    for symbol in sorted(panel):
        rows = panel[symbol]
        if not rows:
            continue
        for instant in instants:
            ranges, unusable = _ranges(rows, starts_millis=instant - window, ends_millis=instant)
            unusable_total += unusable
            for name, estimate in (
                (ABDI_RANALDO, abdi_ranaldo(ranges)),
                (CORWIN_SCHULTZ, corwin_schultz(ranges)),
            ):
                if estimate is None or estimate.pairs < ESTIMATOR_MINIMUM_PAIRS:
                    counts[name][2] += 1
                elif estimate.relative > 0.0:
                    counts[name][0] += 1
                else:
                    counts[name][1] += 1
    scanned = sum(1 for symbol in panel if panel[symbol])

    def summarise(name: str) -> Positivity:
        positive, floored, too_few = counts[name]
        return Positivity(
            estimator=name,
            instrument_periods=positive + floored,
            positive=positive,
            floored=floored,
            too_few_pairs=too_few,
            instruments_scanned=scanned,
            unusable_bars=unusable_total,
        )

    return summarise(ABDI_RANALDO), summarise(CORWIN_SCHULTZ)


def write(calibration: Calibration, destination: Path) -> Path:
    """Write the calibration. The path is a parameter with no default pointing anywhere real."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline=NEWLINE) as handle:
        json.dump(calibration.as_json(), handle, indent=2, sort_keys=True)
        handle.write(NEWLINE)
    return destination


__all__ = [
    "ESTIMATOR_RESULTS",
    "WINDOW_FIRST_MONTH",
    "WINDOW_LAST_MONTH",
    "Calibration",
    "CalibrationIncomplete",
    "Positivity",
    "SymbolCalibration",
    "SymbolMonth",
    "calibrate",
    "month_starts",
    "write",
]
