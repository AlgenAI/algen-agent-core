from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from pathlib import Path
from time import perf_counter
from typing import Any

import uvicorn
from fastapi.responses import FileResponse

from examples.talk_to_data_multi_agent.app import (
    ARCHITECTURE_V1_DIR,
    ARCHITECTURE_V2_DIR,
    CONFIG_PATH,
    DASHBOARD_UI_PATH,
    DEFAULT_SCHEMA,
    SEMANTIC_LAYER_PATH,
)
from examples.talk_to_data_multi_agent.application.analytics_tools import (
    AirlineAnalyticsToolRegistry,
    default_airline_analytics_tools,
)
from examples.talk_to_data_multi_agent.application.analytics_workflow import (
    TRACE_EVALUATION_QUESTION,
    AdvancedTurnStatus,
    AnalyticsWorkflow,
)
from examples.talk_to_data_multi_agent.application.catalog import (
    SchemaCatalog,
    load_schema_catalog,
)
from examples.talk_to_data_multi_agent.application.query_executor import ReadOnlyPostgresExecutor
from examples.talk_to_data_multi_agent.application.visualizations import (
    AirlineVisualizationPlanner,
)
from examples.talk_to_data_multi_agent.application.workflow import WorkflowError
from traccia_runtime.api.app import create_app
from traccia_runtime.config.settings import load_settings
from traccia_runtime.conversations import (
    CodeBlock,
    Conversation,
    ConversationMessage,
    ConversationPresentation,
    ConversationTurnResult,
    DetailsBlock,
    NoticeBlock,
    TableBlock,
)
from traccia_runtime.orchestration.container import Container, build_container
from traccia_runtime.runtime.client import TracciaRuntimeClient
from traccia_runtime.semantics import AnalysisStatus, SemanticLayer
from traccia_runtime.types.contracts import Role, TextBlock

SuggestionProvider = Callable[[str, str, int], Awaitable[Sequence[str]]]

_BUSINESS_PROGRESS: dict[str, tuple[str, str]] = {
    "understand": (
        "Understanding your question",
        "Working out the business measures and comparison you need.",
    ),
    "clarification": (
        "A quick clarification",
        "One detail is needed to make sure the answer matches your intent.",
    ),
    "plan": (
        "Preparing the analysis",
        "Choosing the most relevant information and a reliable way to answer.",
    ),
    "generate_sql": (
        "Gathering the right data",
        "Preparing a safe, focused request for the information needed.",
    ),
    "execute": (
        "Checking the numbers",
        "Retrieving the relevant figures for your question.",
    ),
    "analyze": (
        "Analyzing the results",
        "Comparing the figures and identifying the most useful findings.",
    ),
    "compose": (
        "Preparing your answer",
        "Turning the findings into a concise business explanation.",
    ),
    "verify": (
        "Checking the answer",
        "Making sure the conclusions match the available evidence.",
    ),
    "complete": ("Answer ready", "The analysis has finished."),
}


def _business_progress(developer: dict[str, Any]) -> dict[str, Any]:
    result = dict(developer)
    copy = _BUSINESS_PROGRESS.get(str(developer.get("step")))
    if copy is not None:
        result["label"], result["description"] = copy
    return result


class TalkToDataConversationHandler:
    name = "talk-to-data"

    def __init__(
        self,
        container: Container,
        catalog: SchemaCatalog,
        semantic_layer: SemanticLayer,
        analytics_tools: AirlineAnalyticsToolRegistry,
        executor: ReadOnlyPostgresExecutor | None = None,
        max_clarifications: int = 2,
        suggestion_provider: SuggestionProvider | None = None,
        show_response_time: bool = False,
        show_progress: bool = False,
        show_charts: bool = True,
        presentation: ConversationPresentation | None = None,
    ) -> None:
        self._container = container
        self._catalog = catalog
        self._semantic_layer = semantic_layer
        self._analytics_tools = analytics_tools
        self._visualizations = AirlineVisualizationPlanner()
        self._executor = executor
        self._max_clarifications = max_clarifications
        self._suggestion_provider = suggestion_provider
        self._show_response_time = show_response_time
        self._show_progress = show_progress
        self._show_charts = show_charts
        conversation_service = getattr(container, "conversations", None)
        self._presentation = (
            presentation
            or getattr(conversation_service, "presentation", None)
            or ConversationPresentation()
        )

    async def suggestions(self, tenant_id: str, user_id: str, limit: int) -> tuple[str, ...]:
        if self._suggestion_provider is not None:
            values = await self._suggestion_provider(tenant_id, user_id, limit)
            return tuple(values[:limit])
        return (
            TRACE_EVALUATION_QUESTION,
            "Which routes show sustained high load factor and yield by weekday?",
            "Do forward bookings show an unexpected demand increase by flight?",
            "What is the static revenue impact of increasing average fare by 5%?",
            "Which observable factors are correlated with weaker revenue?",
        )[:limit]

    async def handle(
        self,
        conversation: Conversation,
        user_message: ConversationMessage,
        history: Sequence[ConversationMessage],
        emit: Any,
    ) -> ConversationTurnResult:
        turn_started = perf_counter()
        execution_steps: list[dict[str, Any]] = []

        async def observed_emit(event_type: str, data: dict[str, Any] | None = None) -> None:
            payload = data or {}
            if event_type != "workflow.progress":
                await emit(event_type, payload)
                return
            if not self._show_progress:
                return
            safe_step = {
                key: payload[key]
                for key in (
                    "step",
                    "label",
                    "description",
                    "index",
                    "status",
                    "elapsed_ms",
                )
                if key in payload
            }
            safe_step = self._presentation.progress(
                _business_progress(safe_step),
                safe_step,
            )
            for previous in execution_steps:
                if previous.get("status") == "started":
                    previous["status"] = "completed"
            existing = next(
                (item for item in execution_steps if item.get("step") == safe_step.get("step")),
                None,
            )
            if existing is None:
                execution_steps.append(safe_step)
            else:
                existing.update(safe_step)
            await emit(event_type, {**safe_step, "safe_summary": True})

        def with_execution_metadata(
            metadata: dict[str, Any], *, terminal_status: str = "completed"
        ) -> dict[str, Any]:
            result = dict(metadata)
            if execution_steps and execution_steps[-1].get("status") == "started":
                execution_steps[-1]["status"] = terminal_status
            if self._show_response_time:
                result["response_time_ms"] = round((perf_counter() - turn_started) * 1000, 1)
            if self._show_progress:
                result["execution_steps"] = [dict(item) for item in execution_steps]
            return result

        state = self._pending_state(history, user_message.id)
        if state:
            question = str(state["original_question"])
            clarifications = [tuple(item) for item in state.get("clarifications", [])]
            clarifications.append((str(state["clarification_question"]), user_message.text_content))
            correlation_id = str(state["correlation_id"])
        else:
            question = self._contextual_question(user_message, history)
            clarifications = []
            correlation_id = None

        workflow = AnalyticsWorkflow(
            TracciaRuntimeClient(self._container.runtime),
            self._catalog,
            self._semantic_layer,
            self._analytics_tools,
            tenant_id=conversation.tenant_id,
            user_id=conversation.user_id,
            executor=self._executor,
            max_clarifications=self._max_clarifications,
            method_registry=getattr(self._container, "analytical_methods", None),
            enable_trace_evaluation=True,
        )
        try:
            turn = await workflow.run_turn(
                question,
                clarifications,
                correlation_id=correlation_id,
                conversation_id=conversation.id,
                turn_id=user_message.id,
                emit=observed_emit,
            )
        except WorkflowError as exc:
            display_error = self._presentation.error_message(
                exc,
                business_message=(
                    "I couldn't complete this analysis because the data request could not be "
                    "validated safely. Please try rephrasing or narrowing the question."
                ),
            )
            await observed_emit(
                "workflow.blocked",
                {"reason": display_error, "retryable": exc.retryable},
            )
            return ConversationTurnResult(
                content=(
                    NoticeBlock(
                        level="error",
                        text=display_error,
                    ),
                ),
                run_ids=exc.run_ids,
                outcome="failed",
                metadata=with_execution_metadata(
                    {
                        "kind": "workflow_error",
                        "error_type": type(exc).__name__,
                        "retryable": exc.retryable,
                    },
                    terminal_status="failed",
                ),
                suggested_followups=await self.suggestions(
                    conversation.tenant_id, conversation.user_id, 3
                ),
            )
        if turn.status == AdvancedTurnStatus.NEEDS_CLARIFICATION:
            await observed_emit(
                "clarification.required",
                {"question": turn.message},
            )
            return ConversationTurnResult(
                content=(TextBlock(text=turn.message),),
                run_ids=turn.run_ids,
                outcome="awaiting_clarification",
                metadata=with_execution_metadata(
                    {
                        "kind": "clarification",
                        "state": {
                            "original_question": question,
                            "clarifications": clarifications,
                            "clarification_question": turn.message,
                            "correlation_id": turn.correlation_id,
                        },
                    },
                    terminal_status="waiting",
                ),
            )
        if turn.status == AdvancedTurnStatus.BLOCKED:
            display_message = self._presentation.error_message(
                RuntimeError(turn.message),
                business_message=(
                    "I couldn't complete this analysis with the available governed data. "
                    "Try narrowing the question or ask your data administrator to review data "
                    "availability."
                ),
            )
            return ConversationTurnResult(
                content=(NoticeBlock(level="warning", text=display_message),),
                run_ids=turn.run_ids,
                outcome="blocked",
                metadata=with_execution_metadata(
                    {
                        "kind": "analysis_blocked",
                        "analysis_kind": (turn.intent.analysis_kind.value if turn.intent else None),
                        "required_data": turn.plan.required_data if turn.plan else (),
                    },
                    terminal_status="blocked",
                ),
                suggested_followups=await self.suggestions(
                    conversation.tenant_id, conversation.user_id, 3
                ),
            )

        blocks: list[Any] = [TextBlock(text=turn.message)]
        technical_blocks: list[CodeBlock | TableBlock] = []
        source_ids: list[str] = []
        for query in turn.queries:
            await observed_emit(
                "query.generated",
                {"task_id": query.task_id, "source_ids": query.generation.source_ids},
            )
            source_ids.extend(query.generation.source_ids)
            technical_blocks.append(
                CodeBlock(
                    language="sql",
                    code=query.generation.sql,
                    title=query.purpose,
                )
            )
        if turn.report and (turn.review is None or turn.review.approved):
            blocks[0] = TextBlock(text=turn.report.headline + "\n\n" + turn.message)
            for finding in turn.report.findings:
                blocks.append(NoticeBlock(level="info", text=finding))
            for recommendation in turn.report.recommendations:
                blocks.append(NoticeBlock(level="info", text="Next action: " + recommendation))
        planned_tasks = turn.plan.queries[: len(turn.query_results)] if turn.plan else ()
        for task, query_result in zip(planned_tasks, turn.query_results, strict=True):
            chart = (
                self._visualizations.plan(task, query_result, turn.analysis)
                if self._show_charts
                else None
            )
            if chart is not None:
                blocks.append(chart)
            technical_blocks.append(
                TableBlock(
                    columns=query_result.columns,
                    rows=query_result.rows,
                    truncated=query_result.truncated,
                )
            )
        if turn.analysis:
            for warning in turn.analysis.warnings:
                blocks.append(NoticeBlock(level="warning", text=warning))
            if (
                turn.analysis.status != AnalysisStatus.COMPLETED
                and turn.analysis.summary != turn.message
            ):
                blocks.append(NoticeBlock(level="warning", text=turn.analysis.summary))
        if self._executor is None:
            blocks.append(
                NoticeBlock(
                    level="info",
                    text="Query execution is disabled; configure TALK_TO_DATA_QUERY_DSN to run the analytical plan.",
                )
            )
        if self._presentation.show_technical_details and technical_blocks:
            blocks.append(
                DetailsBlock(
                    title="Technical details",
                    description="SQL and raw query results used to support this answer.",
                    expanded=self._presentation.technical_details_expanded,
                    content=tuple(technical_blocks),
                )
            )
        metadata: dict[str, Any] = {
            "kind": "analytical_result",
            "analysis_kind": turn.intent.analysis_kind.value if turn.intent else None,
            "semantic_layer": self._semantic_layer.definition.name,
            "semantic_layer_version": self._semantic_layer.definition.version,
            "semantic_layer_digest": self._semantic_layer.digest,
            "plan": turn.plan.model_dump(mode="json") if turn.plan else None,
            "analysis": turn.analysis.model_dump(mode="json") if turn.analysis else None,
            "review": turn.review.model_dump(mode="json") if turn.review else None,
            "correlation_id": turn.correlation_id,
        }
        return ConversationTurnResult(
            content=tuple(blocks),
            run_ids=turn.run_ids,
            citations=tuple({"source_id": item} for item in dict.fromkeys(source_ids)),
            suggested_followups=(
                "Break this down by month",
                "Show only the top 10",
                "Explain the assumptions used",
            ),
            metadata=with_execution_metadata(metadata),
        )

    @staticmethod
    def _pending_state(
        history: Sequence[ConversationMessage], current_message_id: str
    ) -> dict[str, Any] | None:
        for message in reversed(history):
            if message.id == current_message_id or message.role != Role.ASSISTANT:
                continue
            if message.metadata.get("kind") == "clarification":
                value = message.metadata.get("state")
                return dict(value) if isinstance(value, dict) else None
            if message.status.value == "completed":
                return None
        return None

    @staticmethod
    def _contextual_question(
        user_message: ConversationMessage, history: Sequence[ConversationMessage]
    ) -> str:
        earlier = [
            f"{item.role.value}: {item.text_content}"
            for item in history
            if item.id != user_message.id and item.text_content
        ][-6:]
        if not earlier:
            return user_message.text_content
        return (
            "Recent conversation (use it only to resolve follow-up references):\n"
            + "\n".join(earlier)
            + "\n\nCurrent request:\n"
            + user_message.text_content
        )


def build_dashboard_app(
    schema_path: Path = DEFAULT_SCHEMA,
) -> Any:
    settings = load_settings((CONFIG_PATH,))
    container = build_container(settings)
    catalog = load_schema_catalog(schema_path, "talk-to-data-example")
    semantic_layer = SemanticLayer.from_yaml(SEMANTIC_LAYER_PATH)
    container.semantics.register(semantic_layer)
    analytics_tools = default_airline_analytics_tools()
    analytics_tools.install(container.analytical_methods)
    query_dsn = os.getenv("TALK_TO_DATA_QUERY_DSN")
    executor = (
        ReadOnlyPostgresExecutor(
            query_dsn,
            catalog.tables,
            cache=container.cache,
            data_version=os.getenv("TALK_TO_DATA_DATA_VERSION", "live"),
            governance=container.query_governance,
            certified_sources=frozenset(
                model.table
                for model in semantic_layer.definition.models
                if model.certification in {"verified", "certified"}
            ),
            authorization_tags=("talk_to_data.read",),
        )
        if query_dsn
        else None
    )
    if executor is not None:
        container = replace(container, resources=(*container.resources, executor))
    container.conversations.handlers.register(
        TalkToDataConversationHandler(
            container,
            catalog,
            semantic_layer,
            analytics_tools,
            executor,
            show_response_time=settings.feature_flags.get("talk_to_data_show_response_time", False),
            show_progress=settings.feature_flags.get("talk_to_data_show_progress", False),
            show_charts=settings.feature_flags.get("talk_to_data_show_charts", True),
        )
    )
    app = create_app(settings, container)

    @app.get("/", include_in_schema=False)
    async def dashboard_ui() -> FileResponse:
        return FileResponse(
            DASHBOARD_UI_PATH,
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/architecture/agents", include_in_schema=False)
    async def agent_interaction_diagram() -> FileResponse:
        return FileResponse(
            ARCHITECTURE_V1_DIR / "agent_interaction_flow.html",
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/architecture/v2/high-level", include_in_schema=False)
    async def high_level_v2_architecture() -> FileResponse:
        return FileResponse(
            ARCHITECTURE_V2_DIR / "high_level.html",
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/architecture/v2/detailed", include_in_schema=False)
    async def detailed_v2_architecture() -> FileResponse:
        return FileResponse(
            ARCHITECTURE_V2_DIR / "detailed.html",
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/architecture/v2/detailed-new", include_in_schema=False)
    async def layered_detailed_v2_architecture() -> FileResponse:
        return FileResponse(
            ARCHITECTURE_V2_DIR / "detailed_new.html",
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/architecture/v2/animated", include_in_schema=False)
    async def animated_v2_architecture() -> FileResponse:
        return FileResponse(
            ARCHITECTURE_V2_DIR / "detailed_animated.html",
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    return app


def main() -> None:
    port = int(os.getenv("TALK_TO_DATA_PORT", "8090"))
    uvicorn.run(build_dashboard_app(), host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
