"""What a run was, recorded so it can be repeated or disbelieved.

A result without its manifest is an anecdote. Every number this project
publishes comes from a run, and a run is defined by the window it covered, the
universe policy it resolved, the seed it used, the cost assumptions it applied,
the version of the code that executed it and the exact bytes of the dataset it
read. Change any one of those and the number changes; omit any one of them from
the record and nobody can tell which.

The dataset checksum set is in here for a specific reason. The quarterly
archives this project runs on are several gigabytes, arrive by hand, and are
git-ignored. The only durable statement that a published result and a later
re-run are looking at the same data is the set of SHA-256 hashes, so it travels
with every result rather than living only in a document somebody has to
remember to check.

Pure data. No I/O: the manifest is built by the wiring layer, which knows the
code version and the checksums, and is serialised by whoever writes the report.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sextant.engine.backtest.window import WalkForwardPlan


@dataclass(frozen=True, slots=True)
class RunManifest:
    """Everything needed to reproduce one run, or to prove two runs differ."""

    run_id: str
    engine_version: str
    code_version: str
    """A git revision where one is available, and an explicit statement that it
    is not where one is not. Never a silent blank."""
    dataset_checksums: Mapping[str, str]
    """SHA-256 per archive file, keyed by quarter label."""
    plan: WalkForwardPlan
    universe_policy: str
    quote_policy: str
    account_currency: str
    initial_equity: str
    positions: int
    seed: int | None
    """None for a construct with no randomness. Never zero as a stand-in."""
    cost_assumptions: Mapping[str, str]
    fx_assumptions: Mapping[str, str]
    delisting_assumptions: Mapping[str, str]
    library_versions: Mapping[str, str]
    notes: Sequence[str] = ()

    def as_json(self) -> dict[str, object]:
        """Serialisable form, sorted so two manifests diff cleanly."""
        return {
            "run_id": self.run_id,
            "engine_version": self.engine_version,
            "code_version": self.code_version,
            "dataset_checksums": dict(sorted(self.dataset_checksums.items())),
            "plan": self.plan.as_json(),
            "universe_policy": self.universe_policy,
            "quote_policy": self.quote_policy,
            "account_currency": self.account_currency,
            "initial_equity": self.initial_equity,
            "positions": self.positions,
            "seed": self.seed,
            "cost_assumptions": dict(sorted(self.cost_assumptions.items())),
            "fx_assumptions": dict(sorted(self.fx_assumptions.items())),
            "delisting_assumptions": dict(sorted(self.delisting_assumptions.items())),
            "library_versions": dict(sorted(self.library_versions.items())),
            "notes": list(self.notes),
        }

    def fingerprint_fields(self) -> tuple[str, ...]:
        """The fields that identify this run for the trial registry.

        Deliberately excludes ``run_id``, which is different every time and
        would make every repetition look like a new trial.
        """
        return (
            self.code_version,
            self.universe_policy,
            self.quote_policy,
            str(sorted(self.dataset_checksums.items())),
            str(self.plan.out_of_sample_span.as_json()),
        )
