"""What a strategy is, from the engine's point of view.

The engine ranks a point-in-time universe and allocates across it. That is the
shape it is built for, and it is stated in the types: an
:class:`Allocator` receives the whole candidate set at once and returns weights
across it.

A strategy that produces a per-symbol signal with no reference to the
cross-section is still expressible - it simply ignores the other candidates -
but it must say so. ``is_cross_sectional`` is a required property, it travels
into the run manifest, and a report can therefore never present a per-symbol
signal as though the cross-section had done any work.

**Fitting and evaluating are different objects.** A :class:`Strategy` is fitted
on an in-sample window and returns an :class:`Allocator`. The allocator is what
the out-of-sample window sees, and it holds no reference to the window it was
fitted on. That is what makes "no code path evaluates a strategy on its own
fitting data" a shape rather than a promise: the object that produces
performance was handed to the engine already fitted, and the engine has no way
to ask it to be fitted again mid-fold.

Pure computation. No I/O, no clock, no venue.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Protocol, runtime_checkable

from sextant.domain.errors import DomainError
from sextant.domain.instrument import Instrument, InstrumentKey
from sextant.domain.time import Timestamp
from sextant.engine.backtest.market import PointInTimeView
from sextant.engine.backtest.window import Window

#: Weights are held to eighteen decimal places and always rounded *down*.
#:
#: One divided by forty-nine, carried at the working precision and added
#: forty-nine times, comes to 1.000000000000000000000000001 - which is more than
#: the account has, and the allocation is rightly refused. Rounding the share
#: down at a fixed scale makes the sum provably at most one for any universe
#: size, and leaves a rounding tail of at most 1e-18 of equity uninvested.
WEIGHT_SCALE = Decimal("1E-18")


class InvalidAllocation(DomainError):
    """An allocator returned weights the engine cannot execute."""


@dataclass(frozen=True, slots=True)
class Allocation:
    """Target weights at one rebalance, as fractions of equity.

    Long-only and never levered: weights must be non-negative and must sum to at
    most one. Both are enforced here rather than in the accounting, because a
    strategy that quietly asks for 140% of equity should fail where it made the
    request, not three layers down where the ledger goes negative.

    Weights are carried as an ordered tuple of pairs rather than a mapping, so
    that iteration order is the allocator's stated order and never a dictionary
    hash. Reproducibility that depends on dictionary ordering is reproducibility
    on one interpreter version.
    """

    weights: tuple[tuple[InstrumentKey, Decimal], ...]
    at: Timestamp
    candidates_considered: int
    note: str = ""
    """Free text recorded on every decision. Used, for instance, when a universe
    was smaller than the number of positions asked for."""

    def __post_init__(self) -> None:
        seen: set[InstrumentKey] = set()
        total = Decimal(0)
        for key, weight in self.weights:
            if key in seen:
                raise InvalidAllocation(f"{key} appears twice in one allocation.")
            seen.add(key)
            if weight < 0:
                raise InvalidAllocation(
                    f"{key} has weight {weight}. This engine is long-only; a short is a "
                    "different instrument set and a different risk model."
                )
            total += weight
        if total > Decimal(1):
            raise InvalidAllocation(
                f"Weights sum to {total}, which is more than the account has. Leverage is "
                "not expressible here."
            )

    @property
    def keys(self) -> tuple[InstrumentKey, ...]:
        """The instruments allocated to, in the allocator's order."""
        return tuple(key for key, _ in self.weights)

    @property
    def invested_fraction(self) -> Decimal:
        """How much of equity this allocation puts to work."""
        return sum((weight for _, weight in self.weights), Decimal(0))

    @classmethod
    def equal_weight(
        cls,
        keys: Sequence[InstrumentKey],
        at: Timestamp,
        *,
        candidates_considered: int,
        note: str = "",
    ) -> Allocation:
        """Spread equity evenly across ``keys``, in the order given.

        The share is rounded **down** to :data:`WEIGHT_SCALE`, so the weights sum
        to at most one for any universe size, and the remainder is left
        uninvested rather than pushed into the last position. One position
        carrying the rounding tail would make the last-named instrument
        systematically larger, which over thirty rebalances is a bias with a
        direction.
        """
        if not keys:
            return cls(weights=(), at=at, candidates_considered=candidates_considered, note=note)
        share = (Decimal(1) / Decimal(len(keys))).quantize(WEIGHT_SCALE, rounding=ROUND_DOWN)
        return cls(
            weights=tuple((key, share) for key in keys),
            at=at,
            candidates_considered=candidates_considered,
            note=note,
        )


@runtime_checkable
class TargetWeights(Protocol):
    """What the engine needs from any allocation, whatever its sign rules.

    Introduced by SEXTANT-006 so that a long-short book can be expressed without
    loosening :class:`Allocation`. The two implementations enforce different
    invariants and neither can be substituted for the other by accident: a
    long-only strategy that developed a short would still be refused where it
    made the request, because it returns an ``Allocation`` and ``Allocation``
    still refuses a negative weight.
    """

    @property
    def weights(self) -> tuple[tuple[InstrumentKey, Decimal], ...]:
        """Target weights as fractions of equity, in the allocator's own order."""
        ...

    @property
    def at(self) -> Timestamp:
        """The rebalance instant these weights are for."""
        ...

    @property
    def candidates_considered(self) -> int:
        """How large the candidate set was. Recorded on every holding period."""
        ...

    @property
    def note(self) -> str:
        """Free text recorded on every decision."""
        ...


@dataclass(frozen=True, slots=True)
class LongShortAllocation:
    """Target weights that may be negative, with a stated gross limit.

    A negative weight is a short. Two invariants replace the two that
    :class:`Allocation` carries, and they are not weaker, they are different:

    **Gross exposure is bounded, and the bound is explicit.** ``sum(abs(w))`` may
    not exceed ``gross_limit``. That number is the leverage knob: one means the
    book's gross notional equals the account's equity, which is what this project
    calls 1x for a two-sided book, and the stage-3 sweep is nothing more than
    running the same variants at a larger one. It is a required field with no
    default, because a default would let a variant be levered by omission.

    **Net exposure is reported, never constrained here.** Whether a book should
    be market-neutral is a property of the strategy, and a strategy that intends
    neutrality and fails to achieve it should be visible in
    :attr:`net_exposure` rather than silently corrected by the type.

    What this type deliberately does *not* express is margin. A pair that is long
    spot and short the perpetual on the same asset ties up the spot notional plus
    the futures leg's initial margin, which is more than the gross figure here
    suggests; that constraint belongs to the strategy that sizes the pair, is
    registered as a number in the family's pre-registration, and is asserted
    where the sizing happens. Putting it here would bake one family's margin
    assumption into a type every family shares.
    """

    weights: tuple[tuple[InstrumentKey, Decimal], ...]
    at: Timestamp
    candidates_considered: int
    gross_limit: Decimal
    note: str = ""

    def __post_init__(self) -> None:
        if self.gross_limit <= 0:
            raise InvalidAllocation(
                f"gross_limit must be positive, got {self.gross_limit}. A book with no "
                "room for exposure is not a book."
            )
        seen: set[InstrumentKey] = set()
        gross = Decimal(0)
        for key, weight in self.weights:
            if key in seen:
                raise InvalidAllocation(f"{key} appears twice in one allocation.")
            seen.add(key)
            gross += abs(weight)
        if gross > self.gross_limit:
            raise InvalidAllocation(
                f"Gross exposure is {gross}, above the stated limit of {self.gross_limit}. "
                "Leverage in this engine is a number a pre-registration states, never a "
                "number an allocator arrives at."
            )

    @property
    def keys(self) -> tuple[InstrumentKey, ...]:
        """The instruments allocated to, in the allocator's order."""
        return tuple(key for key, _ in self.weights)

    @property
    def gross_exposure(self) -> Decimal:
        """``sum(abs(w))``: how much notional the book carries per unit of equity."""
        return sum((abs(weight) for _, weight in self.weights), Decimal(0))

    @property
    def net_exposure(self) -> Decimal:
        """``sum(w)``: how much directional exposure survives the two sides."""
        return sum((weight for _, weight in self.weights), Decimal(0))

    @property
    def long_exposure(self) -> Decimal:
        """The long side alone."""
        return sum((weight for _, weight in self.weights if weight > 0), Decimal(0))

    @property
    def short_exposure(self) -> Decimal:
        """The short side alone, as a positive number."""
        return sum((-weight for _, weight in self.weights if weight < 0), Decimal(0))


@runtime_checkable
class Allocator(Protocol):
    """A fitted strategy: candidates in, weights out."""

    @property
    def name(self) -> str:
        """Short identifier, recorded on every decision and in the manifest."""
        ...

    @property
    def parameter_set_id(self) -> str:
        """Identifies the exact parameters. Part of the trial registry record."""
        ...

    @property
    def is_cross_sectional(self) -> bool:
        """Whether this allocator's output depends on the rest of the candidate set.

        False is allowed and is not a defect. It is a label, and it appears in
        the run metadata so that a per-symbol signal is never read as a
        cross-sectional result.
        """
        ...

    def allocate(
        self,
        candidates: Sequence[Instrument],
        at: Timestamp,
        view: PointInTimeView,
    ) -> TargetWeights:
        """Target weights across ``candidates`` at ``at``.

        Widened from ``Allocation`` to :class:`TargetWeights` by SEXTANT-006 so
        that a long-short allocator satisfies the same protocol. Every allocator
        written before that returns an ``Allocation``, which still refuses a
        negative weight and still refuses to sum above one, so nothing about what
        a long-only strategy may express has changed.
        """
        ...


@runtime_checkable
class LongOnlyAllocator(Protocol):
    """An allocator that returns an :class:`Allocation`, and therefore cannot short.

    A narrowing of :class:`Allocator`, needed because a construct that *wraps* a
    variant - the recorder, the selection-only reweighting, the two nulls - reads
    ``Allocation``-specific things off what it wrapped, such as which names were
    chosen and what fraction of equity they came to. Those questions have no
    answer for a two-sided book, where "the names held" and "the fraction
    invested" are two numbers each. Declaring the narrower protocol is what makes
    a wrapper that was written for one shape refuse the other at the type level
    instead of producing a plausible wrong number.
    """

    @property
    def name(self) -> str:
        """Short identifier, recorded on every decision and in the manifest."""
        ...

    @property
    def parameter_set_id(self) -> str:
        """Identifies the exact parameters. Part of the trial registry record."""
        ...

    @property
    def is_cross_sectional(self) -> bool:
        """Whether this allocator's output depends on the rest of the candidate set."""
        ...

    def allocate(
        self,
        candidates: Sequence[Instrument],
        at: Timestamp,
        view: PointInTimeView,
    ) -> Allocation:
        """Target weights across ``candidates`` at ``at``, all non-negative."""
        ...


@dataclass(frozen=True, slots=True)
class FitRecord:
    """What a fitting produced, deliberately without any performance figure.

    The engine returns one of these for every fold. It carries the window that
    was fitted on, the parameters that came out, and nothing else. There is no
    equity curve, no Sharpe and no return, because an in-sample performance
    number that exists anywhere will eventually be quoted somewhere.
    """

    fold_index: int
    window: Window
    allocator_name: str
    parameter_set_id: str
    is_cross_sectional: bool
    chosen_parameters: Mapping[str, str]

    def as_json(self) -> dict[str, object]:
        """Serialisable form, for the run manifest."""
        return {
            "fold_index": self.fold_index,
            "window": self.window.as_json(),
            "allocator": self.allocator_name,
            "parameter_set_id": self.parameter_set_id,
            "is_cross_sectional": self.is_cross_sectional,
            "chosen_parameters": dict(sorted(self.chosen_parameters.items())),
            "in_sample_performance": (
                "not computed - this engine has no code path that scores a strategy on "
                "the window it was fitted on"
            ),
        }


@runtime_checkable
class Strategy(Protocol):
    """Something that can be fitted on a window and produce an allocator."""

    @property
    def name(self) -> str:
        """Short identifier for the strategy family."""
        ...

    def fit(self, window: Window, view: PointInTimeView) -> tuple[Allocator, FitRecord]:
        """Fit on ``window`` and return the allocator plus what was fitted.

        ``view`` is built at the *end* of the in-sample window, so a fitting
        procedure physically cannot read past it.
        """
        ...


@dataclass(frozen=True, slots=True)
class ParameterFreeStrategy:
    """A strategy with nothing to fit, wrapped so it goes through the same path.

    Every baseline in this project is one of these. They are run through the
    walk-forward machinery rather than around it, because a benchmark measured
    over a different window from the strategy it benchmarks is not a benchmark.
    """

    allocator: Allocator

    @property
    def name(self) -> str:
        """The wrapped allocator's name."""
        return self.allocator.name

    def fit(self, window: Window, view: PointInTimeView) -> tuple[Allocator, FitRecord]:
        """Return the allocator unchanged, and record that nothing was fitted."""
        del view
        return self.allocator, FitRecord(
            fold_index=-1,
            window=window,
            allocator_name=self.allocator.name,
            parameter_set_id=self.allocator.parameter_set_id,
            is_cross_sectional=self.allocator.is_cross_sectional,
            chosen_parameters={"fitted": "nothing - this construct has no free parameters"},
        )
