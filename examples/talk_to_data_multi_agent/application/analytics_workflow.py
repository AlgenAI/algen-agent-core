from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Sequence
from enum import StrEnum
from time import perf_counter
from typing import Any, Protocol, TypeVar, cast
from uuid import uuid4

from opentelemetry import trace
from pydantic import BaseModel, ConfigDict, Field, model_validator

from examples.talk_to_data_multi_agent.application.analytics_tools import (
    AirlineAnalyticsToolRegistry,
)
from examples.talk_to_data_multi_agent.application.catalog import SchemaCatalog
from examples.talk_to_data_multi_agent.application.workflow import (
    SQLGeneration,
    WorkflowError,
    validate_parameters,
    validate_read_only_sql,
)
from traccia_runtime.analytics import AnalyticalResult, GraphExecutionContext, ResultProvenance
from traccia_runtime.cache import CacheContext
from traccia_runtime.exceptions.errors import ConfigurationError, NotFoundError
from traccia_runtime.methods import AnalyticalMethodRegistry, MethodExecutionRequest
from traccia_runtime.semantics import (
    AnalysisKind,
    AnalysisOutcome,
    AnalysisStatus,
    SemanticLayer,
)
from traccia_runtime.semantics.contracts import Certification, Sensitivity
from traccia_runtime.types.contracts import RequestOverrides, RunRequest


class IntentStatus(StrEnum):
    READY = "ready"
    NEEDS_CLARIFICATION = "needs_clarification"
    UNSUPPORTED = "unsupported"


class NamedValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    value: str | int | float | bool


class AnalyticsIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: IntentStatus
    analysis_kind: AnalysisKind
    interpreted_request: str
    metrics: tuple[str, ...]
    dimensions: tuple[str, ...]
    time_horizon: str | None
    scenario_parameters: tuple[NamedValue, ...]
    clarification_question: str | None
    assumptions: tuple[str, ...]
    evidence_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_clarification(self) -> AnalyticsIntent:
        needs = self.status == IntentStatus.NEEDS_CLARIFICATION
        if needs != bool(self.clarification_question):
            raise ValueError("clarification_question must be set only when clarification is needed")
        return self


class QueryTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    purpose: str
    metrics: tuple[str, ...]
    dimensions: tuple[str, ...]
    time_range: str | None
    filters: tuple[str, ...]
    expected_columns: tuple[str, ...]


class AnalyticsPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    executable: bool
    analysis_kind: AnalysisKind
    analysis_tool: str
    tool_parameters: tuple[NamedValue, ...]
    queries: tuple[QueryTask, ...] = Field(max_length=3)
    assumptions: tuple[str, ...]
    blockers: tuple[str, ...]
    required_data: tuple[str, ...]

    @model_validator(mode="after")
    def validate_execution(self) -> AnalyticsPlan:
        if self.executable and (not self.queries or self.blockers):
            raise ValueError("an executable plan requires queries and no blockers")
        if not self.executable and not self.blockers:
            raise ValueError("a blocked plan requires at least one blocker")
        return self


class GeneratedQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    task_id: str
    purpose: str
    generation: SQLGeneration


class InsightReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    headline: str
    summary: str
    findings: tuple[str, ...]
    recommendations: tuple[str, ...]
    limitations: tuple[str, ...]
    confidence: float = Field(ge=0, le=1)


class AnalysisReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    approved: bool
    issues: tuple[str, ...]
    corrected_summary: str | None
    confidence: float = Field(ge=0, le=1)


class AdvancedTurnStatus(StrEnum):
    NEEDS_CLARIFICATION = "needs_clarification"
    COMPLETED = "completed"
    BLOCKED = "blocked"


class AdvancedTurnResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    status: AdvancedTurnStatus
    message: str
    intent: AnalyticsIntent | None = None
    plan: AnalyticsPlan | None = None
    queries: tuple[GeneratedQuery, ...] = ()
    query_results: tuple[Any, ...] = ()
    analysis: AnalysisOutcome | None = None
    report: InsightReport | None = None
    review: AnalysisReview | None = None
    run_ids: tuple[str, ...] = ()
    correlation_id: str


class AgentClient(Protocol):
    async def run(self, request: RunRequest) -> Any: ...


class QueryExecutor(Protocol):
    async def execute(
        self,
        sql: str,
        parameters: tuple[Any, ...],
        *,
        cache_context: CacheContext | None = None,
    ) -> Any: ...


Structured = TypeVar("Structured", bound=BaseModel)

_RANKING_TERMS = re.compile(r"\b(lowest|highest|top|bottom|rank|ranking)\b", re.IGNORECASE)
_NUMERIC_LIMIT = re.compile(r"\blimit\s+(\d+)\b", re.IGNORECASE)
_MAX_RANKING_ROWS = 100
_DEFAULT_RANKING_ROWS = 10


class AnalyticsWorkflow:
    """Bounded multi-agent analytics pipeline with deterministic analytical tools."""

    def __init__(
        self,
        client: AgentClient,
        catalog: SchemaCatalog,
        semantic_layer: SemanticLayer,
        tools: AirlineAnalyticsToolRegistry,
        *,
        tenant_id: str,
        user_id: str,
        executor: QueryExecutor | None = None,
        max_clarifications: int = 2,
        max_intent_repairs: int = 1,
        max_plan_repairs: int = 1,
        max_query_repairs: int = 1,
        minimum_semantic_certification: Certification | None = None,
        maximum_semantic_sensitivity: Sensitivity | None = None,
        semantic_authorization_tags: frozenset[str] = frozenset(),
        method_registry: AnalyticalMethodRegistry | None = None,
    ) -> None:
        self._client = client
        self._catalog = catalog
        self._semantic_layer = semantic_layer
        self._tools = tools
        self._tenant_id = tenant_id
        self._user_id = user_id
        self._executor = executor
        self._max_clarifications = max_clarifications
        self._max_intent_repairs = max_intent_repairs
        self._max_plan_repairs = max_plan_repairs
        self._max_query_repairs = max_query_repairs
        configured_certification = minimum_semantic_certification or str(
            semantic_layer.definition.metadata.get("minimum_query_certification", "verified")
        )
        configured_sensitivity = maximum_semantic_sensitivity or str(
            semantic_layer.definition.metadata.get("maximum_query_sensitivity", "confidential")
        )
        if configured_certification not in {"draft", "verified", "certified"}:
            raise ConfigurationError(
                "minimum semantic certification must be draft, verified, or certified"
            )
        if configured_sensitivity not in {
            "public",
            "internal",
            "confidential",
            "restricted",
        }:
            raise ConfigurationError(
                "maximum semantic sensitivity must be public, internal, confidential, or restricted"
            )
        self._minimum_semantic_certification = cast(Certification, configured_certification)
        self._maximum_semantic_sensitivity = cast(Sensitivity, configured_sensitivity)
        self._semantic_authorization_tags = semantic_authorization_tags
        self._method_registry = method_registry
        self._tracer = trace.get_tracer("examples.talk_to_data.analytics")

    async def run_turn(
        self,
        question: str,
        clarifications: list[tuple[str, str]],
        *,
        correlation_id: str | None = None,
        conversation_id: str | None = None,
        turn_id: str | None = None,
        emit: Any | None = None,
    ) -> AdvancedTurnResult:
        workflow_started = perf_counter()
        correlation = correlation_id or str(uuid4())
        runs: list[str] = []
        await _emit_progress(
            emit,
            workflow_started,
            step="understand",
            label="Understanding the request",
            description="Classifying the question and resolving governed metrics and dimensions.",
            index=1,
        )
        await _emit(emit, "agent.status.changed", {"status": "classifying_analysis"})
        router_payload: dict[str, Any] = {
            "question": question,
            "clarifications": clarifications,
            "semantic_layer": self._semantic_layer.prompt_context(),
        }
        intent, run = await self._call(
            "talk-to-data-router",
            router_payload,
            AnalyticsIntent,
            correlation,
            conversation_id,
            turn_id,
        )
        runs.append(run)
        if intent.status == IntentStatus.NEEDS_CLARIFICATION:
            await _emit_progress(
                emit,
                workflow_started,
                step="clarification",
                label="Waiting for clarification",
                description="A material ambiguity must be resolved before a safe query can be planned.",
                index=2,
            )
            if len(clarifications) >= self._max_clarifications:
                return self._blocked(
                    correlation,
                    runs,
                    "The request remains ambiguous after the clarification limit.",
                    intent,
                )
            return AdvancedTurnResult(
                status=AdvancedTurnStatus.NEEDS_CLARIFICATION,
                message=intent.clarification_question or "Please clarify the request.",
                intent=intent,
                run_ids=tuple(runs),
                correlation_id=correlation,
            )
        if intent.status == IntentStatus.UNSUPPORTED:
            return self._blocked(correlation, runs, intent.interpreted_request, intent)
        intent_validation_error = self._intent_validation_error(intent)
        for repair in range(self._max_intent_repairs):
            if intent_validation_error is None:
                break
            await _emit(
                emit,
                "agent.status.changed",
                {
                    "status": "repairing_analytics_intent",
                    "attempt": repair + 1,
                    "reason": intent_validation_error,
                },
            )
            intent, run = await self._call(
                "talk-to-data-router",
                {
                    **router_payload,
                    "invalid_intent": intent.model_dump(mode="json"),
                    "validation_error": intent_validation_error,
                    "repair_instruction": (
                        "Return a corrected intent using a compatible metric and dimension "
                        "set from one semantic model, unless a documented join permits the "
                        "combination. For a constant-volume average-fare scenario use the "
                        "ticket-sales metrics, not ancillary revenue."
                    ),
                },
                AnalyticsIntent,
                correlation,
                conversation_id,
                turn_id,
                runs[-1],
            )
            runs.append(run)
            intent_validation_error = self._intent_validation_error(intent)
        if intent_validation_error is not None:
            raise WorkflowError(
                "router could not produce a semantically compatible intent after "
                f"{self._max_intent_repairs} repair attempt(s): {intent_validation_error}",
                run_ids=tuple(runs),
            )
        intent = self._canonical_intent(intent)

        await _emit_progress(
            emit,
            workflow_started,
            step="plan",
            label="Planning the analysis",
            description="Checking data sufficiency and selecting bounded queries and analytical methods.",
            index=2,
        )
        await _emit(emit, "agent.status.changed", {"status": "planning_analysis"})
        plan_payload: dict[str, Any] = {
            "intent": intent.model_dump(mode="json"),
            "semantic_layer": self._semantic_layer.prompt_context(),
            "allowed_metric_names": self._semantic_layer.metric_names(),
            "allowed_dimension_names": self._semantic_layer.dimension_names(),
            "available_tools": self._tools.names(),
            "analytics_tool_parameter_schemas": self._tools.specifications(),
        }
        plan, run = await self._call(
            "talk-to-data-planner",
            plan_payload,
            AnalyticsPlan,
            correlation,
            conversation_id,
            turn_id,
            runs[-1],
        )
        runs.append(run)
        validation_error = self._plan_validation_error(plan, intent)
        for repair in range(self._max_plan_repairs):
            if validation_error is None:
                break
            await _emit(
                emit,
                "agent.status.changed",
                {
                    "status": "repairing_analysis_plan",
                    "attempt": repair + 1,
                    "reason": validation_error,
                },
            )
            plan, run = await self._call(
                "talk-to-data-planner",
                {
                    **plan_payload,
                    "invalid_plan": plan.model_dump(mode="json"),
                    "validation_error": validation_error,
                    "repair_instruction": (
                        "Return a corrected plan. metrics and dimensions must use only the exact "
                        "allowed names; aliases such as period belong only in expected_columns."
                    ),
                },
                AnalyticsPlan,
                correlation,
                conversation_id,
                turn_id,
                runs[-1],
            )
            runs.append(run)
            validation_error = self._plan_validation_error(plan, intent)
        if validation_error is not None:
            raise WorkflowError(
                "planner could not produce a semantically valid plan after "
                f"{self._max_plan_repairs} repair attempt(s): {validation_error}",
                run_ids=tuple(runs),
            )
        plan = self._canonical_plan(plan)
        if not plan.executable:
            return AdvancedTurnResult(
                status=AdvancedTurnStatus.BLOCKED,
                message=" ".join(plan.blockers),
                intent=intent,
                plan=plan,
                run_ids=tuple(runs),
                correlation_id=correlation,
            )
        await _emit_progress(
            emit,
            workflow_started,
            step="generate_sql",
            label="Generating safe SQL",
            description="Producing read-only PostgreSQL from certified semantic references and source rules.",
            index=3,
        )
        await _emit(emit, "agent.status.changed", {"status": "generating_queries"})
        generated_pairs = await asyncio.gather(
            *(
                self._generate_query(
                    task, intent, plan, correlation, conversation_id, turn_id, runs[-1]
                )
                for task in plan.queries
            )
        )
        generated = tuple(item[0] for item in generated_pairs)
        runs.extend(run_id for item in generated_pairs for run_id in item[1])
        if self._executor is None:
            await _emit_progress(
                emit,
                workflow_started,
                step="complete",
                label="SQL ready",
                description="The governed SQL is ready; query execution is disabled for this deployment.",
                index=4,
                status="completed",
            )
            return AdvancedTurnResult(
                status=AdvancedTurnStatus.COMPLETED,
                message="Generated governed SQL. Query execution is not configured.",
                intent=intent,
                plan=plan,
                queries=generated,
                run_ids=tuple(runs),
                correlation_id=correlation,
            )

        await _emit_progress(
            emit,
            workflow_started,
            step="execute",
            label="Running the queries",
            description="Executing validated SQL through the bounded read-only PostgreSQL connection.",
            index=4,
        )
        await _emit(emit, "query.execution.started", {"query_count": len(generated)})
        results = tuple(
            await asyncio.gather(
                *(self._execute_query(item, conversation_id, correlation) for item in generated)
            )
        )
        self._validate_result_columns(plan, generated, results)
        rows = self._analysis_rows(plan, generated, results)
        await _emit_progress(
            emit,
            workflow_started,
            step="analyze",
            label="Analyzing the results",
            description="Applying the selected deterministic analytical method to the returned rows.",
            index=5,
        )
        with self._tracer.start_as_current_span(
            "analytics.tool.call",
            attributes={
                "span.type": "tool",
                "tool.name": plan.analysis_tool,
                "tool.side_effect": "read_only",
                "agent.id": "talk-to-data",
                "agent.name": "Talk to Data",
                "analytics.kind": plan.analysis_kind.value,
                "analytics.input_row_count": len(rows),
            },
        ) as span:
            method_parameters = {item.name: item.value for item in plan.tool_parameters}
            if self._method_registry is None:
                analysis = self._tools.execute(plan.analysis_tool, rows, method_parameters)
            else:
                input_task_id = method_parameters.get("input_task_id")
                method_inputs = tuple(
                    AnalyticalResult(
                        node_id=query.task_id,
                        value=list(result.rows),
                        row_count=len(result.rows),
                        provenance=ResultProvenance(
                            source_ids=query.generation.source_ids,
                            source_columns=result.columns,
                            semantic_layer_digest=self._semantic_layer.digest,
                            metric_versions={
                                metric: self._semantic_layer.definition.version
                                for metric in next(
                                    item for item in plan.queries if item.id == query.task_id
                                ).metrics
                            },
                            query_fingerprint=getattr(result, "query_fingerprint", None),
                        ),
                    )
                    for query, result in zip(generated, results, strict=True)
                    if input_task_id is None or query.task_id == input_task_id
                )
                analysis = await self._method_registry.execute(
                    MethodExecutionRequest(
                        method=plan.analysis_tool,
                        version="1.0.0",
                        inputs=method_inputs,
                        parameters=method_parameters,
                    ),
                    GraphExecutionContext(
                        tenant_id=self._tenant_id,
                        user_id=self._user_id,
                        session_id=conversation_id,
                        run_id=correlation_id,
                    ),
                )
            span.set_attribute("analytics.status", analysis.status.value)
            if analysis.method:
                span.set_attribute("analytics.method", analysis.method)
            if analysis.model_version:
                span.set_attribute("analytics.model.version", analysis.model_version)
            if analysis.confidence is not None:
                span.set_attribute("analytics.confidence", analysis.confidence)
        await _emit(emit, "analysis.completed", analysis.model_dump(mode="json"))

        if analysis.status != AnalysisStatus.COMPLETED:
            await _emit_progress(
                emit,
                workflow_started,
                step="complete",
                label="Analysis bounded safely",
                description=(
                    "The deterministic method stopped because its evidence contract "
                    "was not satisfied."
                ),
                index=6,
                status="completed",
            )
            return AdvancedTurnResult(
                status=AdvancedTurnStatus.COMPLETED,
                message=analysis.summary,
                intent=intent,
                plan=plan,
                queries=generated,
                query_results=results,
                analysis=analysis,
                run_ids=tuple(runs),
                correlation_id=correlation,
            )

        if analysis.kind == AnalysisKind.STATIC_SCENARIO:
            await _emit_progress(
                emit,
                workflow_started,
                step="complete",
                label="Deterministic scenario ready",
                description=(
                    "The governed arithmetic result is ready without model-generated narration."
                ),
                index=6,
                status="completed",
            )
            return AdvancedTurnResult(
                status=AdvancedTurnStatus.COMPLETED,
                message=analysis.summary,
                intent=intent,
                plan=plan,
                queries=generated,
                query_results=results,
                analysis=analysis,
                run_ids=tuple(runs),
                correlation_id=correlation,
            )

        await _emit_progress(
            emit,
            workflow_started,
            step="compose",
            label="Preparing the response",
            description="Converting analytical outputs into a concise business explanation with limitations.",
            index=6,
        )
        report, run = await self._call(
            "talk-to-data-insight",
            {
                "intent": intent.model_dump(mode="json"),
                "plan": plan.model_dump(mode="json"),
                "analysis": analysis.model_dump(mode="json"),
                "result_sample": list(rows[:25]),
            },
            InsightReport,
            correlation,
            conversation_id,
            turn_id,
            runs[-1],
        )
        runs.append(run)
        await _emit_progress(
            emit,
            workflow_started,
            step="verify",
            label="Verifying the answer",
            description="Checking claims against evidence, method limits, and reported confidence.",
            index=7,
        )
        review, run = await self._call(
            "talk-to-data-verifier",
            {
                "intent": intent.model_dump(mode="json"),
                "analysis": analysis.model_dump(mode="json"),
                "report": report.model_dump(mode="json"),
            },
            AnalysisReview,
            correlation,
            conversation_id,
            turn_id,
            runs[-1],
        )
        runs.append(run)
        await _emit(emit, "analysis.verification.completed", review.model_dump(mode="json"))
        await _emit_progress(
            emit,
            workflow_started,
            step="complete",
            label="Response ready",
            description="The bounded analytical workflow completed successfully.",
            index=8,
            status="completed",
        )
        return AdvancedTurnResult(
            status=AdvancedTurnStatus.COMPLETED,
            message=review.corrected_summary or report.summary,
            intent=intent,
            plan=plan,
            queries=generated,
            query_results=results,
            analysis=analysis,
            report=report,
            review=review,
            run_ids=tuple(runs),
            correlation_id=correlation,
        )

    async def _execute_query(
        self,
        generated: GeneratedQuery,
        conversation_id: str | None,
        correlation_id: str,
    ) -> Any:
        assert self._executor is not None
        with self._tracer.start_as_current_span(
            "analytics.query.execute",
            attributes={
                "span.type": "tool",
                "tool.name": "postgresql.readonly_query",
                "tool.side_effect": "read_only",
                "db.system": "postgresql",
                "agent.id": "talk-to-data",
                "agent.name": "Talk to Data",
                "analytics.query.task_id": generated.task_id,
                "analytics.query.parameter_count": len(generated.generation.parameters),
            },
        ) as span:
            cache_context = CacheContext(
                tenant_id=self._tenant_id,
                user_id=self._user_id,
                session_id=conversation_id,
                run_id=correlation_id,
            )
            if getattr(self._executor, "supports_cache_context", False):
                result = await self._executor.execute(
                    generated.generation.sql,
                    generated.generation.parameters,
                    cache_context=cache_context,
                )
            else:
                result = await self._executor.execute(
                    generated.generation.sql, generated.generation.parameters
                )
            span.set_attribute("analytics.query.row_count", len(result.rows))
            span.set_attribute("analytics.query.truncated", result.truncated)
            span.set_attribute("analytics.query.duration_ms", result.execution_ms)
            span.set_attribute("cache.hit", bool(getattr(result, "cache_hit", False)))
            span.set_attribute("cache.type", "query_result")
            return result

    def _plan_validation_error(self, plan: AnalyticsPlan, intent: AnalyticsIntent) -> str | None:
        if not plan.executable:
            return None
        if plan.analysis_tool not in self._tools.names():
            return f"unknown analytics tool {plan.analysis_tool!r}"
        if plan.analysis_tool == "static_fare_scenario" and len(plan.queries) != 1:
            return (
                "static_fare_scenario requires exactly one baseline query; return the "
                "requested aggregate or breakdown grain in that query"
            )
        for task in plan.queries:
            try:
                self._semantic_layer.validate_selection(
                    task.metrics,
                    task.dimensions,
                    minimum_certification=self._minimum_semantic_certification,
                    maximum_sensitivity=self._maximum_semantic_sensitivity,
                    authorization_tags=self._semantic_authorization_tags,
                )
            except (ConfigurationError, NotFoundError) as exc:
                return f"task {task.id!r} selected an undefined semantic reference: {exc}"
        if (
            "flight" in intent.interpreted_request.lower()
            and "current_inventory_remaining" in intent.metrics
        ):
            for task in plan.queries:
                if "current_inventory_remaining" not in task.metrics:
                    continue
                if "current_inventory_flight" not in task.dimensions:
                    return (
                        "a flight-level remaining-inventory ranking must group by "
                        "current_inventory_flight"
                    )
                if "current_inventory_rbd" in task.dimensions:
                    return (
                        "a flight-level remaining-inventory ranking must aggregate RBD rows; "
                        "current_inventory_rbd is not an allowed output grain"
                    )
        parameters = {item.name: item.value for item in plan.tool_parameters}
        parameter_error = self._tools.parameter_error(plan.analysis_tool, parameters)
        if parameter_error is not None:
            return (
                f"analytics tool {plan.analysis_tool!r} has invalid parameters: {parameter_error}"
            )
        parameters = self._tools.normalized_parameters(plan.analysis_tool, parameters)
        input_task_id = parameters.get("input_task_id")
        if len(plan.queries) > 1 and not input_task_id:
            return (
                "plans with multiple queries must set tool parameter input_task_id "
                "to identify the sole result consumed by the analytical tool"
            )
        selected_tasks = (
            [task for task in plan.queries if task.id == input_task_id]
            if input_task_id
            else list(plan.queries)
        )
        if input_task_id and not selected_tasks:
            return f"analysis input_task_id {input_task_id!r} does not match a query task"
        if plan.analysis_tool == "static_fare_scenario":
            value_column = parameters.get("value_column")
            if value_column != "ticket_revenue":
                return (
                    "static_fare_scenario value_column must be 'ticket_revenue'; "
                    "average_fare is a per-passenger value and cannot produce portfolio impact"
                )
            if any("ticket_revenue" not in task.metrics for task in selected_tasks):
                return (
                    "static_fare_scenario requires the governed ticket_revenue metric "
                    "in its analysis input query"
                )
        expected_columns = {column for task in selected_tasks for column in task.expected_columns}
        for name, value in parameters.items():
            if name.endswith("_column") and str(value) not in expected_columns:
                return (
                    f"tool parameter {name!r} selects column {value!r}, but analysis "
                    f"task output columns are {sorted(expected_columns)}"
                )
        return None

    @staticmethod
    def _analysis_rows(
        plan: AnalyticsPlan,
        generated: Sequence[GeneratedQuery],
        results: Sequence[Any],
    ) -> tuple[dict[str, Any], ...]:
        parameters = {item.name: item.value for item in plan.tool_parameters}
        input_task_id = parameters.get("input_task_id")
        selected = [
            result
            for query, result in zip(generated, results, strict=True)
            if input_task_id is None or query.task_id == input_task_id
        ]
        return tuple(row for result in selected for row in result.rows)

    @staticmethod
    def _validate_result_columns(
        plan: AnalyticsPlan,
        generated: Sequence[GeneratedQuery],
        results: Sequence[Any],
    ) -> None:
        parameters = {item.name: item.value for item in plan.tool_parameters}
        input_task_id = parameters.get("input_task_id")
        for query, result in zip(generated, results, strict=True):
            if input_task_id is not None and query.task_id != input_task_id:
                continue
            task = next(item for item in plan.queries if item.id == query.task_id)
            missing = set(task.expected_columns) - set(result.columns)
            if missing:
                raise WorkflowError(
                    f"query task {task.id!r} returned columns {sorted(result.columns)} "
                    f"but declared output columns {sorted(missing)} are missing"
                )
            if result.truncated:
                raise WorkflowError(
                    f"query task {task.id!r} exceeded the row limit before analysis; "
                    "use a coarser aggregate grain so the deterministic result is complete"
                )

    def _intent_validation_error(self, intent: AnalyticsIntent) -> str | None:
        try:
            self._semantic_layer.validate_selection(
                intent.metrics,
                intent.dimensions,
                minimum_certification=self._minimum_semantic_certification,
                maximum_sensitivity=self._maximum_semantic_sensitivity,
                authorization_tags=self._semantic_authorization_tags,
            )
        except (ConfigurationError, NotFoundError) as exc:
            return str(exc)
        return None

    def _canonical_intent(self, intent: AnalyticsIntent) -> AnalyticsIntent:
        """Normalize accepted model-qualified references for downstream contracts."""
        return intent.model_copy(
            update={
                "metrics": tuple(
                    self._semantic_layer.metric(name)[1].name for name in intent.metrics
                ),
                "dimensions": tuple(
                    self._semantic_layer.dimension(name)[1].name for name in intent.dimensions
                ),
            }
        )

    def _canonical_plan(self, plan: AnalyticsPlan) -> AnalyticsPlan:
        return plan.model_copy(
            update={
                "queries": tuple(
                    task.model_copy(
                        update={
                            "metrics": tuple(
                                self._semantic_layer.metric(name)[1].name for name in task.metrics
                            ),
                            "dimensions": tuple(
                                self._semantic_layer.dimension(name)[1].name
                                for name in task.dimensions
                            ),
                        }
                    )
                    for task in plan.queries
                )
            }
        )

    async def _generate_query(
        self,
        task: QueryTask,
        intent: AnalyticsIntent,
        plan: AnalyticsPlan,
        correlation: str,
        conversation_id: str | None,
        turn_id: str | None,
        parent_run_id: str,
    ) -> tuple[GeneratedQuery, tuple[str, ...]]:
        payload: dict[str, Any] = {
            "task": task.model_dump(mode="json"),
            "intent": intent.model_dump(mode="json"),
            "analysis_tool": plan.analysis_tool,
            "tool_parameters": [item.model_dump(mode="json") for item in plan.tool_parameters],
            "plan_assumptions": plan.assumptions,
            "semantic_layer": self._semantic_layer.prompt_context(),
            "semantic_query_policy": self._semantic_layer.query_policy_context(
                task.metrics, task.dimensions
            ),
            "allowed_tables": sorted(self._catalog.tables),
        }
        query_runs: list[str] = []
        validation_error: str | None = None
        generation: SQLGeneration | None = None
        sql = ""
        for repair in range(self._max_query_repairs + 1):
            request_payload = payload
            if generation is not None and validation_error is not None:
                request_payload = {
                    **payload,
                    "invalid_generation": generation.model_dump(mode="json"),
                    "validation_error": validation_error,
                    "repair_instruction": (
                        "Return corrected SQL whose projected aliases exactly include every "
                        "task.expected_columns value. The analytical tool, not SQL, owns "
                        "scenario arithmetic unless the task explicitly requests otherwise. "
                        "Apply validation_error literally. Ranking SQL must include an explicit "
                        "ORDER BY in the requested direction and a numeric LIMIT from 1 through "
                        "100; use LIMIT 10 when the user did not request a result count."
                    ),
                }
            generation, run = await self._call(
                "talk-to-data-sql",
                request_payload,
                SQLGeneration,
                correlation,
                conversation_id,
                turn_id,
                query_runs[-1] if query_runs else parent_run_id,
            )
            query_runs.append(run)
            try:
                sql = validate_read_only_sql(generation.sql, self._catalog.tables)
                sql = _normalize_ranking_limit(
                    sql,
                    intent.interpreted_request,
                    task.purpose,
                )
                validate_parameters(sql, generation.parameters)
                self._semantic_layer.validate_generated_sql(task.metrics, task.dimensions, sql)
                _validate_projected_aliases(sql, task.expected_columns)
                _validate_ranking_sql(sql, intent.interpreted_request, task.purpose)
                validation_error = None
                break
            except (ConfigurationError, WorkflowError) as exc:
                validation_error = str(exc)
                if repair >= self._max_query_repairs:
                    break
        if generation is None or validation_error is not None:
            raise WorkflowError(
                "SQL agent could not satisfy the declared output contract after "
                f"{self._max_query_repairs} repair attempt(s): {validation_error}"
            )
        return (
            GeneratedQuery(
                task_id=task.id,
                purpose=task.purpose,
                generation=generation.model_copy(update={"sql": sql}),
            ),
            tuple(query_runs),
        )

    async def _call(
        self,
        agent: str,
        payload: dict[str, Any],
        model: type[Structured],
        correlation: str,
        conversation_id: str | None,
        turn_id: str | None,
        parent_run_id: str | None = None,
    ) -> tuple[Structured, str]:
        result = await self._client.run(
            RunRequest(
                agent=agent,
                input=json.dumps(payload, indent=2, default=str),
                tenant_id=self._tenant_id,
                user_id=self._user_id,
                correlation_id=correlation,
                session_id=conversation_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                parent_run_id=parent_run_id,
                workflow_run_id=correlation,
                metadata={
                    "workflow": "analytics-talk-to-data",
                    "agent_role": agent,
                    "retrieval_query": self._retrieval_query(payload),
                    "semantic_layer": self._semantic_layer.definition.name,
                    "semantic_layer_version": self._semantic_layer.definition.version,
                    "semantic_layer_digest": self._semantic_layer.digest,
                },
                overrides=RequestOverrides(response_schema=model.model_json_schema()),
            )
        )
        run_id = str(getattr(result, "run_id", ""))
        if getattr(result, "error", None):
            raise WorkflowError(
                f"{agent} failed: {result.error}", run_ids=(run_id,) if run_id else ()
            )
        text = str(getattr(result, "output", "") or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
        try:
            return model.model_validate_json(text), run_id
        except ValueError as exc:
            raise WorkflowError(
                f"{agent} returned invalid structured output: {exc}",
                run_ids=(run_id,) if run_id else (),
            ) from exc

    @staticmethod
    def _retrieval_query(payload: dict[str, Any]) -> str:
        for key in ("question", "original_request", "interpreted_request"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        intent = payload.get("intent")
        if isinstance(intent, dict):
            interpreted = intent.get("interpreted_request")
            if isinstance(interpreted, str) and interpreted.strip():
                task = payload.get("task")
                purpose = task.get("purpose") if isinstance(task, dict) else None
                if isinstance(purpose, str) and purpose.strip():
                    return f"{interpreted.strip()}\n{purpose.strip()}"
                return interpreted.strip()
        task = payload.get("task")
        if isinstance(task, dict) and isinstance(task.get("purpose"), str):
            return str(task["purpose"]).strip()
        return "analytics request"

    @staticmethod
    def _blocked(
        correlation: str,
        runs: Sequence[str],
        message: str,
        intent: AnalyticsIntent,
    ) -> AdvancedTurnResult:
        return AdvancedTurnResult(
            status=AdvancedTurnStatus.BLOCKED,
            message=message,
            intent=intent,
            run_ids=tuple(runs),
            correlation_id=correlation,
        )


async def _emit(emit: Any | None, event: str, data: dict[str, Any]) -> None:
    if emit is not None:
        await emit(event, data)


def _validate_projected_aliases(sql: str, expected_columns: Sequence[str]) -> None:
    aliases = {
        match.group(1).lower()
        for match in re.finditer(r'\bas\s+"?([a-z_][a-z0-9_]*)"?', sql, flags=re.IGNORECASE)
    }
    missing = {column.lower() for column in expected_columns} - aliases
    if missing:
        raise WorkflowError(
            "generated SQL must explicitly project the declared output aliases; "
            f"missing {sorted(missing)}"
        )


def _validate_ranking_sql(sql: str, request: str, purpose: str) -> None:
    ranking_language = (request + " " + purpose).lower()
    if not _RANKING_TERMS.search(ranking_language):
        return
    normalized = sql.lower()
    if not re.search(r"\border\s+by\b", normalized):
        raise WorkflowError("ranking SQL must include an explicit ORDER BY clause")
    limit_matches = tuple(_NUMERIC_LIMIT.finditer(normalized))
    limit_match = limit_matches[-1] if limit_matches else None
    if limit_match is None:
        raise WorkflowError("ranking SQL must include an explicit numeric LIMIT")
    limit = int(limit_match.group(1))
    if limit < 1 or limit > _MAX_RANKING_ROWS:
        raise WorkflowError(f"ranking SQL LIMIT must be between 1 and {_MAX_RANKING_ROWS} rows")


def _normalize_ranking_limit(sql: str, request: str, purpose: str) -> str:
    """Apply a deterministic display bound to otherwise valid ranking SQL.

    A model-selected executor ceiling (for example LIMIT 500) is not user intent. For an
    unqualified ranking request we use a concise default, avoiding an unnecessary model repair.
    An explicit count in the user's request remains authoritative and is left for the contract
    validator to reject if it exceeds the safety ceiling.
    """
    ranking_language = f"{request} {purpose}"
    if not _RANKING_TERMS.search(ranking_language):
        return sql
    if not re.search(r"\border\s+by\b", sql, flags=re.IGNORECASE):
        return sql

    requested_limit = _requested_ranking_limit(request)
    desired_limit = requested_limit if requested_limit is not None else _DEFAULT_RANKING_ROWS
    limit_matches = tuple(_NUMERIC_LIMIT.finditer(sql))
    if limit_matches:
        match = limit_matches[-1]
        generated_limit = int(match.group(1))
        if requested_limit is None and 1 <= generated_limit <= _MAX_RANKING_ROWS:
            return sql
        if generated_limit == desired_limit:
            return sql
        start, end = match.span(1)
        return f"{sql[:start]}{desired_limit}{sql[end:]}"

    statement = sql.rstrip()
    terminator = ";" if statement.endswith(";") else ""
    if terminator:
        statement = statement[:-1].rstrip()
    return f"{statement} LIMIT {desired_limit}{terminator}"


def _requested_ranking_limit(request: str) -> int | None:
    patterns = (
        r"\b(?:top|bottom|lowest|highest)\s+(\d+)\b",
        r"\b(\d+)\s+(?:lowest|highest|top|bottom)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, request, flags=re.IGNORECASE)
        if match is not None:
            return int(match.group(1))
    return None


async def _emit_progress(
    emit: Any | None,
    started: float,
    *,
    step: str,
    label: str,
    description: str,
    index: int,
    status: str = "started",
) -> None:
    await _emit(
        emit,
        "workflow.progress",
        {
            "step": step,
            "label": label,
            "description": description,
            "index": index,
            "status": status,
            "elapsed_ms": round((perf_counter() - started) * 1000, 1),
            "safe_summary": True,
        },
    )
