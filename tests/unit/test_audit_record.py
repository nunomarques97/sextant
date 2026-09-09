"""The audit record and the risk verdict types.

These carry no behaviour worth asserting beyond their shape, and the shape is
the point: ``llm_decision`` and ``risk_verdict`` are separate fields, and the
size a model suggested can never be the size the risk layer approved, because
they live in different objects.
"""

from __future__ import annotations

from dataclasses import fields
from decimal import Decimal

import pytest

from sextant.domain.decision import DecisionRecord
from sextant.domain.money import Price, Quantity
from sextant.domain.risk import RiskDecision, RiskVerdict
from sextant.domain.time import Timestamp
from sextant.domain.venue import Venue
from tests.conftest import ts


def record(**overrides: object) -> DecisionRecord:
    """A fully populated decision record, with every optional field set."""
    base: dict[str, object] = {
        "timestamp": ts("2025-03-01T00:00:00"),
        "run_id": "abc123",
        "symbol": "XBTEUR",
        "venue": Venue("kraken"),
        "regime": "trending",
        "strategy": "cross_sectional_momentum",
        "signal": "long",
        "llm_decision": "enter",
        "confidence": Decimal("0.6"),
        "entry": Price(Decimal(100)),
        "stop": Price(Decimal(90)),
        "target": Price(Decimal(120)),
        "size": Quantity(Decimal("1.5")),
        "expected_edge_bps": Decimal(45),
        "expected_costs_bps": Decimal(12),
        "net_expected_edge_bps": Decimal(33),
        "risk_verdict": RiskVerdict.APPROVE,
        "risk_reason": "within budget",
        "outcome": "filled",
    }
    base.update(overrides)
    return DecisionRecord(**base)  # type: ignore[arg-type]


def test_the_model_recommendation_and_the_risk_verdict_are_separate_fields() -> None:
    """Collapsing them destroys the only analysis worth running on this history.

    Once there is a history, the question is not what the system did. It is
    where the model and the deterministic risk layer disagreed and which was
    right, and a single ``decision`` field cannot answer it.
    """
    subject = record(llm_decision="enter", risk_verdict=RiskVerdict.REJECT)

    assert subject.llm_decision == "enter"
    assert subject.risk_verdict is RiskVerdict.REJECT
    field_names = {field.name for field in fields(DecisionRecord)}
    assert {"llm_decision", "risk_verdict"} <= field_names


def test_a_record_may_be_written_before_its_outcome_is_known() -> None:
    subject = record(outcome=None)

    assert subject.outcome is None
    assert subject.risk_reason == "within budget"


def test_a_record_is_frozen_so_an_audit_trail_cannot_be_edited_after_the_fact() -> None:
    subject = record()

    with pytest.raises(AttributeError):
        subject.risk_reason = "changed my mind"  # type: ignore[misc]


def test_every_verdict_the_risk_engine_may_return_is_enumerated() -> None:
    """Three outcomes and no fourth. A missing verdict is not a silent approve."""
    assert {member.value for member in RiskVerdict} == {"approve", "reduce", "reject"}


def test_an_approved_size_is_optional_and_carries_the_budget_it_consumed() -> None:
    approved = RiskDecision(
        verdict=RiskVerdict.REDUCE,
        reason="position would exceed the per-instrument cap",
        approved_size=Quantity(Decimal("0.5")),
        risk_budget_used=Decimal("0.4"),
    )
    refused = RiskDecision(verdict=RiskVerdict.REJECT, reason="no budget left")

    assert approved.approved_size == Quantity(Decimal("0.5"))
    assert approved.risk_budget_used == Decimal("0.4")
    assert refused.approved_size is None
    assert refused.risk_budget_used is None


def test_the_record_timestamp_is_an_aware_instant() -> None:
    subject = record()

    assert isinstance(subject.timestamp, Timestamp)
    assert subject.timestamp.isoformat().endswith("+00:00")
