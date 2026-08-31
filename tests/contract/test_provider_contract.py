import httpx

from agent_core.models.providers.adapters import AnthropicProvider
from agent_core.models.providers.openai_compatible import OpenAICompatibleProvider
from agent_core.types.contracts import Message, ModelCapabilities, ModelRequest, Role


async def test_openai_compatible_normalizes_wire_response() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "id": "response-1", "model": "demo",
            "choices": [{"finish_reason": "stop", "message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1},
        })

    provider = OpenAICompatibleProvider(
        "custom", "https://model.example/v1", None, "demo",
        ModelCapabilities(streaming=True),
        client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    response = await provider.generate(ModelRequest(messages=(Message.text(Role.USER, "hi"),)))
    assert response.provider == "custom"
    assert response.message.text_content == "ok"
    assert response.usage.total_tokens == 4


class StaticSecrets:
    async def get(self, reference: str) -> str:
        return "test-key"


async def test_anthropic_native_messages_contract() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "test-key"
        body = __import__("json").loads(request.content)
        assert body["system"] == "system"
        return httpx.Response(
            200,
            json={
                "id": "msg-1",
                "model": "claude-test",
                "content": [
                    {"type": "text", "text": "checking"},
                    {"type": "tool_use", "id": "call-1", "name": "lookup", "input": {"id": 1}},
                ],
                "usage": {"input_tokens": 4, "output_tokens": 2},
            },
        )

    provider = AnthropicProvider(
        "env://ANTHROPIC_API_KEY",
        "claude-test",
        secret_provider=StaticSecrets(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    response = await provider.generate(
        ModelRequest(
            messages=(Message.text(Role.SYSTEM, "system"), Message.text(Role.USER, "find"))
        )
    )
    assert response.tool_calls[0].name == "lookup"
    assert response.usage.total_tokens == 6
