from agent_core.policies.contracts import PolicyAction
from agent_core.policies.engine import CompositePolicyEngine


async def test_secret_is_transformed() -> None:
    decision = await CompositePolicyEngine().evaluate(
        "input", "api_key=supersecretvalue", {}
    )
    assert decision.action == PolicyAction.TRANSFORM
    assert "supersecretvalue" not in decision.value

