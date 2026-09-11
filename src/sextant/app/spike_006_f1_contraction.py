"""Who survived the contraction, not how many. Amendment 26.1.

Section 21 measured that the venue did not publish one funding settlement, that 423
symbols are missing it, and that the registered rule consequently disqualifies 231 of
339 pairs at one rebalance. It then argued that the rule "can only reduce the
opportunity set".

**That argument is about size and it does not carry to composition.** A rule that
removes two thirds of a universe non-randomly does not merely shrink the opportunity
set; it changes what the opportunity set is. If the survivors differ systematically
from the excluded, that month's return is computed on an unrepresentative slice, and
the direction of the resulting bias is not knowable from the count.

It matters more than an ordinary month because the affected rebalance falls inside the
trailing 24 scored months criterion 6 evaluates. Diluted across 69 months it would be
nothing; it is a twenty-fourth of the test that decides whether the edge persists.

What this module does, and does not do
---------------------------------------

It describes two groups on three attributes that already exist in the world, and
bootstraps each difference by the same method so nothing is chosen per attribute. It
**decides nothing**: criterion 6 is judged on the full series exactly as registered,
and the figures here sit beside it. The rule of section 6 is untouched.

The rebalance is found by computation - the largest month-on-month fall in pair count -
rather than named, so the procedure survives a different dataset and cannot have been
pointed at a month somebody had already looked at.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from itertools import pairwise
from pathlib import Path

from sextant.adapters.exchanges.binance.costs import DEEP_BAND_FLOOR, MID_BAND_FLOOR
from sextant.app.spike_006_f1 import (
    BOOTSTRAP_SEED,
    FUNDING_TRAILING_DAYS,
    RESAMPLES,
)
from sextant.app.spike_006_f1_world import World
from sextant.domain.instrument import Instrument, InstrumentKey
from sextant.domain.time import Timestamp
from sextant.engine.execution.costs import LiquidityBand, MedianTurnoverBands
from sextant.engine.statistics.bootstrap import (
    DifferenceEstimate,
    GroupStatistic,
    difference_with_interval,
    generator_for,
)

#: The attribute names, in the order amendment 26.1 registers them.
#: Where the composition check is written. Its own file rather than a block inside the
#: grid's result: the grid's file is what the verdict was computed from and is never
#: rewritten by something that decides nothing.
CONTRACTION_RESULTS = Path("research") / "spike-006-f1-contraction.json"
ATTRIBUTES = ("median_funding_rate", "contract_age_days", "liquidity_band")

DAYS_PER_YEAR = Decimal(365)


@dataclass(frozen=True, slots=True)
class LegProfile:
    """One perpetual leg, described on the three registered attributes."""

    symbol: str
    median_funding_rate: Decimal | None
    """Median of every rate the venue published in the trailing window.

    A median rather than the trailing *sum*: the excluded group's sum is incomplete by
    construction, so comparing sums would measure the hole rather than the population.
    ``None`` when the venue published nothing at all in the window, which is a
    different fact from a hole and is excluded from the comparison rather than read as
    a zero."""
    settlements_published: int
    contract_age_days: int
    band: LiquidityBand
    funding_interval_hours: int | None
    """The venue's own stated cadence for this contract in the window.

    Carried because it turns out to be the *mechanism*, and it is **described rather
    than tested**: the three attributes amendment 26.1 registers are the three that
    carry an interval, and adding a fourth test after seeing the result would be
    exactly the post-hoc widening the whole task is arranged to prevent. What this
    field supports is an explanation, not a finding."""

    @property
    def annualised_funding_percent(self) -> Decimal | None:
        """The median rate as an annual percentage, for a reader's sense of scale.

        Reported and never compared: the comparison runs on the raw median so that a
        cadence difference between an eight-hourly and a four-hourly contract cannot
        enter it. Two contracts on different cadences annualise differently from the
        same per-settlement rate, and that is a fact about the schedule rather than
        about the carry.
        """
        if self.median_funding_rate is None or self.settlements_published == 0:
            return None
        per_day = Decimal(self.settlements_published) / Decimal(FUNDING_TRAILING_DAYS)
        return self.median_funding_rate * per_day * DAYS_PER_YEAR * 100


@dataclass(frozen=True, slots=True)
class GroupSummary:
    """One group's size and its distribution across the liquidity bands."""

    name: str
    size: int
    with_a_funding_median: int
    band_counts: Mapping[str, int]
    cadence_counts: Mapping[str, int]

    @property
    def deep_share(self) -> float:
        """Fraction in the cheapest band, which is what the band difference tests."""
        if self.size == 0:
            return 0.0
        return self.band_counts.get(LiquidityBand.DEEP.value, 0) / self.size

    def as_json(self) -> dict[str, object]:
        return {
            "group": self.name,
            "size": self.size,
            "legs_with_a_funding_median": self.with_a_funding_median,
            "band_counts": dict(sorted(self.band_counts.items())),
            "band_shares": {
                band: round(count / self.size, 4) if self.size else 0.0
                for band, count in sorted(self.band_counts.items())
            },
            "deep_share": round(self.deep_share, 4),
            "funding_interval_hours_counts": dict(sorted(self.cadence_counts.items())),
        }


@dataclass(frozen=True, slots=True)
class ContractionReport:
    """Everything amendment 26.1 requires about the contraction rebalance."""

    at: Timestamp
    pairs_before: int
    pairs_after: int
    admitted: GroupSummary
    excluded: GroupSummary
    differences: Mapping[str, DifferenceEstimate]
    admitted_profiles: tuple[LegProfile, ...]
    excluded_profiles: tuple[LegProfile, ...]

    @property
    def systematic_attributes(self) -> tuple[str, ...]:
        """Attributes whose interval excludes zero, in registered order."""
        return tuple(
            name
            for name in ATTRIBUTES
            if name in self.differences and self.differences[name].excludes_zero
        )

    @property
    def is_unrepresentative(self) -> bool:
        """Whether the survivors differ systematically on any attribute."""
        return bool(self.systematic_attributes)

    def verdict(self) -> str:
        """The sentence the report carries, in one of exactly two forms."""
        if not self.is_unrepresentative:
            return (
                f"The {self.pairs_after} pairs surviving {self.at.isoformat()[:10]} do not "
                f"differ systematically from the {self.pairs_before - self.pairs_after} "
                "excluded on any of the three registered attributes: every interval spans "
                "zero. The slice is smaller and is not shown to be unrepresentative."
            )
        named = ", ".join(self.systematic_attributes)
        return (
            f"The {self.pairs_after} pairs surviving {self.at.isoformat()[:10]} differ "
            f"systematically from the {self.pairs_before - self.pairs_after} excluded on: "
            f"{named}. That month's return is therefore computed on an unrepresentative "
            "slice, and the direction of the bias is not knowable from the count alone. "
            "The headline is reported with and without this rebalance."
        )

    def mechanism(self) -> str:
        """Why these legs and not others, from the cadence distribution.

        Described rather than tested, and stated because a finding whose cause is
        known is a different thing to act on from one whose cause is not. The
        registered rule did not select on age or on liquidity: it selected on whether
        a contract had a settlement at the missing instant, which only a contract on a
        fast cadence does. Age and liquidity follow from that, because the venue puts
        newer and more volatile contracts on faster funding.
        """
        admitted = _dominant(self.admitted.cadence_counts)
        excluded = _dominant(self.excluded.cadence_counts)
        if admitted is None or excluded is None or admitted == excluded:
            return (
                "The two groups do not separate on the venue's funding cadence, so the "
                "cadence explanation does not hold here and the cause is unexplained."
            )
        return (
            f"The split is on the venue's funding cadence, not on anything the rule names: "
            f"{self.admitted.cadence_counts.get(admitted, 0)} of {self.admitted.size} admitted "
            f"legs settle every {admitted} hours, against "
            f"{self.excluded.cadence_counts.get(excluded, 0)} of {self.excluded.size} excluded "
            f"legs every {excluded} hours. Only a contract on the faster cadence has a "
            "settlement at the instant the venue failed to publish, so the rule removed that "
            "population almost entirely. Contract age and liquidity differ as a consequence, "
            "because the venue assigns faster funding to newer and more volatile contracts."
        )

    def as_json(self) -> dict[str, object]:
        return {
            "at": self.at.isoformat(),
            "pairs_before": self.pairs_before,
            "pairs_after": self.pairs_after,
            "pairs_excluded": self.pairs_before - self.pairs_after,
            "groups": [self.admitted.as_json(), self.excluded.as_json()],
            "differences": {
                name: estimate.as_json() for name, estimate in sorted(self.differences.items())
            },
            "systematic_attributes": list(self.systematic_attributes),
            "is_unrepresentative": self.is_unrepresentative,
            "verdict": self.verdict(),
            "mechanism": self.mechanism(),
            "mechanism_status": (
                "DESCRIBED, NOT TESTED. The funding cadence is not one of the three attributes "
                "amendment 26.1 registers, carries no interval, and supports no verdict. Adding "
                "a fourth test after seeing the result would be the post-hoc widening this task "
                "is arranged to prevent."
            ),
            "what_this_decides": (
                "Nothing. Criterion 6 is judged on the full series exactly as registered; "
                "these figures sit beside it, and section 6 rule 4 is unchanged."
            ),
            "difference_convention": (
                "admitted minus excluded, so a positive difference means the surviving group "
                "scores higher on that attribute."
            ),
        }


def largest_contraction(world: World) -> tuple[Timestamp, int, int] | None:
    """The rebalance with the biggest month-on-month fall in pair count.

    Found rather than named, so this cannot have been pointed at a month somebody had
    already examined. ``None`` when the universe never contracts, which would be a
    surprise worth its own line in the report rather than a silent skip.
    """
    ordered = sorted(world.universe.bases)
    if len(ordered) < 2:
        return None
    worst: tuple[Timestamp, int, int] | None = None
    for previous, current in pairwise(ordered):
        before = len(world.universe.bases[previous])
        after = len(world.universe.bases[current])
        if worst is None or (after - before) < (worst[2] - worst[1]):
            worst = (current, before, after)
    if worst is None or worst[2] >= worst[1]:
        return None
    return worst


def _profile(
    world: World,
    instrument: Instrument,
    at: Timestamp,
    bands: MedianTurnoverBands,
) -> LegProfile:
    """One perpetual leg on the three registered attributes."""
    after = at.plus(-timedelta(days=FUNDING_TRAILING_DAYS))
    published = world.funding.settlements(instrument.key, after, at)
    rates = sorted(item.rate for item in published)
    median = _median_of(rates)
    age = (at.epoch_millis - instrument.listed_at.epoch_millis) // (24 * 60 * 60 * 1000)
    return LegProfile(
        symbol=instrument.symbol,
        median_funding_rate=median,
        settlements_published=len(published),
        contract_age_days=int(age),
        band=bands.band_at(instrument.key, at),
        funding_interval_hours=published[0].interval_hours if published else None,
    )


def _median_of(values: Sequence[Decimal]) -> Decimal | None:
    """Exact median of published rates, or None when nothing was published.

    Decimal throughout: a funding rate is five significant figures of a very small
    number, and the whole comparison is between two such numbers.
    """
    if not values:
        return None
    middle = len(values) // 2
    if len(values) % 2 == 1:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2


def _summarise(name: str, profiles: Sequence[LegProfile]) -> GroupSummary:
    """One group's size and band distribution."""
    counts: dict[str, int] = {}
    cadences: dict[str, int] = {}
    for profile in profiles:
        counts[profile.band.value] = counts.get(profile.band.value, 0) + 1
        label = (
            "unpublished"
            if profile.funding_interval_hours is None
            else str(profile.funding_interval_hours)
        )
        cadences[label] = cadences.get(label, 0) + 1
    return GroupSummary(
        name=name,
        size=len(profiles),
        with_a_funding_median=sum(1 for item in profiles if item.median_funding_rate is not None),
        band_counts=counts,
        cadence_counts=cadences,
    )


def examine(world: World) -> ContractionReport | None:
    """Describe the contraction rebalance's two groups. Decides nothing.

    ``None`` when the universe never contracts. The caller reports that rather than
    treating it as nothing to say.
    """
    found = largest_contraction(world)
    if found is None:
        return None
    at, before, after = found
    bands = MedianTurnoverBands(
        histories=world.perpetual_histories,
        deep_floor=DEEP_BAND_FLOOR,
        mid_floor=MID_BAND_FLOOR,
    )
    admitted_bases = set(world.universe.bases[at])
    previous = sorted(world.universe.bases)[sorted(world.universe.bases).index(at) - 1]
    excluded_bases = set(world.universe.bases[previous]) - admitted_bases
    perpetuals = _perpetuals_by_base(world)

    admitted_profiles = tuple(
        _profile(world, perpetuals[base], at, bands)
        for base in sorted(admitted_bases)
        if base in perpetuals
    )
    excluded_profiles = tuple(
        _profile(world, perpetuals[base], at, bands)
        for base in sorted(excluded_bases)
        if base in perpetuals
    )
    generator = generator_for(BOOTSTRAP_SEED)
    differences: dict[str, DifferenceEstimate] = {}

    left_rates = [
        float(item.median_funding_rate)
        for item in admitted_profiles
        if item.median_funding_rate is not None
    ]
    right_rates = [
        float(item.median_funding_rate)
        for item in excluded_profiles
        if item.median_funding_rate is not None
    ]
    if left_rates and right_rates:
        differences["median_funding_rate"] = difference_with_interval(
            left_rates,
            right_rates,
            statistic=GroupStatistic.MEDIAN,
            generator=generator,
            resamples=RESAMPLES,
        )
    if admitted_profiles and excluded_profiles:
        differences["contract_age_days"] = difference_with_interval(
            [float(item.contract_age_days) for item in admitted_profiles],
            [float(item.contract_age_days) for item in excluded_profiles],
            statistic=GroupStatistic.MEDIAN,
            generator=generator,
            resamples=RESAMPLES,
        )
        differences["liquidity_band"] = difference_with_interval(
            [1.0 if item.band is LiquidityBand.DEEP else 0.0 for item in admitted_profiles],
            [1.0 if item.band is LiquidityBand.DEEP else 0.0 for item in excluded_profiles],
            statistic=GroupStatistic.MEAN,
            generator=generator,
            resamples=RESAMPLES,
        )

    return ContractionReport(
        at=at,
        pairs_before=before,
        pairs_after=after,
        admitted=_summarise("admitted", admitted_profiles),
        excluded=_summarise("excluded_by_funding_evaluability", excluded_profiles),
        differences=differences,
        admitted_profiles=admitted_profiles,
        excluded_profiles=excluded_profiles,
    )


def _dominant(counts: Mapping[str, int]) -> str | None:
    """The cadence most of a group shares, or None when nothing dominates.

    "Dominant" means a strict majority. A plurality would let a 40/35/25 split be
    described as though it were a clean separation, which is the kind of overstatement
    a mechanism sentence should not make.
    """
    if not counts:
        return None
    total = sum(counts.values())
    label, count = max(counts.items(), key=lambda item: (item[1], item[0]))
    return label if count * 2 > total else None


def _perpetuals_by_base(world: World) -> Mapping[str, Instrument]:
    """Every perpetual leg the world holds, keyed by base asset."""
    return {
        instrument.base: instrument
        for key, instrument in world.instruments.items()
        if key in world.perpetual_histories
    }


def without_month(
    series: Sequence[tuple[Timestamp, Decimal]], excluded: Timestamp
) -> tuple[tuple[Timestamp, Decimal], ...]:
    """The same monthly series with one month's observation removed.

    The month is identified by calendar year and month rather than by exact instant,
    because a monthly series is stamped at whichever boundary its own convention uses
    and a run should not depend on which.
    """
    target = (excluded.value.year, excluded.value.month)
    return tuple((at, value) for at, value in series if (at.value.year, at.value.month) != target)


def excluded_key(world: World) -> InstrumentKey | None:
    """Present so a caller can name one affected contract in prose. Diagnostic only."""
    found = largest_contraction(world)
    if found is None:
        return None
    at, _, _ = found
    ordered = sorted(world.universe.bases)
    previous = ordered[ordered.index(at) - 1]
    lost = sorted(set(world.universe.bases[previous]) - set(world.universe.bases[at]))
    perpetuals = _perpetuals_by_base(world)
    return perpetuals[lost[0]].key if lost and lost[0] in perpetuals else None


__all__ = [
    "ATTRIBUTES",
    "CONTRACTION_RESULTS",
    "ContractionReport",
    "GroupSummary",
    "LegProfile",
    "examine",
    "excluded_key",
    "largest_contraction",
    "without_month",
]


def write(report: ContractionReport, destination: Path) -> Path:
    """Write the composition check. The destination is a parameter, always."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report.as_json(), indent=2, sort_keys=True), encoding="utf-8")
    return destination
