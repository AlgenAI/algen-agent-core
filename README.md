# Agent Core

Agent Core is a provider-neutral Python 3.12 runtime for configuration-driven agents. It exposes the same typed agent contract as a library and a FastAPI service, with deterministic state transitions, tool approvals, model fallback, streaming events, resumability, tenant isolation, and offline tests.

## Architecture

```mermaid
flowchart LR
  Client --> API[REST / SSE adapter]
  API --> Runtime[AgentRuntime state machine]
  Runtime --> Policies[Policy middleware]
  Runtime --> Context[ContextBuilder]
  Runtime --> Planner[Planner]
  Runtime --> Router[ModelRouter]
  Router --> Providers[Provider adapters]
  Runtime --> Executor[ToolExecutor]
  Executor --> Plugins[Tools / plugins / MCP adapters]
  Runtime --> Verifier[Verifier pipeline]
  Runtime --> Stores[Run / memory / artifact stores]
  Runtime --> Events[Events / audit / telemetry]
```

The domain layer contains immutable Pydantic contracts and protocols. Orchestration depends only on those protocols. The composition root resolves configured adapters and registries; provider SDK or wire details never enter the runtime.

```mermaid
stateDiagram-v2
  [*] --> received
  received --> validating
  validating --> building_context
  building_context --> planning
  planning --> invoking_model
  planning --> invoking_tool
  planning --> awaiting_clarification
  planning --> awaiting_approval
  invoking_model --> planning: tool calls
  invoking_model --> retrying: transient error
  invoking_tool --> building_context
  invoking_tool --> retrying: transient error
  retrying --> planning
  planning --> verifying
  verifying --> composing: valid
  verifying --> retrying: repairable
  composing --> completed
  received --> cancelled
  awaiting_approval --> planning: approved / modified
  awaiting_approval --> failed: rejected
  awaiting_clarification --> building_context: answered
  state terminal <<choice>>
  completed --> terminal
  failed --> terminal
  cancelled --> terminal
  timed_out --> terminal
```

No hidden reasoning is persisted. `ExecutionSummary` records concise decisions, call counts, retries, usage, versions, and trace identifiers. Reproduction requires the versioned agent definition, normalized request, persisted checkpoint, referenced prompt/config versions, and deterministic provider/tool fixtures.

## Quick start

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
pytest
AGENT_CORE_CONFIG=examples/minimal_agent/agent.yaml agent-core
```

Requests require identity headers so tenant isolation cannot be accidentally omitted:

```bash
curl -X POST http://localhost:8000/v1/runs \
  -H 'content-type: application/json' \
  -H 'x-tenant-id: demo' -H 'x-user-id: user-1' \
  -d '{"agent":"minimal","input":"Hello"}'
```

See [architecture](docs/architecture.md), [operations](docs/operations.md), [provider extension](docs/providers.md), [compatibility](docs/provider-compatibility.md), and [threat model](docs/threat-model.md).

