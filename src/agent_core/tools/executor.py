from __future__ import annotations

import asyncio
import inspect
import json
from collections import defaultdict
from typing import Any

import jsonschema

from agent_core.exceptions.errors import PolicyDeniedError, ToolExecutionError
from agent_core.tools.contracts import Idempotency, ToolContext, ToolResult
from agent_core.tools.registry import ToolRegistry


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, policy_engine: Any) -> None:
        self._registry = registry
        self._policy_engine = policy_engine
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._results: dict[str, ToolResult] = {}
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def execute(self, name: str, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
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
        if definition.idempotency != Idempotency.NON_IDEMPOTENT:
            cached = self._results.get(context.idempotency_key)
            if cached:
                return cached
        semaphore = self._semaphores.setdefault(name, asyncio.Semaphore(definition.max_concurrency))
        async with self._locks[context.idempotency_key], semaphore:
            cached = self._results.get(context.idempotency_key)
            if cached and definition.idempotency != Idempotency.NON_IDEMPOTENT:
                return cached
            attempts = (
                definition.retry_policy.max_attempts
                if definition.idempotency != Idempotency.NON_IDEMPOTENT
                else 1
            )
            result: Any = None
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
            try:
                jsonschema.validate(result, definition.output_schema)
            except jsonschema.ValidationError as exc:
                raise ToolExecutionError(f"invalid output from {name}: {exc.message}") from exc
            output_decision = await self._policy_engine.evaluate(
                "after_tool", result, {"tool": definition, "context": context}
            )
            if output_decision.action == "deny":
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
            if definition.idempotency != Idempotency.NON_IDEMPOTENT:
                self._results[context.idempotency_key] = wrapped
            return wrapped
