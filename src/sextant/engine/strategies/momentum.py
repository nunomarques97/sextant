"""The sixteen pre-registered variants, and the constructs that decompose them.

Everything here implements `docs/PRE-REGISTRATION-005.md` part 1 section 8 and
nothing else. No parameter is chosen here; every one arrives from the grid the
pre-registration fixed, and a value that is not in that grid produces a
post-hoc trial that the registry has to carry separately.

Exposure is a weight, not a flag
---------------------------------

Both shapes give every held name a weight of exactly ``1/N``, where *N* is the
variant's position count. For the cross-sectional shape that normally sums to
one, because it holds *N* names. For the long/flat shape it sums to
``held / N``, and the remainder is cash earning nothing. That is the whole
mechanism by which a trend variant reduces its market exposure, and expressing
it as a weight rather than as a separate cash instrument is what lets the same
three constructs decompose both shapes without special-casing either.

The three constructs, and what each one removes
------------------------------------------------

* :class:`FullyInvested` keeps the variant's *selection* and deletes its timing,
  by rescaling whatever it chose to sum to one;
* :class:`ExposureMatched` keeps the variant's *timing* and deletes its
  selection, by drawing the same number of names uniformly at every rebalance;
* :class:`ScaledUniverse` also keeps only the timing, deterministically, by
  holding the whole executable universe at the variant's own invested fraction.

The second and third answer different questions and both are required. The
random one says whether the selection beat chance at that exposure; the
deterministic one says what the exposure path alone was worth. A variant that
beats the first and not the second timed the market, and the report has to be
able to say so.

Signals are computed from what the recorder saw
------------------------------------------------

:class:`Recording` wraps a variant and remembers, per rebalance instant, which
names it chose and what fraction it invested. The nulls are built from that
record rather than from a re-derivation, so an exposure-matched null is matched
to the path the variant actually took and not to a second guess at it.

Pure computation over an already-resolved point-in-time view. No I/O, no venue,
no clock, no float: every signal is a ``Decimal`` ratio of two ``Decimal``
prices.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum

from sextant.domain.errors import DomainError
from sextant.domain.instrument import Instrument, InstrumentKey
from sextant.domain.market_data import Bar
from sextant.domain.time import Timestamp
from sextant.engine.backtest.allocation import Allocation, Allocator, InvalidAllocation
from sextant.engine.backtest.market import PointInTimeView

#: Fraction of the daily bars inside a lookback window that must be present for
#: the window to carry a signal at all. Part 1 section 8.1.
MINIMUM_COVERAGE = Decimal("0.9")

#: Weights are quantised to this before they reach an allocation, so that
#: ``1/3`` sums to at most one across three positions rather than to
#: ``1.000...1``. Matches the scale the allocation type uses for equal weight.
WEIGHT_SCALE = Decimal("1E-18")


class SignalError(DomainError):
    """A variant was asked for something its specification does not define."""


class TrendRule(StrEnum):
    """The two long/flat trend tests part 1 section 8.3 fixes."""

    RETURN_SIGN = "return_sign"
    ABOVE_SMA = "above_sma"


# ---------------------------------------------------------------------------
# The signal
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Window:
    """One instrument's lookback, and whether it carries a signal at all."""

    far_close: Decimal | None
    last_close: Decimal | None
    closes_in_window: tuple[Decimal, ...]
    required: int

    @property
    def is_evaluable(self) -> bool:
        """Both ends present, and enough of the days in between.

        Three conditions, and all three are the pre-registered rule rather than
        a convenience: a close at or before the far end, a close at or before
        the near end, and at least 90 per cent of the daily bars between them.
        An instrument that fails any of them is neither ranked nor held, and is
        counted as not-evaluable rather than silently dropped.
        """
        return (
            self.far_close is not None
            and self.last_close is not None
            and self.far_close > 0
            and len(self.closes_in_window) >= self.required
        )


def lookback_window(series: Sequence[Bar], at: Timestamp, lookback_days: int) -> Window:
    """Cut one instrument's bars into the pre-registered lookback window.

    ``series`` is what the point-in-time view returned, so nothing in it closes
    after ``at`` and the caller cannot reach a price it could not have seen.
    """
    far_end = at.plus(-timedelta(days=lookback_days))
    far: Decimal | None = None
    inside: list[Decimal] = []
    for bar in series:
        close = bar.close.amount
        if bar.close_time <= far_end:
            far = close
        elif bar.close_time <= at:
            inside.append(close)
    last = inside[-1] if inside else None
    required = int((MINIMUM_COVERAGE * Decimal(lookback_days)).to_integral_value())
    return Window(
        far_close=far,
        last_close=last,
        closes_in_window=tuple(inside),
        required=required,
    )


def signal_for(window: Window, rule: TrendRule | None) -> Decimal | None:
    """The variant's signal over one window, or None when it is not evaluable.

    ``rule`` of None is the total-return signal both the cross-sectional shape
    and ``return_sign`` use; ``ABOVE_SMA`` is the price against its own moving
    average. Both are written out in part 1 sections 8.2 and 8.3.
    """
    if not window.is_evaluable:
        return None
    last = window.last_close
    far = window.far_close
    if last is None or far is None:  # narrowed by is_evaluable; kept for the type
        return None
    if rule is TrendRule.ABOVE_SMA:
        average = sum(window.closes_in_window, Decimal(0)) / Decimal(len(window.closes_in_window))
        if average <= 0:
            return None
        return last / average - Decimal(1)
    return last / far - Decimal(1)


def _ranked(
    scores: Mapping[InstrumentKey, Decimal],
) -> tuple[InstrumentKey, ...]:
    """Signal descending, ties broken by symbol ascending. Part 1 section 8.1."""
    return tuple(
        key for key, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0].symbol))
    )


def _at_share(
    keys: Sequence[InstrumentKey], positions: int, at: Timestamp, *, considered: int, note: str
) -> Allocation:
    """Weight each name at exactly ``1/positions``, leaving the rest in cash."""
    if not keys:
        return Allocation(weights=(), at=at, candidates_considered=considered, note=note)
    share = (Decimal(1) / Decimal(positions)).quantize(WEIGHT_SCALE)
    return Allocation(
        weights=tuple((key, share) for key in keys),
        at=at,
        candidates_considered=considered,
        note=note,
    )


# ---------------------------------------------------------------------------
# The two pre-registered shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CrossSectionalMomentum:
    """Rank the whole universe on total return, hold the top N. Part 1 8.2."""

    lookback_days: int
    positions: int

    def __post_init__(self) -> None:
        if self.lookback_days <= 0 or self.positions <= 0:
            raise SignalError("lookback and positions must both be positive")

    @property
    def name(self) -> str:
        """Identifier recorded on every decision and in the manifest."""
        return f"xs-momentum-{self.lookback_days}-{self.positions}"

    @property
    def parameter_set_id(self) -> str:
        """The grid point this variant occupies."""
        return f"lookback_days={self.lookback_days};positions={self.positions}"

    @property
    def is_cross_sectional(self) -> bool:
        """True: the rank depends on every other candidate in the slice."""
        return True

    def allocate(
        self,
        candidates: Sequence[Instrument],
        at: Timestamp,
        view: PointInTimeView,
    ) -> Allocation:
        """Top N by trailing return, equally weighted at 1/N each."""
        series = view.bars(candidates)
        scores: dict[InstrumentKey, Decimal] = {}
        for item in candidates:
            score = signal_for(lookback_window(series[item.key], at, self.lookback_days), rule=None)
            if score is not None:
                scores[item.key] = score
        chosen = _ranked(scores)[: self.positions]
        return _at_share(
            chosen,
            self.positions,
            at,
            considered=len(candidates),
            note=(
                f"{len(scores)} of {len(candidates)} candidates carried an evaluable "
                f"{self.lookback_days}-day signal; held the top {len(chosen)}"
            ),
        )


@dataclass(frozen=True, slots=True)
class TimeSeriesTrend:
    """Each instrument in or out on its own trend. Part 1 8.3.

    The family whose exposure varies. When nothing passes its trend test the
    allocation is empty and the whole account sits in cash earning zero, which
    is a position and is accounted for as one.
    """

    lookback_days: int
    positions: int
    rule: TrendRule

    def __post_init__(self) -> None:
        if self.lookback_days <= 0 or self.positions <= 0:
            raise SignalError("lookback and positions must both be positive")

    @property
    def name(self) -> str:
        """Identifier recorded on every decision and in the manifest."""
        short = "ret" if self.rule is TrendRule.RETURN_SIGN else "sma"
        return f"ts-trend-{self.lookback_days}-{short}"

    @property
    def parameter_set_id(self) -> str:
        """The grid point this variant occupies."""
        return (
            f"lookback_days={self.lookback_days};positions={self.positions};rule={self.rule.value}"
        )

    @property
    def is_cross_sectional(self) -> bool:
        """False, and it says so.

        Each instrument's trend test is its own. The cap at N positions ranks
        the survivors against each other, but an instrument's eligibility does
        not depend on what else is listed, and labelling this cross-sectional
        would let a per-symbol signal be read as a breadth result.
        """
        return False

    def allocate(
        self,
        candidates: Sequence[Instrument],
        at: Timestamp,
        view: PointInTimeView,
    ) -> Allocation:
        """Hold the passing names, up to N, each at 1/N. The rest is cash."""
        series = view.bars(candidates)
        passing: dict[InstrumentKey, Decimal] = {}
        evaluable = 0
        for item in candidates:
            score = signal_for(
                lookback_window(series[item.key], at, self.lookback_days), rule=self.rule
            )
            if score is None:
                continue
            evaluable += 1
            if score > 0:
                passing[item.key] = score
        chosen = _ranked(passing)[: self.positions]
        return _at_share(
            chosen,
            self.positions,
            at,
            considered=len(candidates),
            note=(
                f"{len(passing)} of {evaluable} evaluable candidates were in trend; "
                f"held {len(chosen)} at {self.positions} slots, "
                f"invested {len(chosen)}/{self.positions}"
            ),
        )


# ---------------------------------------------------------------------------
# Recording what a variant actually did
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Recording:
    """Wraps a variant and remembers the path it took.

    Stateful, and deliberately so: the record is what the two nulls are matched
    against, and matching them to a re-derivation instead would mean the null
    could drift from the thing it is supposed to hold constant.
    """

    inner: Allocator
    positions: int
    chosen: dict[Timestamp, tuple[InstrumentKey, ...]] = field(default_factory=dict)
    invested: dict[Timestamp, Decimal] = field(default_factory=dict)
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
    ) -> Allocation:
        """Delegate, and record the selection and the exposure it implied."""
        allocation = self.inner.allocate(candidates, at, view)
        self.chosen[at] = allocation.keys
        self.invested[at] = allocation.invested_fraction
        self.universe[at] = len(candidates)
        return allocation

    def held_counts(self) -> Mapping[Timestamp, int]:
        """How many names the variant held at each rebalance."""
        return {at: len(keys) for at, keys in self.chosen.items()}


# ---------------------------------------------------------------------------
# The three decomposition constructs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FullyInvested:
    """The variant's selection with its timing deleted. Part 1 section 9.

    Same names, rescaled to sum to one. Where the variant held nothing at all
    there is nothing to rescale and this holds nothing either: a month the
    variant spent in cash is a month its selection made no claim about, and
    inventing one would be the report answering a question nobody asked.
    """

    inner: Allocator

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
    ) -> Allocation:
        """The same names, weighted to full investment."""
        allocation = self.inner.allocate(candidates, at, view)
        keys = allocation.keys
        if not keys:
            return Allocation(
                weights=(),
                at=at,
                candidates_considered=allocation.candidates_considered,
                note="the variant held nothing here; its selection claims nothing",
            )
        return Allocation.equal_weight(
            keys,
            at,
            candidates_considered=allocation.candidates_considered,
            note=f"the variant's {len(keys)} names, rescaled to full investment",
        )


@dataclass(slots=True)
class ExposureMatched:
    """The variant's exposure with its selection deleted. Part 1 section 9.

    Draws uniformly without replacement exactly as many names as the variant
    held at that rebalance, at the same ``1/N`` weight, so the cash fraction
    matches too. Stateful for the same reason the fully-invested random null is:
    one seed describes one whole path, not a fresh draw per month.
    """

    seed: int
    positions: int
    held: Mapping[Timestamp, int]
    label: str = "exposure-matched-null"
    _generator: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._generator = random.Random(self.seed)

    @classmethod
    def for_seed(
        cls, seed: int, positions: int, held: Mapping[Timestamp, int], label: str
    ) -> ExposureMatched:
        """A fresh construct for one seed. Never reuse one across runs."""
        return cls(seed=seed, positions=positions, held=held, label=label)

    @property
    def name(self) -> str:
        """Identifier recorded on every decision and in the manifest."""
        return self.label

    @property
    def parameter_set_id(self) -> str:
        """Positions and seed together identify one path through the null."""
        return f"positions={self.positions};seed={self.seed}"

    @property
    def is_cross_sectional(self) -> bool:
        """True: the draw is from the cross-section and depends on its size."""
        return True

    def allocate(
        self,
        candidates: Sequence[Instrument],
        at: Timestamp,
        view: PointInTimeView,
    ) -> Allocation:
        """Draw the variant's own position count, uniformly, at 1/N each."""
        del view
        wanted = self.held.get(at, 0)
        keys = [item.key for item in candidates]
        if wanted <= 0 or not keys:
            return Allocation(
                weights=(),
                at=at,
                candidates_considered=len(keys),
                note="the variant held nothing here, so neither does its exposure match",
            )
        if len(keys) <= wanted:
            chosen = sorted(keys)
            note = f"universe of {len(keys)} is at or below the {wanted} the variant held"
        else:
            chosen = sorted(self._generator.sample(keys, wanted))
            note = f"drew {wanted} of {len(keys)} without replacement, matching the variant"
        return _at_share(chosen, self.positions, at, considered=len(keys), note=note)


@dataclass(frozen=True, slots=True)
class ScaledUniverse:
    """The variant's exposure path applied to the whole universe. Part 1 §9.

    The deterministic half of the timing question. It holds every executable
    instrument, scaled so that the total invested fraction equals what the
    variant invested at the same rebalance. Beating this is not a selection
    claim; the gap between this and the exposure-matched null is what tells a
    reader whether a win came from choosing names or from standing aside.
    """

    invested: Mapping[Timestamp, Decimal]
    label: str = "timing-null"

    @property
    def name(self) -> str:
        """Identifier recorded on every decision and in the manifest."""
        return self.label

    @property
    def parameter_set_id(self) -> str:
        """The construct carries no parameter of its own."""
        return "construct=timing_null"

    @property
    def is_cross_sectional(self) -> bool:
        """True: it holds the whole slice, so its weights depend on its size."""
        return True

    def allocate(
        self,
        candidates: Sequence[Instrument],
        at: Timestamp,
        view: PointInTimeView,
    ) -> Allocation:
        """The whole universe, scaled to the variant's invested fraction."""
        del view
        fraction = self.invested.get(at, Decimal(0))
        keys = sorted(item.key for item in candidates)
        if fraction <= 0 or not keys:
            return Allocation(
                weights=(),
                at=at,
                candidates_considered=len(keys),
                note="the variant was in cash here, so the timing null is too",
            )
        share = (fraction / Decimal(len(keys))).quantize(WEIGHT_SCALE)
        if share <= 0:
            return Allocation(
                weights=(),
                at=at,
                candidates_considered=len(keys),
                note=(
                    f"a {fraction} exposure across {len(keys)} names rounds to nothing at "
                    "the weight scale; held nothing rather than a fabricated minimum"
                ),
            )
        try:
            return Allocation(
                weights=tuple((key, share) for key in keys),
                at=at,
                candidates_considered=len(keys),
                note=f"whole universe of {len(keys)} at the variant's {fraction} exposure",
            )
        except InvalidAllocation:  # rounding pushed the sum over one
            trimmed = keys[:-1]
            return Allocation(
                weights=tuple((key, share) for key in trimmed),
                at=at,
                candidates_considered=len(keys),
                note=(
                    f"whole universe of {len(keys)} at the variant's {fraction} exposure, "
                    "one name dropped so the quantised weights sum to at most one"
                ),
            )


# ---------------------------------------------------------------------------
# The pre-registered grid
# ---------------------------------------------------------------------------

#: Part 1 section 8.2 and 8.3, verbatim. The runner iterates this and nothing
#: else; a variant absent from here is a post-hoc trial by construction.
LOOKBACKS: tuple[int, ...] = (30, 90, 180, 360)
CROSS_SECTIONAL_POSITIONS: tuple[int, ...] = (5, 10)
TIME_SERIES_POSITIONS = 10


def registered_variants() -> tuple[Allocator, ...]:
    """The sixteen variants, in a fixed order, exactly as pre-registered."""
    variants: list[Allocator] = [
        CrossSectionalMomentum(lookback_days=lookback, positions=positions)
        for lookback in LOOKBACKS
        for positions in CROSS_SECTIONAL_POSITIONS
    ]
    variants.extend(
        TimeSeriesTrend(lookback_days=lookback, positions=TIME_SERIES_POSITIONS, rule=rule)
        for lookback in LOOKBACKS
        for rule in (TrendRule.RETURN_SIGN, TrendRule.ABOVE_SMA)
    )
    return tuple(variants)


def positions_of(variant: Allocator) -> int:
    """The position count a variant weights against, for its matched nulls."""
    if isinstance(variant, CrossSectionalMomentum | TimeSeriesTrend):
        return variant.positions
    raise SignalError(f"{variant.name} has no pre-registered position count")
