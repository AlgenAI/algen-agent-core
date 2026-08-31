from __future__ import annotations

from agent_core.types.contracts import ErrorKind


class AgentCoreError(Exception):
    error_kind = ErrorKind.UNKNOWN
    retryable = False


class ConfigurationError(AgentCoreError):
    error_kind = ErrorKind.INVALID_REQUEST


class NotFoundError(AgentCoreError):
    error_kind = ErrorKind.INVALID_REQUEST


class ConflictError(AgentCoreError):
    error_kind = ErrorKind.INVALID_REQUEST


class PolicyDeniedError(AgentCoreError):
    error_kind = ErrorKind.AUTHORIZATION


class CapabilityError(AgentCoreError):
    error_kind = ErrorKind.INVALID_REQUEST


class ProviderError(AgentCoreError):
    def __init__(self, message: str, kind: ErrorKind = ErrorKind.UNKNOWN, retryable: bool = False):
        super().__init__(message)
        self.error_kind = kind
        self.retryable = retryable


class ToolExecutionError(AgentCoreError):
    pass


class BudgetExceededError(AgentCoreError):
    pass


class VerificationError(AgentCoreError):
    error_kind = ErrorKind.INVALID_RESPONSE


class RunPaused(AgentCoreError):
    pass

