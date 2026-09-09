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
from decimal import Decimal
from typing import Protocol, runtime_checkable

from sextant.domain.errors import DomainError
from sextant.domain.instrument import Instrument, InstrumentKey
from sextant.domain.time import Timestamp
from sextant.engine.backtest.market import PointInTimeView
from sextant.engine.backtest.window import Window


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

        The remainder from the division is left uninvested rather than pushed
        into the last position. One position carrying a rounding tail would make
        the last-named instrument systematically larger, which over thirty
        rebalances is a bias with a direction.
        """
        if not keys:
            return cls(weights=(), at=at, candidates_considered=candidates_considered, note=note)
        share = Decimal(1) / Decimal(len(keys))
        return cls(
            weights=tuple((key, share) for key in keys),
            at=at,
            candidates_considered=candidates_considered,
            note=note,
        )


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
    ) -> Allocation:
        """Target weights across ``candidates`` at ``at``."""
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
