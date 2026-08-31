from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator

from agent_core.events.contracts import AuditEvent, RunEvent


class InMemoryEventBus:
    def __init__(self, history_limit: int = 1000) -> None:
        self._history: dict[str, list[RunEvent]] = defaultdict(list)
        self._subscribers: dict[str, set[asyncio.Queue[RunEvent]]] = defaultdict(set)
        self._history_limit = history_limit
        self._lock = asyncio.Lock()

    async def publish(self, event: RunEvent) -> None:
        async with self._lock:
            history = self._history[event.run_id]
            history.append(event)
            del history[:-self._history_limit]
            subscribers = tuple(self._subscribers[event.run_id])
        for queue in subscribers:
            queue.put_nowait(event)

    async def history(self, run_id: str, after: int = 0) -> tuple[RunEvent, ...]:
        async with self._lock:
            return tuple(event for event in self._history.get(run_id, ()) if event.sequence > after)

    async def subscribe(self, run_id: str, after: int = 0) -> AsyncIterator[RunEvent]:
        queue: asyncio.Queue[RunEvent] = asyncio.Queue(maxsize=1000)
        async with self._lock:
            self._subscribers[run_id].add(queue)
            backlog = tuple(
                event for event in self._history.get(run_id, ()) if event.sequence > after
            )
        try:
            for event in backlog:
                yield event
            while True:
                yield await queue.get()
        finally:
            async with self._lock:
                self._subscribers[run_id].discard(queue)


class InMemoryAuditLog:
    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._lock = asyncio.Lock()

    async def append(self, event: AuditEvent) -> None:
        async with self._lock:
            self._events.append(event)

    async def list(self, tenant_id: str, resource_id: str | None = None) -> tuple[AuditEvent, ...]:
        async with self._lock:
            return tuple(
                event
                for event in self._events
                if event.tenant_id == tenant_id
                and (resource_id is None or event.resource_id == resource_id)
            )
