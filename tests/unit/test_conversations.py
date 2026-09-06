from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

import pytest

from traccia_runtime.conversations import (
    CodeBlock,
    Conversation,
    ConversationHandlerRegistry,
    ConversationMessage,
    ConversationService,
    ConversationTurnResult,
    TableBlock,
)
from traccia_runtime.conversations.stores import (
    InMemoryConversationEventBus,
    InMemoryConversationStore,
)
from traccia_runtime.exceptions.errors import NotFoundError, TracciaRuntimeError
from traccia_runtime.types.contracts import Role, TextBlock


class Handler:
    name = "test"

    async def handle(
        self,
        conversation: Conversation,
        user_message: ConversationMessage,
        history: Sequence[ConversationMessage],
        emit: Any,
    ) -> ConversationTurnResult:
        del conversation, history
        await emit("agent.status.changed", {"status": "working"})
        return ConversationTurnResult(
            content=(
                TextBlock(text=f"Answer: {user_message.text_content}"),
                CodeBlock(language="sql", code="SELECT 1;"),
                TableBlock(columns=("value",), rows=({"value": 1},)),
            )
        )


def service() -> ConversationService:
    handlers = ConversationHandlerRegistry()
    handlers.register(Handler())
    return ConversationService(
        InMemoryConversationStore(), InMemoryConversationEventBus(), handlers
    )


async def test_conversation_turn_is_persisted_with_typed_content() -> None:
    conversations = service()
    conversation = await conversations.create(
        tenant_id="tenant-a", user_id="user-a", agent="agent", handler="test"
    )
    _, assistant = await conversations.submit(conversation.id, "tenant-a", "user-a", "show me")
    for _ in range(100):
        messages = await conversations.messages(conversation.id, "tenant-a")
        if messages[-1].status.value == "completed":
            break
        await asyncio.sleep(0.001)

    assert messages[-1].id == assistant.id
    assert [block.type for block in messages[-1].content] == ["text", "code", "table"]
    assert [event.type for event in await conversations.events.history(conversation.id)] == [
        "conversation.created",
        "conversation.message.accepted",
        "assistant.message.started",
        "agent.status.changed",
        "assistant.message.completed",
    ]


async def test_conversation_access_is_tenant_and_user_isolated() -> None:
    conversations = service()
    conversation = await conversations.create(
        tenant_id="tenant-a", user_id="user-a", agent="agent", handler="test"
    )
    with pytest.raises(NotFoundError):
        await conversations.submit(conversation.id, "tenant-b", "user-a", "no")
    with pytest.raises(NotFoundError):
        await conversations.submit(conversation.id, "tenant-a", "user-b", "no")


async def test_recovery_does_not_replay_interrupted_handler_side_effects() -> None:
    conversations = service()
    conversation = await conversations.create(
        tenant_id="tenant-a", user_id="user-a", agent="agent", handler="test"
    )
    pending = ConversationMessage(
        conversation_id=conversation.id,
        tenant_id="tenant-a",
        role=Role.ASSISTANT,
        content=(),
        status="streaming",
    )
    await conversations.store.append_message(pending)

    assert await conversations.recover() == 1
    messages = await conversations.messages(conversation.id, "tenant-a")
    assert messages[-1].status.value == "failed"
    assert messages[-1].metadata["error_type"] == "InterruptedResponse"


async def test_handler_runtime_errors_are_safe_but_actionable() -> None:
    class FailingHandler:
        name = "failing"

        async def handle(self, conversation, user_message, history, emit):
            del conversation, user_message, history, emit
            raise TracciaRuntimeError("Documented schema is insufficient for this query")

    handlers = ConversationHandlerRegistry()
    handlers.register(FailingHandler())
    conversations = ConversationService(
        InMemoryConversationStore(), InMemoryConversationEventBus(), handlers
    )
    conversation = await conversations.create(
        tenant_id="tenant-a", user_id="user-a", agent="agent", handler="failing"
    )
    await conversations.submit(conversation.id, "tenant-a", "user-a", "question")
    for _ in range(100):
        messages = await conversations.messages(conversation.id, "tenant-a")
        if messages[-1].status.value == "failed":
            break
        await asyncio.sleep(0.001)

    assert messages[-1].text_content == "Documented schema is insufficient for this query"


async def test_handler_can_return_a_traced_failed_outcome_with_run_ids() -> None:
    class BoundedFailureHandler:
        name = "bounded-failure"

        async def handle(self, conversation, user_message, history, emit):
            del conversation, user_message, history, emit
            return ConversationTurnResult(
                content=(TextBlock(text="Semantic validation failed"),),
                run_ids=("router-run", "repair-run"),
                outcome="failed",
            )

    handlers = ConversationHandlerRegistry()
    handlers.register(BoundedFailureHandler())
    events = InMemoryConversationEventBus()
    conversations = ConversationService(InMemoryConversationStore(), events, handlers)
    conversation = await conversations.create(
        tenant_id="tenant-a",
        user_id="user-a",
        agent="agent",
        handler="bounded-failure",
    )
    await conversations.submit(conversation.id, "tenant-a", "user-a", "question")
    for _ in range(100):
        messages = await conversations.messages(conversation.id, "tenant-a")
        if messages[-1].status.value == "failed":
            break
        await asyncio.sleep(0.001)

    assert messages[-1].run_ids == ("router-run", "repair-run")
    assert messages[-1].text_content == "Semantic validation failed"
    final_event = (await events.history(conversation.id))[-1]
    assert final_event.type == "assistant.message.failed"
    assert final_event.data["message"] == "Semantic validation failed"
