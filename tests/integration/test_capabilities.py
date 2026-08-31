import pytest

from agent_core.exceptions.errors import CapabilityError
from agent_core.models.base import ModelRouter
from agent_core.models.providers.mock import MockModelProvider
from agent_core.types.contracts import Message, ModelProfile, ModelRequest, Role


class TextOnlyProvider(MockModelProvider):
    provider_id = "text-only"

    async def capabilities(self, model):
        capabilities = await super().capabilities(model)
        return capabilities.model_copy(update={"tools": False})


async def test_unsupported_capability_is_detected_before_call() -> None:
    router = ModelRouter()
    provider = TextOnlyProvider(["must not run"])
    router.register_provider(provider)
    with pytest.raises(CapabilityError, match="no healthy allowed model"):
        await router.generate(
            ModelRequest(messages=(Message.text(Role.USER, "x"),)),
            (
                ModelProfile(
                    name="tools",
                    provider="text-only",
                    model="text",
                    required_capabilities=frozenset({"chat", "tools"}),
                ),
            ),
        )
    assert provider.requests == []

