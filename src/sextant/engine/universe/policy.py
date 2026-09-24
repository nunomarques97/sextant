"""Research and executable universes, kept as two distinct objects.

Per decision D1, Phase 0 conflated two questions that deserve separate
answers:

* **research** - does the edge exist at all? Judged on tradability and liquidity
  in the market, with no reference to how much money we happen to have;
* **executable** - can *this* account harvest it? The research set intersected
  with the account's minimum-notional and lot-size constraints.

Filtering the research set by our own account size would throw away statistical
power for no reason, and it would be the account rather than the market doing
the filtering. Every backtest reports both, and a large gap between them is
itself a finding.

Evaluation is deterministic: the same candidates and histories produce the same
membership and the same per-rule counts, every time, in a stable order.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from sextant.domain.instrument import Instrument, InstrumentKey
from sextant.domain.time import Timestamp
from sextant.engine.universe.rules import PointInTimeRule, RuleOutcome


@dataclass(frozen=True, slots=True)
class UniverseEvaluation:
    """What a policy concluded about a candidate set at one instant.

    The per-rule tallies are *independent*: every rule is evaluated against
    every candidate, so an instrument failing three rules appears in three
    counts. That is deliberate. Attributing each rejection to whichever rule
    happened to run first would answer "which rule fired?" when the question
    worth asking is "which threshold is actually binding?".
    """

    policy: str
    at: Timestamp
    members: tuple[InstrumentKey, ...]
    rejected_by: Mapping[str, tuple[InstrumentKey, ...]]
    not_evaluable_by: Mapping[str, tuple[InstrumentKey, ...]]
    candidates: int

    @property
    def size(self) -> int:
        """How many instruments satisfied every rule."""
        return len(self.members)

    def rejection_counts(self) -> Mapping[str, int]:
        """Instruments each rule rejected, independently of the others."""
        return {name: len(keys) for name, keys in self.rejected_by.items()}

    def not_evaluable_counts(self) -> Mapping[str, int]:
        """Instruments each rule could not judge for want of data."""
        return {name: len(keys) for name, keys in self.not_evaluable_by.items()}


@dataclass(frozen=True, slots=True)
class UniversePolicy:
    """A named set of point-in-time rules, applied together."""

    name: str
    rules: tuple[PointInTimeRule, ...]

    @classmethod
    def of(cls, name: str, rules: Iterable[PointInTimeRule]) -> UniversePolicy:
        """Build a policy from any iterable of rules."""
        return cls(name=name, rules=tuple(rules))

    @property
    def rule_names(self) -> tuple[str, ...]:
        """The applied rule names, for run metadata."""
        return tuple(rule.name for rule in self.rules)

    def evaluate(
        self,
        candidates: Sequence[Instrument],
        at: Timestamp,
    ) -> UniverseEvaluation:
        """Judge every candidate against every rule as of ``at``."""
        members: list[InstrumentKey] = []
        rejected: dict[str, list[InstrumentKey]] = {rule.name: [] for rule in self.rules}
        unknown: dict[str, list[InstrumentKey]] = {rule.name: [] for rule in self.rules}

        for instrument in candidates:
            admitted = instrument.is_listed_at(at)
            for rule in self.rules:
                outcome = rule.evaluate(instrument, at)
                if outcome is RuleOutcome.REJECT:
                    rejected[rule.name].append(instrument.key)
                    admitted = False
                elif outcome is RuleOutcome.NOT_EVALUABLE:
                    unknown[rule.name].append(instrument.key)
                    admitted = False
            if admitted:
                members.append(instrument.key)

        return UniverseEvaluation(
            policy=self.name,
            at=at,
            members=tuple(sorted(members)),
            rejected_by={name: tuple(sorted(keys)) for name, keys in rejected.items()},
            not_evaluable_by={name: tuple(sorted(keys)) for name, keys in unknown.items()},
            candidates=len(candidates),
        )


def executable_from(
    research: UniversePolicy, account_rules: Iterable[PointInTimeRule]
) -> UniversePolicy:
    """The executable policy: the research rules plus the account's constraints.

    Constructed by extension rather than defined separately, so the executable
    universe can never drift into being a different market from the research
    one. It is the same market, seen through a smaller wallet.
    """
    return UniversePolicy.of(f"{research.name}+executable", (*research.rules, *account_rules))


#: Bands from docs/PHASE-0-FINDINGS.md §5. A universe below 15 names is excluded
#: from any aggregate that assumes cross-sectional breadth; 15-25 is reported as
#: low power. Neither is a reason to loosen a threshold.
LOW_POWER_CEILING = 25
UNUSABLE_CEILING = 15


def power_band(size: int) -> str:
    """Which reporting band a universe of this size falls into."""
    if size < UNUSABLE_CEILING:
        return "too-thin"
    if size <= LOW_POWER_CEILING:
        return "low-power"
    return "ok"
