from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any, Protocol, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from examples.talk_to_data_multi_agent.application.catalog import SchemaCatalog
from traccia_runtime.exceptions.errors import TracciaRuntimeError
from traccia_runtime.types.contracts import RequestOverrides, RunRequest

_TABLE_REFERENCE = re.compile(
    r"\b(?:from|join)\s+(?!\()(?:(?:\"?[a-zA-Z_][\w$]*\"?)\.)?\"?([a-zA-Z_][\w$]*)\"?",
    re.IGNORECASE,
)
_CTE = re.compile(r"(?:\bwith|,)\s*\"?([a-zA-Z_][\w$]*)\"?\s+as\s*\(", re.IGNORECASE)
_FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|copy|call|do|execute|merge)\b",
    re.IGNORECASE,
)
_DANGEROUS_FUNCTION = re.compile(
    r"\b(pg_sleep|dblink|lo_import|lo_export|pg_read_file)\s*\(", re.IGNORECASE
)
_PLACEHOLDER = re.compile(r"\$(\d+)\b")


class IntentStatus(StrEnum):
    READY = "ready"
    NEEDS_CLARIFICATION = "needs_clarification"
    UNSUPPORTED = "unsupported"


class IntentAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: IntentStatus
    interpreted_request: str
    clarification_question: str | None
    assumptions: tuple[str, ...]
    relevant_tables: tuple[str, ...]
    evidence_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_question(self) -> IntentAssessment:
        if self.status == IntentStatus.NEEDS_CLARIFICATION and not self.clarification_question:
            raise ValueError("clarification_question is required when clarification is needed")
        if self.status != IntentStatus.NEEDS_CLARIFICATION and self.clarification_question:
            raise ValueError("clarification_question is only valid when clarification is needed")
        return self


class SQLParameter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    position: int = Field(ge=1)
    name: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    value: str | int | float | bool | None


class SQLGeneration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sql: str = Field(min_length=1)
    parameters: tuple[SQLParameter, ...]
    summary: str
    assumptions: tuple[str, ...]
    source_ids: tuple[str, ...]


class TalkToDataResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sql: str
    parameters: tuple[SQLParameter, ...]
    interpreted_request: str
    assumptions: tuple[str, ...]
    source_ids: tuple[str, ...]
    clarification_count: int
    correlation_id: str


class TalkToDataTurnStatus(StrEnum):
    NEEDS_CLARIFICATION = "needs_clarification"
    COMPLETED = "completed"
    UNSUPPORTED = "unsupported"


class TalkToDataTurnResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: TalkToDataTurnStatus
    clarification_question: str | None = None
    message: str | None = None
    result: TalkToDataResult | None = None
    run_ids: tuple[str, ...] = ()
    correlation_id: str

    @model_validator(mode="after")
    def validate_payload(self) -> TalkToDataTurnResult:
        if self.status == TalkToDataTurnStatus.NEEDS_CLARIFICATION:
            if not self.clarification_question or self.result is not None or self.message:
                raise ValueError("a clarification turn requires only clarification_question")
        elif self.status == TalkToDataTurnStatus.UNSUPPORTED:
            if not self.message or self.result is not None or self.clarification_question:
                raise ValueError("an unsupported turn requires only message")
        elif self.result is None or self.clarification_question is not None or self.message:
            raise ValueError("a completed turn requires only result")
        return self


class WorkflowError(TracciaRuntimeError):
    def __init__(
        self,
        message: str,
        *,
        run_ids: tuple[str, ...] = (),
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.run_ids = run_ids
        self.retryable = retryable


class AgentClient(Protocol):
    async def run(self, request: RunRequest) -> Any: ...


ClarificationHandler = Callable[[str], Awaitable[str]]
StructuredOutput = TypeVar("StructuredOutput", bound=BaseModel)


class TalkToDataWorkflow:
    """Coordinates two independently configured agents with a bounded clarification loop."""

    def __init__(
        self,
        client: AgentClient,
        catalog: SchemaCatalog,
        *,
        tenant_id: str,
        user_id: str,
        max_clarifications: int = 2,
    ) -> None:
        if max_clarifications < 0 or max_clarifications > 5:
            raise ValueError("max_clarifications must be between 0 and 5")
        self._client = client
        self._catalog = catalog
        self._tenant_id = tenant_id
        self._user_id = user_id
        self._max_clarifications = max_clarifications

    async def run(
        self, question: str, clarification_handler: ClarificationHandler
    ) -> TalkToDataResult:
        if not question.strip():
            raise ValueError("question cannot be empty")
        correlation_id = str(uuid4())
        conversation: list[tuple[str, str]] = []
        assessment: IntentAssessment | None = None

        for attempt in range(self._max_clarifications + 1):
            assessment, _ = await self._assess_intent(question, conversation, correlation_id)
            if assessment.status == IntentStatus.READY:
                break
            if assessment.status == IntentStatus.UNSUPPORTED:
                raise WorkflowError(assessment.interpreted_request)
            if attempt >= self._max_clarifications:
                raise WorkflowError(
                    "The request is still ambiguous after the allowed clarification attempts: "
                    f"{assessment.clarification_question}"
                )
            clarification = (
                await clarification_handler(assessment.clarification_question or "")
            ).strip()
            if not clarification:
                raise WorkflowError("clarification cannot be empty")
            conversation.append((assessment.clarification_question or "", clarification))

        assert assessment is not None
        generated, _ = await self._generate_sql(question, conversation, assessment, correlation_id)
        sql = validate_read_only_sql(generated.sql, self._catalog.tables)
        validate_parameters(sql, generated.parameters)
        assumptions = tuple(dict.fromkeys((*assessment.assumptions, *generated.assumptions)))
        return TalkToDataResult(
            sql=sql,
            parameters=generated.parameters,
            interpreted_request=assessment.interpreted_request,
            assumptions=assumptions,
            source_ids=generated.source_ids,
            clarification_count=len(conversation),
            correlation_id=correlation_id,
        )

    async def run_turn(
        self,
        question: str,
        clarifications: list[tuple[str, str]],
        *,
        correlation_id: str | None = None,
        conversation_id: str | None = None,
        turn_id: str | None = None,
    ) -> TalkToDataTurnResult:
        """Execute one non-blocking dashboard turn and return a resumable result."""
        if not question.strip():
            raise ValueError("question cannot be empty")
        resolved_correlation_id = correlation_id or str(uuid4())
        assessment, intent_run = await self._assess_intent(
            question,
            clarifications,
            resolved_correlation_id,
            conversation_id,
            turn_id,
        )
        intent_run_id = str(getattr(intent_run, "run_id", ""))
        if assessment.status == IntentStatus.UNSUPPORTED:
            return TalkToDataTurnResult(
                status=TalkToDataTurnStatus.UNSUPPORTED,
                message=(
                    "I cannot answer that safely from the currently documented database schema. "
                    + assessment.interpreted_request
                ),
                run_ids=tuple(filter(None, (intent_run_id,))),
                correlation_id=resolved_correlation_id,
            )
        if assessment.status == IntentStatus.NEEDS_CLARIFICATION:
            if len(clarifications) >= self._max_clarifications:
                raise WorkflowError(
                    "The request is still ambiguous after the allowed clarification attempts: "
                    f"{assessment.clarification_question}"
                )
            return TalkToDataTurnResult(
                status=TalkToDataTurnStatus.NEEDS_CLARIFICATION,
                clarification_question=assessment.clarification_question,
                run_ids=tuple(filter(None, (intent_run_id,))),
                correlation_id=resolved_correlation_id,
            )

        generated, sql_run = await self._generate_sql(
            question,
            clarifications,
            assessment,
            resolved_correlation_id,
            conversation_id,
            turn_id,
            intent_run_id or None,
        )
        sql = validate_read_only_sql(generated.sql, self._catalog.tables)
        validate_parameters(sql, generated.parameters)
        assumptions = tuple(dict.fromkeys((*assessment.assumptions, *generated.assumptions)))
        result = TalkToDataResult(
            sql=sql,
            parameters=generated.parameters,
            interpreted_request=assessment.interpreted_request,
            assumptions=assumptions,
            source_ids=generated.source_ids,
            clarification_count=len(clarifications),
            correlation_id=resolved_correlation_id,
        )
        return TalkToDataTurnResult(
            status=TalkToDataTurnStatus.COMPLETED,
            result=result,
            run_ids=tuple(filter(None, (intent_run_id, str(getattr(sql_run, "run_id", ""))))),
            correlation_id=resolved_correlation_id,
        )

    async def _assess_intent(
        self,
        question: str,
        conversation: list[tuple[str, str]],
        correlation_id: str,
        conversation_id: str | None = None,
        turn_id: str | None = None,
    ) -> tuple[IntentAssessment, Any]:
        transcript = "\n".join(
            f"Clarification question: {prompt}\nHuman answer: {answer}"
            for prompt, answer in conversation
        )
        result = await self._client.run(
            RunRequest(
                agent="talk-to-data-intent",
                input=(
                    f"Original request:\n{question}\n\n"
                    f"Clarifications so far:\n{transcript or '(none)'}\n\n"
                    "Determine whether this is ready for SQL generation."
                ),
                tenant_id=self._tenant_id,
                user_id=self._user_id,
                correlation_id=correlation_id,
                session_id=conversation_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                workflow_run_id=correlation_id,
                metadata={
                    "workflow": "talk-to-data",
                    "agent_role": "intent",
                    "retrieval_query": question,
                },
                overrides=RequestOverrides(response_schema=IntentAssessment.model_json_schema()),
            )
        )
        assessment = self._validated_output(result, IntentAssessment)
        unknown = set(assessment.relevant_tables) - self._catalog.tables
        if unknown:
            documented = tuple(
                table for table in assessment.relevant_tables if table in self._catalog.tables
            )
            if assessment.status == IntentStatus.NEEDS_CLARIFICATION:
                assessment = assessment.model_copy(update={"relevant_tables": documented})
            elif "pnr_flight" in self._catalog.tables:
                assessment = assessment.model_copy(
                    update={
                        "status": IntentStatus.NEEDS_CLARIFICATION,
                        "clarification_question": (
                            "The preferred aggregate tables are mentioned but not documented. "
                            "Should I calculate bookings and revenue from the raw pnr_flight data?"
                        ),
                        "relevant_tables": ("pnr_flight",),
                        "assumptions": (
                            *assessment.assumptions,
                            "Undocumented aggregate tables cannot be queried safely.",
                        ),
                    }
                )
            else:
                raise WorkflowError(
                    f"The request depends on undocumented tables: {sorted(unknown)}"
                )
        self._validate_sources(result, assessment.evidence_ids)
        return assessment, result

    async def _generate_sql(
        self,
        question: str,
        conversation: list[tuple[str, str]],
        assessment: IntentAssessment,
        correlation_id: str,
        conversation_id: str | None = None,
        turn_id: str | None = None,
        parent_run_id: str | None = None,
    ) -> tuple[SQLGeneration, Any]:
        result = await self._client.run(
            RunRequest(
                agent="talk-to-data-sql",
                input=json.dumps(
                    {
                        "original_request": question,
                        "clarifications": conversation,
                        "intent": assessment.model_dump(mode="json"),
                        "allowed_tables": sorted(self._catalog.tables),
                    },
                    indent=2,
                ),
                tenant_id=self._tenant_id,
                user_id=self._user_id,
                correlation_id=correlation_id,
                session_id=conversation_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                parent_run_id=parent_run_id,
                workflow_run_id=correlation_id,
                metadata={
                    "workflow": "talk-to-data",
                    "agent_role": "text_to_sql",
                    "retrieval_query": assessment.interpreted_request,
                },
                overrides=RequestOverrides(response_schema=SQLGeneration.model_json_schema()),
            )
        )
        generated = self._validated_output(result, SQLGeneration)
        self._validate_sources(result, generated.source_ids)
        return generated, result

    @staticmethod
    def _validated_output(result: Any, model: type[StructuredOutput]) -> StructuredOutput:
        if getattr(result, "error", None):
            raise WorkflowError(str(result.error))
        output = str(getattr(result, "output", "") or "").strip()
        if output.startswith("```"):
            output = re.sub(r"^```(?:json)?\s*|\s*```$", "", output, flags=re.IGNORECASE)
        try:
            return model.model_validate_json(output)
        except ValueError as exc:
            raise WorkflowError(f"agent returned invalid structured output: {exc}") from exc

    @staticmethod
    def _validate_sources(result: Any, source_ids: tuple[str, ...]) -> None:
        citations = tuple(getattr(result, "citations", ()) or ())
        if not citations:
            return
        available = {str(citation.id) for citation in citations}
        unknown = set(source_ids) - available
        if unknown:
            raise WorkflowError(f"agent referenced unknown schema sources: {sorted(unknown)}")


def validate_read_only_sql(sql: str, allowed_tables: frozenset[str]) -> str:
    normalized = sql.strip()
    if normalized.endswith(";"):
        normalized = normalized[:-1].rstrip()
    if not normalized or ";" in normalized:
        raise WorkflowError("generated SQL must contain exactly one statement")
    if "--" in normalized or "/*" in normalized or "*/" in normalized:
        raise WorkflowError("generated SQL comments are not allowed")
    first = normalized.split(maxsplit=1)[0].lower()
    if first not in {"select", "with"}:
        raise WorkflowError("generated SQL must be a SELECT or WITH statement")
    forbidden = _FORBIDDEN_SQL.search(normalized)
    if forbidden:
        raise WorkflowError(f"generated SQL contains forbidden keyword {forbidden.group(1)!r}")
    if _DANGEROUS_FUNCTION.search(normalized):
        raise WorkflowError("generated SQL contains a prohibited PostgreSQL function")

    ctes = {name.lower() for name in _CTE.findall(normalized)}
    referenced = {name.lower() for name in _TABLE_REFERENCE.findall(normalized)} - ctes
    unknown = referenced - {table.lower() for table in allowed_tables}
    if unknown:
        raise WorkflowError(f"generated SQL references undocumented tables: {sorted(unknown)}")
    if not referenced:
        raise WorkflowError("generated SQL does not reference a documented table")
    return normalized + ";"


def validate_parameters(sql: str, parameters: tuple[SQLParameter, ...]) -> None:
    placeholders = {int(value) for value in _PLACEHOLDER.findall(sql)}
    positions = [parameter.position for parameter in parameters]
    if len(positions) != len(set(positions)):
        raise WorkflowError("generated SQL parameters contain duplicate positions")
    expected = set(range(1, max(placeholders, default=0) + 1))
    if placeholders != expected or set(positions) != expected:
        raise WorkflowError("generated SQL placeholders and parameter positions do not match")
