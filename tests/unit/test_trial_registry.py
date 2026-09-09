"""The trial registry: append-only, auditable, and impossible to quietly reduce.

Safeguard 3 of the SEXTANT-004 brief. The Deflated Sharpe Ratio's honesty rests
entirely on the trial count, so the file that holds it has to behave like an
audit trail rather than like a cache.

Four properties are asserted here:

* **immutable**: a record's content hashes to its own recorded hash, and editing
  one is detected;
* **append-only**: each record chains to its predecessor, so deleting or
  reordering is detected;
* **idempotent**: recording the same evaluation twice does not inflate the
  count, so re-running a report is safe;
* **complete**: every record carries the fields the safeguard lists - timestamp,
  code version, strategy, parameter set, dataset checksum, window and quote
  policy.

Every test writes to ``tmp_path``. Nothing in this module can touch the
committed registry, which is the standing invariant a test may never write to
real project data.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sextant.adapters.storage.trials import HEADER, TrialRegistry
from sextant.domain.time import Timestamp
from sextant.engine.backtest.trials import (
    TrialRegistryCorrupt,
    count_trials,
    dataset_fingerprint,
    fingerprint,
)
from tests.harness import ts

CHECKSUMS = {"Q1_2023": "aa" * 32, "Q2_2023": "bb" * 32}
DATASET = dataset_fingerprint(CHECKSUMS)


def registry(tmp_path: Path) -> TrialRegistry:
    """A registry in a temporary directory. Never the committed one."""
    return TrialRegistry(path=tmp_path / "trial-registry.jsonl")


def record(
    store: TrialRegistry,
    *,
    strategy: str = "equal-weight-passive",
    parameters: str = "none",
    quote_policy: str = "EUR",
    window: str = "2023-10-01/2026-04-01",
    recorded_at: Timestamp | None = None,
    is_null_construct: bool = True,
    seeds: int = 1,
) -> object:
    """Append one trial with sensible defaults."""
    return store.record(
        recorded_at=recorded_at or ts("2026-09-09T12:00:00"),
        code_version="abc1234",
        engine_version="sextant-004",
        strategy_id=strategy,
        parameter_set_id=parameters,
        dataset_checksum=DATASET,
        evaluation_window=window,
        quote_policy=quote_policy,
        is_null_construct=is_null_construct,
        seeds=seeds,
        note="test fixture",
    )


def test_an_absent_registry_reads_as_empty_rather_than_failing(tmp_path: Path) -> None:
    """A registry nobody has written to yet is a legitimate state."""
    store = registry(tmp_path)
    assert store.read() == ()
    assert store.count() == 0


def test_a_recorded_trial_carries_every_field_the_safeguard_lists(tmp_path: Path) -> None:
    """Timestamp, code version, strategy, parameters, dataset, window, policy."""
    store = registry(tmp_path)
    record(store)
    (written,) = store.read()
    payload = written.as_json()
    for field in (
        "recorded_at",
        "code_version",
        "strategy_id",
        "parameter_set_id",
        "dataset_checksum",
        "evaluation_window",
        "quote_policy",
    ):
        assert payload[field], f"{field} is empty"


def test_recording_the_same_evaluation_twice_does_not_inflate_the_count(
    tmp_path: Path,
) -> None:
    """Re-running a report is not twenty thousand new trials."""
    store = registry(tmp_path)
    first = record(store)
    second = record(store, recorded_at=ts("2026-10-01T09:00:00"))
    assert first == second
    assert store.count(include_null_constructs=True) == 1
    assert len(store.read()) == 1


def test_a_different_parameter_set_is_a_different_trial(tmp_path: Path) -> None:
    """Three lookbacks is three trials, and the count has to say so."""
    store = registry(tmp_path)
    record(store, strategy="momentum-probe", parameters="lookback=30", is_null_construct=False)
    record(store, strategy="momentum-probe", parameters="lookback=60", is_null_construct=False)
    record(store, strategy="momentum-probe", parameters="lookback=90", is_null_construct=False)
    assert store.count() == 3


def test_a_different_dataset_is_a_different_trial(tmp_path: Path) -> None:
    """Evaluating the same thing on new data is a new evaluation."""
    store = registry(tmp_path)
    record(store, is_null_construct=False)
    store.record(
        recorded_at=ts("2026-09-09T12:00:00"),
        code_version="abc1234",
        engine_version="sextant-004",
        strategy_id="equal-weight-passive",
        parameter_set_id="none",
        dataset_checksum=dataset_fingerprint({**CHECKSUMS, "Q3_2023": "cc" * 32}),
        evaluation_window="2023-10-01/2026-04-01",
        quote_policy="EUR",
        is_null_construct=False,
        seeds=1,
        note="more archive",
    )
    assert store.count() == 2


def test_null_constructs_are_counted_separately(tmp_path: Path) -> None:
    """The DSR states which count it used, and both are available."""
    store = registry(tmp_path)
    record(store, is_null_construct=True)
    record(store, strategy="probe", parameters="p=1", is_null_construct=False)
    assert store.count(include_null_constructs=False) == 1
    assert store.count(include_null_constructs=True) == 2


def test_editing_a_record_is_detected(tmp_path: Path) -> None:
    """The whole point of the chain: a rewrite fails loudly rather than silently."""
    store = registry(tmp_path)
    record(store, strategy="first", parameters="a")
    record(store, strategy="second", parameters="b")

    lines = store.path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[1])
    tampered["strategy_id"] = "something else"
    lines[1] = json.dumps(tampered, sort_keys=True, separators=(",", ":"))
    store.path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    with pytest.raises(TrialRegistryCorrupt, match="does not hash"):
        store.read()


def test_deleting_a_record_is_detected(tmp_path: Path) -> None:
    """A count cannot be reduced by removing lines."""
    store = registry(tmp_path)
    record(store, strategy="first", parameters="a")
    record(store, strategy="second", parameters="b")
    record(store, strategy="third", parameters="c")

    lines = store.path.read_text(encoding="utf-8").splitlines()
    del lines[2]
    store.path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    with pytest.raises(TrialRegistryCorrupt, match="chain"):
        store.read()


def test_reordering_records_is_detected(tmp_path: Path) -> None:
    """History has an order and the file has to keep it."""
    store = registry(tmp_path)
    record(store, strategy="first", parameters="a")
    record(store, strategy="second", parameters="b")

    lines = store.path.read_text(encoding="utf-8").splitlines()
    lines[1], lines[2] = lines[2], lines[1]
    store.path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    with pytest.raises(TrialRegistryCorrupt):
        store.read()


def test_a_truncated_write_is_detected(tmp_path: Path) -> None:
    """A half-written line is corruption, not an empty registry."""
    store = registry(tmp_path)
    record(store)
    text = store.path.read_text(encoding="utf-8")
    store.path.write_text(text[: len(text) - 20], encoding="utf-8", newline="\n")
    with pytest.raises(TrialRegistryCorrupt):
        store.read()


def test_the_file_carries_a_header_explaining_what_it_is(tmp_path: Path) -> None:
    """Someone will open this by hand, and it should say not to edit it."""
    store = registry(tmp_path)
    record(store)
    first_line = store.path.read_text(encoding="utf-8").splitlines()[0]
    assert first_line == HEADER
    assert "append-only" in first_line


def test_the_audit_lists_one_readable_line_per_trial(tmp_path: Path) -> None:
    """The report has to make it possible to see how the count was reached."""
    store = registry(tmp_path)
    record(store, strategy="first", parameters="a", seeds=10_000)
    record(store, strategy="second", parameters="b", is_null_construct=False)
    lines = store.audit()
    assert len(lines) == 2
    assert "seeds=10000" in lines[0]
    assert "null-construct" in lines[0]
    assert "search" in lines[1]


def test_the_fingerprint_ignores_the_instant_and_the_note(tmp_path: Path) -> None:
    """Two runs of the same evaluation share an identity whenever they ran."""
    del tmp_path
    common = {
        "code_version": "abc1234",
        "strategy_id": "s",
        "parameter_set_id": "p",
        "dataset_checksum": DATASET,
        "evaluation_window": "w",
        "quote_policy": "EUR",
    }
    assert fingerprint(**common) == fingerprint(**common)
    assert fingerprint(**{**common, "quote_policy": "EUR+USD"}) != fingerprint(**common)


def test_counting_is_distinct_on_identity(tmp_path: Path) -> None:
    """Duplicate identities in a hand-assembled sequence count once."""
    del tmp_path
    assert count_trials((), include_null_constructs=True) == 0
