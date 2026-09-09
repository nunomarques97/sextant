"""The audit record for one decision.

``llm_decision`` and ``risk_verdict`` are separate fields on purpose. Once
there is history, the interesting question is not what the system did but where
the model and the deterministic risk layer disagreed, and which of the two was
right. Collapsing them into one "decision" field destroys that analysis before
it can be run.

Nothing writes this record yet. It is defined now so that the fields are fixed
before the first strategy exists, rather than reverse-engineered from whatever
the first strategy happened to log.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sextant.domain.money import Price, Quantity
from sextant.domain.risk import RiskVerdict
from sextant.domain.time import Timestamp
from sextant.domain.venue import Venue


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """One fully audited decision, from signal through risk verdict to outcome."""

    timestamp: Timestamp
    run_id: str
    symbol: str
    venue: Venue
    regime: str | None
    strategy: str
    signal: str
    llm_decision: str | None
    confidence: Decimal | None
    entry: Price | None
    stop: Price | None
    target: Price | None
    size: Quantity | None
    expected_edge_bps: Decimal | None
    expected_costs_bps: Decimal | None
    net_expected_edge_bps: Decimal | None
    risk_verdict: RiskVerdict
    risk_reason: str
    outcome: str | None = None
