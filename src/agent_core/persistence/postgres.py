from __future__ import annotations

from typing import Any

from agent_core.exceptions.errors import ConflictError
from agent_core.types.contracts import RunState


class PostgresRunStore:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def create(self, state: RunState) -> None:
        await self._pool.execute(
            "INSERT INTO agent_core_runs (id, tenant_id, version, state) VALUES ($1, $2, $3, $4::jsonb)",
            state.id, state.request.tenant_id, state.version, state.model_dump_json(),
        )

    async def get(self, run_id: str, tenant_id: str) -> RunState | None:
        row = await self._pool.fetchrow(
            "SELECT state FROM agent_core_runs WHERE id=$1 AND tenant_id=$2", run_id, tenant_id
        )
        return RunState.model_validate_json(row["state"]) if row else None

    async def save(self, state: RunState, expected_version: int) -> None:
        next_version = expected_version + 1
        saved = state.model_copy(update={"version": next_version})
        result = await self._pool.execute(
            "UPDATE agent_core_runs SET version=$1, state=$2::jsonb, updated_at=now() "
            "WHERE id=$3 AND tenant_id=$4 AND version=$5",
            next_version, saved.model_dump_json(), state.id, state.request.tenant_id, expected_version,
        )
        if result != "UPDATE 1":
            raise ConflictError(f"run {state.id!r} was concurrently modified")
        state.version = next_version
