import pytest

from traccia_runtime.exceptions.errors import ConflictError
from traccia_runtime.runtime.state_machine import validate_transition
from traccia_runtime.types.contracts import RunStatus


def test_legal_transition() -> None:
    validate_transition(RunStatus.RECEIVED, RunStatus.VALIDATING)


def test_illegal_transition_is_rejected() -> None:
    with pytest.raises(ConflictError):
        validate_transition(RunStatus.RECEIVED, RunStatus.COMPLETED)

