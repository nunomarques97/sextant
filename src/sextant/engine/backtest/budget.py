"""The per-family trial budget, and the refusal that makes it a budget.

A pre-registration that says "this family gets eight variants" and then relies on
whoever runs it to stop at eight has not registered anything. The number has to
be enforced by something that can say no, at the moment a ninth run would start,
or it is a promise rather than a budget. This module is that something.

What it enforces
-----------------

Three separate refusals, because they catch three different ways a search widens
after somebody has seen a result:

1. **an unregistered variant.** The budget names its variants; a run of anything
   not on the list is refused. This is the "no ninth variant, no adjusted
   parameter, no additional lookback" rule of the F1 registration's section 3,
   turned into an exception;
2. **an unregistered cost cell.** The same rule on the other axis. A variant that
   failed in the headline cell cannot be quietly re-examined in a cheaper one
   that was never registered;
3. **an exhausted allowance.** Once the registered number of distinct trials has
   been charged, every further charge is refused. A family that spends its budget
   without clearing its criteria is closed, and closed here means the next call
   raises.

What it deliberately does not do
---------------------------------

**Nulls and benchmarks are not charged.** They are not attempts at finding an
edge, and charging them would make the honest thing - running more nulls -
consume the allowance for the thing that needs restraining. They are still
counted in full in the trial registry, which is what the Deflated Sharpe Ratio
reads, so nothing is hidden by leaving them uncharged here.

**Charging is idempotent on the pair.** Re-running the whole grid spends the
allowance once, not twice, exactly as the registry recognises a repeated
evaluation as the same trial. A budget that fell over on a rerun would be a
budget that discouraged reproducing a result.

**It cannot be widened at run time.** There is no extend, no reserve and no
override flag. Widening a budget means editing the registered configuration and
committing it, which is visible in the git ordering the report cites. That is the
point: the budget is enforced here and the *change* to the budget is enforced by
the history.

Pure data and set arithmetic. Nothing here reads a file or a clock.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from sextant.domain.errors import DomainError
from sextant.engine.backtest.trials import TrialRecord

NEWLINE = chr(10)


class BudgetViolation(DomainError):
    """A run was attempted that the registered budget does not cover."""


class UnregisteredTrial(BudgetViolation):
    """The variant or the cost cell is not one the family registered.

    Distinct from exhaustion because the remedy differs: exhaustion means the
    family is closed, whereas this means something is being run that was never
    pre-registered at all.
    """


class BudgetExhausted(BudgetViolation):
    """The family has spent its whole declared allowance. It is closed."""


@dataclass(frozen=True, slots=True)
class TrialBudget:
    """One family's declared allowance, read from its registered configuration.

    ``maximum_trials`` must equal ``len(variants) * len(cost_cells)`` exactly.
    Registering fewer trials than the product and holding the difference in
    reserve is the behaviour a budget exists to prevent: a reserve is an
    invitation to spend it after seeing a result. The type therefore refuses a
    budget with slack in it, which forces a registration to be a complete list
    rather than a ceiling.
    """

    family: str
    """The family identifier, ``F1`` through ``F6``."""
    engine_version: str
    """How this family's trials identify themselves in the registry, so budget
    consumption can be recomputed from the committed file rather than from a
    counter that lives only inside one run."""
    variants: tuple[str, ...]
    cost_cells: tuple[str, ...]
    maximum_trials: int

    def __post_init__(self) -> None:
        if not self.family:
            raise BudgetViolation("a budget must name its family")
        if not self.variants:
            raise BudgetViolation(f"{self.family}: a budget with no variants registers nothing")
        if not self.cost_cells:
            raise BudgetViolation(f"{self.family}: a budget with no cost cells registers nothing")
        if len(set(self.variants)) != len(self.variants):
            raise BudgetViolation(f"{self.family}: a variant is registered twice")
        if len(set(self.cost_cells)) != len(self.cost_cells):
            raise BudgetViolation(f"{self.family}: a cost cell is registered twice")
        if self.maximum_trials != self.registered_trials:
            raise BudgetViolation(
                f"{self.family}: the declared maximum of {self.maximum_trials} trials does not "
                f"equal the {len(self.variants)} registered variants across the "
                f"{len(self.cost_cells)} registered cost cells, which is "
                f"{self.registered_trials}. A budget with slack in it is a reserve, and a "
                "reserve is what gets spent after a result is seen. Register the exact grid."
            )

    @property
    def registered_trials(self) -> int:
        """Every variant in every cell: the whole grid and the whole allowance."""
        return len(self.variants) * len(self.cost_cells)

    def covers(self, variant: str, cell: str) -> bool:
        """Whether this pair is one the family registered."""
        return variant in self.variants and cell in self.cost_cells


@dataclass(slots=True)
class BudgetLedger:
    """What a run has spent so far, and the gate every strategy trial passes.

    Held by the runner and consulted *before* the engine runs, never after. A
    check that happens once the run has already produced an equity curve is a
    check somebody can be tempted to argue with.
    """

    budget: TrialBudget
    already_spent: int = 0
    """Trials this family has already made outside its registration, read from the
    committed registry before the run starts.

    Post-hoc trials reduce the allowance rather than being ignored, because they
    were searches in this family and they inflate the multiplicity the Deflated
    Sharpe Ratio has to deflate for. A family does not get to spend its whole
    registered budget *on top of* whatever it already tried. Reruns of registered
    trials do not land here - they are recognised by identity and cost nothing - so
    reproducing a result is never penalised."""
    _charged: set[tuple[str, str]] = field(default_factory=set)

    def charge(self, variant: str, cell: str) -> None:
        """Spend one trial, or refuse and name which of the three rules refused it."""
        if variant not in self.budget.variants:
            raise UnregisteredTrial(
                f"{self.budget.family}: {variant!r} is not a registered variant. The registered "
                f"variants are {', '.join(self.budget.variants)}. Anything else is post-hoc: it "
                "needs a new pre-registered version, committed before it is run, and it is "
                "reported in its own section with the reason it was added."
            )
        if cell not in self.budget.cost_cells:
            raise UnregisteredTrial(
                f"{self.budget.family}: {cell!r} is not a registered cost cell. The registered "
                f"cells are {', '.join(self.budget.cost_cells)}."
            )
        pair = (variant, cell)
        if pair in self._charged:
            return
        if self.spent >= self.budget.maximum_trials:
            raise BudgetExhausted(
                f"{self.budget.family} has spent its whole declared budget of "
                f"{self.budget.maximum_trials} strategy trials. The family is closed: no ninth "
                "variant, no adjusted parameter, no additional cell. If it did not clear its "
                "criteria, that is the answer."
            )
        self._charged.add(pair)

    @property
    def spent(self) -> int:
        """Distinct registered trials charged, plus any prior post-hoc ones.

        A rerun counts once: the set is keyed on the variant and cell pair, which
        is how the registry itself recognises a repeated evaluation.
        """
        return self.already_spent + len(self._charged)

    @property
    def remaining(self) -> int:
        return self.budget.maximum_trials - self.spent

    @property
    def is_exhausted(self) -> bool:
        return self.remaining <= 0

    @property
    def charged(self) -> tuple[tuple[str, str], ...]:
        """Every pair charged, ordered, for the report."""
        return tuple(sorted(self._charged))


@dataclass(frozen=True, slots=True)
class BudgetConsumption:
    """What the committed registry says a family actually spent.

    Recomputed from ``research/trial-registry.jsonl`` rather than from the run's
    own counter, so the figure in the report is one a reader can reproduce from
    the file in the repository without trusting the runner that wrote it.
    """

    budget: TrialBudget
    registered: tuple[tuple[str, str], ...]
    """Distinct registered variant and parameter-set trials the registry holds."""
    post_hoc: tuple[tuple[str, str], ...]
    """Non-null trials recorded against this family whose variant the budget does
    not name. Empty is the expected answer. Anything here is reported separately
    with the reason it was added, and counts in full in the DSR's trial count
    either way."""
    nulls: int
    """Null and benchmark constructs recorded against this family. Not charged,
    reported so the uncharged half of the registry is visible too."""

    @property
    def spent(self) -> int:
        return len(self.registered)

    @property
    def remaining(self) -> int:
        return self.budget.maximum_trials - self.spent

    @property
    def is_exhausted(self) -> bool:
        return self.remaining <= 0

    @property
    def is_overspent(self) -> bool:
        """True when the registry holds more of this family than was registered."""
        return self.spent > self.budget.maximum_trials or bool(self.post_hoc)

    def summary(self) -> str:
        """One fact per line, for the report's budget table."""
        lines = [
            f"family: {self.budget.family}",
            f"declared budget: {self.budget.maximum_trials} strategy trials "
            f"({len(self.budget.variants)} variants over "
            f"{len(self.budget.cost_cells)} cost cells)",
            f"charged: {self.spent}",
            f"remaining: {self.remaining}",
            f"null and benchmark constructs, not charged: {self.nulls}",
        ]
        if self.post_hoc:
            lines.append(
                f"POST-HOC, outside the registered budget: {len(self.post_hoc)} - "
                + ", ".join(f"{variant} [{parameters}]" for variant, parameters in self.post_hoc)
            )
        return NEWLINE.join(lines)


def consumption_from(budget: TrialBudget, records: Sequence[TrialRecord]) -> BudgetConsumption:
    """What the registry says this family spent, by the budget's own engine version.

    Distinct on the pair of strategy identifier and parameter-set identifier,
    which is how the registry itself distinguishes one evaluation from another, so
    a rerun of the grid reads as the same trials rather than as twice as many.
    """
    mine = [record for record in records if record.engine_version == budget.engine_version]
    registered: set[tuple[str, str]] = set()
    post_hoc: set[tuple[str, str]] = set()
    nulls: set[tuple[str, str]] = set()
    for record in mine:
        pair = (record.strategy_id, record.parameter_set_id)
        if record.is_null_construct:
            nulls.add(pair)
        elif record.strategy_id in budget.variants:
            registered.add(pair)
        else:
            post_hoc.add(pair)
    return BudgetConsumption(
        budget=budget,
        registered=tuple(sorted(registered)),
        post_hoc=tuple(sorted(post_hoc)),
        nulls=len(nulls),
    )


def budget_from(
    *,
    family: str,
    engine_version: str,
    variants: Iterable[str],
    cost_cells: Iterable[str],
    maximum_trials: int,
) -> TrialBudget:
    """A budget built from the registered configuration's own lists."""
    return TrialBudget(
        family=family,
        engine_version=engine_version,
        variants=tuple(variants),
        cost_cells=tuple(cost_cells),
        maximum_trials=maximum_trials,
    )


def ledger_for(budget: TrialBudget, consumption: BudgetConsumption) -> BudgetLedger:
    """A ledger opened against what the committed registry already holds.

    Seeds only the post-hoc count. Registered trials already in the registry are
    deliberately *not* seeded: a rerun of the registered grid must be free, or the
    guard would make reproducing a published number impossible.
    """
    if consumption.budget != budget:
        raise BudgetViolation(
            f"{budget.family}: the consumption was measured against a different budget "
            f"({consumption.budget.family}/{consumption.budget.engine_version})."
        )
    return BudgetLedger(budget=budget, already_spent=len(consumption.post_hoc))


__all__ = [
    "BudgetConsumption",
    "BudgetExhausted",
    "BudgetLedger",
    "BudgetViolation",
    "TrialBudget",
    "UnregisteredTrial",
    "budget_from",
    "consumption_from",
    "ledger_for",
]
