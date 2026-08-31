from __future__ import annotations

from dataclasses import dataclass

from agent_core.approvals.service import InMemoryApprovalService
from agent_core.config.registry import InMemoryAgentRegistry
from agent_core.config.settings import AppSettings
from agent_core.context.builder import ContextBuilderRegistry, DefaultContextBuilder
from agent_core.events.bus import InMemoryAuditLog, InMemoryEventBus
from agent_core.models.base import ModelRouter
from agent_core.models.providers.adapters import (
    AnthropicProvider,
    AzureOpenAIProvider,
    DeepSeekProvider,
    HuggingFaceInferenceProvider,
    OllamaProvider,
    OpenAIProvider,
)
from agent_core.models.providers.local_transformers import LocalTransformersProvider
from agent_core.models.providers.openai_compatible import OpenAICompatibleProvider
from agent_core.persistence.memory import (
    InMemoryArtifactStore,
    InMemoryMemoryStore,
    InMemoryRunStore,
)
from agent_core.planning.planners import PlannerRegistry
from agent_core.policies.engine import CompositePolicyEngine
from agent_core.responses.composer import ResponseComposerRegistry
from agent_core.runtime.runtime import AgentRuntime
from agent_core.tools.executor import ToolExecutor
from agent_core.tools.registry import ToolRegistry
from agent_core.types.interfaces import ModelProvider
from agent_core.verification.verifiers import VerificationService


@dataclass(frozen=True)
class Container:
    runtime: AgentRuntime
    agents: InMemoryAgentRegistry
    tools: ToolRegistry
    artifacts: InMemoryArtifactStore
    events: InMemoryEventBus


def build_container(settings: AppSettings) -> Container:
    agents = InMemoryAgentRegistry()
    for definition in settings.agents:
        agents.register(definition)
    tools = ToolRegistry()
    memory = InMemoryMemoryStore()
    runs = InMemoryRunStore()
    artifacts = InMemoryArtifactStore(settings.security.max_artifact_bytes)
    events = InMemoryEventBus()
    audits = InMemoryAuditLog()
    policies = CompositePolicyEngine()
    planners = PlannerRegistry()
    contexts = ContextBuilderRegistry(DefaultContextBuilder(memory))
    verifiers = VerificationService()
    composers = ResponseComposerRegistry()
    approvals = InMemoryApprovalService()
    router = ModelRouter()
    for name, config in settings.providers.items():
        kwargs = {"default_model": config.default_model}
        provider: ModelProvider
        if config.type == "openai":
            provider = OpenAIProvider(config.api_key or "env://OPENAI_API_KEY", **kwargs)
        elif config.type == "azure_openai":
            provider = AzureOpenAIProvider(
                config.base_url or "", config.api_key or "env://AZURE_OPENAI_API_KEY",
                config.default_model, config.api_version or "2024-10-21"
            )
        elif config.type == "anthropic":
            provider = AnthropicProvider(config.api_key or "env://ANTHROPIC_API_KEY", **kwargs)
        elif config.type == "deepseek":
            provider = DeepSeekProvider(config.api_key or "env://DEEPSEEK_API_KEY", **kwargs)
        elif config.type == "ollama":
            provider = OllamaProvider(base_url=config.base_url or "http://127.0.0.1:11434/v1", **kwargs)
        elif config.type == "huggingface_inference":
            provider = HuggingFaceInferenceProvider(config.api_key or "env://HF_TOKEN", **kwargs)
        elif config.type == "local_transformers":
            provider = LocalTransformersProvider(config.default_model)
        elif config.type == "openai_compatible":
            provider = OpenAICompatibleProvider(
                name,
                config.base_url or "http://127.0.0.1:8001/v1",
                config.api_key,
                config.default_model,
                config.capabilities,
            )
        else:
            raise ValueError(f"unsupported provider type {config.type!r}")
        router.register_provider(
            provider,
            config.requests_per_minute,
            config.cost_per_1k_input,
            config.cost_per_1k_output,
            is_local=config.type in {"ollama", "local_transformers"},
        )
    executor = ToolExecutor(tools, policies)
    runtime = AgentRuntime(
        agents=agents,
        router=router,
        tools=tools,
        tool_executor=executor,
        planners=planners,
        contexts=contexts,
        policies=policies,
        verifiers=verifiers,
        composers=composers,
        runs=runs,
        memory=memory,
        events=events,
        approvals=approvals,
        audits=audits,
    )
    return Container(runtime=runtime, agents=agents, tools=tools, artifacts=artifacts, events=events)
