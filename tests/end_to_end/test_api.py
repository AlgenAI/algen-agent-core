import asyncio

import httpx
from conftest import make_runtime

from agent_core.api.app import create_app
from agent_core.config.settings import AppSettings
from agent_core.orchestration.container import Container
from agent_core.persistence.memory import InMemoryArtifactStore
from agent_core.types.contracts import RunStatus


async def test_rest_create_and_read_run() -> None:
    runtime = make_runtime()
    container = Container(
        runtime=runtime,
        agents=runtime.agents,
        tools=runtime.tools,
        artifacts=InMemoryArtifactStore(),
        events=runtime.events,
    )
    transport = httpx.ASGITransport(app=create_app(AppSettings(), container))
    headers = {"x-tenant-id": "tenant", "x-user-id": "user"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/v1/runs", headers=headers, json={"agent": "test-agent", "input": "hello"}
        )
        assert created.status_code == 202
        run_id = created.json()["run_id"]
        for _ in range(100):
            response = await client.get(f"/v1/runs/{run_id}", headers=headers)
            if response.json()["status"] in {"completed", "failed"}:
                break
            await asyncio.sleep(0.001)
        assert response.json()["status"] == RunStatus.COMPLETED
        events = await client.get(f"/v1/runs/{run_id}/events", headers=headers)
        assert events.status_code == 200
        assert "event: run.completed" in events.text
        assert "event: model.completed" in events.text


async def test_api_enforces_tenant_identity() -> None:
    runtime = make_runtime()
    container = Container(
        runtime=runtime,
        agents=runtime.agents,
        tools=runtime.tools,
        artifacts=InMemoryArtifactStore(),
        events=runtime.events,
    )
    transport = httpx.ASGITransport(app=create_app(AppSettings(), container))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/v1/runs", json={"agent": "test-agent", "input": "x"})
    assert response.status_code == 422
