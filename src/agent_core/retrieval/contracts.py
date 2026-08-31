from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RetrievalMode(StrEnum):
    VECTOR = "vector"
    KEYWORD = "keyword"
    HYBRID = "hybrid"


class RetrievalQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    text: str
    tenant_id: str
    mode: RetrievalMode = RetrievalMode.HYBRID
    limit: int = Field(default=10, ge=1, le=100)
    filters: dict[str, Any] = Field(default_factory=dict)


class RetrievedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    text: str
    source: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)

