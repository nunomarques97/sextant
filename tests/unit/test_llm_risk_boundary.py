"""The boundary between what a model may recommend and what only code may decide.

This is the test that must break if a future change widens the LLM schemas.
It is not testing a convention; it is testing that the schema physically cannot
express a position size, and that adding such a field is a build failure.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from sextant.ports.llm import (
    LLM_RESPONSE_SCHEMAS,
    AnalysisRequest,
    MarketAction,
    RegimeAssessment,
    RegimeLabel,
    RiskFieldInSchema,
    StrategyRecommendation,
    assert_no_risk_fields,
)

FORBIDDEN_PAYLOAD_KEYS = [
    "position_size",
    "leverage",
    "exposure",
    "stop_distance",
    "max_exposure",
    "risk_budget",
    "stop_loss",
    "portfolio_weight",
]


def valid_recommendation_payload() -> dict[str, object]:
    return {
        "action": MarketAction.LONG.value,
        "strategy": "cross_sectional_momentum",
        "regime": RegimeLabel.TRENDING_UP.value,
        "confidence": 0.62,
        "rationale": "Breadth improving across the universe.",
    }


def test_a_valid_recommendation_parses() -> None:
    recommendation = StrategyRecommendation.model_validate(valid_recommendation_payload())
    assert recommendation.action is MarketAction.LONG
    assert recommendation.confidence == pytest.approx(0.62)


@pytest.mark.parametrize("forbidden_key", FORBIDDEN_PAYLOAD_KEYS)
def test_the_recommendation_schema_rejects_a_risk_field_in_the_payload(forbidden_key: str) -> None:
    payload = valid_recommendation_payload()
    payload[forbidden_key] = 1

    with pytest.raises(ValidationError) as raised:
        StrategyRecommendation.model_validate(payload)

    assert "extra_forbidden" in str(raised.value)


@pytest.mark.parametrize("forbidden_key", FORBIDDEN_PAYLOAD_KEYS)
def test_the_regime_schema_rejects_a_risk_field_in_the_payload(forbidden_key: str) -> None:
    with pytest.raises(ValidationError):
        RegimeAssessment.model_validate(
            {"regime": RegimeLabel.RANGING.value, "confidence": 0.4, forbidden_key: 1}
        )


def test_no_shipped_response_schema_declares_a_risk_owned_field() -> None:
    for schema in LLM_RESPONSE_SCHEMAS:
        assert_no_risk_fields(schema)


@pytest.mark.parametrize(
    "field_name",
    ["position_size", "leverage", "max_exposure", "stop_distance", "risk_budget_pct"],
)
def test_adding_a_risk_field_to_a_response_schema_is_a_build_failure(field_name: str) -> None:
    """A future change that widens the schema breaks here, not in the risk model."""
    widened = type(
        "WidenedRecommendation",
        (BaseModel,),
        {
            "model_config": ConfigDict(extra="forbid"),
            "__annotations__": {"action": MarketAction, field_name: float},
            field_name: 0.0,
        },
    )

    with pytest.raises(RiskFieldInSchema) as raised:
        assert_no_risk_fields(widened)

    assert raised.value.field_name == field_name


def test_confidence_must_stay_within_zero_and_one() -> None:
    payload = valid_recommendation_payload()
    payload["confidence"] = 1.4
    with pytest.raises(ValidationError):
        StrategyRecommendation.model_validate(payload)


def test_free_form_text_is_carried_as_commentary_and_never_as_a_field_of_its_own() -> None:
    recommendation = StrategyRecommendation.model_validate(valid_recommendation_payload())
    assert recommendation.rationale.startswith("Breadth")
    assert set(StrategyRecommendation.model_fields) == {
        "action",
        "strategy",
        "regime",
        "confidence",
        "rationale",
    }


def test_the_request_schema_also_forbids_extras() -> None:
    with pytest.raises(ValidationError):
        AnalysisRequest.model_validate(
            {
                "as_of": "2024-01-01T00:00:00+00:00",
                "venue": "kraken",
                "symbols": ["BTCEUR"],
                "timeframe": "1h",
                "account_equity": 1750,
            }
        )
