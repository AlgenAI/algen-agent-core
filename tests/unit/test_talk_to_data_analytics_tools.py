from __future__ import annotations

import pytest
from pydantic import ValidationError

from examples.talk_to_data_multi_agent.application.analytics_tools import (
    default_airline_analytics_tools,
)
from traccia_runtime.semantics import AnalysisStatus


def test_static_fare_scenario_is_explicitly_constant_volume() -> None:
    outcome = default_airline_analytics_tools().execute(
        "static_fare_scenario",
        ({"revenue": 100.0}, {"revenue": 200.0}),
        {"value_column": "revenue", "percent_change": 10},
    )

    assert outcome.values["scenario"] == 330.0
    assert outcome.values["delta"] == 30.0
    assert "not a demand forecast" in outcome.warnings[0]


def test_static_fare_scenario_rejects_missing_column_contract() -> None:
    with pytest.raises(ValidationError, match="value_column"):
        default_airline_analytics_tools().execute(
            "static_fare_scenario",
            ({"ticket_revenue": 100.0},),
            {"percent_change": 5},
        )


def test_causal_scenario_requires_a_real_model_and_inputs() -> None:
    outcome = default_airline_analytics_tools().execute(
        "causal_scenario_gate", (), {"scenario": "adding a flight"}
    )

    assert outcome.status == AnalysisStatus.REQUIRES_MODEL
    assert {item.name for item in outcome.requirements} == {
        "intervention_history",
        "demand_model",
        "operational_constraints",
    }


def test_linear_forecast_refuses_too_little_history() -> None:
    outcome = default_airline_analytics_tools().execute(
        "linear_forecast", ({"value": 1.0}, {"value": 2.0}), {}
    )

    assert outcome.status == AnalysisStatus.INSUFFICIENT_DATA


def test_opportunity_screen_ranks_only_supplied_governed_scores() -> None:
    outcome = default_airline_analytics_tools().execute(
        "opportunity_screen",
        (
            {"route": "A-B", "opportunity_score": 2.0},
            {"route": "C-D", "opportunity_score": 8.0},
        ),
        {"top_n": 1},
    )

    assert outcome.values["candidates"] == [
        {"entity": "C-D", "opportunity_score": 8.0}
    ]
    assert "does not establish causal uplift" in outcome.warnings[1]
