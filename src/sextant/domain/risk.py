"""Risk verdict types.

The Risk Engine itself lives in ``sextant.engine.risk`` and is not implemented
yet. These are the types its verdicts take, and they are in the
domain because the audit record refers to them.

The engine has veto authority over every decision, including anything an LLM
recommends. There is no path from a model response to an order that does not
pass through a verdict of this type.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from sextant.domain.money import Quantity


class RiskVerdict(StrEnum):
    """The three outcomes the Risk Engine may return."""

    APPROVE = "approve"
    REDUCE = "reduce"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """A verdict, its reason, and the size deterministic code actually allows.

    ``approved_size`` is produced exclusively by deterministic code. No value
    supplied by a language model may reach this field.
    """

    verdict: RiskVerdict
    reason: str
    approved_size: Quantity | None = None
    risk_budget_used: Decimal | None = None
