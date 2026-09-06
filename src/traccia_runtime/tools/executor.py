from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any

import jsonschema

from traccia_runtime.cache import CacheContext, CacheService
from traccia_runtime.exceptions.errors import PolicyDeniedError, ToolExecutionError
from traccia_runtime.persistence.tool_executions import InMemoryToolExecutionStore
from traccia_runtime.tools.contracts import (
    Idempotency,
    SideEffect,
    ToolContext,
    ToolExecutionStatus,
    ToolExecutionStore,
    ToolResult,
)
from traccia_runtime.tools.registry import ToolRegistry


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        policy_engine: Any,
        executions: ToolExecutionStore | None = None,
        cache: CacheService | None = None,
    ) -> None:
        self._registry = registry
        self._policy_engine = policy_engine
        self._executions = executions or InMemoryToolExecutionStore()
        self._cache = cache
        self._semaphores: dict[str, asyncio.Semaphore] = {}

    async def execute(
        self, name: str, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        tool = self._registry.get(name)
        definition = tool.definition
        missing = definition.required_permissions - context.permissions
        if missing:
            raise PolicyDeniedError(f"missing permissions for {name}: {sorted(missing)}")
        try:
            jsonschema.validate(arguments, definition.input_schema)
        except jsonschema.ValidationError as exc:
            raise ToolExecutionError(f"invalid input for {name}: {exc.message}") from exc
        decision = await self._policy_engine.evaluate(
            "before_tool", arguments, {"tool": definition, "context": context}
        )
        if decision.action != "allow":
            raise PolicyDeniedError(decision.reason)
        execution, reserved = await self._executions.begin(context, name)
        if not reserved:
            if execution.status == ToolExecutionStatus.COMPLETED and execution.result:
                return execution.result
            raise ToolExecutionError(
                f"tool {name} has an unfinished {execution.status.value} execution; "
                "manual reconciliation is required"
            )
        cache = self._cache
        cacheable = (
            cache is not None
            and definition.side_effect in {SideEffect.NONE, SideEffect.READ}
            and definition.idempotency == Idempotency.IDEMPOTENT
        )
        cache_context = CacheContext(
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            run_id=context.run_id,
            authorization_fingerprint=",".join(sorted(context.permissions)),
        )
        cache_material = {
            "tool": definition.name,
            "version": definition.version,
            "arguments": arguments,
        }
        if cacheable:
            assert cache is not None
            cached = await cache.get_json(
                "tool_results", "tool.result", cache_material, cache_context
            )
            if cached is not None:
                wrapped = ToolResult.model_validate(cached).model_copy(
                    update={"metadata": {**cached.get("metadata", {}), "runtime_cache_hit": True}}
                )
                await self._executions.complete(context.idempotency_key, wrapped)
                return wrapped
        semaphore = self._semaphores.setdefault(name, asyncio.Semaphore(definition.max_concurrency))
        async with semaphore:
            attempts = (
                definition.retry_policy.max_attempts
                if definition.idempotency != Idempotency.NON_IDEMPOTENT
                else 1
            )
            result: Any = None
            try:
                for attempt in range(attempts):
                    try:
                        result = tool.handler(arguments, context)
                        if inspect.isawaitable(result):
                            result = await asyncio.wait_for(result, definition.timeout_seconds)
                        break
                    except TimeoutError as exc:
                        if attempt + 1 >= attempts:
                            raise ToolExecutionError(f"tool {name} timed out") from exc
                    except (PolicyDeniedError, ToolExecutionError):
                        raise
                    except Exception as exc:
                        if attempt + 1 >= attempts:
                            raise ToolExecutionError(
                                f"tool {name} failed: {type(exc).__name__}"
                            ) from exc
                    delay = min(
                        definition.retry_policy.max_backoff_seconds,
                        definition.retry_policy.initial_backoff_seconds * (2**attempt),
                    )
                    await asyncio.sleep(delay)
            except BaseException as exc:
                indeterminate = definition.side_effect not in {SideEffect.NONE, SideEffect.READ}
                await self._executions.fail(
                    context.idempotency_key,
                    type(exc).__name__,
                    indeterminate=indeterminate,
                )
                raise
            try:
                try:
                    jsonschema.validate(result, definition.output_schema)
                except jsonschema.ValidationError as exc:
                    raise ToolExecutionError(f"invalid output from {name}: {exc.message}") from exc
                output_decision = await self._policy_engine.evaluate(
                    "after_tool", result, {"tool": definition, "context": context}
                )
                if output_decision.action not in {"allow", "redact", "transform"}:
                    raise PolicyDeniedError(output_decision.reason)
                if output_decision.action in {"redact", "transform"}:
                    result = output_decision.value
                    try:
                        jsonschema.validate(result, definition.output_schema)
                    except jsonschema.ValidationError as exc:
                        raise ToolExecutionError(
                            f"policy-transformed output from {name} is invalid: {exc.message}"
                        ) from exc
                encoded = json.dumps(result, default=str).encode()
                if len(encoded) > definition.max_result_bytes:
                    raise ToolExecutionError(f"result from {name} exceeds configured size limit")
                wrapped = ToolResult(value=result)
                await self._executions.complete(context.idempotency_key, wrapped)
                if cacheable:
                    assert cache is not None
                    await cache.set_json(
                        "tool_results",
                        "tool.result",
                        cache_material,
                        wrapped.model_dump(mode="json"),
                        cache_context,
                        tags=(f"tool:{definition.name}:{definition.version}",),
                    )
                return wrapped
            except BaseException as exc:
                indeterminate = definition.side_effect not in {SideEffect.NONE, SideEffect.READ}
                await self._executions.fail(
                    context.idempotency_key,
                    type(exc).__name__,
                    indeterminate=indeterminate,
                )
                raise
