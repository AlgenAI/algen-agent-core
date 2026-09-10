from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from time import monotonic
from typing import Any

from examples.talk_to_data_multi_agent.application.workflow import (
    SQLParameter,
    WorkflowError,
    validate_read_only_sql,
)
from traccia_runtime.cache import CacheContext, CacheService
from traccia_runtime.governance import (
    DataTrustRequirement,
    GovernedQueryRequest,
    QueryEstimate,
    QueryGovernanceEngine,
    query_fingerprint,
)


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    truncated: bool
    execution_ms: float
    cache_hit: bool = False
    query_fingerprint: str | None = None


class ReadOnlyPostgresExecutor:
    """Executes validated analytics SQL through a read-only PostgreSQL transaction."""

    supports_cache_context = True

    def __init__(
        self,
        dsn: str,
        allowed_tables: frozenset[str],
        *,
        statement_timeout_ms: int = 15_000,
        max_rows: int = 500,
        max_result_bytes: int = 2_000_000,
        cache: CacheService | None = None,
        data_version: str = "live",
        governance: QueryGovernanceEngine | None = None,
        certified_sources: frozenset[str] = frozenset(),
        authorization_tags: tuple[str, ...] = (),
        purpose: str = "executive_analytics",
    ) -> None:
        self._dsn = dsn
        self._allowed_tables = allowed_tables
        self._statement_timeout_ms = statement_timeout_ms
        self._max_rows = max_rows
        self._max_result_bytes = max_result_bytes
        self._cache = cache
        self._data_version = data_version
        self._governance = governance
        self._certified_sources = frozenset(
            alias for source in certified_sources for alias in _source_aliases(source)
        )
        self._authorization_tags = authorization_tags
        self._purpose = purpose
        self._pool: Any = None

    async def start(self) -> None:
        try:
            import asyncpg
        except ImportError as exc:
            raise RuntimeError("install traccia-runtime[talk-to-data]") from exc
        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def execute(
        self,
        sql: str,
        parameters: tuple[SQLParameter, ...],
        *,
        cache_context: CacheContext | None = None,
    ) -> QueryResult:
        validated = validate_read_only_sql(sql, self._allowed_tables).rstrip(";")
        bounded = f"SELECT * FROM ({validated}) AS traccia_bounded_query LIMIT {self._max_rows + 1}"
        values = [item.value for item in sorted(parameters, key=lambda item: item.position)]
        material = {
            "sql": validated,
            "parameters": values,
            "data_version": self._data_version,
            "row_limit": self._max_rows,
        }
        fingerprint = query_fingerprint(
            validated,
            tuple(values),
            authorization_fingerprint=(
                cache_context.authorization_fingerprint if cache_context else None
            ),
        )
        if self._governance is not None:
            normalized_sql = validated.replace('"', "").lower()
            source_ids = tuple(
                sorted(table for table in self._allowed_tables if table.lower() in normalized_sql)
            )
            self._governance.enforce(
                GovernedQueryRequest(
                    tenant_id=cache_context.tenant_id
                    if cache_context and cache_context.tenant_id
                    else "unknown",
                    user_id=cache_context.user_id
                    if cache_context and cache_context.user_id
                    else "unknown",
                    purpose=self._purpose,
                    source_ids=source_ids,
                    authorization_tags=self._authorization_tags,
                    trust=tuple(
                        DataTrustRequirement(
                            source_id=source,
                            completeness=1.0,
                            minimum_completeness=1.0,
                            certified=bool(_source_aliases(source) & self._certified_sources),
                        )
                        for source in source_ids
                    ),
                    estimate=QueryEstimate(
                        estimated_rows=self._max_rows,
                        estimated_bytes_scanned=self._max_result_bytes,
                        estimated_compute_seconds=self._statement_timeout_ms / 1000,
                    ),
                    query_fingerprint=fingerprint,
                )
            )

        async def load() -> dict[str, Any]:
            result = await self._execute_database(bounded, values)
            return {
                "columns": result.columns,
                "rows": result.rows,
                "truncated": result.truncated,
                "execution_ms": result.execution_ms,
            }

        if self._cache is not None and cache_context is not None:
            value, hit = await self._cache.get_or_set_json(
                "query_results",
                "postgresql.readonly_query",
                material,
                cache_context,
                load,
                tags=(f"data-version:{self._data_version}",),
            )
            result = QueryResult(
                columns=tuple(value["columns"]),
                rows=tuple(dict(row) for row in value["rows"]),
                truncated=bool(value["truncated"]),
                execution_ms=0.0 if hit else float(value["execution_ms"]),
                cache_hit=hit,
                query_fingerprint=fingerprint,
            )
            return result
        result = await self._execute_database(bounded, values)
        return QueryResult(
            columns=result.columns,
            rows=result.rows,
            truncated=result.truncated,
            execution_ms=result.execution_ms,
            cache_hit=False,
            query_fingerprint=fingerprint,
        )

    async def _execute_database(self, bounded: str, values: list[Any]) -> QueryResult:
        if self._pool is None:
            await self.start()
        started = monotonic()
        async with self._pool.acquire() as connection:
            async with connection.transaction(readonly=True):
                await connection.execute(
                    f"SET LOCAL statement_timeout = {self._statement_timeout_ms}"
                )
                records = await connection.fetch(bounded, *values)
        truncated = len(records) > self._max_rows
        selected = records[: self._max_rows]
        rows = tuple(
            {key: _json_value(value) for key, value in dict(record).items()} for record in selected
        )
        if len(json.dumps(rows, default=str).encode()) > self._max_result_bytes:
            raise WorkflowError("query result exceeds the configured response-size limit")
        columns = tuple(rows[0]) if rows else ()
        return QueryResult(
            columns=columns,
            rows=rows,
            truncated=truncated,
            execution_ms=(monotonic() - started) * 1000,
        )


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return "<binary>"
    return value


def _source_aliases(source: str) -> frozenset[str]:
    """Return comparable qualified and catalog source identities.

    The Markdown schema catalog intentionally stores unqualified table names while semantic
    models use schema-qualified PostgreSQL names. Governance must compare those two trusted
    registries canonically rather than treating qualification as a certification difference.
    """
    canonical = source.replace('"', "").strip().lower()
    if not canonical:
        return frozenset()
    return frozenset((canonical, canonical.rsplit(".", 1)[-1]))

