from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, cast

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from agent_core.api.dependencies import Principal, principal, require_scope
from agent_core.config.settings import AppSettings, load_settings
from agent_core.events.contracts import RunEvent
from agent_core.exceptions.errors import AgentCoreError, ConflictError, NotFoundError
from agent_core.observability.setup import configure_logging, configure_telemetry
from agent_core.orchestration.container import Container, build_container
from agent_core.types.contracts import AgentDefinition, RequestOverrides, RunRequest


class CreateRunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent: str
    agent_version: str | None = None
    input: str = Field(min_length=1)
    session_id: str | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)
    stream: bool = False
    metadata: dict[str, str] = Field(default_factory=dict)
    overrides: RequestOverrides = Field(default_factory=RequestOverrides)


class ClarificationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clarification: str = Field(min_length=1)


class ApprovalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str
    modified_parameters: dict[str, Any] | None = None


def _sse(event: RunEvent) -> bytes:
    payload = event.model_dump_json()
    return f"id: {event.sequence}\nevent: {event.type}\ndata: {payload}\n\n".encode()


def create_app(settings: AppSettings | None = None, container: Container | None = None) -> FastAPI:
    resolved = settings or AppSettings()
    dependencies = container or build_container(resolved)
    configure_logging()
    configure_telemetry(resolved.telemetry)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.container = dependencies
        yield

    app = FastAPI(title="Agent Core", version="1.0.0", lifespan=lifespan)
    app.state.container = dependencies

    @app.middleware("http")
    async def payload_limit(request: Request, call_next: Any) -> Response:
        length = int(request.headers.get("content-length", "0") or 0)
        if length > resolved.security.max_request_bytes:
            return JSONResponse(status_code=413, content={"detail": "request payload too large"})
        return cast(Response, await call_next(request))

    @app.exception_handler(NotFoundError)
    async def not_found(request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ConflictError)
    async def conflict(request: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(AgentCoreError)
    async def core_error(request: Request, exc: AgentCoreError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc), "kind": exc.error_kind.value})

    @app.post("/v1/runs", status_code=status.HTTP_202_ACCEPTED)
    async def create_run(body: CreateRunBody, identity: Annotated[Principal, Depends(principal)]) -> dict[str, Any]:
        require_scope(identity, "runs:write")
        state = await dependencies.runtime.start(
            RunRequest(
                **body.model_dump(), tenant_id=identity.tenant_id, user_id=identity.user_id
            )
        )
        return {"run_id": state.id, "session_id": state.session_id, "status": state.status}

    @app.get("/v1/runs/{run_id}")
    async def read_run(run_id: str, identity: Annotated[Principal, Depends(principal)]) -> dict[str, Any]:
        require_scope(identity, "runs:read")
        state = await dependencies.runtime.status(run_id, identity.tenant_id)
        return state.model_dump(mode="json", exclude={"messages", "pending_tool_calls"})

    @app.delete("/v1/runs/{run_id}")
    async def cancel_run(run_id: str, identity: Annotated[Principal, Depends(principal)]) -> dict[str, Any]:
        require_scope(identity, "runs:write")
        state = await dependencies.runtime.cancel(run_id, identity.tenant_id)
        return {"run_id": state.id, "status": state.status}

    @app.post("/v1/runs/{run_id}/clarification", status_code=202)
    async def clarify_run(
        run_id: str, body: ClarificationBody, identity: Annotated[Principal, Depends(principal)]
    ) -> dict[str, Any]:
        require_scope(identity, "runs:write")
        state = await dependencies.runtime.resume(run_id, identity.tenant_id, body.model_dump())
        return {"run_id": state.id, "status": state.status}

    @app.post("/v1/runs/{run_id}/approval", status_code=202)
    async def approve_run(
        run_id: str, body: ApprovalBody, identity: Annotated[Principal, Depends(principal)]
    ) -> dict[str, Any]:
        require_scope(identity, "runs:write")
        state = await dependencies.runtime.resume(run_id, identity.tenant_id, body.model_dump())
        return {"run_id": state.id, "status": state.status}

    @app.get("/v1/runs/{run_id}/events")
    async def stream_events(
        run_id: str,
        identity: Annotated[Principal, Depends(principal)],
        request: Request,
    ) -> StreamingResponse:
        require_scope(identity, "runs:read")
        await dependencies.runtime.status(run_id, identity.tenant_id)
        last_id = int(request.headers.get("last-event-id", "0") or 0)

        async def generate() -> AsyncIterator[bytes]:
            delivered = last_id
            async for event in dependencies.events.subscribe(run_id, after=last_id):
                if event.sequence <= delivered:
                    continue
                delivered = event.sequence
                yield _sse(event)
                if event.type in {"run.completed", "run.failed", "run.cancelled"}:
                    return

        return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/v1/artifacts/{artifact_id}")
    async def read_artifact(
        artifact_id: str, identity: Annotated[Principal, Depends(principal)]
    ) -> Response:
        require_scope(identity, "runs:read")
        artifact = await dependencies.artifacts.get(artifact_id, identity.tenant_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        return Response(artifact.data, media_type=artifact.media_type, headers={"Content-Disposition": f'attachment; filename="{artifact.name}"'})

    @app.get("/v1/agents")
    async def list_agents(identity: Annotated[Principal, Depends(principal)]) -> list[dict[str, Any]]:
        require_scope(identity, "agents:read")
        return [item.model_dump(mode="json") for item in dependencies.agents.list()]

    @app.post("/v1/agents", status_code=201)
    async def register_agent(
        definition: AgentDefinition, identity: Annotated[Principal, Depends(principal)]
    ) -> dict[str, str]:
        require_scope(identity, "agents:write")
        dependencies.agents.register(definition)
        return {"key": definition.key}

    @app.get("/health/live")
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def readiness(response: Response) -> dict[str, Any]:
        providers = {}
        for provider in dependencies.runtime.router.providers():
            providers[provider.provider_id] = await provider.health()
        ready = all(providers.values()) if providers else True
        if not ready:
            response.status_code = 503
        return {"status": "ready" if ready else "degraded", "providers": providers}

    return app


def main() -> None:
    config_files = tuple(filter(None, os.getenv("AGENT_CORE_CONFIG", "").split(os.pathsep)))
    settings = load_settings(tuple(Path(item) for item in config_files)) if config_files else AppSettings()
    uvicorn.run(create_app(settings), host=settings.api.host, port=settings.api.port)


app = create_app()
