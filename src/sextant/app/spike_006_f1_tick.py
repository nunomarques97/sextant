"""Rules T1, T2, T3 and T3H: the tick, the circularity and what the band model becomes.

Amendment 13, section 35. Rule B1 cut the liquidity bands on relative tick size at a
rank correlation of 0.900, and two separate objections land on that result.

**The x-axis was partly impossible.** The tick was derived as the greatest common
divisor of published prices, which is a *multiple* of the true increment. On 5 of 11
sampled symbols it came out wider than that symbol's own measured quoted spread. Rule T1
removes the derivation and reads the venue's own instrument metadata instead.

**The correlation may be circular.** If a symbol's quoted spread *is* one tick, then
correlating spread against tick size measures the tick against itself, and a rho of 0.900
would be mechanical on exactly the instruments a cost model least needs. Rule T2 measures
how many symbols are in that position and recomputes rule B1's own procedure twice: once
on metadata ticks, and once on the non-tick-bound subset alone.

**And if most instruments are tick-bound, a band is the wrong object entirely.** A band
assigns a default to something unmeasured; an instrument whose spread is one tick needs
no default, because its half-spread is ``tick/2`` exactly. Rule T3 replaces the band model
for those instruments with that per-symbol figure and keeps a band default only for the
residual that floats above the tick.

Nothing here reads the endpoint: everything reads the committed snapshot. Nothing here
touches the headline spread level, so nothing here can move a verdict letter.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from sextant.adapters.exchanges.binance import futures_metadata
from sextant.adapters.exchanges.binance.futures_metadata import TickSnapshot
from sextant.app.futures_archive import STORE_ROOT as PERP_ROOT
from sextant.app.spike_006_f1 import (
    BAND_MINIMUM_SYMBOLS,
    CONSTANT_RATIO_RULE,
    PER_SYMBOL_TICK_RULE,
    RATIO_DEEP,
    RATIO_HYPOTHESIS_FACTOR,
    RATIO_MID,
    RESEARCH_ROOT,
    TICK_BOUND_CEILING,
    TICK_BOUND_DOMINANCE,
    TICK_CIRCULARITY_RULE,
    TICK_METADATA_LEVEL,
    TICK_METADATA_RULE,
    TICK_SUBSET_MINIMUM_SYMBOLS,
)
from sextant.app.spike_006_f1_bands import (
    BASIS_POINTS_IN_ONE,
    BandStudyIncomplete,
    Candidate,
    Recut,
    candidates_for,
    measured_half_spreads,
    recut,
    reference_prices,
    scored,
    selection_instants,
)
from sextant.app.spike_006_f1_extended import EXTENDED_RESULTS
from sextant.app.spike_006_f1_spread import SPREAD_RESULTS
from sextant.domain.time import Timestamp

#: Where the venue's answer was snapshotted, and the only thing anything here reads.
TICK_METADATA_SNAPSHOT = RESEARCH_ROOT / "spike-006-f1-tick-metadata.json"

#: Where this study lands. Committed, like every other result file.
TICK_RESULTS = RESEARCH_ROOT / "spike-006-f1-tick.json"

#: The candidate name rule B1 gave the quantity whose x-axis this replaces.
TICK_CANDIDATE = "relative tick size"

NEWLINE = chr(10)


class TickStudyIncomplete(BandStudyIncomplete):
    """The study cannot be assembled as registered, and nothing is substituted."""


@dataclass(frozen=True, slots=True)
class SymbolTickReading:
    """One symbol's spread, expressed in the venue's own increments.

    ``spread_in_ticks`` is the whole quoted spread over the whole tick, not a half over
    a half: the ratio is the same either way and the numerator a reader recognises is
    the published one.
    """

    symbol: str
    metadata_tick: Decimal
    derived_tick: Decimal | None
    reference_price: Decimal
    measured_half_spread_bps: float
    selected_at: str

    @property
    def relative_tick_bps(self) -> float:
        """The increment as a fraction of the price, in basis points."""
        return float(self.metadata_tick / self.reference_price) * float(BASIS_POINTS_IN_ONE)

    @property
    def measured_spread_bps(self) -> float:
        """The whole measured quoted spread, which is twice the half the model charges."""
        return self.measured_half_spread_bps * 2.0

    @property
    def spread_in_ticks(self) -> float:
        """How many increments wide the measured quoted spread is."""
        return self.measured_spread_bps / self.relative_tick_bps

    @property
    def is_tick_bound(self) -> bool:
        """Whether the spread sits at the venue's floor, under rule T2's threshold."""
        return Decimal(str(self.spread_in_ticks)) <= TICK_BOUND_CEILING

    @property
    def spread_is_below_one_tick(self) -> bool:
        """Whether the measured spread came in narrower than a single increment.

        Impossible at a constant tick, and therefore evidence about the tick rather than
        about the spread. Two things can produce it and they are not equally innocent:
        the increment changed inside the window, or the denominator is off, because the
        relative tick divides by a thirty-day median close while the spread was measured
        on six particular days. A shortfall of a few per cent is the second; a factor of
        two is the first.
        """
        return self.spread_in_ticks < 1.0

    @property
    def half_spread_from_the_tick_bps(self) -> float:
        """``tick/2``, which is a tick-bound instrument's half-spread exactly."""
        return self.relative_tick_bps / 2.0

    @property
    def derivation_overestimated(self) -> bool:
        """Whether rule B1's divisor came out wider than this symbol's own spread."""
        if self.derived_tick is None:
            return False
        derived_bps = float(self.derived_tick / self.reference_price) * float(BASIS_POINTS_IN_ONE)
        return derived_bps > self.measured_spread_bps

    @property
    def derived_over_metadata(self) -> float | None:
        """How many times the derivation overstated the increment, where both exist."""
        if self.derived_tick is None or self.metadata_tick <= 0:
            return None
        return float(self.derived_tick / self.metadata_tick)

    def as_json(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "selected_at": self.selected_at,
            "metadata_tick": str(self.metadata_tick),
            "derived_tick": None if self.derived_tick is None else str(self.derived_tick),
            "derived_over_metadata": self.derived_over_metadata,
            "reference_price": str(self.reference_price),
            "relative_tick_bps": self.relative_tick_bps,
            "measured_quoted_spread_bps": self.measured_spread_bps,
            "measured_half_spread_bps": self.measured_half_spread_bps,
            "spread_in_ticks": self.spread_in_ticks,
            "is_tick_bound": self.is_tick_bound,
            "spread_is_below_one_metadata_tick": self.spread_is_below_one_tick,
            "half_spread_from_the_tick_bps": self.half_spread_from_the_tick_bps,
            "the_derivation_overestimated_this_symbol": self.derivation_overestimated,
        }


@dataclass(frozen=True, slots=True)
class Correlation:
    """One rank correlation, computed by rule B1's own procedure on a stated sample."""

    label: str
    symbols: tuple[str, ...]
    candidate: Candidate | None
    refused_because: str | None

    @property
    def rho(self) -> float | None:
        return None if self.candidate is None else self.candidate.rho

    @property
    def p_value(self) -> float | None:
        return None if self.candidate is None else self.candidate.p_value

    def as_json(self) -> dict[str, object]:
        return {
            "on": self.label,
            "symbols": list(self.symbols),
            "symbol_count": len(self.symbols),
            "spearman_rho": self.rho,
            "permutation_p_value": self.p_value,
            "clears_the_registered_level": (
                None if self.candidate is None else self.candidate.clears
            ),
            "refused_because": self.refused_because,
        }


@dataclass(frozen=True, slots=True)
class TickStudy:
    """Rules T1 to T3H, computed: the readings, the two correlations, the collapse."""

    snapshot_digest: str
    snapshot_fetched_at: str
    readings: tuple[SymbolTickReading, ...]
    whole_sample: Correlation
    floating_subset: Correlation
    residual_recut: Recut | None
    ratio_deep: Decimal
    ratio_mid: Decimal

    @property
    def tick_bound(self) -> tuple[str, ...]:
        return tuple(item.symbol for item in self.readings if item.is_tick_bound)

    @property
    def floating(self) -> tuple[str, ...]:
        return tuple(item.symbol for item in self.readings if not item.is_tick_bound)

    @property
    def tick_bound_share(self) -> Decimal | None:
        """What fraction of the measured sample sits at the venue's floor."""
        if not self.readings:
            return None
        return Decimal(len(self.tick_bound)) / Decimal(len(self.readings))

    @property
    def tick_bound_dominates(self) -> bool:
        """Rule T3's condition: more than half the sample needs no band at all."""
        share = self.tick_bound_share
        return share is not None and share > TICK_BOUND_DOMINANCE

    @property
    def overestimated_by_the_derivation(self) -> tuple[str, ...]:
        """Symbols where rule B1's divisor exceeded the symbol's own measured spread."""
        return tuple(item.symbol for item in self.readings if item.derivation_overestimated)

    @property
    def below_one_metadata_tick(self) -> tuple[str, ...]:
        """Symbols whose measured spread came in narrower than one published increment."""
        return tuple(item.symbol for item in self.readings if item.spread_is_below_one_tick)

    @property
    def residual_is_below_the_registered_minimum(self) -> bool:
        """Whether the residual is too small to evidence even one band under rule B1."""
        return 0 < len(self.floating) < BAND_MINIMUM_SYMBOLS

    @property
    def bands_the_evidence_supports(self) -> int:
        """How many band defaults survive rule T3, counting only the residual.

        Zero when every measured instrument is tick-bound: there would be nothing left
        for a default to be a default *for*, and a band with no members is not a band.
        """
        if not self.floating:
            return 0
        if self.residual_recut is None:
            return 1
        return self.residual_recut.bands_supported

    @property
    def ratio_gap(self) -> float:
        """How far apart the two measured assumption-to-measurement ratios are."""
        return float(max(self.ratio_deep, self.ratio_mid) / min(self.ratio_deep, self.ratio_mid))

    def as_json(self) -> dict[str, object]:
        return {
            "rule_t1": {
                "rule": TICK_METADATA_RULE,
                "what_it_is": TICK_METADATA_LEVEL,
                "snapshot": TICK_METADATA_SNAPSHOT.name,
                "response_sha256": self.snapshot_digest,
                "fetched_at": self.snapshot_fetched_at,
                "the_derivation_is_removed_not_improved": (
                    "The greatest common divisor of published prices is a MULTIPLE of the "
                    "increment and equals it only when the sample happens to use every "
                    "one. More history makes it a tighter upper bound on the tick, never a "
                    "measurement of it. The endpoint publishes the quantity itself."
                ),
                "symbols_the_derivation_overestimated": list(self.overestimated_by_the_derivation),
                "symbols_whose_spread_is_below_one_metadata_tick": list(
                    self.below_one_metadata_tick
                ),
                "what_a_spread_below_one_tick_proves": (
                    "A quoted spread cannot be narrower than one increment at a constant "
                    "tick, so a reading below one tick is evidence about the TICK and not "
                    "about the spread. Either the increment changed inside the evaluation "
                    "window, or the denominator is off because the relative tick divides "
                    "by a thirty-day median close while the spread was measured on six "
                    "particular days. A shortfall of a few per cent is the second; a "
                    "factor of two is the first, and the first is exactly the "
                    "point-in-time limitation this rule registered."
                ),
                "point_in_time_limitation": (
                    "TODAY'S tick applied to a historical window. The venue publishes no "
                    "history of its instrument metadata. Registered as an ASSUMPTION with "
                    "that label under invariant 12, never as a measurement."
                ),
            },
            "rule_t2": {
                "rule": TICK_CIRCULARITY_RULE,
                "tick_bound_ceiling_in_ticks": str(TICK_BOUND_CEILING),
                "per_symbol": [item.as_json() for item in self.readings],
                "symbols_measured": len(self.readings),
                "tick_bound": list(self.tick_bound),
                "floats_above_the_tick": list(self.floating),
                "tick_bound_share": (
                    None if (share := self.tick_bound_share) is None else str(share)
                ),
                "correlation_on_the_whole_sample": self.whole_sample.as_json(),
                "correlation_on_the_floating_subset": self.floating_subset.as_json(),
                "minimum_symbols_for_a_subset_correlation": TICK_SUBSET_MINIMUM_SYMBOLS,
                "what_a_tick_bound_only_correlation_would_mean": (
                    "Tick size predicts spread because the spread IS the tick. A true and "
                    "useful fact about those instruments, and an empty one about every "
                    "other - in particular about the thin band, which is the band that "
                    "could not be measured."
                ),
            },
            "rule_t3": {
                "rule": PER_SYMBOL_TICK_RULE,
                "dominance_threshold": str(TICK_BOUND_DOMINANCE),
                "tick_bound_dominates": self.tick_bound_dominates,
                "half_spread_bps_by_symbol_from_the_tick": {
                    item.symbol: item.half_spread_from_the_tick_bps
                    for item in self.readings
                    if item.is_tick_bound
                },
                "residual_that_floats_above_the_tick": list(self.floating),
                "band_defaults_the_evidence_supports": self.bands_the_evidence_supports,
                "residual_is_below_the_registered_minimum": (
                    self.residual_is_below_the_registered_minimum
                ),
                "what_the_residual_minimum_means": (
                    f"Rule B1 requires at least {BAND_MINIMUM_SYMBOLS} sampled symbols "
                    "before a band is a band. A residual smaller than that keeps ONE "
                    "default and that default is an assumption with nothing behind it, "
                    "not an evidenced band. Reported as such rather than counted as a "
                    "band because it happens to be the only one left."
                ),
                "residual_recut": (
                    None if self.residual_recut is None else self.residual_recut.as_json()
                ),
                "what_it_does_not_touch": (
                    "The headline spread level. Rule T3 changes only how the BOUND column "
                    "is computed, and no criterion reads the bound."
                ),
            },
            "rule_t3h": {
                "rule": CONSTANT_RATIO_RULE,
                "status": "hypothesis, not a result",
                "deep_band_ratio": str(self.ratio_deep),
                "mid_band_ratio": str(self.ratio_mid),
                "how_far_apart_the_two_are": self.ratio_gap,
                "falsification_factor": str(RATIO_HYPOTHESIS_FACTOR),
                "what_would_test_it": (
                    "A third measured band. The THIN band is the observation that would "
                    "discriminate, so its absence is not only a gap in coverage: it is the "
                    "gap that would have tested this."
                ),
                "why_it_is_not_adopted": (
                    "Two bands is two points and proves nothing on its own. Registered as a "
                    "hypothesis so that it cannot later be adopted as though it had been "
                    "tested."
                ),
            },
        }


def readings(
    snapshot: TickSnapshot,
    measured: Mapping[str, float],
    prices: Mapping[str, Decimal],
    derived: Mapping[str, float],
    instants: Mapping[str, Timestamp],
) -> tuple[SymbolTickReading, ...]:
    """One reading per measured symbol the venue still lists, at its own instant.

    A symbol the venue no longer publishes metadata for is **left out and named by its
    absence** rather than given a derived tick as a stand-in. Mixing the two sources in
    one column is exactly the substitution rule T1 exists to stop.
    """
    out: list[SymbolTickReading] = []
    for symbol in sorted(measured):
        tick = snapshot.tick(symbol)
        price = prices.get(symbol)
        if tick is None or price is None or price <= 0:
            continue
        relative = derived.get(symbol)
        out.append(
            SymbolTickReading(
                symbol=symbol,
                metadata_tick=tick,
                derived_tick=None if relative is None else Decimal(str(relative)) * price,
                reference_price=price,
                measured_half_spread_bps=measured[symbol],
                selected_at=instants[symbol].isoformat()[:10] if symbol in instants else "",
            )
        )
    return tuple(out)


def correlate(
    label: str,
    items: Sequence[SymbolTickReading],
    *,
    minimum: int,
) -> Correlation:
    """Rule B1's procedure, unchanged, over whichever sample is handed to it.

    Refuses below ``minimum`` rather than reporting a rho nobody could interpret. At four
    symbols the smallest attainable two-sided permutation p-value is one in twenty-four,
    which clears the registered level in the single most extreme arrangement and in no
    other, so a significant result there says only that the ordering was perfect.
    """
    symbols = tuple(item.symbol for item in items)
    if len(items) < minimum:
        return Correlation(
            label=label,
            symbols=symbols,
            candidate=None,
            refused_because=(
                f"{len(items)} symbols, and rule T2 reports a correlation only at "
                f"{minimum} or more. Below that the permutation test cannot produce an "
                "interpretable p-value, so the answer is that the subset is too small to "
                "say rather than a number."
            ),
        )
    candidate = Candidate(
        name=TICK_CANDIDATE,
        values={item.symbol: item.relative_tick_bps for item in items},
        rho=None,
        p_value=None,
    )
    measured = {item.symbol: item.measured_half_spread_bps for item in items}
    return Correlation(
        label=label,
        symbols=symbols,
        candidate=scored(candidate, measured),
        refused_because=None,
    )


def residual_bands(items: Sequence[SymbolTickReading], others: Sequence[Candidate]) -> Recut | None:
    """Rule T3's band model for the residual alone, under rule B1's own thresholds.

    ``None`` when the residual cannot hold even one band at the registered minimum. That
    is an answer: a default for fewer than three measured instruments is a default with
    nothing behind it.
    """
    floating = [item for item in items if not item.is_tick_bound]
    if len(floating) < BAND_MINIMUM_SYMBOLS:
        return None
    measured = {item.symbol: item.measured_half_spread_bps for item in floating}
    tick = Candidate(
        name=TICK_CANDIDATE,
        values={item.symbol: item.relative_tick_bps for item in floating},
        rho=None,
        p_value=None,
    )
    candidates = [tick, *(item for item in others if item.name != TICK_CANDIDATE)]
    return recut(measured, tuple(scored(item, measured) for item in candidates))


def study(
    *,
    snapshot_path: Path = TICK_METADATA_SNAPSHOT,
    spread_path: Path = SPREAD_RESULTS,
    extended_path: Path = EXTENDED_RESULTS,
    store_root: Path = PERP_ROOT,
) -> TickStudy:
    """Rules T1 to T3H, end to end, from the committed snapshot and the committed samples."""
    if not snapshot_path.is_file():
        raise TickStudyIncomplete(
            f"{snapshot_path} does not exist, so rule T1's snapshot has not been taken and "
            "nothing downstream of it may be computed from a derived tick instead."
        )
    snapshot = futures_metadata.read(snapshot_path)
    payload = json.loads(spread_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TickStudyIncomplete(f"{spread_path} does not hold a mapping.")
    measured = measured_half_spreads({str(key): value for key, value in payload.items()})
    extended = extended_path if extended_path.is_file() else None
    if extended is not None:
        extra = json.loads(extended.read_text(encoding="utf-8"))
        if isinstance(extra, dict):
            measured = {
                **measured,
                **measured_half_spreads({str(key): value for key, value in extra.items()}),
            }
    instants = selection_instants(spread_path, extended)
    picked = {symbol: instants[symbol] for symbol in measured if symbol in instants}
    prices = reference_prices(store_root, picked)
    others = candidates_for(store_root, picked)
    derived = next((item.values for item in others if item.name == TICK_CANDIDATE), {})
    items = readings(snapshot, measured, prices, derived, picked)
    if not items:
        raise TickStudyIncomplete(
            "the venue lists none of the measured symbols, so rule T2 has no tick to "
            "express a spread in and nothing is substituted for one."
        )
    return TickStudy(
        snapshot_digest=snapshot.digest,
        snapshot_fetched_at=snapshot.fetched_at,
        readings=items,
        whole_sample=correlate(
            "every measured symbol, on metadata ticks",
            items,
            minimum=BAND_MINIMUM_SYMBOLS,
        ),
        floating_subset=correlate(
            "the non-tick-bound subset alone, on metadata ticks",
            [item for item in items if not item.is_tick_bound],
            minimum=TICK_SUBSET_MINIMUM_SYMBOLS,
        ),
        residual_recut=residual_bands(items, others),
        ratio_deep=RATIO_DEEP,
        ratio_mid=RATIO_MID,
    )


def write(result: TickStudy, destination: Path) -> Path:
    """Write the study. The path is a parameter with no default pointing anywhere real."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline=NEWLINE) as handle:
        json.dump(result.as_json(), handle, indent=2, sort_keys=True)
        handle.write(NEWLINE)
    return destination


__all__ = [
    "TICK_CANDIDATE",
    "TICK_METADATA_SNAPSHOT",
    "TICK_RESULTS",
    "Correlation",
    "SymbolTickReading",
    "TickStudy",
    "TickStudyIncomplete",
    "correlate",
    "readings",
    "residual_bands",
    "study",
    "write",
]
