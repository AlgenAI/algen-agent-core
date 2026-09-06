from traccia_runtime.policies.contracts import PolicyAction
from traccia_runtime.policies.engine import CompositePolicyEngine
from traccia_runtime.types.contracts import Message, Role


async def test_secret_is_transformed() -> None:
    decision = await CompositePolicyEngine().evaluate(
        "input", "api_key=supersecretvalue", {}
    )
    assert decision.action == PolicyAction.TRANSFORM
    assert "supersecretvalue" not in decision.value


async def test_secret_is_redacted_inside_retrieved_context_messages() -> None:
    decision = await CompositePolicyEngine().evaluate(
        "after_retrieval",
        [Message.text(Role.SYSTEM, "Retrieved api_key=supersecretvalue")],
        {},
    )

    assert decision.action == PolicyAction.TRANSFORM
    assert decision.value[0].role == Role.SYSTEM
    assert "supersecretvalue" not in decision.value[0].text_content
