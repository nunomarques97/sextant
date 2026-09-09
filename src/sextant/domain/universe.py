"""Point-in-time universe resolution.

A universe is never a hardcoded list of the assets that happen to be popular
today. It is a set of candidate instruments plus rules that are re-evaluated at
a given instant, using only information that existed at that instant.

Two biases are being designed out here:

* **survivorship** - candidates include instruments that were later delisted, so
  a backtest run over 2021 sees the pairs that existed in 2021;
* **look-ahead** - membership is a function of ``at``, so a rule cannot admit an
  instrument on the strength of something that had not happened yet.

The listing-window check is unconditional and cannot be switched off by a rule.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from sextant.domain.instrument import Instrument
from sextant.domain.time import Timestamp


@runtime_checkable
class UniverseRule(Protocol):
    """A point-in-time admission rule.

    Implementations must decide using only information available at ``at``. A
    rule that consults a future price, a future volume or a present-day ranking
    is a look-ahead defect, not a configuration choice.
    """

    @property
    def name(self) -> str:
        """Short identifier, recorded in run metadata so a universe is reproducible."""
        ...

    def admits(self, instrument: Instrument, at: Timestamp) -> bool:
        """Whether ``instrument`` satisfies this rule as of ``at``."""
        ...


@dataclass(frozen=True, slots=True)
class Universe:
    """A candidate set plus the rules that filter it at a point in time."""

    candidates: frozenset[Instrument]
    rules: tuple[UniverseRule, ...] = field(default_factory=tuple)

    @classmethod
    def of(
        cls,
        candidates: Iterable[Instrument],
        rules: Iterable[UniverseRule] = (),
    ) -> Universe:
        """Build a Universe from any iterables of candidates and rules."""
        return cls(candidates=frozenset(candidates), rules=tuple(rules))

    def members_at(self, at: Timestamp) -> frozenset[Instrument]:
        """The instruments in this universe as of ``at``."""
        return frozenset(
            instrument
            for instrument in self.candidates
            if instrument.is_listed_at(at)
            and all(rule.admits(instrument, at) for rule in self.rules)
        )

    @property
    def rule_names(self) -> tuple[str, ...]:
        """The names of the applied rules, for run metadata."""
        return tuple(rule.name for rule in self.rules)
