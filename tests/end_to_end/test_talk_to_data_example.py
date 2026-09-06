from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from examples.talk_to_data_multi_agent.app import (
    ARCHITECTURE_V2_DIR,
    CONFIG_PATH,
    DEFAULT_SCHEMA,
    SEMANTIC_LAYER_PATH,
)
from examples.talk_to_data_multi_agent.application.analytics_tools import (
    default_airline_analytics_tools,
)
from examples.talk_to_data_multi_agent.application.analytics_workflow import (
    AnalyticsIntent,
    AnalyticsWorkflow,
)
from examples.talk_to_data_multi_agent.application.catalog import load_schema_catalog
from examples.talk_to_data_multi_agent.application.query_executor import QueryResult
from examples.talk_to_data_multi_agent.application.workflow import (
    IntentAssessment,
    SQLGeneration,
    SQLParameter,
    TalkToDataWorkflow,
    WorkflowError,
    validate_parameters,
    validate_read_only_sql,
)
from examples.talk_to_data_multi_agent.dashboard import (
    TalkToDataConversationHandler,
    build_dashboard_app,
)
from traccia_runtime.config.settings import load_settings
from traccia_runtime.conversations import Conversation, ConversationMessage
from traccia_runtime.semantics import SemanticLayer
from traccia_runtime.types.contracts import Role


def intent(
    status: str = "ready", question: str | None = None, interpreted: str = "List current fares"
) -> str:
    return json.dumps(
        {
            "status": status,
            "interpreted_request": interpreted,
            "clarification_question": question,
            "assumptions": ["Use all available dates"],
            "relevant_tables": ["pp_competitor_fares"],
            "evidence_ids": ["S1"],
        }
    )


def sql_output(
    sql: str = (
        "SELECT query_sector AS sector, MIN(price) AS price "
        "FROM public.pp_competitor_fares WHERE is_current = TRUE GROUP BY query_sector"
    ),
) -> str:
    return json.dumps(
        {
            "sql": sql,
            "parameters": [],
            "summary": "Returns current fares by sector.",
            "assumptions": [],
            "source_ids": ["S1"],
        }
    )


def analytics_intent(status: str = "ready", question: str | None = None) -> str:
    return json.dumps(
        {
            "status": status,
            "analysis_kind": "descriptive",
            "interpreted_request": "List current competitor fares",
            "metrics": ["current_minimum_fare"],
            "dimensions": ["competitor_market"],
            "time_horizon": None,
            "scenario_parameters": [],
            "clarification_question": question,
            "assumptions": [],
            "evidence_ids": ["S1"],
        }
    )


def analytics_plan() -> str:
    return json.dumps(
        {
            "executable": True,
            "analysis_kind": "descriptive",
            "analysis_tool": "none",
            "tool_parameters": [],
            "queries": [
                {
                    "id": "current_fares",
                    "purpose": "List current fares by route",
                    "metrics": ["current_minimum_fare"],
                    "dimensions": ["competitor_market"],
                    "time_range": None,
                    "filters": ["is_current = true"],
                    "expected_columns": ["sector", "price"],
                }
            ],
            "assumptions": [],
            "blockers": [],
            "required_data": [],
        }
    )


class FakeClient:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.requests: list[Any] = []

    async def run(self, request: Any) -> Any:
        self.requests.append(request)
        return SimpleNamespace(
            output=self.outputs.pop(0), error=None, run_id=f"run-{len(self.requests)}"
        )


class FakeExecutor:
    async def execute(self, sql: str, parameters: tuple[Any, ...]) -> QueryResult:
        del sql, parameters
        return QueryResult(
            columns=("sector", "price"),
            rows=({"sector": "DEL-BOM", "price": 5100.0},),
            truncated=False,
            execution_ms=1.0,
        )


def catalog() -> Any:
    return load_schema_catalog(DEFAULT_SCHEMA, "tenant-a")


def test_catalog_parses_supplied_markdown_into_retrievable_sections() -> None:
    parsed = catalog()

    assert len(parsed.tables) >= 40
    assert "pnr_flight" in parsed.tables
    assert "pp_rbd_inventory_daily" in parsed.tables
    assert len(parsed.documents) >= 10
    assert all(document.metadata["untrusted_content"] is True for document in parsed.documents)


def test_agent_configuration_has_distinct_intent_and_sql_roles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PGVECTOR_DSN", "postgresql://localhost/example")
    config_path = CONFIG_PATH
    settings = load_settings((config_path,))
    agents = {agent.name: agent for agent in settings.agents}

    assert config_path.name == "agent.yaml"
    assert set(agents) == {
        "talk-to-data-router",
        "talk-to-data-planner",
        "talk-to-data-intent",
        "talk-to-data-sql",
        "talk-to-data-insight",
        "talk-to-data-verifier",
    }
    assert agents["talk-to-data-intent"].context_builder == "retrieval.database_schema"
    assert agents["talk-to-data-router"].default_model.model == "gpt-5-mini"
    assert agents["talk-to-data-intent"].default_model.model == "gpt-5-mini"
    assert agents["talk-to-data-insight"].default_model.model == "gpt-5-mini"
    assert agents["talk-to-data-planner"].default_model.model == "gpt-5"
    assert agents["talk-to-data-sql"].default_model.model == "gpt-5"
    assert agents["talk-to-data-verifier"].default_model.model == "gpt-5"
    assert agents["talk-to-data-sql"].default_model.provider == "openai"
    assert agents["talk-to-data-planner"].budget.max_tokens == 24_000
    assert agents["talk-to-data-planner"].budget.max_output_tokens == 8_000
    assert agents["talk-to-data-planner"].default_model.extensions == {
        "reasoning_effort": "low"
    }
    assert settings.storage.run_store == "postgres"
    assert settings.storage.event_store == "postgres"
    assert settings.storage.audit_store == "postgres"
    assert settings.storage.approval_store == "postgres"
    assert settings.storage.artifact_store == "postgres"
    assert settings.storage.tool_execution_store == "postgres"
    assert settings.storage.conversation_store == "postgres"
    assert settings.storage.postgres_dsn == "env://PGVECTOR_DSN"
    assert settings.runtime.recover_incomplete_runs is True
    assert settings.feature_flags["talk_to_data_show_response_time"] is True
    assert settings.feature_flags["talk_to_data_show_progress"] is True


async def test_dashboard_html_is_served_by_example_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PGVECTOR_DSN", "postgresql://localhost/example")
    transport = httpx.ASGITransport(app=build_dashboard_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")
        diagram = await client.get("/architecture/agents")
        high_level_v2 = await client.get("/architecture/v2/high-level")
        detailed_v2 = await client.get("/architecture/v2/detailed")
        animated_v2 = await client.get("/architecture/v2/animated")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    assert "Talk to Data · Traccia" in response.text
    assert "workflow.progress" in response.text
    assert "Response time:" in response.text
    assert "How this response was prepared" in response.text
    assert diagram.status_code == 200
    assert "How the agents interact" in diagram.text
    assert high_level_v2.status_code == 200
    assert "Executive airline" in high_level_v2.text
    assert detailed_v2.status_code == 200
    assert "Governed conversational airline analytics" in detailed_v2.text
    assert animated_v2.status_code == 200
    assert "Follow a question through the system" in animated_v2.text
    assert "Which agencies are materially gaining or losing market share" in animated_v2.text
    assert (ARCHITECTURE_V2_DIR / "high_level.html").is_file()
    assert (ARCHITECTURE_V2_DIR / "detailed.html").is_file()
    assert (ARCHITECTURE_V2_DIR / "detailed_animated.html").is_file()


@pytest.mark.parametrize("model", [IntentAssessment, AnalyticsIntent, SQLGeneration])
def test_structured_output_schemas_require_every_property(model: Any) -> None:
    schema = model.model_json_schema()
    assert set(schema["properties"]) == set(schema["required"])
async def test_workflow_skips_clarification_when_intent_is_adequate() -> None:
    client = FakeClient([intent(), sql_output()])
    asked: list[str] = []

    async def clarify(question: str) -> str:
        asked.append(question)
        return "unused"

    result = await TalkToDataWorkflow(
        client, catalog(), tenant_id="tenant-a", user_id="user-a"
    ).run("List current competitor fares", clarify)

    assert result.sql.endswith(";")
    assert result.clarification_count == 0
    assert asked == []
    assert [request.agent for request in client.requests] == [
        "talk-to-data-intent",
        "talk-to-data-sql",
    ]
    assert len({request.correlation_id for request in client.requests}) == 1


async def test_workflow_reassesses_after_one_minimal_clarification() -> None:
    client = FakeClient(
        [
            intent("needs_clarification", "Do you want current or historical fares?"),
            intent(interpreted="List current competitor fares"),
            sql_output(),
        ]
    )

    async def clarify(question: str) -> str:
        assert question == "Do you want current or historical fares?"
        return "Current fares"

    result = await TalkToDataWorkflow(
        client, catalog(), tenant_id="tenant-a", user_id="user-a"
    ).run("Show competitor fares", clarify)

    assert result.clarification_count == 1
    assert "Current fares" in client.requests[1].input
    assert len(client.requests) == 3


async def test_workflow_stops_after_bounded_clarifications() -> None:
    client = FakeClient(
        [intent("needs_clarification", "Which measure?")] * 2
    )

    async def clarify(question: str) -> str:
        return "I do not know"

    with pytest.raises(WorkflowError, match="still ambiguous"):
        await TalkToDataWorkflow(
            client,
            catalog(),
            tenant_id="tenant-a",
            user_id="user-a",
            max_clarifications=1,
        ).run("Show performance", clarify)


async def test_dashboard_turn_converts_undocumented_source_into_clarification() -> None:
    output = json.dumps(
        {
            "status": "ready",
            "interpreted_request": "Compare bookings and revenue by route this month",
            "clarification_question": None,
            "assumptions": [],
            "relevant_tables": ["unknown_commercial_rollup"],
            "evidence_ids": ["S1"],
        }
    )
    turn = await TalkToDataWorkflow(
        FakeClient([output]), catalog(), tenant_id="tenant-a", user_id="user-a"
    ).run_turn("Compare bookings and revenue by route this month", [])

    assert turn.status.value == "needs_clarification"
    assert "raw pnr_flight" in (turn.clarification_question or "")


async def test_dashboard_handler_resumes_clarification_as_next_message() -> None:
    runtime = FakeClient(
        [
            analytics_intent("needs_clarification", "Current or historical fares?"),
            analytics_intent(),
            analytics_plan(),
            sql_output(),
        ]
    )
    handler = TalkToDataConversationHandler(
        SimpleNamespace(runtime=runtime),
        catalog(),
        SemanticLayer.from_yaml(SEMANTIC_LAYER_PATH),
        default_airline_analytics_tools(),
        max_clarifications=2,
        show_response_time=True,
        show_progress=True,
    )
    conversation = Conversation(
        tenant_id="tenant-a",
        user_id="user-a",
        agent="talk-to-data-intent",
        handler="talk-to-data",
    )
    first_user = ConversationMessage.text(
        conversation_id=conversation.id,
        tenant_id="tenant-a",
        role=Role.USER,
        text="Show competitor fares",
    )

    events: list[tuple[str, dict[str, Any]]] = []

    async def emit(event_type: str, data: dict[str, Any] | None = None) -> None:
        events.append((event_type, data or {}))

    clarification = await handler.handle(conversation, first_user, (first_user,), emit)
    assert clarification.metadata["kind"] == "clarification"
    first_assistant = ConversationMessage(
        conversation_id=conversation.id,
        tenant_id="tenant-a",
        role=Role.ASSISTANT,
        content=clarification.content,
        metadata=clarification.metadata,
    )
    second_user = ConversationMessage.text(
        conversation_id=conversation.id,
        tenant_id="tenant-a",
        role=Role.USER,
        text="Current",
    )
    completed = await handler.handle(
        conversation, second_user, (first_user, first_assistant, second_user), emit
    )

    assert completed.metadata["kind"] == "analytical_result"
    assert completed.metadata["response_time_ms"] >= 0
    assert [step["step"] for step in completed.metadata["execution_steps"]] == [
        "understand",
        "plan",
        "generate_sql",
        "complete",
    ]
    progress_events = [data for name, data in events if name == "workflow.progress"]
    assert progress_events
    assert all(event["safe_summary"] is True for event in progress_events)
    assert any(block.type == "code" for block in completed.content)
    assert completed.run_ids == ("run-2", "run-3", "run-4")
    assert runtime.requests[1].conversation_id == conversation.id
    assert runtime.requests[2].parent_run_id == "run-2"
    assert runtime.requests[3].parent_run_id == "run-3"


async def test_advanced_workflow_runs_bounded_specialist_pipeline() -> None:
    report = json.dumps(
        {
            "headline": "Current fare observed",
            "summary": "The current DEL-BOM fare is 5,100.",
            "findings": ["One current route fare was returned."],
            "recommendations": ["Compare against history before acting."],
            "limitations": ["Single snapshot."],
            "confidence": 0.8,
        }
    )
    review = json.dumps(
        {
            "approved": True,
            "issues": [],
            "corrected_summary": None,
            "confidence": 0.8,
        }
    )
    runtime = FakeClient(
        [analytics_intent(), analytics_plan(), sql_output(), report, review]
    )
    result = await AnalyticsWorkflow(
        runtime,
        catalog(),
        SemanticLayer.from_yaml(SEMANTIC_LAYER_PATH),
        default_airline_analytics_tools(),
        tenant_id="tenant-a",
        user_id="user-a",
        executor=FakeExecutor(),
    ).run_turn("List current competitor fares", [])

    assert result.status.value == "completed"
    assert result.analysis is not None
    assert result.review is not None and result.review.approved
    assert [request.agent for request in runtime.requests] == [
        "talk-to-data-router",
        "talk-to-data-planner",
        "talk-to-data-sql",
        "talk-to-data-insight",
        "talk-to-data-verifier",
    ]
    assert runtime.requests[0].metadata["retrieval_query"] == (
        "List current competitor fares"
    )
    assert runtime.requests[2].metadata["retrieval_query"] == (
        "List current competitor fares\nList current fares by route"
    )
    assert len(runtime.requests[0].metadata["retrieval_query"]) < len(
        runtime.requests[0].input
    )
    assert len(result.run_ids) == 5


async def test_advanced_workflow_repairs_undefined_planner_dimension() -> None:
    invalid_plan = json.loads(analytics_plan())
    invalid_plan["queries"][0]["id"] = "q1_route_flight"
    invalid_plan["queries"][0]["dimensions"] = ["period"]
    runtime = FakeClient(
        [
            analytics_intent(),
            json.dumps(invalid_plan),
            analytics_plan(),
            sql_output(),
        ]
    )
    events: list[tuple[str, dict[str, Any]]] = []

    async def emit(event_type: str, data: dict[str, Any] | None = None) -> None:
        events.append((event_type, data or {}))

    result = await AnalyticsWorkflow(
        runtime,
        catalog(),
        SemanticLayer.from_yaml(SEMANTIC_LAYER_PATH),
        default_airline_analytics_tools(),
        tenant_id="tenant-a",
        user_id="user-a",
    ).run_turn("List current competitor fares", [], emit=emit)

    assert result.status.value == "completed"
    assert result.run_ids == ("run-1", "run-2", "run-3", "run-4")
    assert [request.agent for request in runtime.requests] == [
        "talk-to-data-router",
        "talk-to-data-planner",
        "talk-to-data-planner",
        "talk-to-data-sql",
    ]
    repair_input = json.loads(runtime.requests[2].input)
    assert repair_input["invalid_plan"]["queries"][0]["dimensions"] == ["period"]
    assert "semantic dimension 'period' is not defined" in repair_input["validation_error"]
    assert "period" not in repair_input["allowed_dimension_names"]
    assert any(
        event_type == "agent.status.changed"
        and data.get("status") == "repairing_analysis_plan"
        for event_type, data in events
    )


async def test_advanced_workflow_repairs_incompatible_router_selection() -> None:
    invalid_intent = json.loads(analytics_intent())
    invalid_intent.update(
        {
            "analysis_kind": "static_scenario",
            "interpreted_request": "Increase average fare by 5% at constant volume",
            "metrics": ["average_fare", "ticket_revenue", "ancillary_revenue"],
            "dimensions": ["route", "flight_date"],
            "scenario_parameters": [{"name": "percent_change", "value": 5}],
        }
    )
    repaired_intent = {
        **invalid_intent,
        "metrics": ["average_fare", "ticket_revenue", "bookings"],
    }
    static_plan = {
        "executable": True,
        "analysis_kind": "static_scenario",
        "analysis_tool": "static_fare_scenario",
        "tool_parameters": [
            {"name": "value_column", "value": "revenue"},
            {"name": "percent_change", "value": 5},
        ],
        "queries": [
            {
                "id": "fare_scenario",
                "purpose": "Calculate baseline ticket revenue by route",
                "metrics": ["ticket_revenue"],
                "dimensions": ["route"],
                "time_range": None,
                "filters": [],
                "expected_columns": ["route", "revenue"],
            }
        ],
        "assumptions": ["Passenger volume remains constant"],
        "blockers": [],
        "required_data": [],
    }
    invalid_static_plan = json.loads(json.dumps(static_plan))
    invalid_static_plan["tool_parameters"] = [
        {"name": "percent_change", "value": 5}
    ]
    generation = sql_output(
        "SELECT sector AS route, SUM(total_amount) AS revenue "
        "FROM public.pnr_flight GROUP BY sector"
    )
    invalid_generation = sql_output(
        "SELECT sector AS route, SUM(total_amount) AS ticket_revenue "
        "FROM public.pnr_flight GROUP BY sector"
    )
    runtime = FakeClient(
        [
            json.dumps(invalid_intent),
            json.dumps(repaired_intent),
            json.dumps(invalid_static_plan),
            json.dumps(static_plan),
            invalid_generation,
            generation,
        ]
    )
    events: list[tuple[str, dict[str, Any]]] = []

    async def emit(event_type: str, data: dict[str, Any] | None = None) -> None:
        events.append((event_type, data or {}))

    result = await AnalyticsWorkflow(
        runtime,
        catalog(),
        SemanticLayer.from_yaml(SEMANTIC_LAYER_PATH),
        default_airline_analytics_tools(),
        tenant_id="tenant-a",
        user_id="user-a",
    ).run_turn(
        "What is the static revenue impact of increasing average fare by 5%?",
        [],
        emit=emit,
    )

    assert result.status.value == "completed"
    assert result.intent is not None
    assert "ancillary_revenue" not in result.intent.metrics
    repair_input = json.loads(runtime.requests[1].input)
    assert "ancillary_revenue" in repair_input["invalid_intent"]["metrics"]
    assert "cannot be grouped" in repair_input["validation_error"]
    plan_repair_input = json.loads(runtime.requests[3].input)
    assert "value_column" in plan_repair_input["validation_error"]
    assert "Field required" in plan_repair_input["validation_error"]
    sql_repair_input = json.loads(runtime.requests[5].input)
    assert "missing ['revenue']" in sql_repair_input["validation_error"]
    assert sql_repair_input["invalid_generation"]["sql"].endswith(
        "GROUP BY sector"
    )
    assert any(
        event_type == "agent.status.changed"
        and data.get("status") == "repairing_analytics_intent"
        for event_type, data in events
    )
    assert any(
        event_type == "agent.status.changed"
        and data.get("status") == "repairing_analysis_plan"
        for event_type, data in events
    )


async def test_advanced_workflow_does_not_narrate_insufficient_analysis() -> None:
    anomaly_intent = json.loads(analytics_intent())
    anomaly_intent["analysis_kind"] = "anomaly"
    anomaly_plan = json.loads(analytics_plan())
    anomaly_plan.update(
        {
            "analysis_kind": "anomaly",
            "analysis_tool": "anomaly_detection",
            "tool_parameters": [{"name": "value_column", "value": "price"}],
        }
    )
    runtime = FakeClient(
        [json.dumps(anomaly_intent), json.dumps(anomaly_plan), sql_output()]
    )

    result = await AnalyticsWorkflow(
        runtime,
        catalog(),
        SemanticLayer.from_yaml(SEMANTIC_LAYER_PATH),
        default_airline_analytics_tools(),
        tenant_id="tenant-a",
        user_id="user-a",
        executor=FakeExecutor(),
    ).run_turn("Find unexpected competitor fares", [])

    assert result.analysis is not None
    assert result.analysis.status.value == "insufficient_data"
    assert result.report is None
    assert result.review is None
    assert [request.agent for request in runtime.requests] == [
        "talk-to-data-router",
        "talk-to-data-planner",
        "talk-to-data-sql",
    ]


async def test_flight_inventory_ranking_repairs_unbounded_sql() -> None:
    inventory_intent = {
        "status": "ready",
        "analysis_kind": "descriptive",
        "interpreted_request": "Rank current flights by lowest remaining inventory",
        "metrics": ["current_inventory_remaining"],
        "dimensions": [
            "current_inventory_date",
            "current_inventory_route",
            "current_inventory_flight",
        ],
        "time_horizon": None,
        "scenario_parameters": [],
        "clarification_question": None,
        "assumptions": ["Use current inventory state"],
        "evidence_ids": ["S1"],
    }
    inventory_plan = {
        "executable": True,
        "analysis_kind": "descriptive",
        "analysis_tool": "none",
        "tool_parameters": [],
        "queries": [
            {
                "id": "lowest_inventory_flights",
                "purpose": "Rank flight instances by lowest remaining inventory",
                "metrics": ["current_inventory_remaining"],
                "dimensions": [
                    "current_inventory_date",
                    "current_inventory_route",
                    "current_inventory_flight",
                ],
                "time_range": None,
                "filters": [],
                "expected_columns": [
                    "flight_date",
                    "route",
                    "flight",
                    "remaining_inventory",
                ],
            }
        ],
        "assumptions": [],
        "blockers": [],
        "required_data": [],
    }
    projection = (
        "SELECT flight_date AS flight_date, sector AS route, flight_no AS flight, "
        "SUM(authorized_units - sold_seats) FILTER (WHERE authorized_units IS NOT NULL "
        "AND sold_seats IS NOT NULL) AS remaining_inventory "
        "FROM public.pp_rbd_inventory_daily "
        "GROUP BY flight_date, sector, flight_no"
    )
    runtime = FakeClient(
        [
            json.dumps(inventory_intent),
            json.dumps(inventory_plan),
            sql_output(projection),
            sql_output(projection + " ORDER BY remaining_inventory ASC LIMIT 10"),
        ]
    )

    result = await AnalyticsWorkflow(
        runtime,
        catalog(),
        SemanticLayer.from_yaml(SEMANTIC_LAYER_PATH),
        default_airline_analytics_tools(),
        tenant_id="tenant-a",
        user_id="user-a",
    ).run_turn("Which flights have the lowest remaining inventory?", [])

    assert result.status.value == "completed"
    assert result.queries[0].generation.sql.endswith(
        "ORDER BY remaining_inventory ASC LIMIT 10;"
    )
    repair_input = json.loads(runtime.requests[3].input)
    assert "ORDER BY" in repair_input["validation_error"]


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM pnr_flight",
        "SELECT * FROM undocumented_table",
        "SELECT pg_read_file('/etc/passwd') FROM pnr_flight",
        "SELECT * FROM pnr_flight; SELECT * FROM rbd_daily",
        "SELECT * FROM pnr_flight -- bypass",
    ],
)
def test_sql_guard_rejects_unsafe_or_undocumented_sql(statement: str) -> None:
    with pytest.raises(WorkflowError):
        validate_read_only_sql(statement, catalog().tables)


def test_sql_guard_supports_documented_tables_and_ctes() -> None:
    sql = validate_read_only_sql(
        "WITH fares AS (SELECT sector, price FROM pp_competitor_fares "
        "WHERE is_current = TRUE) SELECT sector, AVG(price) FROM fares GROUP BY sector",
        catalog().tables,
    )
    assert sql.endswith(";")


def test_parameter_guard_requires_consecutive_matching_positions() -> None:
    validate_parameters(
        "SELECT * FROM pnr_flight WHERE flight_number = $1;",
        (SQLParameter(position=1, name="flight_number", value="XY123"),),
    )
    with pytest.raises(WorkflowError, match="do not match"):
        validate_parameters("SELECT * FROM pnr_flight WHERE flight_number = $2;", ())
