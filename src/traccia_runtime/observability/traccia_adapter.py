from __future__ import annotations

import importlib
import os
from collections.abc import Mapping
from contextlib import AbstractContextManager, nullcontext
from types import ModuleType
from typing import Any, Protocol, cast

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from traccia_runtime.config.settings import TelemetrySettings, TracciaSettings
from traccia_runtime.exceptions.errors import ConfigurationError


class ObservabilityAdapter(Protocol):
    def start(self) -> None: ...

    def run_scope(
        self, *, agent_id: str, agent_name: str, tenant_id: str
    ) -> AbstractContextManager[Any]: ...

    def span_attributes(self) -> Mapping[str, str]: ...

    def stop(self) -> None: ...


class NoopObservabilityAdapter:
    def start(self) -> None:
        return None

    def run_scope(
        self, *, agent_id: str, agent_name: str, tenant_id: str
    ) -> AbstractContextManager[Any]:
        del agent_id, agent_name, tenant_id
        return nullcontext()

    def span_attributes(self) -> Mapping[str, str]:
        return {}

    def stop(self) -> None:
        return None


class TracciaObservabilityAdapter:
    """Optional Traccia lifecycle and per-run identity bridge."""

    def __init__(self, settings: TracciaSettings, service_name: str) -> None:
        self._settings = settings
        self._service_name = service_name
        self._module: ModuleType | Any | None = None
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        try:
            module = importlib.import_module("traccia")
        except ModuleNotFoundError as exc:
            raise ConfigurationError(
                "Traccia observability is enabled but the SDK is not installed; "
                "install traccia-runtime[traccia]"
            ) from exc
        options: dict[str, Any] = {
            "service_name": self._service_name,
            "service_role": self._settings.service_role,
            "env": self._settings.environment,
            "auto_start_trace": False,
            "sample_rate": self._settings.sample_rate,
            "enable_patching": self._settings.enable_patching,
            "enable_token_counting": self._settings.enable_token_counting,
            "enable_costs": self._settings.enable_costs,
            "enable_metrics": self._settings.enable_metrics,
            "redact_pii": self._settings.redact_pii,
        }
        if self._settings.endpoint:
            options["endpoint"] = self._settings.endpoint
        if self._settings.metrics_endpoint:
            options["metrics_endpoint"] = self._settings.metrics_endpoint
        if self._settings.max_spans_per_second is not None:
            options["max_spans_per_second"] = self._settings.max_spans_per_second
        if self._settings.api_key:
            variable = self._settings.api_key.removeprefix("env://")
            value = os.getenv(variable)
            if not value:
                raise ConfigurationError(
                    f"required Traccia secret environment variable {variable!r} is not set"
                )
            options["api_key"] = value
        provider = module.init(**options)
        self._register_otel_provider(provider)
        self._module = module
        self._started = True

    @staticmethod
    def _register_otel_provider(provider: Any) -> None:
        otel_provider = getattr(provider, "_otel_tracer_provider", None)
        if not isinstance(otel_provider, TracerProvider):
            raise ConfigurationError(
                "installed Traccia SDK does not expose a compatible OpenTelemetry provider"
            )
        current = trace.get_tracer_provider()
        if isinstance(current, trace.ProxyTracerProvider):
            trace.set_tracer_provider(otel_provider)
        elif current is not otel_provider:
            raise ConfigurationError(
                "Traccia must be initialized before another global OpenTelemetry provider"
            )

    def run_scope(
        self, *, agent_id: str, agent_name: str, tenant_id: str
    ) -> AbstractContextManager[Any]:
        self.start()
        assert self._module is not None
        identity = {
            "agent_id": agent_id,
            "agent_name": agent_name,
            "env": self._settings.environment,
            "tenant_id": tenant_id,
        }
        if self._settings.project_id:
            identity["project_id"] = self._settings.project_id
        return cast(
            AbstractContextManager[Any],
            self._module.runtime_config.run_identity(**identity),
        )

    def span_attributes(self) -> Mapping[str, str]:
        return {
            "env": self._settings.environment,
            "environment": self._settings.environment,
        }

    def stop(self) -> None:
        if not self._started or self._module is None:
            return
        self._module.stop_tracing(self._settings.flush_timeout_seconds)
        self._started = False


def observability_adapter(settings: TelemetrySettings) -> ObservabilityAdapter:
    if settings.traccia.enabled:
        return TracciaObservabilityAdapter(settings.traccia, settings.service_name)
    return NoopObservabilityAdapter()
