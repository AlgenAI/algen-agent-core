from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Any

from opentelemetry import trace

from agent_core.approvals.service import ApprovalStatus, InMemoryApprovalService
from agent_core.config.registry import InMemoryAgentRegistry
from agent_core.context.builder import ContextBuilderRegistry
from agent_core.events.bus import InMemoryAuditLog, InMemoryEventBus
from agent_core.events.contracts import AuditEvent, EventType, RunEvent
from agent_core.exceptions.errors import (
    AgentCoreError,
    BudgetExceededError,
    ConflictError,
    NotFoundError,
    PolicyDeniedError,
    ProviderError,
    VerificationError,
)
from agent_core.models.base import ModelRouter
from agent_core.planning.contracts import ActionType, PlannedAction
from agent_core.planning.planners import PlannerRegistry
from agent_core.policies.contracts import PolicyAction
from agent_core.responses.composer import ResponseComposerRegistry
from agent_core.runtime.state_machine import validate_transition
from agent_core.security.redaction import redact
from agent_core.tools.contracts import ToolContext
from agent_core.tools.executor import ToolExecutor
from agent_core.tools.registry import ToolRegistry
from agent_core.types.contracts import (
    TERMINAL_STATUSES,
    AgentDefinition,
    Message,
    ModelRequest,
    Role,
    RunRequest,
    RunResult,
    RunState,
    RunStatus,
    TextBlock,
    TokenUsage,
    ToolSpec,
    utc_now,
)
from agent_core.types.interfaces import MemoryStore, RunStore
from agent_core.verification.verifiers import VerificationService


class AgentRuntime:
    def __init__(
        self,
        *,
        agents: InMemoryAgentRegistry,
        router: ModelRouter,
        tools: ToolRegistry,
        tool_executor: ToolExecutor,
        planners: PlannerRegistry,
        contexts: ContextBuilderRegistry,
        policies: Any,
        verifiers: VerificationService,
        composers: ResponseComposerRegistry,
        runs: RunStore,
        memory: MemoryStore,
        events: InMemoryEventBus,
        approvals: InMemoryApprovalService,
        audits: InMemoryAuditLog | None = None,
    ) -> None:
        self.agents = agents
        self.router = router
        self.tools = tools
        self.tool_executor = tool_executor
        self.planners = planners
        self.contexts = contexts
        self.policies = policies
        self.verifiers = verifiers
        self.composers = composers
        self.runs = runs
        self.memory = memory
        self.events = events
        self.approvals = approvals
        self.audits = audits or InMemoryAuditLog()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._sequence: dict[str, int] = {}
        self._task_lock = asyncio.Lock()
        self._tracer = trace.get_tracer("agent_core.runtime")

    async def start(self, request: RunRequest) -> RunState:
        agent = self.agents.get(request.agent, request.agent_version)
        timeout = request.timeout_seconds or agent.budget.max_latency_seconds
        state_values: dict[str, Any] = {
            "request": request,
            "agent_key": agent.key,
            "deadline": utc_now()
            + timedelta(seconds=min(timeout, agent.budget.max_latency_seconds)),
        }
        if request.session_id:
            state_values["session_id"] = request.session_id
        state = RunState(**state_values)
        await self.runs.create(state)
        await self._emit(state, "run.started", {"agent": agent.key})
        await self._schedule(state.id, request.tenant_id)
        return state

    async def run(self, request: RunRequest) -> RunResult:
        state = await self.start(request)
        task = self._tasks[state.id]
        await task
        current = await self._require_state(state.id, request.tenant_id)
        agent = self.agents.get(current.request.agent, current.request.agent_version)
        composer = self.composers.get(agent.response_composer)
        return await composer.compose(current)

    async def status(self, run_id: str, tenant_id: str) -> RunState:
        return await self._require_state(run_id, tenant_id)

    async def cancel(self, run_id: str, tenant_id: str) -> RunState:
        state = await self._require_state(run_id, tenant_id)
        if state.status in TERMINAL_STATUSES:
            return state
        async with self._task_lock:
            task = self._tasks.get(run_id)
            if task and not task.done():
                task.cancel()
        if state.status not in TERMINAL_STATUSES:
            await self._transition(state, RunStatus.CANCELLED)
            await self._emit(state, "run.cancelled", {})
        return state

    async def resume(self, run_id: str, tenant_id: str, payload: Mapping[str, Any]) -> RunState:
        state = await self._require_state(run_id, tenant_id)
        if state.status == RunStatus.AWAITING_CLARIFICATION:
            clarification = str(payload.get("clarification", "")).strip()
            if not clarification:
                raise ValueError("clarification is required")
            state.messages.append(Message.text(Role.USER, clarification))
            state.pause_payload = None
            await self._transition(state, RunStatus.BUILDING_CONTEXT)
        elif state.status == RunStatus.AWAITING_APPROVAL:
            approval_id = str((state.pause_payload or {}).get("approval_id", ""))
            raw_decision = str(payload.get("decision", ""))
            try:
                decision = ApprovalStatus(raw_decision)
            except ValueError as exc:
                raise ValueError("decision must be approved, rejected, or modified") from exc
            approval = await self.approvals.decide(
                approval_id, tenant_id, decision, payload.get("modified_parameters")
            )
            call_id = str((state.pause_payload or {}).get("tool_call_id", ""))
            if approval.status == ApprovalStatus.REJECTED:
                state.error = "Proposed action was rejected."
                await self._transition(state, RunStatus.FAILED)
                await self._emit(state, "run.failed", {"reason": "approval_rejected"})
                return state
            state.approved_tool_call_ids.add(call_id)
            if approval.status == ApprovalStatus.MODIFIED:
                for index, call in enumerate(state.pending_tool_calls):
                    if call.id == call_id:
                        state.pending_tool_calls[index] = call.model_copy(
                            update={"arguments": approval.modified_parameters or {}}
                        )
            state.pause_payload = None
            await self._transition(state, RunStatus.PLANNING)
        else:
            raise ConflictError(f"run in {state.status.value!r} cannot be resumed")
        await self._schedule(run_id, tenant_id)
        return state

    async def _schedule(self, run_id: str, tenant_id: str) -> None:
        async with self._task_lock:
            existing = self._tasks.get(run_id)
            if existing and not existing.done():
                raise ConflictError("run is already executing")
            self._tasks[run_id] = asyncio.create_task(self._drive(run_id, tenant_id))

    async def _drive(self, run_id: str, tenant_id: str) -> None:
        state = await self._require_state(run_id, tenant_id)
        agent = self.agents.get(state.request.agent, state.request.agent_version)
        try:
            remaining = max(0.001, (state.deadline - utc_now()).total_seconds()) if state.deadline else None
            async with asyncio.timeout(remaining):
                await self._execute(state, agent)
        except TimeoutError:
            current = await self._require_state(run_id, tenant_id)
            if current.status not in TERMINAL_STATUSES:
                await self._transition(current, RunStatus.TIMED_OUT)
                await self._emit(current, "run.failed", {"reason": "timeout"})
        except asyncio.CancelledError:
            current = await self._require_state(run_id, tenant_id)
            if current.status not in TERMINAL_STATUSES:
                await self._transition(current, RunStatus.CANCELLED)
                await self._emit(current, "run.cancelled", {})
        except Exception as exc:
            current = await self._require_state(run_id, tenant_id)
            if current.status not in TERMINAL_STATUSES:
                current.error = self._safe_error(exc)
                await self._transition(current, RunStatus.FAILED)
                await self._emit(current, "run.failed", {"error": current.error})

    async def _execute(self, state: RunState, agent: AgentDefinition) -> None:
        if state.status == RunStatus.RECEIVED:
            await self._transition(state, RunStatus.VALIDATING)
            decision = await self.policies.evaluate(
                "input", state.request.input, {"tenant_id": state.request.tenant_id, "agent": agent}
            )
            if decision.action == PolicyAction.DENY:
                raise PolicyDeniedError(decision.reason)
            if decision.action in {PolicyAction.TRANSFORM, PolicyAction.REDACT}:
                state.messages = [Message.text(Role.USER, str(decision.value))]
            await self._transition(state, RunStatus.BUILDING_CONTEXT)
        while state.status not in TERMINAL_STATUSES and state.status not in {
            RunStatus.AWAITING_APPROVAL, RunStatus.AWAITING_CLARIFICATION
        }:
            if state.step_count >= agent.max_steps:
                raise BudgetExceededError(f"maximum step limit {agent.max_steps} reached")
            self._enforce_budget(state, agent)
            if state.status == RunStatus.BUILDING_CONTEXT:
                await self._policy_value(
                    "before_retrieval", state.request.input, state, {"agent": agent}
                )
                builder = self.contexts.get(agent.context_builder)
                with self._tracer.start_as_current_span(
                    "agent.context.build", attributes=self._span_attributes(state)
                ):
                    state.messages = list(await builder.build(state, agent))
                checked_messages = await self._policy_value(
                    "after_retrieval", state.messages, state, {"agent": agent}
                )
                if isinstance(checked_messages, list):
                    state.messages = checked_messages
                await self._emit(state, "context.retrieved", {"message_count": len(state.messages)})
                await self._transition(state, RunStatus.PLANNING)
            if state.status == RunStatus.PLANNING:
                planner = self.planners.get(agent.planning_strategy)
                plan = await planner.plan(state, agent)
                state.summary = state.summary.model_copy(
                    update={"decisions": (*state.summary.decisions, plan.decision_summary)}
                )
                action = plan.actions[0]
                await self._emit(state, "step.started", {"action": action.type.value}, action.id)
                await self._execute_action(state, agent, action)

    async def _execute_action(self, state: RunState, agent: AgentDefinition, action: PlannedAction) -> None:
        state.step_count += 1
        if action.type == ActionType.CLARIFY:
            state.pause_payload = {"question": action.description}
            await self._transition(state, RunStatus.AWAITING_CLARIFICATION)
            await self._emit(state, "clarification.required", state.pause_payload, action.id)
        elif action.type == ActionType.TOOL:
            await self._execute_tool(state, agent, action)
        elif action.type == ActionType.MODEL:
            await self._execute_model(state, agent, action)
        elif action.type == ActionType.COMPLETE:
            await self._verify_and_complete(state, agent, action.id)
        elif action.type == ActionType.FAIL:
            state.error = action.description
            await self._transition(state, RunStatus.FAILED)
            await self._emit(state, "run.failed", {"error": state.error}, action.id)
        else:
            raise ConflictError(f"unsupported planned action {action.type.value}")

    async def _execute_model(self, state: RunState, agent: AgentDefinition, action: PlannedAction) -> None:
        await self._transition(state, RunStatus.INVOKING_MODEL)
        tool_specs = tuple(
            ToolSpec(
                name=tool.definition.name,
                description=tool.definition.description,
                input_schema=tool.definition.input_schema,
            )
            for tool in self.tools.list()
            if tool.definition.name in agent.enabled_tools
        )
        request = ModelRequest(
            messages=tuple(state.messages),
            tools=tool_specs,
            response_schema=state.request.overrides.response_schema,
            temperature=state.request.overrides.temperature,
            max_output_tokens=min(
                state.request.overrides.max_output_tokens
                or agent.budget.max_tokens,
                max(1, agent.budget.max_tokens - state.summary.usage.total_tokens),
            ),
            timeout_seconds=max(0.001, (state.deadline - utc_now()).total_seconds()) if state.deadline else None,
            stream=state.request.stream,
        )
        await self._policy_value("before_model", request, state, {"agent": agent})
        await self._emit(state, "model.started", {}, action.id)
        profiles = (agent.default_model, *agent.fallback_models)
        with self._tracer.start_as_current_span(
            "agent.model.call", attributes=self._span_attributes(state, action.id)
        ) as span:
            response = await self._model_with_retry(state, agent, request, profiles, action.id)
            span.set_attribute("gen_ai.system", response.provider)
            span.set_attribute("gen_ai.request.model", response.model)
            span.set_attribute("gen_ai.usage.input_tokens", response.usage.input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", response.usage.output_tokens)
        checked_output = await self._policy_value(
            "after_model", response.message.text_content, state, {"agent": agent}
        )
        if checked_output != response.message.text_content:
            response = response.model_copy(
                update={"message": Message.text(Role.ASSISTANT, str(checked_output))}
            )
        state.output_text = response.message.text_content or None
        state.pending_tool_calls = list(response.tool_calls)
        if response.message.text_content or response.tool_calls:
            state.messages.append(
                response.message.model_copy(update={"tool_calls": response.tool_calls})
            )
        state.summary = state.summary.model_copy(
            update={
                "model_calls": state.summary.model_calls + 1,
                "usage": self._add_usage(state.summary.usage, response.usage),
            }
        )
        await self._emit(
            state,
            "model.completed",
            {"provider": response.provider, "model": response.model, "finish_reason": response.finish_reason.value},
            action.id,
        )
        await self._audit(
            state,
            "model.call",
            "completed",
            f"{response.provider}/{response.model}",
            {"step_id": action.id, "usage": response.usage.model_dump(mode="json")},
        )
        await self._transition(state, RunStatus.PLANNING)

    async def _model_with_retry(
        self,
        state: RunState,
        agent: AgentDefinition,
        request: ModelRequest,
        profiles: Sequence[Any],
        step_id: str,
    ) -> Any:
        last_error: Exception | None = None
        for attempt in range(agent.retry_policy.max_attempts):
            try:
                if request.stream:
                    text_parts: list[str] = []
                    completed = None
                    async for event in self.router.stream(request, profiles, agent.model_allowlist):
                        if event.delta:
                            text_parts.append(event.delta)
                            await self._emit(state, "model.delta", {"delta": event.delta}, step_id)
                        if event.response:
                            completed = event.response
                    if completed is None:
                        raise ProviderError("stream ended without a completed response", retryable=True)
                    if text_parts and not completed.message.text_content:
                        completed = completed.model_copy(
                            update={"message": Message.text(Role.ASSISTANT, "".join(text_parts))}
                        )
                    return completed
                return await self.router.generate(request, profiles, agent.model_allowlist)
            except ProviderError as exc:
                last_error = exc
                if not exc.retryable or attempt + 1 >= agent.retry_policy.max_attempts:
                    break
                state.summary = state.summary.model_copy(update={"retries": state.summary.retries + 1})
                state.attempt_count += 1
                await self._transition(state, RunStatus.RETRYING)
                delay = min(
                    agent.retry_policy.max_backoff_seconds,
                    agent.retry_policy.initial_backoff_seconds * (2**attempt),
                )
                await asyncio.sleep(delay)
                await self._transition(state, RunStatus.INVOKING_MODEL)
        assert last_error is not None
        raise last_error

    async def _execute_tool(self, state: RunState, agent: AgentDefinition, action: PlannedAction) -> None:
        if action.tool_name not in agent.enabled_tools:
            raise PolicyDeniedError(f"tool {action.tool_name!r} is not enabled for this agent")
        tool = self.tools.get(action.tool_name or "")
        call_id = action.id.removeprefix("tool-")
        decision = await self.policies.evaluate(
            "plan_tool",
            action.arguments,
            {"tool": tool.definition, "approval_policy": agent.approval_policy},
        )
        if decision.action == PolicyAction.REQUIRE_APPROVAL and call_id not in state.approved_tool_call_ids:
            approval = await self.approvals.create(
                state.id,
                action.id,
                state.request.tenant_id,
                f"Execute {tool.definition.name}",
                self._redact_mapping(action.arguments),
                decision.audit_metadata.get("risk", tool.definition.side_effect.value),
                agent.approval_policy.expires_seconds,
            )
            state.pause_payload = {
                "approval_id": approval.id,
                "tool_call_id": call_id,
                "proposed_action": approval.proposed_action,
                "side_effect_summary": approval.side_effect_summary,
                "redacted_parameters": approval.redacted_parameters,
                "risk": approval.risk,
                "expires_at": approval.expires_at.isoformat(),
                "options": ["approved", "rejected", "modified"],
            }
            await self._transition(state, RunStatus.AWAITING_APPROVAL)
            await self._emit(state, "approval.required", state.pause_payload, action.id)
            return
        await self._transition(state, RunStatus.INVOKING_TOOL)
        await self._emit(state, "tool.started", {"tool": tool.definition.name}, action.id)
        context = ToolContext(
            run_id=state.id,
            step_id=action.id,
            tenant_id=state.request.tenant_id,
            user_id=state.request.user_id,
            permissions=agent.tool_permissions,
            idempotency_key=f"{state.id}:{call_id}",
        )
        with self._tracer.start_as_current_span(
            "agent.tool.call", attributes=self._span_attributes(state, action.id)
        ) as span:
            span.set_attribute("agent.tool.name", tool.definition.name)
            span.set_attribute("agent.tool.side_effect", tool.definition.side_effect.value)
            result = await self.tool_executor.execute(tool.definition.name, action.arguments, context)
        state.completed_tool_call_ids.add(call_id)
        state.pending_tool_calls = [call for call in state.pending_tool_calls if call.id != call_id]
        state.messages.append(
            Message(
                role=Role.TOOL,
                name=tool.definition.name,
                tool_call_id=call_id,
                content=(TextBlock(text=json.dumps(result.value, default=str)),),
            )
        )
        state.output_text = None
        state.summary = state.summary.model_copy(update={"tool_calls": state.summary.tool_calls + 1})
        await self._emit(state, "tool.completed", {"tool": tool.definition.name}, action.id)
        await self._audit(
            state,
            "tool.call",
            "completed",
            tool.definition.name,
            {"step_id": action.id, "side_effect": tool.definition.side_effect.value},
        )
        await self._transition(state, RunStatus.BUILDING_CONTEXT)

    async def _verify_and_complete(self, state: RunState, agent: AgentDefinition, step_id: str) -> None:
        await self._transition(state, RunStatus.VERIFYING)
        with self._tracer.start_as_current_span(
            "agent.verification", attributes=self._span_attributes(state, step_id)
        ):
            results = await self.verifiers.verify(
                agent.verification_policy.verifiers,
                state.output_text,
                {"state": state, "agent": agent},
            )
        passed = all(item.passed and item.confidence >= agent.verification_policy.minimum_confidence for item in results)
        await self._emit(
            state,
            "verification.completed",
            {"passed": passed, "results": [item.model_dump(mode="json") for item in results]},
            step_id,
        )
        if not passed:
            if state.attempt_count < agent.verification_policy.max_repairs:
                state.attempt_count += 1
                state.output_text = None
                state.messages.append(Message.text(Role.SYSTEM, "Repair the previous response to satisfy validation."))
                state.summary = state.summary.model_copy(update={"retries": state.summary.retries + 1})
                await self._transition(state, RunStatus.RETRYING)
                await self._transition(state, RunStatus.PLANNING)
                return
            raise VerificationError("final output failed verification")
        state.output_text = str(
            await self._policy_value(
                "final_response", state.output_text or "", state, {"agent": agent}
            )
        )
        await self._transition(state, RunStatus.COMPOSING)
        if agent.memory_policy.enabled:
            persisted = [Message.text(Role.USER, state.request.input)]
            if state.output_text:
                persisted.append(Message.text(Role.ASSISTANT, state.output_text))
            await self._policy_value(
                "before_memory_write", persisted, state, {"agent": agent}
            )
            with self._tracer.start_as_current_span(
                "agent.memory.write", attributes=self._span_attributes(state)
            ):
                await self.memory.append(state.request.tenant_id, state.session_id, persisted)
            await self._policy_value(
                "after_memory_write", {"items": len(persisted)}, state, {"agent": agent}
            )
        await self._transition(state, RunStatus.COMPLETED)
        await self._emit(state, "run.completed", {"outcome": "completed"}, step_id)

    def _enforce_budget(self, state: RunState, agent: AgentDefinition) -> None:
        usage = state.summary.usage
        if usage.total_tokens >= agent.budget.max_tokens:
            raise BudgetExceededError("token budget exhausted")
        if usage.estimated_cost_usd >= agent.budget.max_cost_usd:
            raise BudgetExceededError("cost budget exhausted")

    async def _transition(self, state: RunState, target: RunStatus) -> None:
        validate_transition(state.status, target)
        expected = state.version
        state.status = target
        state.updated_at = utc_now()
        await self.runs.save(state, expected)

    async def _emit(
        self,
        state: RunState,
        event_type: EventType,
        data: dict[str, Any],
        step_id: str | None = None,
    ) -> None:
        sequence = self._sequence.get(state.id, 0) + 1
        self._sequence[state.id] = sequence
        await self.events.publish(
            RunEvent(
                type=event_type,
                run_id=state.id,
                tenant_id=state.request.tenant_id,
                session_id=state.session_id,
                correlation_id=state.request.correlation_id,
                step_id=step_id,
                sequence=sequence,
                data=data,
            )
        )

    async def _require_state(self, run_id: str, tenant_id: str) -> RunState:
        state = await self.runs.get(run_id, tenant_id)
        if state is None:
            raise NotFoundError(f"run {run_id!r} not found")
        return state

    async def _policy_value(
        self,
        point: str,
        payload: Any,
        state: RunState,
        context: Mapping[str, Any],
    ) -> Any:
        decision = await self.policies.evaluate(
            point,
            payload,
            {
                **context,
                "run_id": state.id,
                "tenant_id": state.request.tenant_id,
                "user_id": state.request.user_id,
            },
        )
        await self._audit(
            state,
            "policy.decision",
            decision.action.value,
            point,
            {
                "reason_code": decision.reason_code,
                "audit_metadata": decision.audit_metadata,
            },
        )
        if decision.action == PolicyAction.DENY:
            raise PolicyDeniedError(f"{decision.reason_code}: {decision.reason}")
        if decision.action in {PolicyAction.REDACT, PolicyAction.TRANSFORM}:
            return decision.value
        if decision.action in {
            PolicyAction.REQUIRE_APPROVAL,
            PolicyAction.REQUIRE_CLARIFICATION,
        }:
            raise PolicyDeniedError(
                f"policy action {decision.action.value} is unsupported at boundary {point}"
            )
        return payload

    async def _audit(
        self,
        state: RunState,
        action: str,
        outcome: str,
        resource_id: str,
        metadata: dict[str, Any],
    ) -> None:
        await self.audits.append(
            AuditEvent(
                action=action,
                outcome=outcome,
                tenant_id=state.request.tenant_id,
                actor_id=state.request.user_id,
                resource_id=resource_id,
                metadata=redact(metadata),
            )
        )

    @staticmethod
    def _span_attributes(state: RunState, step_id: str | None = None) -> dict[str, str]:
        attributes = {
            "agent.run.id": state.id,
            "agent.session.id": state.session_id,
            "agent.tenant.id": state.request.tenant_id,
            "agent.user.id": state.request.user_id,
            "agent.correlation.id": state.request.correlation_id,
        }
        if step_id:
            attributes["agent.step.id"] = step_id
        return attributes

    @staticmethod
    def _add_usage(left: TokenUsage, right: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=left.input_tokens + right.input_tokens,
            output_tokens=left.output_tokens + right.output_tokens,
            cached_tokens=left.cached_tokens + right.cached_tokens,
            estimated_cost_usd=left.estimated_cost_usd + right.estimated_cost_usd,
        )

    @staticmethod
    def _redact_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
        sensitive = {"password", "secret", "token", "api_key", "authorization"}
        return {key: "[REDACTED]" if key.lower() in sensitive else item for key, item in value.items()}

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        if isinstance(exc, AgentCoreError):
            return str(exc)
        return f"Internal error ({type(exc).__name__})"
