"""The trial registry on disk: append-only JSON Lines, committed to the repo.

Committed, not stored under ``data/``. The archives are git-ignored because they
are gigabytes; this file is a few kilobytes and it is the one artifact whose
whole value is that it survives across sessions, machines and people. A trial
counter that lives in a git-ignored directory is a trial counter that resets the
first time somebody clones the repository, and a Deflated Sharpe Ratio computed
against a reset counter is worse than none at all.

**Append-only.** :meth:`TrialRegistry.record` writes one line and never rewrites
one. Every read verifies the hash chain and raises
:class:`~sextant.engine.backtest.trials.TrialRegistryCorrupt` if a record was
edited, removed or reordered.

**Idempotent on identity.** Recording an evaluation that is already in the file -
same code version, strategy, parameters, dataset and window - returns the
existing record and appends nothing. Re-running a report does not inflate the
count.

**The path is always explicit.** No default pointing at the real file. A test
that could write to the committed registry would be a test that corrupts the
evidence, and that class of accident has already happened once in this project
with the archive checksums.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sextant.domain.time import Timestamp
from sextant.engine.backtest.trials import (
    GENESIS,
    TrialRecord,
    TrialRegistryCorrupt,
    build_record,
    count_trials,
    verify_chain,
)

#: Where the committed registry lives. Passed explicitly by the wiring layer;
#: never a default on any function in this module.
REGISTRY_PATH = Path("research") / "trial-registry.jsonl"

HEADER = (
    "# Every strategy and parameter set ever evaluated against this dataset, one "
    "JSON object per line, append-only and hash-chained. Read by the Deflated "
    "Sharpe Ratio as its trial count. Do not edit by hand: verify_chain() will "
    "fail and name the record you touched."
)


@dataclass(frozen=True, slots=True)
class TrialRegistry:
    """The append-only file of everything ever tried against this dataset."""

    path: Path

    def read(self) -> tuple[TrialRecord, ...]:
        """Every record, in order, with the chain verified."""
        if not self.path.is_file():
            return ()
        records: list[TrialRecord] = []
        with self.path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                text = line.strip()
                if not text or text.startswith("#"):
                    continue
                try:
                    raw = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise TrialRegistryCorrupt(
                        f"{self.path}:{number} is not valid JSON. The registry is "
                        "append-only and machine-written; a malformed line means it was "
                        "edited or a write was truncated."
                    ) from exc
                records.append(_from_json(raw))
        verify_chain(records)
        return tuple(records)

    def record(
        self,
        *,
        recorded_at: Timestamp,
        code_version: str,
        engine_version: str,
        strategy_id: str,
        parameter_set_id: str,
        dataset_checksum: str,
        evaluation_window: str,
        quote_policy: str,
        is_null_construct: bool,
        seeds: int,
        note: str,
    ) -> TrialRecord:
        """Append one trial, or return the existing one if it is already there."""
        existing = self.read()
        candidate = build_record(
            recorded_at=recorded_at,
            code_version=code_version,
            engine_version=engine_version,
            strategy_id=strategy_id,
            parameter_set_id=parameter_set_id,
            dataset_checksum=dataset_checksum,
            evaluation_window=evaluation_window,
            quote_policy=quote_policy,
            is_null_construct=is_null_construct,
            seeds=seeds,
            note=note,
            previous_hash=existing[-1].record_hash if existing else GENESIS,
        )
        for record in existing:
            if record.trial_id == candidate.trial_id:
                return record

        self.path.parent.mkdir(parents=True, exist_ok=True)
        new_file = not self.path.is_file()
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            if new_file:
                handle.write(f"{HEADER}\n")
            handle.write(json.dumps(candidate.as_json(), sort_keys=True, separators=(",", ":")))
            handle.write("\n")
        return candidate

    def count(self, *, include_null_constructs: bool = False) -> int:
        """How many distinct trials are recorded."""
        return count_trials(self.read(), include_null_constructs=include_null_constructs)

    def audit(self) -> Sequence[str]:
        """One human-readable line per trial, for the report's audit section."""
        return [
            f"{record.recorded_at.isoformat()}  {record.trial_id[:12]}  "
            f"{record.strategy_id} [{record.parameter_set_id}]  "
            f"{record.quote_policy}  {record.evaluation_window}  "
            f"{'null-construct' if record.is_null_construct else 'search'}  "
            f"seeds={record.seeds}"
            for record in self.read()
        ]


def _from_json(raw: object) -> TrialRecord:
    """One line back into a record, refusing anything malformed."""
    if not isinstance(raw, dict):
        raise TrialRegistryCorrupt(f"A registry line is not a JSON object: {raw!r}")
    try:
        return TrialRecord(
            trial_id=str(raw["trial_id"]),
            recorded_at=Timestamp.parse(str(raw["recorded_at"])),
            code_version=str(raw["code_version"]),
            engine_version=str(raw["engine_version"]),
            strategy_id=str(raw["strategy_id"]),
            parameter_set_id=str(raw["parameter_set_id"]),
            dataset_checksum=str(raw["dataset_checksum"]),
            evaluation_window=str(raw["evaluation_window"]),
            quote_policy=str(raw["quote_policy"]),
            is_null_construct=bool(raw["is_null_construct"]),
            seeds=int(raw["seeds"]),
            note=str(raw["note"]),
            previous_hash=str(raw["previous_hash"]),
            record_hash=str(raw["record_hash"]),
        )
    except KeyError as exc:
        raise TrialRegistryCorrupt(f"A registry line is missing {exc}: {raw!r}") from exc
