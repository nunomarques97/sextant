"""The SEXTANT-006 F1 variants: cash-and-carry on spot against the perpetual.

`docs/PRE-REGISTRATION-006-F1.md` is the specification. This module implements
it and nothing else: eight variants, three nulls and the two benchmark shapes the
family needs, all fixed before any funding number was computed.

What a carry position is
-------------------------

A pair. Long ``q`` units of an asset's USDT-quoted **spot** pair and short ``q``
units of its USDT-quoted **perpetual**, the same ``q`` in the same base asset.
With equal base quantity the pair's profit is the change in the *basis* between
the two prices plus the funding stream, and carries no term in the asset's own
price. Equal notional would leave a residual delta exactly the size of the basis,
which is the quantity the whole construction exists to isolate.

The engine tracks a position's value rather than its coin count, so "the same
``q``" is expressed here as equal *notional at entry* on the two legs, which is
the same thing at the instant it is opened - the two legs are priced within a
fraction of a percent of each other, and the difference is the basis itself. What
the pair then holds is two positions that move apart by exactly the basis, which
is what the ledger records.

Why the weights are what they are
----------------------------------

One pair consumes the spot leg's notional in cash plus the futures leg's initial
margin, so ``1 + margin_fraction`` times one leg. At 1x - which is what every
variant here runs at - the sum of that across held pairs is the account's equity.
With ``N`` pairs each leg is therefore ``1 / (N * (1 + margin_fraction))`` of
equity, the long and the short are mirror images, and gross exposure comes to
``2 / (1 + margin_fraction)`` with net exposure of zero. Gross above one is not
leverage here in any risk sense - the two sides cancel - and the constraint that
actually binds is the capital one, which is why it is the capital one that the
pre-registration fixes and this module asserts.

Ranking, and what each variant is asking
-----------------------------------------

``basket`` ranks on liquidity and would hold the same names whatever funding did:
it asks whether carry pays *at all*. ``rank30``, ``rank90`` and ``premium`` rank
on three views of the same underlying quantity and ask whether choosing *which*
assets to carry adds anything. ``positive`` is the only variant whose exposure
varies, which is why the decomposition has something to decompose.

Amendment 6 adds a fourth question, cadence, at exactly one point.
:class:`CadencedCarry` carries it, so :class:`CashAndCarry` still holds no
rebalance frequency of its own and the eight variants registered before the
amendment are unchanged in every field.

Pure computation. No I/O, no clock, no venue - the funding settlements and the
premium series arrive as plain values from the layer that is allowed to read a
store.
"""

from __future__ import annotations

import bisect
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum
from typing import Protocol

from sextant.domain.errors import DomainError
from sextant.domain.instrument import Instrument, InstrumentKey
from sextant.domain.time import Timestamp
from sextant.engine.backtest.allocation import Allocation, LongShortAllocation
from sextant.engine.backtest.market import PointInTimeView
from sextant.engine.execution.funding import FundingSchedule

#: Weights are quantised to this before they reach an allocation, so that a
#: share repeated across positions sums to at most the gross limit rather than
#: to one ulp above it. Same scale the long-only allocation uses.
WEIGHT_SCALE = Decimal("1E-18")

#: How many trailing daily bars the liquidity signal reads. Registered in
#: section 6 rule 5 and section 9.2.
TURNOVER_DAYS = 30


class CarryError(DomainError):
    """A carry construction was asked for something it cannot express."""


class CarryAllocator(Protocol):
    """What a carry construct must offer to be recorded and to be nulled against.

    Narrower than :class:`~sextant.engine.backtest.allocation.Allocator`: the
    return type is a long-short allocation, because the constructs that read a
    record read its weights and need both signs.
    """

    @property
    def name(self) -> str: ...

    @property
    def parameter_set_id(self) -> str: ...

    @property
    def is_cross_sectional(self) -> bool: ...

    @property
    def positions(self) -> int:
        """The REGISTERED position count, which is every construct's denominator."""
        ...

    @property
    def margin_fraction(self) -> Decimal:
        """What one pair consumes beyond its spot leg, as a fraction of it."""
        ...

    def allocate(
        self,
        candidates: Sequence[Instrument],
        at: Timestamp,
        view: PointInTimeView,
    ) -> LongShortAllocation: ...


class CarrySignal(StrEnum):
    """Which quantity a variant ranks the carry universe on."""

    TURNOVER = "turnover"
    """Trailing 30-day median quote turnover of the perpetual leg. No view of
    funding at all: this is the variant that would hold the same names whatever
    the funding stream did."""

    FUNDING_30 = "funding_30"
    """Sum of every rate settled in the trailing 30 days."""

    FUNDING_90 = "funding_90"
    """Sum of every rate settled in the trailing 90 days."""

    PREMIUM = "premium"
    """The perpetual's premium-index close for the last bar closed at or before
    the rebalance instant. Related to funding and not identical to it: the venue
    clamps the funding rate and does not clamp the premium."""


@dataclass(frozen=True, slots=True)
class CarryPair:
    """One asset's two legs, resolved from the candidate set."""

    base: str
    spot: Instrument
    perpetual: Instrument


def pairs_from(candidates: Sequence[Instrument], *, perpetual_venue: str) -> tuple[CarryPair, ...]:
    """Every asset in ``candidates`` that has both legs, ascending by base asset.

    Pairing is by **exact base-asset match**, per section 6 rule 1. A
    scaled-unit perpetual such as ``1000PEPEUSDT`` has base ``1000PEPE`` and
    therefore pairs with nothing, which is the registered behaviour: pairing it
    with ``PEPE`` spot needs a unit-conversion rule, and a rule invented after
    the data was seen is a degree of freedom.

    An asset appearing with two spot legs or two perpetual legs is a defect in
    the candidate set rather than a condition to resolve here, and is refused.
    """
    spots: dict[str, Instrument] = {}
    perps: dict[str, Instrument] = {}
    for item in candidates:
        target = perps if item.venue.name == perpetual_venue else spots
        if item.base in target:
            raise CarryError(
                f"{item.base} appears twice on the same side of the carry universe "
                f"({target[item.base].key} and {item.key}). A pair is two legs, not three."
            )
        target[item.base] = item
    return tuple(
        CarryPair(base=base, spot=spots[base], perpetual=perps[base])
        for base in sorted(set(spots) & set(perps))
    )


def _median_turnover(view: PointInTimeView, instrument: Instrument, days: int) -> Decimal | None:
    """Median quote turnover over the last ``days`` closed bars, or None.

    None when the view holds no bar for the instrument. Computed from the view
    rather than from a precomputed history so that the read is recorded by the
    look-ahead harness like every other read.
    """
    series = view.bars([instrument])[instrument.key]
    if not series:
        return None
    recent = series[-days:]
    values = sorted(bar.quote_volume.amount for bar in recent if bar.quote_volume is not None)
    if not values:
        return None
    middle = len(values) // 2
    if len(values) % 2 == 1:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2


@dataclass(frozen=True, slots=True)
class PremiumIndex:
    """Daily premium-index closes, indexed for a point-in-time lookup.

    Signed: a perpetual trading below its index has a negative premium, which is
    the ordinary state of a falling market and not an error.
    """

    by_instrument: Mapping[InstrumentKey, tuple[tuple[Timestamp, Decimal], ...]]
    _index: dict[InstrumentKey, tuple[int, ...]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        ordered = {
            key: tuple(sorted(value, key=lambda item: item[0]))
            for key, value in self.by_instrument.items()
        }
        object.__setattr__(self, "by_instrument", ordered)
        self._index.clear()
        for key, value in ordered.items():
            self._index[key] = tuple(at.epoch_millis for at, _ in value)

    def at_or_before(self, key: InstrumentKey, at: Timestamp) -> Decimal | None:
        """The last premium close knowable at ``at``, or None when there is none."""
        held = self.by_instrument.get(key)
        if not held:
            return None
        position = bisect.bisect_right(self._index[key], at.epoch_millis) - 1
        return held[position][1] if position >= 0 else None


# ---------------------------------------------------------------------------
# The variants
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CashAndCarry:
    """One pre-registered carry variant: rank the pairs, hold the best N.

    Every variant in section 9.2 is one of these. The differences between them
    are the signal, the position count and whether a sign filter applies, and
    nothing else - no weighting scheme, no threshold ladder and no rebalance
    frequency, which :class:`CadencedCarry` carries instead. Parameters are fixed
    by the pre-registration and nothing fits them.
    """

    label: str
    signal: CarrySignal
    positions: int
    funding: FundingSchedule
    premium: PremiumIndex
    perpetual_venue: str
    margin_fraction: Decimal
    require_positive: bool = False
    """``carry-positive-10`` only. Filters to pairs whose trailing 30-day funding
    is strictly positive before ranking, which is what makes it the one variant
    whose exposure varies and therefore the one the timing null can bite on."""

    def __post_init__(self) -> None:
        if self.positions <= 0:
            raise CarryError(f"{self.label}: positions must be positive, got {self.positions}")
        if self.margin_fraction < 0:
            raise CarryError(f"{self.label}: margin_fraction must not be negative")
        if self.require_positive and self.signal is not CarrySignal.FUNDING_30:
            raise CarryError(
                f"{self.label}: the positive-funding filter is registered against the "
                "30-day funding signal and against no other."
            )

    @property
    def name(self) -> str:
        """Short identifier, recorded on every decision and in the manifest."""
        return self.label

    @property
    def parameter_set_id(self) -> str:
        """The exact parameters, for the trial registry."""
        return (
            f"signal={self.signal.value};positions={self.positions};"
            f"margin_fraction={self.margin_fraction};require_positive={self.require_positive}"
        )

    @property
    def is_cross_sectional(self) -> bool:
        """True: every variant here ranks the whole carry universe against itself."""
        return True

    @property
    def gross_limit(self) -> Decimal:
        """``2 / (1 + margin_fraction)``: two mirrored legs per unit of capital."""
        return Decimal(2) / (Decimal(1) + self.margin_fraction)

    def allocate(
        self,
        candidates: Sequence[Instrument],
        at: Timestamp,
        view: PointInTimeView,
    ) -> LongShortAllocation:
        """Rank the carry universe and open the best ``positions`` pairs."""
        pairs = pairs_from(candidates, perpetual_venue=self.perpetual_venue)
        scored: list[tuple[Decimal, str, CarryPair]] = []
        for pair in pairs:
            value = self._score(pair, at, view)
            if value is None:
                continue
            if self.require_positive and value <= 0:
                continue
            scored.append((value, pair.base, pair))
        scored.sort(key=lambda item: (-item[0], item[1]))
        chosen = [item[2] for item in scored[: self.positions]]
        return build_pair_allocation(
            chosen,
            at,
            candidates_considered=len(pairs),
            positions=self.positions,
            margin_fraction=self.margin_fraction,
            note=(
                f"{len(chosen)} of {self.positions} pairs opened from a carry universe "
                f"of {len(pairs)}"
            ),
        )

    def _score(self, pair: CarryPair, at: Timestamp, view: PointInTimeView) -> Decimal | None:
        """The variant's ranking quantity for one pair, or None when unevaluable."""
        if self.signal is CarrySignal.TURNOVER:
            return _median_turnover(view, pair.perpetual, TURNOVER_DAYS)
        if self.signal is CarrySignal.PREMIUM:
            return self.premium.at_or_before(pair.perpetual.key, at)
        days = 30 if self.signal is CarrySignal.FUNDING_30 else 90
        after = at.plus(-timedelta(days=days))
        if not self.funding.covers(pair.perpetual.key, after, at):
            return None
        return self.funding.total_rate(pair.perpetual.key, after, at)


def build_pair_allocation(
    pairs: Sequence[CarryPair],
    at: Timestamp,
    *,
    candidates_considered: int,
    positions: int,
    margin_fraction: Decimal,
    note: str = "",
) -> LongShortAllocation:
    """Equal capital across ``pairs``, long the spot leg and short the perpetual.

    The denominator is ``positions`` rather than ``len(pairs)``. A variant that
    finds only six qualifying pairs where it registered ten holds six pairs at
    the size ten would have had and leaves the rest of the capital idle earning
    nothing. Rescaling to full investment instead would make a variant that
    could not find candidates quietly more concentrated than the one that could,
    and would erase the exposure difference the timing null exists to measure.

    The share is rounded **down**, so the gross figure is provably at or below
    the stated limit for any position count and the rounding tail stays
    uninvested rather than landing on the last-named pair.
    """
    if positions <= 0:
        raise CarryError(f"positions must be positive, got {positions}")
    gross_limit = Decimal(2) / (Decimal(1) + margin_fraction)
    if not pairs:
        return LongShortAllocation(
            weights=(),
            at=at,
            candidates_considered=candidates_considered,
            gross_limit=gross_limit,
            note=note,
        )
    leg = (Decimal(1) / (Decimal(positions) * (Decimal(1) + margin_fraction))).quantize(
        WEIGHT_SCALE, rounding=ROUND_DOWN
    )
    weights: list[tuple[InstrumentKey, Decimal]] = []
    for pair in pairs:
        weights.append((pair.spot.key, leg))
        weights.append((pair.perpetual.key, -leg))
    return LongShortAllocation(
        weights=tuple(weights),
        at=at,
        candidates_considered=candidates_considered,
        gross_limit=gross_limit,
        note=note,
    )


# ---------------------------------------------------------------------------
# Cadence: amendment 6
# ---------------------------------------------------------------------------


#: Rebalance months are anchored to the calendar rather than to the run, so
#: ``(month - 1) % rebalance_months == 0``. At three that is January, April, July
#: and October, exactly as section 28.2 registers it. Calendar-anchored on purpose:
#: a cadence measured from the window start would move every rebalance date if the
#: fold structure ever changed, and the variant would silently become a different
#: variant.
def is_rebalance_month(at: Timestamp, rebalance_months: int) -> bool:
    """Whether ``at`` falls on one of the variant's rebalance instants."""
    if rebalance_months <= 0:
        raise CarryError(f"rebalance_months must be positive, got {rebalance_months}")
    return (at.value.month - 1) % rebalance_months == 0


@dataclass(slots=True)
class CadencedCarry:
    """A carry variant that only looks at its signal every ``rebalance_months``.

    Registered by amendment 6 for ``carry-rank90-10-quarterly`` and for nothing
    else. The engine still reprices and still records a return every month, so the
    return series is monthly whatever the cadence is; what changes is how often the
    book may be *decided*.

    Between rebalances
    ------------------

    The remembered target is returned unchanged, so the engine finds no difference
    to trade and the book pays nothing. Two things can still remove a pair, and
    neither is a cadence-specific rule:

    **A sourced delisting.** The engine writes down and sells any held position
    whose series end takes a haircut, whatever the allocation asks for. That
    happens to every variant on the same terms.

    **Leaving the carry universe.** A remembered pair whose legs are no longer in
    the candidate set is dropped from the target here. It has to be: keeping it
    would ask the engine to re-buy an instrument that the point-in-time universe no
    longer admits, which is how a delisted name gets re-entered.

    So the book between rebalances can only shrink, never grow, and the capital a
    dropped pair used sits idle until the next rebalance instant. That follows from
    the registered weighting rule rather than adding to it: the denominator is the
    registered position count, so a pair leaving does not concentrate the rest.

    Before the first rebalance instant
    ----------------------------------

    Nothing is held. Section 28.2 registers the signal as evaluated *only* at
    quarterly instants, so a run whose first month is not one of them starts in
    cash rather than taking an extra rebalance the registration does not grant.
    The months this affects are reported.
    """

    inner: CashAndCarry
    rebalance_months: int
    _target: tuple[tuple[InstrumentKey, Decimal], ...] | None = None
    decided_at: list[Timestamp] = field(default_factory=list)
    carried_at: list[Timestamp] = field(default_factory=list)
    idle_before_first: list[Timestamp] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.rebalance_months <= 0:
            raise CarryError(
                f"{self.inner.label}: rebalance_months must be positive, "
                f"got {self.rebalance_months}"
            )

    @property
    def label(self) -> str:
        """The registered variant id, which already names the cadence."""
        return self.inner.label

    @property
    def name(self) -> str:
        return self.inner.label

    @property
    def parameter_set_id(self) -> str:
        """The wrapped parameters plus the cadence, for the trial registry.

        The cadence is in the identifier because two trials that differ only in it
        are two trials, and a registry that could not tell them apart would be
        counting one where the Deflated Sharpe Ratio needs two.
        """
        return f"{self.inner.parameter_set_id};rebalance_months={self.rebalance_months}"

    @property
    def is_cross_sectional(self) -> bool:
        return self.inner.is_cross_sectional

    @property
    def positions(self) -> int:
        """The wrapped variant's registered position count, unchanged."""
        return self.inner.positions

    @property
    def margin_fraction(self) -> Decimal:
        """The wrapped variant's margin fraction, unchanged."""
        return self.inner.margin_fraction

    @property
    def rebalance_count(self) -> int:
        """How many times the signal was actually consulted.

        Printed beside this variant's result everywhere it appears, per section
        28.3: roughly a third of its siblings' count on the same window is thinner
        evidence, and a reader must see that where the number is.
        """
        return len(self.decided_at)

    def allocate(
        self,
        candidates: Sequence[Instrument],
        at: Timestamp,
        view: PointInTimeView,
    ) -> LongShortAllocation:
        """Decide on a rebalance instant; otherwise carry what is already held."""
        pairs = pairs_from(candidates, perpetual_venue=self.inner.perpetual_venue)
        if is_rebalance_month(at, self.rebalance_months):
            allocation = self.inner.allocate(candidates, at, view)
            self._target = allocation.weights
            self.decided_at.append(at)
            return allocation
        if self._target is None:
            self.idle_before_first.append(at)
            return build_pair_allocation(
                (),
                at,
                candidates_considered=len(pairs),
                positions=self.inner.positions,
                margin_fraction=self.inner.margin_fraction,
                note=(
                    "no rebalance instant has been reached yet, so nothing is held; "
                    f"{len(pairs)} pairs in the carry universe"
                ),
            )
        self.carried_at.append(at)
        available = {instrument.key for instrument in candidates}
        kept = tuple((key, weight) for key, weight in self._target if key in available)
        dropped = (len(self._target) - len(kept)) // 2
        return LongShortAllocation(
            weights=kept,
            at=at,
            candidates_considered=len(pairs),
            gross_limit=self.inner.gross_limit,
            note=(
                f"carried between rebalances: {len(kept) // 2} pairs held, "
                f"{dropped} dropped for leaving the carry universe"
            ),
        )


# ---------------------------------------------------------------------------
# The constructs the nulls and the decomposition need
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RecordingCarry:
    """Wraps a carry variant and remembers the path it took.

    Stateful, deliberately. The two nulls are matched against the record rather
    than against a re-derivation, so that the thing they hold constant cannot
    drift from the thing the variant actually did.
    """

    inner: CarryAllocator
    """Either a variant or a cadenced variant. The record must describe what the
    account actually did at every monthly instant, so a cadence wrapper belongs
    inside the recorder rather than outside it: the exposure-matched null draws
    against the pair count the variant was really holding that month, which
    between rebalances is a carried count and not a fresh decision."""
    chosen: dict[Timestamp, tuple[str, ...]] = field(default_factory=dict)
    held: dict[Timestamp, int] = field(default_factory=dict)
    universe: dict[Timestamp, int] = field(default_factory=dict)

    @property
    def name(self) -> str:
        """The wrapped variant's own identifier, unchanged."""
        return self.inner.name

    @property
    def parameter_set_id(self) -> str:
        """The wrapped variant's own parameters, unchanged."""
        return self.inner.parameter_set_id

    @property
    def is_cross_sectional(self) -> bool:
        """The wrapped variant's own label, unchanged."""
        return self.inner.is_cross_sectional

    def allocate(
        self,
        candidates: Sequence[Instrument],
        at: Timestamp,
        view: PointInTimeView,
    ) -> LongShortAllocation:
        """Delegate, and record how many pairs were opened and which."""
        allocation = self.inner.allocate(candidates, at, view)
        bases = tuple(key.symbol for key, weight in allocation.weights if weight > 0)
        self.chosen[at] = bases
        self.held[at] = len(bases)
        self.universe[at] = allocation.candidates_considered
        return allocation

    def held_counts(self) -> Mapping[Timestamp, int]:
        """How many pairs the variant held at each rebalance."""
        return dict(self.held)


@dataclass(frozen=True, slots=True)
class RandomCarry:
    """The exposure-matched null: as many pairs as the variant held, drawn at random.

    Inherits the variant's exposure path exactly and differs from it only in
    which pairs it draws, which is what makes it the null that answers "did
    choosing these assets do anything?". Drawing is without replacement within a
    rebalance, because holding a pair twice is not something a real book can do.

    The generator is constructed per allocator and seeded explicitly. A
    cryptographic one would make the null unreproducible, which is the defect
    here rather than the safeguard.
    """

    label: str
    seed: int
    positions: int
    held: Mapping[Timestamp, int]
    perpetual_venue: str
    margin_fraction: Decimal

    @property
    def name(self) -> str:
        """Identifier recorded on every decision and in the manifest."""
        return self.label

    @property
    def parameter_set_id(self) -> str:
        """The seed and the exposure path this null is matched to."""
        return f"seed={self.seed};positions={self.positions};construct=exposure_matched"

    @property
    def is_cross_sectional(self) -> bool:
        """False: a draw is not a use of the cross-section, and must not read as one."""
        return False

    def allocate(
        self,
        candidates: Sequence[Instrument],
        at: Timestamp,
        view: PointInTimeView,
    ) -> LongShortAllocation:
        """Draw as many pairs as the variant held at this instant."""
        del view
        pairs = pairs_from(candidates, perpetual_venue=self.perpetual_venue)
        wanted = min(self.held.get(at, 0), len(pairs))
        generator = random.Random(f"{self.seed}:{at.isoformat()}")
        drawn = (
            sorted(generator.sample(list(pairs), wanted), key=lambda item: item.base)
            if wanted
            else []
        )
        return build_pair_allocation(
            drawn,
            at,
            candidates_considered=len(pairs),
            positions=self.positions,
            margin_fraction=self.margin_fraction,
            note=f"exposure-matched draw of {wanted} from {len(pairs)}",
        )


@dataclass(frozen=True, slots=True)
class WholeCarryUniverse:
    """The timing null: every pair in the universe, scaled to the variant's exposure.

    The variant's timing and none of its selection. Where the variant held six
    pairs of a registered ten, this holds *all* of them at six tenths of the
    size, so the two books have the same capital at work and differ only in
    which assets carry it.
    """

    label: str
    positions: int
    invested: Mapping[Timestamp, int]
    perpetual_venue: str
    margin_fraction: Decimal

    @property
    def name(self) -> str:
        """Identifier recorded on every decision and in the manifest."""
        return self.label

    @property
    def parameter_set_id(self) -> str:
        """The exposure path this null reproduces."""
        return f"positions={self.positions};construct=timing_null"

    @property
    def is_cross_sectional(self) -> bool:
        """False: holding everything is the absence of a cross-sectional decision."""
        return False

    def allocate(
        self,
        candidates: Sequence[Instrument],
        at: Timestamp,
        view: PointInTimeView,
    ) -> LongShortAllocation:
        """Hold the whole carry universe at the variant's invested fraction."""
        del view
        pairs = pairs_from(candidates, perpetual_venue=self.perpetual_venue)
        held = self.invested.get(at, 0)
        if not pairs or held <= 0:
            return build_pair_allocation(
                (),
                at,
                candidates_considered=len(pairs),
                positions=self.positions,
                margin_fraction=self.margin_fraction,
                note="timing null: nothing held at this instant",
            )
        scale = Decimal(held) / Decimal(self.positions)
        gross_limit = Decimal(2) / (Decimal(1) + self.margin_fraction)
        leg = (scale / (Decimal(len(pairs)) * (Decimal(1) + self.margin_fraction))).quantize(
            WEIGHT_SCALE, rounding=ROUND_DOWN
        )
        weights: list[tuple[InstrumentKey, Decimal]] = []
        for pair in pairs:
            weights.append((pair.spot.key, leg))
            weights.append((pair.perpetual.key, -leg))
        return LongShortAllocation(
            weights=tuple(weights),
            at=at,
            candidates_considered=len(pairs),
            gross_limit=gross_limit,
            note=f"timing null: {len(pairs)} pairs at {held}/{self.positions} exposure",
        )


@dataclass(frozen=True, slots=True)
class SelectionOnlyCarry:
    """The selection effect: the variant's own pairs, scaled to full investment.

    The variant's selection and none of its timing. For a variant that is always
    fully invested this is the variant itself and the two series are identical,
    which is the correct answer and is reported as such rather than hidden: only
    ``carry-positive-10`` can stand aside, so only it can differ here.

    A cadenced variant is accepted too, and for it the construct is the carried book
    scaled to full investment rather than a fresh selection: the effect being isolated
    is the one the variant actually had, cadence included.
    """

    inner: CarryAllocator

    @property
    def name(self) -> str:
        """Identifier recorded on every decision and in the manifest."""
        return f"{self.inner.name}/selection-only"

    @property
    def parameter_set_id(self) -> str:
        """The wrapped variant's parameters, plus the construct."""
        return f"{self.inner.parameter_set_id};construct=selection_only"

    @property
    def is_cross_sectional(self) -> bool:
        """The wrapped variant's own label, unchanged."""
        return self.inner.is_cross_sectional

    def allocate(
        self,
        candidates: Sequence[Instrument],
        at: Timestamp,
        view: PointInTimeView,
    ) -> LongShortAllocation:
        """The same pairs, sized as though the full position count had been found."""
        allocation = self.inner.allocate(candidates, at, view)
        longs = [key for key, weight in allocation.weights if weight > 0]
        if not longs:
            return allocation
        margin = self.inner.margin_fraction
        leg = (Decimal(1) / (Decimal(len(longs)) * (Decimal(1) + margin))).quantize(
            WEIGHT_SCALE, rounding=ROUND_DOWN
        )
        weights: list[tuple[InstrumentKey, Decimal]] = []
        for key, weight in allocation.weights:
            weights.append((key, leg if weight > 0 else -leg))
        return LongShortAllocation(
            weights=tuple(weights),
            at=at,
            candidates_considered=allocation.candidates_considered,
            gross_limit=Decimal(2) / (Decimal(1) + margin),
            note=f"selection only: {len(longs)} pairs at full investment",
        )


@dataclass(frozen=True, slots=True)
class LongSpotUniverse:
    """The long-only benchmark: every spot leg of the carry universe, equally weighted.

    Deliberately long-only and deliberately spot. It is what the same capital
    would have done in the same assets without the short leg, which is the
    comparison that says whether the hedge earned its costs.
    """

    label: str
    perpetual_venue: str

    @property
    def name(self) -> str:
        """Identifier recorded on every decision and in the manifest."""
        return self.label

    @property
    def parameter_set_id(self) -> str:
        """No parameters: this construct holds everything it is given."""
        return "construct=long_spot_universe"

    @property
    def is_cross_sectional(self) -> bool:
        """False: holding everything is the absence of a cross-sectional decision."""
        return False

    def allocate(
        self,
        candidates: Sequence[Instrument],
        at: Timestamp,
        view: PointInTimeView,
    ) -> Allocation:
        """Equal weight across the spot legs of every pair in the universe."""
        del view
        pairs = pairs_from(candidates, perpetual_venue=self.perpetual_venue)
        return Allocation.equal_weight(
            [pair.spot.key for pair in pairs],
            at,
            candidates_considered=len(pairs),
            note=f"long-only spot benchmark over {len(pairs)} paired assets",
        )
