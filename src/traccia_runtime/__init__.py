"""Composable, provider-neutral agent runtime."""

from traccia_runtime.cache import CacheContext, CachePolicy, CacheScope, CacheService
from traccia_runtime.retrieval.contracts import RetrievalQuery, RetrievedDocument, SourceDocument
from traccia_runtime.retrieval.memory import HashingEmbedder, InMemoryRetriever
from traccia_runtime.runtime.client import TracciaRuntimeClient
from traccia_runtime.runtime.runtime import AgentRuntime
from traccia_runtime.types.contracts import AgentDefinition, Citation, RunRequest, RunResult

__all__ = [
    "AgentDefinition",
    "AgentRuntime",
    "CacheContext",
    "CachePolicy",
    "CacheScope",
    "CacheService",
    "Citation",
    "HashingEmbedder",
    "InMemoryRetriever",
    "RetrievalQuery",
    "RetrievedDocument",
    "RunRequest",
    "RunResult",
    "SourceDocument",
    "TracciaRuntimeClient",
]
__version__ = "0.1.0"
