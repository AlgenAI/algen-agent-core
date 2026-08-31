from __future__ import annotations

import asyncio
from datetime import timedelta
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from agent_core.exceptions.errors import ConflictError, NotFoundError
from agent_core.types.contracts import utc_now


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"
    EXPIRED = "expired"


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    step_id: str
    tenant_id: str
    proposed_action: str
    side_effect_summary: str
    redacted_parameters: dict[str, Any]
    risk: str
    expires_at: Any
    status: ApprovalStatus = ApprovalStatus.PENDING
    modified_parameters: dict[str, Any] | None = None


class InMemoryApprovalService:
    def __init__(self) -> None:
        self._items: dict[str, ApprovalRequest] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        run_id: str,
        step_id: str,
        tenant_id: str,
        proposed_action: str,
        parameters: dict[str, Any],
        risk: str,
        expires_seconds: int,
    ) -> ApprovalRequest:
        request = ApprovalRequest(
            run_id=run_id,
            step_id=step_id,
            tenant_id=tenant_id,
            proposed_action=proposed_action,
            side_effect_summary=f"Proposed {risk} operation: {proposed_action}",
            redacted_parameters=parameters,
            risk=risk,
            expires_at=utc_now() + timedelta(seconds=expires_seconds),
        )
        async with self._lock:
            self._items[request.id] = request
        return request.model_copy(deep=True)

    async def decide(
        self,
        approval_id: str,
        tenant_id: str,
        decision: ApprovalStatus,
        modified_parameters: dict[str, Any] | None = None,
    ) -> ApprovalRequest:
        async with self._lock:
            item = self._items.get(approval_id)
            if item is None or item.tenant_id != tenant_id:
                raise NotFoundError("approval request not found")
            if item.status != ApprovalStatus.PENDING:
                raise ConflictError("approval request has already been decided")
            if item.expires_at <= utc_now():
                item.status = ApprovalStatus.EXPIRED
                raise ConflictError("approval request expired")
            if decision == ApprovalStatus.MODIFIED and modified_parameters is None:
                raise ValueError("modified decision requires modified_parameters")
            item.status = decision
            item.modified_parameters = modified_parameters
            return item.model_copy(deep=True)

    async def get(self, approval_id: str, tenant_id: str) -> ApprovalRequest | None:
        async with self._lock:
            item = self._items.get(approval_id)
            return item.model_copy(deep=True) if item and item.tenant_id == tenant_id else None

