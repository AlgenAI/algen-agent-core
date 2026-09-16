CREATE TABLE IF NOT EXISTS traccia_runtime_conversation_feedback (
    id text PRIMARY KEY,
    tenant_id text NOT NULL,
    user_id text NOT NULL,
    conversation_id text NOT NULL REFERENCES traccia_runtime_conversations(id),
    message_id text NOT NULL REFERENCES traccia_runtime_conversation_messages(id),
    feedback jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, user_id, message_id)
);
CREATE INDEX IF NOT EXISTS traccia_runtime_conversation_feedback_conversation_idx
    ON traccia_runtime_conversation_feedback (tenant_id, user_id, conversation_id, created_at);
