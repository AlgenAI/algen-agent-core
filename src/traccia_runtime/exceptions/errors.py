from __future__ import annotations

from traccia_runtime.types.contracts import ErrorKind


class TracciaRuntimeError(Exception):
    error_kind = ErrorKind.UNKNOWN
    retryable = False


class ConfigurationError(TracciaRuntimeError):
    error_kind = ErrorKind.INVALID_REQUEST


class NotFoundError(TracciaRuntimeError):
    error_kind = ErrorKind.INVALID_REQUEST


class ConflictError(TracciaRuntimeError):
    error_kind = ErrorKind.INVALID_REQUEST


class PolicyDeniedError(TracciaRuntimeError):
    error_kind = ErrorKind.AUTHORIZATION


class CapabilityError(TracciaRuntimeError):
    error_kind = ErrorKind.INVALID_REQUEST


class ProviderError(TracciaRuntimeError):
    def __init__(self, message: str, kind: ErrorKind = ErrorKind.UNKNOWN, retryable: bool = False):
        super().__init__(message)
        self.error_kind = kind
        self.retryable = retryable


class ToolExecutionError(TracciaRuntimeError):
    pass


class BudgetExceededError(TracciaRuntimeError):
    pass


class VerificationError(TracciaRuntimeError):
    error_kind = ErrorKind.INVALID_RESPONSE


class RunPaused(TracciaRuntimeError):
    pass
