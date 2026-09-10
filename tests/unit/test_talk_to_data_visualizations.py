from __future__ import annotations

from examples.talk_to_data_multi_agent.application.analytics_tools import (
    default_airline_analytics_tools,
)
from examples.talk_to_data_multi_agent.application.analytics_workflow import QueryTask
from examples.talk_to_data_multi_agent.application.query_executor import QueryResult
from examples.talk_to_data_multi_agent.application.visualizations import (
    AirlineVisualizationPlanner,
)


def _result(columns: tuple[str, ...], rows: tuple[dict[str, object], ...]) -> QueryResult:
    return QueryResult(columns=columns, rows=rows, truncated=False, execution_ms=1.0)


def test_inventory_visualization_uses_flight_identity_and_remaining_inventory() -> None:
    task = QueryTask(
        id="lowest_inventory",
        purpose="Rank flights by lowest remaining inventory",
        metrics=("current_inventory_remaining",),
        dimensions=(
            "current_inventory_date",
            "current_inventory_route",
            "current_inventory_flight",
        ),
        time_range=None,
        filters=(),
        expected_columns=("flight_date", "route", "flight", "remaining_inventory"),
    )
    result = _result(
        ("flight_date", "route", "flight", "remaining_inventory"),
        (
            {
                "flight_date": "2026-09-10",
                "route": "HYD-GOX",
                "flight": "6E-101",
                "remaining_inventory": 4,
            },
        ),
    )

    chart = AirlineVisualizationPlanner().plan(task, result)

    assert chart is not None
    assert chart.title == "Flights with the lowest remaining inventory"
    assert chart.specification["encoding"]["x"]["field"] == "remaining_inventory"
    assert chart.specification["encoding"]["y"]["field"] == "flight_label"
    assert (
        chart.specification["data"]["values"][0]["flight_label"] == "6E-101 · HYD-GOX · 2026-09-10"
    )


def test_static_scenario_visualization_uses_deterministic_analysis_values() -> None:
    task = QueryTask(
        id="baseline",
        purpose="Calculate portfolio baseline",
        metrics=("ticket_revenue",),
        dimensions=(),
        time_range=None,
        filters=(),
        expected_columns=("ticket_revenue",),
    )
    result = _result(("ticket_revenue",), ({"ticket_revenue": 100.0},))
    analysis = default_airline_analytics_tools().execute(
        "static_fare_scenario",
        result.rows,
        {"value_column": "ticket_revenue", "percent_change": 5},
    )

    chart = AirlineVisualizationPlanner().plan(task, result, analysis)

    assert chart is not None
    assert chart.title == "Revenue impact"
    assert chart.specification["data"]["values"] == [
        {"case": "Baseline", "revenue": 100.0},
        {"case": "Scenario", "revenue": 105.0},
    ]


def test_temporal_result_uses_line_chart() -> None:
    task = QueryTask(
        id="revenue_trend",
        purpose="Show revenue trend",
        metrics=("ticket_revenue",),
        dimensions=("flight_date",),
        time_range="last 30 days",
        filters=(),
        expected_columns=("flight_date", "ticket_revenue"),
    )
    result = _result(
        ("flight_date", "ticket_revenue"),
        (
            {"flight_date": "2026-09-01", "ticket_revenue": 100.0},
            {"flight_date": "2026-09-02", "ticket_revenue": 120.0},
        ),
    )

    chart = AirlineVisualizationPlanner().plan(task, result)

    assert chart is not None
    assert chart.specification["mark"]["type"] == "line"
    assert chart.specification["encoding"]["x"]["type"] == "temporal"
