from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from agent_core.exceptions.errors import ConfigurationError
from agent_core.types.contracts import AgentDefinition, ModelCapabilities


class StrictSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProviderSettings(StrictSettings):
    type: str
    base_url: str | None = None
    api_key: str | None = Field(default=None, description="Secret reference, for example env://KEY")
    api_version: str | None = None
    organization: str | None = None
    default_model: str
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
    timeout_seconds: float = Field(default=60, gt=0)
    max_concurrency: int = Field(default=20, ge=1)
    requests_per_minute: int = Field(default=600, ge=1)
    cost_per_1k_input: float = Field(default=0, ge=0)
    cost_per_1k_output: float = Field(default=0, ge=0)
    extensions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_literal_secret(self) -> ProviderSettings:
        if self.api_key and not self.api_key.startswith(("env://", "secret://")):
            raise ValueError("api_key must be a secret reference (env:// or secret://)")
        return self


class StorageSettings(StrictSettings):
    run_store: str = "memory"
    memory_store: str = "memory"
    artifact_store: str = "memory"
    postgres_dsn: str | None = None
    redis_url: str | None = None


class TelemetrySettings(StrictSettings):
    enabled: bool = True
    service_name: str = "agent-core"
    otlp_endpoint: str | None = None
    include_content: bool = False


class SecuritySettings(StrictSettings):
    max_request_bytes: int = Field(default=1_048_576, ge=1024)
    max_artifact_bytes: int = Field(default=10_485_760, ge=1024)
    allowed_http_hosts: tuple[str, ...] = ()
    allow_private_networks: bool = False
    allow_subprocess_tools: bool = False
    trusted_plugin_prefixes: tuple[str, ...] = ()


class ApiSettings(StrictSettings):
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    auth_enabled: bool = False


class AppSettings(StrictSettings):
    providers: dict[str, ProviderSettings] = Field(default_factory=dict)
    agents: tuple[AgentDefinition, ...] = ()
    storage: StorageSettings = Field(default_factory=StorageSettings)
    telemetry: TelemetrySettings = Field(default_factory=TelemetrySettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    feature_flags: dict[str, bool] = Field(default_factory=dict)


def _merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def _parse_env_value(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _environment(prefix: str = "AGENT_CORE__") -> dict[str, Any]:
    root: dict[str, Any] = {}
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue
        parts = key[len(prefix) :].lower().split("__")
        cursor = root
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = _parse_env_value(value)
    return root


def load_settings(
    files: tuple[str | Path, ...] = (),
    deployment_overrides: dict[str, Any] | None = None,
) -> AppSettings:
    data: dict[str, Any] = {}
    try:
        for file in files:
            loaded = yaml.safe_load(Path(file).read_text(encoding="utf-8")) or {}
            if not isinstance(loaded, dict):
                raise ConfigurationError(f"configuration root in {file} must be a mapping")
            data = _merge(data, loaded)
        data = _merge(data, _environment())
        data = _merge(data, deployment_overrides or {})
        return AppSettings.model_validate(data)
    except (OSError, yaml.YAMLError, ValueError) as exc:
        raise ConfigurationError(f"invalid configuration: {exc}") from exc


class EnvironmentSecretProvider:
    async def get(self, reference: str) -> str:
        if not reference.startswith("env://"):
            raise ConfigurationError("environment secret provider only supports env:// references")
        name = reference.removeprefix("env://")
        value = os.getenv(name)
        if value is None:
            raise ConfigurationError(f"required secret environment variable {name!r} is not set")
        return SecretStr(value).get_secret_value()

