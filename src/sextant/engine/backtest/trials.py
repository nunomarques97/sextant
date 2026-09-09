"""The trial counter, and what makes it trustworthy.

The Deflated Sharpe Ratio's whole purpose is to ask "given how many things were
tried, how surprising is this one?". The answer is only as honest as the count,
and a count is only honest if it was started before anybody had a reason to want
it small. This exists now, before a single strategy has been written, because
that is the only moment at which it can ever be true.

What a trial is
----------------

One evaluation of one strategy at one parameter set against this dataset. Not
one commit, not one session, not one report. Fitting the same strategy at three
lookbacks is three trials. Running the same configuration twice is *not* two
trials - the record is idempotent on its fingerprint, so re-running a report
does not inflate the count.

Append-only, and how that is enforced
--------------------------------------

Each record carries the SHA-256 of the record before it. The chain is verified
on every read: change a record, delete one, or reorder two, and the verification
fails and names the record where the chain breaks. A later run therefore cannot
silently rewrite, reset or reduce the history - it can only fail loudly, which
is what an audit trail is for.

The chain is not a security mechanism. Anyone with write access can recompute
the whole chain. It is an accident detector, and accidents - a truncated write,
a merge that dropped a line, a test that pointed at the real registry - are what
actually threaten this file.

Pure data and hashing. The persistence lives in ``adapters.storage``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sextant.domain.errors import DomainError
from sextant.domain.time import Timestamp

#: The first record's predecessor. A fixed, obviously-not-a-hash sentinel.
GENESIS = "0" * 64


class TrialRegistryCorrupt(DomainError):
    """The recorded trial history is not internally consistent.

    Raised rather than repaired. A registry that repairs itself is a registry
    that can be made to say whatever the last run wanted it to say.
    """


@dataclass(frozen=True, slots=True)
class TrialRecord:
    """One immutable evaluation of one strategy at one parameter set."""

    trial_id: str
    """Deterministic: the fingerprint hash. Two identical evaluations share it,
    which is what makes recording idempotent."""
    recorded_at: Timestamp
    code_version: str
    engine_version: str
    strategy_id: str
    parameter_set_id: str
    dataset_checksum: str
    """One hash over the whole archive checksum set, so a changed dataset is a
    different trial rather than the same one with different data."""
    evaluation_window: str
    quote_policy: str
    is_null_construct: bool
    """True for the benchmark and null constructs, which are evaluations but are
    not searches for edge. The DSR's trial count can then be stated both ways,
    and the report says which was used."""
    seeds: int
    """How many seeds this evaluation covered. One for a deterministic run."""
    note: str
    previous_hash: str
    record_hash: str

    def as_json(self) -> dict[str, object]:
        """Serialisable form. Field order is fixed by ``sort_keys`` on write."""
        return {
            "trial_id": self.trial_id,
            "recorded_at": self.recorded_at.isoformat(),
            "code_version": self.code_version,
            "engine_version": self.engine_version,
            "strategy_id": self.strategy_id,
            "parameter_set_id": self.parameter_set_id,
            "dataset_checksum": self.dataset_checksum,
            "evaluation_window": self.evaluation_window,
            "quote_policy": self.quote_policy,
            "is_null_construct": self.is_null_construct,
            "seeds": self.seeds,
            "note": self.note,
            "previous_hash": self.previous_hash,
            "record_hash": self.record_hash,
        }


def fingerprint(
    *,
    code_version: str,
    strategy_id: str,
    parameter_set_id: str,
    dataset_checksum: str,
    evaluation_window: str,
    quote_policy: str,
) -> str:
    """The identity of a trial: what was tried, on what data, over what window.

    Excludes the instant it was run and the code version's *build*, so that
    re-running the identical evaluation is recognised as the same trial. Include
    the code version itself, because a change to the engine changes what the
    evaluation means.
    """
    payload = "|".join(
        (
            code_version,
            strategy_id,
            parameter_set_id,
            dataset_checksum,
            evaluation_window,
            quote_policy,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def hash_record(body: Mapping[str, object], previous_hash: str) -> str:
    """The chain hash for one record: its content plus its predecessor's hash."""
    encoded = json.dumps(dict(body), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{previous_hash}|{encoded}".encode()).hexdigest()


def dataset_fingerprint(checksums: Mapping[str, str]) -> str:
    """One hash over a whole set of archive checksums."""
    encoded = json.dumps(dict(sorted(checksums.items())), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_record(
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
    previous_hash: str,
) -> TrialRecord:
    """Assemble one record, computing both its identity and its chain hash."""
    trial_id = fingerprint(
        code_version=code_version,
        strategy_id=strategy_id,
        parameter_set_id=parameter_set_id,
        dataset_checksum=dataset_checksum,
        evaluation_window=evaluation_window,
        quote_policy=quote_policy,
    )
    body: dict[str, object] = {
        "trial_id": trial_id,
        "recorded_at": recorded_at.isoformat(),
        "code_version": code_version,
        "engine_version": engine_version,
        "strategy_id": strategy_id,
        "parameter_set_id": parameter_set_id,
        "dataset_checksum": dataset_checksum,
        "evaluation_window": evaluation_window,
        "quote_policy": quote_policy,
        "is_null_construct": is_null_construct,
        "seeds": seeds,
        "note": note,
    }
    return TrialRecord(
        trial_id=trial_id,
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
        previous_hash=previous_hash,
        record_hash=hash_record(body, previous_hash),
    )


def verify_chain(records: Sequence[TrialRecord]) -> None:
    """Raise unless every record's hash matches its content and its predecessor."""
    expected_previous = GENESIS
    for position, record in enumerate(records):
        if record.previous_hash != expected_previous:
            raise TrialRegistryCorrupt(
                f"Record {position} ({record.trial_id[:12]}) claims predecessor "
                f"{record.previous_hash[:12]} but the chain is at "
                f"{expected_previous[:12]}. A record was inserted, removed or reordered."
            )
        body = record.as_json()
        del body["previous_hash"], body["record_hash"]
        recomputed = hash_record(body, record.previous_hash)
        if recomputed != record.record_hash:
            raise TrialRegistryCorrupt(
                f"Record {position} ({record.trial_id[:12]}) does not hash to its own "
                "recorded hash. Its content was edited after it was written."
            )
        expected_previous = record.record_hash


def count_trials(records: Sequence[TrialRecord], *, include_null_constructs: bool) -> int:
    """How many distinct trials the registry holds.

    Distinct on ``trial_id``, so a re-run of an identical evaluation counts
    once. The DSR quotes the count *excluding* null constructs by default and
    the report states which count it used and what the other one was, because
    the choice moves the answer.
    """
    identifiers = {
        record.trial_id
        for record in records
        if include_null_constructs or not record.is_null_construct
    }
    return len(identifiers)
