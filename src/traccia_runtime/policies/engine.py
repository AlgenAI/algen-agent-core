from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from pydantic import BaseModel

from traccia_runtime.policies.contracts import PolicyAction, PolicyDecision
from traccia_runtime.tools.contracts import SideEffect


class Policy(Protocol):
    name: str
    async def evaluate(self, point: str, payload: Any, context: Mapping[str, Any]) -> PolicyDecision: ...


class SecretRedactionPolicy:
    name = "secrets"
    _pattern = re.compile(
        r"(?i)(?:api[_-]?key|password|secret|token)\s*[:=]\s*([A-Za-z0-9_\-/.]{8,})"
    )

    async def evaluate(self, point: str, payload: Any, context: Mapping[str, Any]) -> PolicyDecision:
        transformed, changed = self._redact(payload)
        if changed:
            return PolicyDecision(
                action=PolicyAction.TRANSFORM,
                reason_code="secret.redacted",
                reason="Potential secret was redacted.",
                value=transformed,
            )
        return PolicyDecision(action=PolicyAction.ALLOW, reason_code="secret.clear", reason="No secret detected.")

    def _redact(self, value: Any) -> tuple[Any, bool]:
        if isinstance(value, str):
            transformed, count = self._pattern.subn(
                lambda match: match.group(0).replace(match.group(1), "[REDACTED]"),
                value,
            )
            return transformed, bool(count)
        if isinstance(value, BaseModel):
            updates: dict[str, Any] = {}
            changed = False
            for name in type(value).model_fields:
                item, item_changed = self._redact(getattr(value, name))
                updates[name] = item
                changed = changed or item_changed
            return value.model_copy(update=updates) if changed else value, changed
        if isinstance(value, Mapping):
            transformed_mapping: dict[Any, Any] = {}
            changed = False
            for key, item in value.items():
                transformed_item, item_changed = self._redact(item)
                transformed_mapping[key] = transformed_item
                changed = changed or item_changed
            return transformed_mapping if changed else value, changed
        if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            transformed_items = []
            changed = False
            for item in value:
                transformed_item, item_changed = self._redact(item)
                transformed_items.append(transformed_item)
                changed = changed or item_changed
            if not changed:
                return value, False
            return tuple(transformed_items) if isinstance(value, tuple) else transformed_items, True
        return value, False


class AuthorizationPolicy:
    name = "authorization"

    async def evaluate(self, point: str, payload: Any, context: Mapping[str, Any]) -> PolicyDecision:
        if point == "before_tool":
            definition = context["tool"]
            tool_context = context["context"]
            if not definition.required_permissions.issubset(tool_context.permissions):
                return PolicyDecision(
                    action=PolicyAction.DENY,
                    reason_code="auth.missing_permission",
                    reason="Tool permission check failed.",
                )
        return PolicyDecision(action=PolicyAction.ALLOW, reason_code="auth.allowed", reason="Authorized.")


class SideEffectPolicy:
    name = "side_effects"

    async def evaluate(self, point: str, payload: Any, context: Mapping[str, Any]) -> PolicyDecision:
        definition = context.get("tool")
        approval_policy = context.get("approval_policy")
        if point == "plan_tool" and definition and approval_policy:
            side_effect = definition.side_effect
            required = (
                approval_policy.require_for_destructive and side_effect == SideEffect.DESTRUCTIVE
            ) or (
                approval_policy.require_for_side_effects
                and side_effect in {SideEffect.WRITE, SideEffect.EXTERNAL, SideEffect.DESTRUCTIVE}
            )
            if required:
                return PolicyDecision(
                    action=PolicyAction.REQUIRE_APPROVAL,
                    reason_code="side_effect.approval_required",
                    reason=f"Tool {definition.name} has {side_effect.value} side effects.",
                    audit_metadata={"risk": side_effect.value},
                )
        return PolicyDecision(action=PolicyAction.ALLOW, reason_code="side_effect.allowed", reason="Allowed.")


class CompositePolicyEngine:
    def __init__(self, policies: Sequence[Policy] | None = None) -> None:
        self._policies = tuple(policies or (SecretRedactionPolicy(), AuthorizationPolicy(), SideEffectPolicy()))

    async def evaluate(self, point: str, payload: Any, context: Mapping[str, Any]) -> PolicyDecision:
        transformed = payload
        transforms: list[str] = []
        for policy in self._policies:
            decision = await policy.evaluate(point, transformed, context)
            if decision.action in {
                PolicyAction.DENY,
                PolicyAction.REQUIRE_CLARIFICATION,
                PolicyAction.REQUIRE_APPROVAL,
            }:
                return decision
            if decision.action in {PolicyAction.REDACT, PolicyAction.TRANSFORM}:
                transformed = decision.value
                transforms.append(decision.reason_code)
        if transforms:
            return PolicyDecision(
                action=PolicyAction.TRANSFORM,
                reason_code="policy.transformed",
                reason="Payload transformed by policy.",
                value=transformed,
                audit_metadata={"transforms": transforms},
            )
        return PolicyDecision(action=PolicyAction.ALLOW, reason_code="policy.allowed", reason="Allowed.")
