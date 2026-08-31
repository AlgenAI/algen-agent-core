from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_core.config.settings import ProviderSettings, load_settings
from agent_core.exceptions.errors import ConfigurationError
from agent_core.types.contracts import RunRequest


def test_literal_secret_is_rejected() -> None:
    with pytest.raises(ValueError, match="secret reference"):
        ProviderSettings(type="openai", default_model="x", api_key="literal-secret")


def test_unknown_configuration_is_rejected(tmp_path: Path) -> None:
    config = tmp_path / "bad.yaml"
    config.write_text("unknown: true\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="invalid configuration"):
        load_settings((config,))


def test_nested_environment_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_CORE__API__PORT", "9000")
    assert load_settings().api.port == 9000


def test_unknown_request_override_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RunRequest(
            agent="agent",
            input="x",
            tenant_id="tenant",
            user_id="user",
            overrides={"unbounded_option": True},
        )
