from __future__ import annotations

from typing import Any

import pytest

from agent_core.approvals.service import InMemoryApprovalService
from agent_core.config.registry import InMemoryAgentRegistry
from agent_core.context.builder import ContextBuilderRegistry, DefaultContextBuilder
from agent_core.events.bus import InMemoryEventBus
from agent_core.models.base import ModelRouter
from agent_core.models.providers.mock import MockModelProvider
from agent_core.persistence.memory import InMemoryMemoryStore, InMemoryRunStore
from agent_core.planning.planners import PlannerRegistry
from agent_core.policies.engine import CompositePolicyEngine
from agent_core.responses.composer import ResponseComposerRegistry
from agent_core.runtime.runtime import AgentRuntime
from agent_core.tools.executor import ToolExecutor
from agent_core.tools.registry import ToolRegistry
from agent_core.types.contracts import AgentDefinition, ModelProfile
from agent_core.verification.verifiers import VerificationService


def make_agent(**updates: Any) -> AgentDefinition:
    values = {
        "name": "test-agent",
        "version": "1.0.0",
        "description": "deterministic test agent",
        "system_instructions": "Be deterministic.",
        "default_model": ModelProfile(name="test", provider="mock", model="deterministic"),
        "planning_strategy": "react",
        "max_steps": 8,
    }
    values.update(updates)
    return AgentDefinition(**values)


def make_runtime(provider: Any | None = None, agent: AgentDefinition | None = None) -> AgentRuntime:
    agents = InMemoryAgentRegistry()
    agents.register(agent or make_agent())
    memory = InMemoryMemoryStore()
    tools = ToolRegistry()
    policies = CompositePolicyEngine()
    router = ModelRouter(circuit_failure_threshold=1)
    router.register_provider(provider or MockModelProvider(["answer"]))
    return AgentRuntime(
        agents=agents,
        router=router,
        tools=tools,
        tool_executor=ToolExecutor(tools, policies),
        planners=PlannerRegistry(),
        contexts=ContextBuilderRegistry(DefaultContextBuilder(memory)),
        policies=policies,
        verifiers=VerificationService(),
        composers=ResponseComposerRegistry(),
        runs=InMemoryRunStore(),
        memory=memory,
        events=InMemoryEventBus(),
        approvals=InMemoryApprovalService(),
    )


@pytest.fixture
def runtime() -> AgentRuntime:
    return make_runtime()

