from agent_core.exceptions.errors import ProviderError
from agent_core.models.base import ModelRouter
from agent_core.models.providers.mock import MockModelProvider
from agent_core.types.contracts import ErrorKind, Message, ModelProfile, ModelRequest, Role


class FailingProvider(MockModelProvider):
    provider_id = "failing"
    async def generate(self, request):
        raise ProviderError("down", ErrorKind.UNAVAILABLE, True)


async def test_router_falls_back_after_provider_failure() -> None:
    router = ModelRouter()
    router.register_provider(FailingProvider())
    router.register_provider(MockModelProvider(["fallback"]))
    response = await router.generate(
        ModelRequest(messages=(Message.text(Role.USER, "x"),)),
        (
            ModelProfile(name="first", provider="failing", model="x", quality_tier=2),
            ModelProfile(name="second", provider="mock", model="x", quality_tier=1),
        ),
    )
    assert response.message.text_content == "fallback"

