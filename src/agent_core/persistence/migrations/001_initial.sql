CREATE TABLE IF NOT EXISTS agent_core_runs (
    id text PRIMARY KEY,
    tenant_id text NOT NULL,
    version bigint NOT NULL DEFAULT 0,
    state jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_core_runs_tenant_idx ON agent_core_runs (tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS agent_core_events (
    id text PRIMARY KEY,
    run_id text NOT NULL REFERENCES agent_core_runs(id),
    tenant_id text NOT NULL,
    sequence bigint NOT NULL,
    event jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, sequence)
);

CREATE TABLE IF NOT EXISTS agent_core_audit_events (
    id text PRIMARY KEY,
    tenant_id text NOT NULL,
    event jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_core_audit_tenant_idx ON agent_core_audit_events (tenant_id, created_at DESC);

