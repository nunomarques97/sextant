"""The LLMAnalyst port and its response schemas.

The boundary, stated once:

* the model **may recommend** a direction or action, which strategy applies, a
  confidence score, a regime classification, and free-form context;
* the model **may not determine** position size, leverage, maximum exposure,
  risk limits, stop-loss risk budget, or any portfolio risk budget.

That second list is enforced structurally rather than by convention. The
response schemas forbid extra fields, so a model that volunteers
``position_size`` is rejected at parse time; and ``assert_no_risk_fields`` runs
at import, so a future session that *adds* such a field to the schema breaks the
build instead of quietly widening what a language model is allowed to decide.

The flow is one-way and always passes through deterministic code:

    LLM recommendation -> Risk Engine -> APPROVE | REDUCE | REJECT -> execution

There is no path from a model response to an order. Free-form output is carried
as commentary for the audit record and is never parsed as an instruction.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from sextant.domain.errors import SextantError


class RiskFieldInSchema(SextantError):
    """A response schema declared a field the risk layer owns exclusively."""

    def __init__(self, model_name: str, field_name: str, token: str) -> None:
        self.model_name = model_name
        self.field_name = field_name
        self.token = token
        super().__init__(
            f"{model_name}.{field_name} contains the forbidden token {token!r}. "
            "Position size, leverage, exposure, stop distance and risk budgets are "
            "decided exclusively by deterministic code and the Risk Engine. A value "
            "that cannot be expressed cannot be smuggled through."
        )


#: Substrings that may not appear in any field name of an LLM response schema.
#: Matched on the field name, so ``stop_distance``, ``max_exposure`` and
#: ``risk_budget_pct`` are all caught by their token rather than by an exact name.
FORBIDDEN_FIELD_TOKENS: frozenset[str] = frozenset(
    {
        "size",
        "sizing",
        "leverage",
        "exposure",
        "stop",
        "risk",
        "budget",
        "notional",
        "quantity",
        "qty",
        "allocation",
        "weight",
        "margin",
        "capital",
    }
)


def assert_no_risk_fields(model: type[BaseModel]) -> None:
    """Raise ``RiskFieldInSchema`` if ``model`` declares a risk-owned field."""
    for field_name in model.model_fields:
        lowered = field_name.lower()
        for token in sorted(FORBIDDEN_FIELD_TOKENS):
            if token in lowered:
                raise RiskFieldInSchema(model.__name__, field_name, token)


class MarketAction(StrEnum):
    """The directional actions a model is allowed to name."""

    LONG = "long"
    SHORT = "short"
    FLAT = "flat"
    NO_ACTION = "no_action"


class RegimeLabel(StrEnum):
    """Coarse market-state labels a model is allowed to assign."""

    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    HIGH_VOLATILITY = "high_volatility"
    UNKNOWN = "unknown"


class AnalysisRequest(BaseModel):
    """What the analyst is asked about. Values are pre-computed by deterministic code."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    as_of: str = Field(description="ISO-8601 UTC instant the analysis is anchored to.")
    venue: str = Field(description="Venue slug, for the record only. Never branched on.")
    symbols: Sequence[str] = Field(description="Explicit instruments under analysis.")
    timeframe: str = Field(description="Bar timeframe the features were computed on.")
    features: Mapping[str, str] = Field(
        default_factory=dict,
        description="Deterministically computed features, serialised exactly as strings.",
    )


class RegimeAssessment(BaseModel):
    """A regime classification and the reasoning behind it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    regime: RegimeLabel
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(
        default="",
        description="Free-form commentary. Recorded for audit, never parsed as an instruction.",
    )


class StrategyRecommendation(BaseModel):
    """What the analyst suggests. Advisory only; the Risk Engine decides."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: MarketAction
    strategy: str = Field(description="Name of the strategy the analyst believes applies.")
    regime: RegimeLabel = RegimeLabel.UNKNOWN
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(
        default="",
        description="Free-form commentary. Recorded for audit, never parsed as an instruction.",
    )


#: Every schema a language model is allowed to populate. Checked at import time.
LLM_RESPONSE_SCHEMAS: tuple[type[BaseModel], ...] = (
    RegimeAssessment,
    StrategyRecommendation,
)

for _schema in LLM_RESPONSE_SCHEMAS:
    assert_no_risk_fields(_schema)


@runtime_checkable
class LLMAnalyst(Protocol):
    """Non-latency-critical analysis. Never on the order path."""

    def classify_regime(self, request: AnalysisRequest) -> RegimeAssessment:
        """Classify the market regime for the requested instruments."""
        ...

    def recommend(self, request: AnalysisRequest) -> StrategyRecommendation:
        """Recommend an action and a strategy. Advisory input to the Risk Engine."""
        ...
