from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PolicyAction(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REDACT = "redact"
    TRANSFORM = "transform"
    REQUIRE_CLARIFICATION = "require_clarification"
    REQUIRE_APPROVAL = "require_approval"


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action: PolicyAction
    reason_code: str
    reason: str
    value: Any = None
    audit_metadata: dict[str, Any] = Field(default_factory=dict)

