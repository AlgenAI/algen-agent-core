from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from agent_core.types.contracts import AgentDefinition, Message, Role, RunState
from agent_core.types.interfaces import ContextBuilder, MemoryStore


@dataclass(frozen=True)
class ContextItem:
    message: Message
    source: str
    priority: int
    relevance: float = 1.0
    token_estimate: int = 0


class DefaultContextBuilder:
    name = "default"

    def __init__(self, memory: MemoryStore, max_estimated_tokens: int = 16_000) -> None:
        self._memory = memory
        self._max_tokens = max_estimated_tokens

    async def build(self, state: RunState, agent: AgentDefinition) -> Sequence[Message]:
        history = await self._memory.get(
            state.request.tenant_id,
            state.session_id,
            agent.memory_policy.max_items,
        )
        items = [Message.text(Role.SYSTEM, agent.system_instructions), *history]
        if not state.messages:
            items.append(Message.text(Role.USER, state.request.input))
        else:
            items.extend(state.messages)
        return self._pack(items)

    def _pack(self, messages: Sequence[Message]) -> tuple[Message, ...]:
        selected: list[Message] = []
        remaining = self._max_tokens
        for message in reversed(messages):
            estimate = max(1, len(message.text_content) // 4)
            if estimate > remaining and selected:
                continue
            selected.append(message)
            remaining -= min(estimate, remaining)
            if remaining <= 0:
                break
        return tuple(reversed(selected))


class ContextBuilderRegistry:
    def __init__(self, default: DefaultContextBuilder) -> None:
        self._items: dict[str, ContextBuilder] = {default.name: default}

    def register(self, builder: ContextBuilder) -> None:
        self._items[builder.name] = builder

    def get(self, name: str) -> ContextBuilder:
        try:
            return self._items[name]
        except KeyError as exc:
            raise KeyError(f"context builder {name!r} is not registered") from exc
