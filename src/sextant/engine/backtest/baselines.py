"""The three things a strategy has to beat, none of which is a strategy.

These are benchmark constructs. They have no signal, no parameter to tune and
no view about anything, and that is the whole point: what they produce, net of
real costs, is what "no edge" looks like over this window. Any strategy this
project ever proposes is measured against these numbers.

**Equal-weight passive.** Hold the entire executable universe, rebalanced
monthly. The market, as this account could have bought it.

**Random selection.** Pick a fixed number of instruments uniformly from the
point-in-time executable universe at every rebalance, hold them equally
weighted, repeat over many seeds. The distribution that comes out is the null:
what pure chance produces, with these costs, over this window.

**Single-asset buy and hold.** One instrument, bought at the start and held.
The thing anyone can do with no system at all.

Sampling is without replacement, and that is a decision
-------------------------------------------------------

Within one rebalance, the same instrument is never drawn twice. The null has to
mirror the constraint a real strategy operates under: you cannot hold two
positions in the same name, and eight positions means eight distinct names.
Sampling with replacement would let a run concentrate two or three slots in one
instrument, which no strategy we will ever build can do. It would make the null
both easier to beat on average and structurally different from the thing it is
supposed to calibrate.

Randomness, and why it is stdlib
---------------------------------

The generator is an explicitly constructed ``random.Random``, seeded per run and
held by the allocator. The global ``random`` module state is never touched, and
neither is ``numpy.random``'s legacy global state: any library imported anywhere
in the process can disturb either, and when reproducibility fails that way it
fails silently.

``random.Random.sample`` over a **sorted sequence** is what makes a seed mean
the same thing twice. The candidate set arrives from the engine already sorted
by instrument key. Nothing here iterates a set or a dictionary, because the
order of either is not a property of this program.

Pure computation. No I/O, no clock, no venue.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from sextant.domain.errors import DomainError
from sextant.domain.instrument import Instrument, InstrumentKey
from sextant.domain.time import Timestamp
from sextant.engine.backtest.allocation import Allocation
from sextant.engine.backtest.market import PointInTimeView


class BaselineError(DomainError):
    """A benchmark construct was configured with something it cannot use."""


@dataclass(frozen=True, slots=True)
class EqualWeightPassive:
    """Hold everything in the executable universe, equally weighted.

    Cross-sectional in the trivial sense: what it holds depends entirely on the
    composition of the candidate set, and adding one instrument changes every
    weight. It is labelled as cross-sectional because that is true, and because
    the label's purpose is to say whether the cross-section did any work rather
    than whether the work was clever.
    """

    label: str = "equal-weight-passive"

    @property
    def name(self) -> str:
        """Identifier recorded on every decision and in the manifest."""
        return self.label

    @property
    def parameter_set_id(self) -> str:
        """There is nothing to parameterise, and the identifier says so."""
        return "none"

    @property
    def is_cross_sectional(self) -> bool:
        """True: every weight depends on how many other candidates there are."""
        return True

    def allocate(
        self,
        candidates: Sequence[Instrument],
        at: Timestamp,
        view: PointInTimeView,
    ) -> Allocation:
        """Equal weight across the whole universe."""
        del view
        return Allocation.equal_weight(
            [item.key for item in candidates],
            at,
            candidates_considered=len(candidates),
            note=f"held the whole executable universe ({len(candidates)} instruments)",
        )


@dataclass(slots=True)
class RandomSelection:
    """Pick ``positions`` instruments uniformly at random, without replacement.

    Stateful on purpose: the generator advances as the run walks forward, so the
    draw at the second rebalance depends on the first. That is what makes one
    seed describe one whole path rather than thirty independent draws that would
    have to be seeded separately.

    Because it is stateful, an allocator must not be reused across runs.
    :meth:`for_seed` builds a fresh one and is the only way the null experiment
    constructs them.
    """

    seed: int
    positions: int
    label: str = "random-selection"
    _generator: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.positions <= 0:
            raise BaselineError(f"positions must be positive, got {self.positions}")
        self._generator = random.Random(self.seed)

    @classmethod
    def for_seed(cls, seed: int, positions: int) -> RandomSelection:
        """A fresh allocator for one seed. Never reuse one across runs."""
        return cls(seed=seed, positions=positions)

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
        """Sample without replacement, equally weight what comes out.

        When the universe is smaller than the number of positions asked for,
        everything in it is taken and the shortfall is recorded on the
        allocation. Padding with a repeat, or with cash, would both change the
        null in a direction someone would later have to unpick.
        """
        del view
        keys = [item.key for item in candidates]
        if not keys:
            return Allocation(
                weights=(),
                at=at,
                candidates_considered=0,
                note="universe empty at this rebalance; nothing held",
            )
        if len(keys) <= self.positions:
            chosen = keys
            note = (
                f"universe of {len(keys)} is at or below the {self.positions} positions "
                "asked for; held all of it"
            )
        else:
            chosen = self._generator.sample(keys, self.positions)
            note = f"sampled {self.positions} of {len(keys)} without replacement"
        return Allocation.equal_weight(
            sorted(chosen),
            at,
            candidates_considered=len(keys),
            note=note,
        )


@dataclass(frozen=True, slots=True)
class SingleAssetBuyAndHold:
    """Hold one named instrument, whenever it is executable.

    Not cross-sectional, and it says so. Its allocation is the same whatever
    else is in the universe, which is exactly the property the label exists to
    expose.

    It still rebalances monthly like everything else, which means it pays entry
    and exit costs every month rather than once. That is deliberate: a benchmark
    priced under a different cost regime from the thing it benchmarks is not a
    benchmark. The cost of the difference is reported separately in the
    baseline report.
    """

    key: InstrumentKey
    label: str = "single-asset-buy-and-hold"

    @property
    def name(self) -> str:
        """Identifier recorded on every decision and in the manifest."""
        return f"{self.label}:{self.key.symbol}"

    @property
    def parameter_set_id(self) -> str:
        """The instrument is the only parameter."""
        return f"symbol={self.key.symbol}"

    @property
    def is_cross_sectional(self) -> bool:
        """False. Stated, so no report can imply otherwise."""
        return False

    def allocate(
        self,
        candidates: Sequence[Instrument],
        at: Timestamp,
        view: PointInTimeView,
    ) -> Allocation:
        """Everything in one instrument, whatever else the universe contains.

        Deliberately not filtered by the universe rules. This benchmark exists
        to say what holding the asset would have done; the universe rules are
        about what a cross-sectional strategy may *select from*, which is a
        different question. It still cannot trade a price it could not see: the
        engine skips a position whose entry price is not knowable at the
        rebalance, and equity simply sits in cash for that period.
        """
        del candidates, view
        return Allocation(
            weights=((self.key, Decimal(1)),),
            at=at,
            candidates_considered=1,
            note=f"held {self.key.symbol} outright",
        )
