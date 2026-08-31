import pytest

from agent_core.exceptions.errors import ConflictError
from agent_core.runtime.state_machine import validate_transition
from agent_core.types.contracts import RunStatus


def test_legal_transition() -> None:
    validate_transition(RunStatus.RECEIVED, RunStatus.VALIDATING)


def test_illegal_transition_is_rejected() -> None:
    with pytest.raises(ConflictError):
        validate_transition(RunStatus.RECEIVED, RunStatus.COMPLETED)

