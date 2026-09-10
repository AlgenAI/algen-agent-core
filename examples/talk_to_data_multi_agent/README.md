# Talk to Data multi-agent example

## What this example demonstrates

This example provides a conversational, governed analytics application over read-only PostgreSQL. A human asks a business question, the workflow resolves intent, requests only essential clarification, plans bounded evidence gathering, generates and validates SQL, optionally executes it, applies an approved analytical method, verifies the claims, and returns typed dashboard content.

The implementation is deliberately split between:

- **Traccia Runtime**, which supplies reusable conversations, orchestration, state, model routing, policies, persistence, streaming, telemetry, and audit.
- **The application**, which supplies airline schema knowledge, semantic definitions, analytical methods, prompts, UI behavior, and business acceptance tests.

The current implementation is a development reference, not a complete airline revenue-management product. Its current capabilities cover a subset of B2C, flown performance, inventory, schedule, and competitor-fare analytics. The V2 documents describe the additional data and runtime capabilities required for the broader B2C, Series, Groups, and Offer Management question catalogue.

## Folder structure

```text
talk_to_data_multi_agent/
├── README.md                         # This setup, architecture and extension guide
├── __init__.py
├── app.py                            # CLI entry point and shared path constants
├── dashboard.py                      # FastAPI dashboard composition and handler
├── application/                      # Airline-specific Python implementation
│   ├── __init__.py
│   ├── analytics_tools.py            # Deterministic analytical method plugins
│   ├── analytics_workflow.py         # Router/planner/SQL/analysis/insight flow
│   ├── catalog.py                    # Markdown schema parser and RAG documents
│   ├── query_executor.py             # Bounded read-only PostgreSQL execution
│   └── workflow.py                   # Smaller intent-to-SQL CLI workflow
├── config/
│   ├── agent.yaml                    # Providers, storage, retrieval, agents and telemetry
│   └── semantic_layer.yaml           # Governed airline metrics and dimensions
├── data/
│   └── schema/
│       ├── database_context.md       # Primary governed NL2SQL/data context
│       └── raw_tables_documentation.md # Older source retained for comparison
├── database/
│   └── migrations/
│       └── 001_schema_embeddings.sql # Reference pgvector schema
├── docs/
│   ├── customer_data_and_runtime_requirements.md
│   ├── production_pending.md
│   └── architecture/
│       ├── v1/                       # Current development architecture
│       │   ├── agent_interaction_flow.html
│       │   ├── detailed.html
│       │   └── high_level.html
│       └── v2/                       # Target architecture for the customer catalogue
│           ├── detailed.html
│           ├── detailed_animated.html
│           ├── detailed_new.html     # Layered, ownership-aware interactive view
│           └── high_level.html
└── ui/
    └── dashboard.html                # Self-contained ChatGPT-style test interface
```

Python domain code belongs in `application/`; configuration and customer data should not be mixed with executable modules. Keep future architecture and operational documentation under `docs/`, browser assets under `ui/`, and database-administration assets under `database/`.

## Current architecture

One dashboard turn follows this bounded flow:

```mermaid
flowchart LR
    U[Human question] --> R[Router agent]
    S[(Semantic layer)] --> R
    C[(Schema RAG in pgvector)] --> R
    R -->|material ambiguity| Q[Persisted clarification]
    Q --> R
    R --> P[Planner agent]
    P -->|invalid semantic plan| RP[One bounded repair]
    RP --> P
    P --> X[Up to 3 parallel SQL agents]
    C --> X
    S --> X
    X --> G[Deterministic SQL guard]
    G --> D[(Read-only PostgreSQL)]
    D --> A[Application analytical plugin]
    A --> I[Insight agent]
    I --> V[Verifier agent]
    V --> O[Typed conversational response]
```

The model interprets, plans, generates SQL, and composes language. It never directly executes SQL or generated code. PostgreSQL permissions, SQL validation, semantic contracts, deterministic analytical plugins, model-service gates, and verification determine what runs and what may be claimed.

Current diagrams:

- [High-level V1 architecture](docs/architecture/v1/high_level.html)
- [Detailed V1 architecture](docs/architecture/v1/detailed.html)
- [Current agent interaction flow](docs/architecture/v1/agent_interaction_flow.html)

V2 architecture based on the broader customer question catalogue and implemented Runtime services:

- [High-level V2 architecture](docs/architecture/v2/high_level.html)
- [Detailed V2 architecture](docs/architecture/v2/detailed.html)
- [Layered interactive V2 architecture](docs/architecture/v2/detailed_new.html)
- [Animated V2 question walkthrough](docs/architecture/v2/detailed_animated.html)
- [Customer data and runtime requirements](docs/customer_data_and_runtime_requirements.md)

## Current behavior and limits

- At most one question is asked per turn, with two clarification turns by default.
- Ordinary joins, aggregations, ordering, and optional filters should be inferred when governed definitions make them unambiguous.
- Planner metrics and dimensions must match exact semantic names. One repair is attempted for an invalid semantic plan.
- Semantic models and metrics carry independent certification and sensitivity levels. The dashboard analytical workflow accepts `verified` or `certified` selections by default and rejects `draft` selections.
- Point-in-time models declare mandatory filter dimensions. Inventory and competitor-history queries must constrain their capture date.
- Model default filters are enforced by both the semantic compiler and the generated-SQL gate. Current competitor queries cannot omit or override `is_current = true`.
- SQL output is one parameterized PostgreSQL `SELECT` or `WITH` statement.
- The deterministic guard rejects writes, multiple statements, comments, dangerous functions, undocumented tables, mismatched parameters, semantic source substitution, and missing governed filters.
- SQL agents can fan out to at most three independent query tasks.
- The dashboard executes queries only when a separate read-only query DSN is supplied.
- Query execution is bounded by timeout, row count, and serialized result size.
- Query execution is gated by Runtime purpose, authorization, source-certification, workload-quota,
  SQL-visibility/download policy, and authorization-aware fingerprint controls.
- Schema retrieval, exact short-lived model responses, and read-only query results use scoped caches;
  in-flight request coalescing prevents identical concurrent misses from stampeding dependencies.
- Descriptive summaries, anomaly screens, a linear baseline, constant-volume fare sensitivity, capacity screening, correlation diagnostics, and opportunity ranking are application plugins registered through the Runtime's versioned analytical-method registry.
- Adding flights, changing schedules, changing fare-class availability, and behavioral fare response are gated when validated causal, demand, cost, and optimization models are unavailable.
- SQL outputs are represented as independent `AnalyticalResult` handles with source/column lineage,
  semantic version, metric versions, and query fingerprint before the selected method runs. Runtime
  `SemanticQueryGraphPlanner` compiles independent governed queries into `AnalyticalGraph` nodes;
  the graph supports dependent joins, comparisons, calculations, models, ranking, verification, and
  composition. Compound semantics that have not been explicitly lowered fail closed.
- Forecast, simulation, causal, and optimization endpoints use the Runtime model-service registry,
  not LLM provider or prompt contracts. Missing, unhealthy, drifted, or unapproved models block safely.
- PostgreSQL graph checkpoints and leased work queues are available for deployed workers; this example
  executes turns in-process by default for straightforward local development.

## Model allocation

The example deliberately uses different model tiers by responsibility:

| Agent | Default model | Reason |
|---|---|---|
| `talk-to-data-router` | `gpt-5-mini` | Bounded classification, semantic-reference selection, and clarification |
| `talk-to-data-intent` | `gpt-5-mini` | Legacy intent resolution over retrieved schema evidence |
| `talk-to-data-planner` | `gpt-5` | Multi-query decomposition, sufficiency checks, and analytical-method selection |
| `talk-to-data-sql` | `gpt-5` | High-risk SQL generation involving grain, snapshots, joins, and PostgreSQL semantics |
| `talk-to-data-insight` | `gpt-5-mini` | Constrained presentation of deterministic results |
| `talk-to-data-verifier` | `gpt-5` | Independent review of claims, evidence, uncertainty, and method limits |

Treat this as an evaluated starting point, not a universal rule. Promote another role to a smaller
model only after its golden-question results meet the same plan-validity, SQL-correctness, clarification,
claim-grounding, latency, and cost thresholds. Agent versions must change when model allocation changes.

## Prerequisites

- Python 3.12 or newer.
- PostgreSQL with pgvector for runtime persistence and schema retrieval.
- An OpenAI API key for the configured model and embedding provider.
- A Traccia API key when Traccia export is enabled.
- A separately permissioned analytics PostgreSQL account if query execution is enabled.

Install the example dependencies:

```bash
pip install -e '.[dev,talk-to-data]'
```

Start the disposable local PostgreSQL service:

```bash
docker compose up -d postgres
```

Set development environment variables:

```bash
export OPENAI_API_KEY='...'
export TRACCIA_API_KEY='tr_live_...'
export PGVECTOR_DSN='postgresql://traccia:local-only@localhost:5432/traccia_runtime'
```

The password above is only for the local Compose service. Use a secret manager in deployed environments.

To work without Traccia export, disable it explicitly:

```bash
export TRACCIA_RUNTIME__TELEMETRY__TRACCIA__ENABLED=false
```

## Index the schema documentation

Index the bundled customer schema before running the agents:

```bash
python -m examples.talk_to_data_multi_agent.app --index
```

Index a different schema document:

```bash
python -m examples.talk_to_data_multi_agent.app \
  --schema /absolute/path/to/schema_documentation.md \
  --index
```

Re-index after the schema document changes. The example replaces its logical schema sections for the configured tenant.

## Run the CLI workflow

The CLI uses the smaller intent-to-SQL workflow and prints validated PostgreSQL without executing it:

```bash
python -m examples.talk_to_data_multi_agent.app \
  "Show current competitor fares by sector for flights departing next month"
```

Use `--max-clarifications 0..5` to override the CLI clarification limit.

## Run the dashboard workflow

Without `TALK_TO_DATA_QUERY_DSN`, the dashboard generates and displays validated SQL but does not run it:

```bash
python -m examples.talk_to_data_multi_agent.dashboard
```

Open [http://localhost:8090](http://localhost:8090).

The example enables presentation features in `config/agent.yaml`:

```yaml
feature_flags:
  talk_to_data_show_response_time: true
  talk_to_data_show_progress: true
  talk_to_data_show_charts: true

conversation_presentation:
  progress_audience: business
  error_audience: business
  show_technical_details: false
  technical_details_expanded: false
```

`talk_to_data_show_response_time` stores and displays the end-to-end handler time on
the assistant message. `talk_to_data_show_progress` streams safe workflow-stage
summaries (understand, plan, generate SQL, execute, analyze, compose, and verify) and
stores the completed list with the response so it remains available after refresh.
Disable either flag independently. Progress descriptions are operational summaries;
they do not contain model chain-of-thought, prompts, query results, or credentials.

`conversation_presentation` is a reusable Traccia Runtime policy rather than a UI-only switch.
Business mode uses outcome-oriented progress and safe errors. Developer mode exposes component-level
progress and actionable validation errors. Technical SQL and raw tables are hidden by default; when
`show_technical_details` is enabled, they are returned in a typed `details` block that this dashboard
renders collapsed unless `technical_details_expanded` is explicitly enabled. Full diagnostics,
traces, and audit evidence remain available to authorized operators regardless of presentation mode.

Enable controlled execution with a different, read-only database identity:

```bash
export TALK_TO_DATA_QUERY_DSN='postgresql://dashboard_reader:...@db:5432/analytics'
python -m examples.talk_to_data_multi_agent.dashboard
```

Override the port when necessary:

```bash
TALK_TO_DATA_PORT=8091 python -m examples.talk_to_data_multi_agent.dashboard
```

Architecture pages are also served by the dashboard:

- `http://localhost:8090/architecture/agents`
- `http://localhost:8090/architecture/v2/high-level`
- `http://localhost:8090/architecture/v2/detailed`
- `http://localhost:8090/architecture/v2/detailed-new`
- `http://localhost:8090/architecture/v2/animated`

## Conversation API

Create a conversation:

```bash
curl -sS http://localhost:8090/v1/conversations \
  -H 'content-type: application/json' \
  -H 'x-tenant-id: talk-to-data-example' \
  -H 'x-user-id: executive-123' \
  -d '{"agent":"talk-to-data","handler":"talk-to-data"}'
```

Submit a message:

```bash
curl -sS http://localhost:8090/v1/conversations/CONVERSATION_ID/messages \
  -H 'content-type: application/json' \
  -H 'x-tenant-id: talk-to-data-example' \
  -H 'x-user-id: executive-123' \
  -d '{"text":"Which routes show sustained high load factor and yield by weekday?"}'
```

Consume `/v1/conversations/{id}/events` as fetch-based SSE and reconnect with `Last-Event-ID`. Browser `EventSource` is unsuitable when bearer authorization headers are required.

Responses contain typed blocks:

| Type | Intended rendering |
|---|---|
| `text` | Assistant narrative |
| `code` | SQL with syntax highlighting and copy control |
| `notice` | Assumption, warning, limitation, error, or execution status |
| `table` | Sortable/export-controlled result grid |
| `chart` | Vega-Lite visualization |

### Interactive visualizations

The example enables `talk_to_data_show_charts` by default. Its domain-owned
`AirlineVisualizationPlanner` chooses a chart from the governed query task, result schema, and
deterministic analysis outcome. It currently produces ranked flight-inventory bars,
baseline-versus-scenario revenue comparisons, load-factor/yield scatter plots, time-series lines,
and categorical comparisons. Charts are supplemental; enable `show_technical_details` to include the
complete accessible result table in the collapsed technical disclosure.

Traccia Runtime validates every `ChartBlock` as bounded inline Vega-Lite before persistence or API
delivery. External data URLs, links, expression transforms, embed metadata, oversized payloads, and
more than 1,000 inline rows are rejected. The example further limits rendered points to 50.

The demonstration HTML loads Vega, Vega-Lite, and Vega-Embed from versioned jsDelivr URLs, renders
responsive SVG with tooltips, and permits PNG/SVG export. If those browser dependencies cannot load,
it falls back to a simple local bar rendering and the accessible table. Production dashboards should
bundle these JavaScript dependencies into their own audited frontend build and Content Security
Policy rather than depending on a public CDN.

A clarification is an ordinary assistant message with `metadata.kind: clarification`. The next human message resumes the persisted workflow state.

## Critical configuration values

Review these values for every deployment or new domain example. Do not copy them unchanged into production.

| Location | Value | What must be managed |
|---|---|---|
| `app.py` | `TENANT_ID` | Replace the example tenant convention or derive it exclusively from authenticated deployment context. |
| `config/agent.yaml` | Provider model names and allowlists | Select approved models and capabilities; define fallbacks, cost limits, latency limits, rate limits, and secret references. |
| `config/agent.yaml` | Agent names and versions | Increment versions when prompts, contracts, tools, or behavior materially change. Keep names globally stable. |
| `config/agent.yaml` | Token, output, cost, step and latency budgets | Tune from observed workloads and enforce organization limits. Larger budgets do not fix invalid plans or missing data. |
| `config/agent.yaml` | `PGVECTOR_DSN` and storage choices | Use managed secrets, separate environments, explicit schema administration, backups, retention, and pool sizing. |
| `config/agent.yaml` | Retrieval table, embedding model and dimensions | Use a unique table per logical corpus where appropriate. Rebuild the index when model or dimensions change. |
| `config/agent.yaml` | Retrieval limits, score weights and token limits | Evaluate against real schema questions; excessive context increases cost and can reduce SQL precision. |
| `config/agent.yaml` | Cache backend, scope, TTL and maximum value bytes | Use Redis across replicas. Keep RLS-dependent retrieval/results at user scope, use short query TTLs, and invalidate on governed data releases. |
| `config/agent.yaml` | `analytical_execution` and `distributed_execution` | Use PostgreSQL graph/checkpoint and queue backends across replicas. Set lease length above normal node latency, renew long work, and supervise workers. |
| `config/agent.yaml` | `query_governance` | Define permitted purposes, trust/certification, row/scan/compute quotas, concurrency, SQL visibility, downloads, artifact exports and audit retention. |
| `config/agent.yaml` | `retrieval.*.max_query_chars` | Bound text sent to the embedding provider. Workflow calls set `retrieval_query` to the human question or resolved intent so serialized semantic definitions are not embedded. |
| `config/agent.yaml` | CORS origins | Allow only actual dashboard origins. Never use unrestricted credentialed CORS. |
| `config/agent.yaml` | Traccia project, environment, sampling and content flags | Use distinct project/environment identity. Decide whether conversation content may leave the process and configure redaction/retention accordingly. |
| `config/agent.yaml` | Talk-to-data response time and progress flags | Enable only the UI detail the deployment should expose. Keep progress events limited to safe stage summaries; never emit private model reasoning. |
| `config/agent.yaml` | `conversation_presentation` | Default to business audiences and hidden technical details. Enable developer errors, SQL, and raw tables only for authorized diagnostic deployments. |
| `config/semantic_layer.yaml` | Name, version, owner and certification | Treat this as a governed business contract. Increment the version and rerun evaluations after changes. |
| `config/semantic_layer.yaml` | Metric expressions, grains, joins and units | Obtain data-owner approval. Validate fan-out, nulls, currency, dates, cancellations, snapshots and non-additive measures. |
| `config/semantic_layer.yaml` | `default_filters` and `required_filter_dimensions` | Use default filters for immutable source rules such as current-version selection. Require explicit filters for point-in-time or high-volume historical sources. These rules are enforced, not merely prompt guidance. |
| `config/semantic_layer.yaml` | Certification and sensitivity | The dashboard requires `verified` selections and permits up to `confidential` by default. Keep unresolved metrics as `draft`; lower sensitivity or promote certification only after owner approval and regression tests. |
| `dashboard.py` | Clarification limit and suggestions | Tune using conversational evaluation; suggested questions must only advertise supported capabilities. |
| `application/analytics_tools.py` | Method manifests and implementations | Version methods, declare schemas/data/history/model requirements, attach validation/backtest evidence, and require approval where needed. |
| Runtime model-service registry | Predictive/prescriptive services | Pin validated versions; configure input/output schemas, health, drift, feature lineage, timeouts, fallbacks and confidence behavior. |
| `application/query_executor.py` | Timeout, maximum rows and result bytes | Set warehouse-specific limits and pair them with database roles, RLS, statement policy and resource quotas. |
| `application/analytics_tools.py` | Method thresholds and versions | Move business thresholds into governed configuration. Backtest and version every analytical method. |
| Environment | `TALK_TO_DATA_QUERY_DSN` | Must be a separate read-only identity scoped to approved views. It should not own runtime or indexing tables. |
| Environment | `TALK_TO_DATA_DATA_VERSION` | Change on a governed data/schema release to prevent reuse of query results from an older version. `live` still relies on the short query-result TTL. |

For local development, `agent.yaml` selects the bounded in-memory cache. To use Redis without editing
the file:

```bash
export TRACCIA_RUNTIME_REDIS_URL='redis://localhost:6379/1'
export TRACCIA_RUNTIME_CACHE_KEY_SECRET='replace-with-secret-manager-value'
export TRACCIA_RUNTIME__CACHE__BACKEND=redis
export TRACCIA_RUNTIME__CACHE__REDIS_URL='env://TRACCIA_RUNTIME_REDIS_URL'
export TRACCIA_RUNTIME__CACHE__KEY_SECRET='env://TRACCIA_RUNTIME_CACHE_KEY_SECRET'
export TALK_TO_DATA_DATA_VERSION='schema-1.2.0-data-2026-09-06'
```

The example uses tenant scope for schema retrieval, user scope for exact model and analytical query
results, and global scope only for model capability metadata. Query-result TTL is 60 seconds so a
dashboard refresh or repeated question is fast without presenting long-stale operational data. Tune
this against source refresh cadence and never broaden scope merely to improve hit rate.

### Analytical tool contracts

Every application-owned analytical tool registers a typed Pydantic parameter model in
`application/analytics_tools.py`. The planner receives those schemas and must provide
all required parameters with their canonical names. Parameters ending in `_column`
must match a declared query output alias, and generated SQL must explicitly project
every declared output column.

The workflow performs three checks before accepting an analytical result:

1. Validate the planned tool parameters and selected query output contract.
2. Validate or repair generated SQL aliases before database execution.
3. Compare actual database columns with the contract and reject truncated analytical
   inputs.

If deterministic analysis returns `insufficient_data`, `requires_model`, or another
non-completed state, the workflow returns that bounded result directly. It does not ask
an insight model to reinterpret sample rows or independently calculate an answer.

## Adapting the example to another customer or domain

1. Copy the example into a new example directory; do not add customer concepts to `src/traccia_runtime`.
2. Replace the schema document under `data/schema/` and validate that every table and join is fully documented.
3. Create a domain semantic layer under `config/` with stable names, definitions, owners, certification, permissions, and versions.
4. Define application-owned analytical methods and register them in the dashboard composition root.
5. Rename and version the agents, update their prompts, and constrain model/tool allowlists.
6. Allocate unique persistence, retrieval, telemetry project, tenant, and environment identifiers.
7. Replace example header identity with real authentication and enforce database RLS or equivalent controls.
8. Create golden question tests covering intent, clarification, plan, SQL properties, numerical results, claims, safety, and regressions.
9. Document unsupported scenarios and return explicit data/model requirements rather than speculative answers.
10. Validate load, cancellation, recovery, tracing, privacy, query cost, and business acceptance before deployment.

## Telemetry

Each user message is represented as one conversation-turn trace. Specialist agent runs are child spans, and turns share the conversation ID as the Traccia session identifier. Model calls, retrieval, query execution, analytical methods, verification, usage, cost, prompt versions, semantic versions, and cache operations remain correlated. Cache spans record hit/miss/error and timing but never keys or values.

`telemetry.include_content: false` prevents general model/tool payload capture. The example separately enables capped, redacted conversation content. Set `TRACCIA_RUNTIME__TELEMETRY__INCLUDE_CONVERSATION_CONTENT=false` when conversation text must remain inside the process.

Never log database credentials, access tokens, raw sensitive result sets, private reasoning, or unrestricted prompts.

## Production readiness

The major outstanding controls are summarized in [production_pending.md](docs/production_pending.md). For the broader customer question catalogue, use [customer_data_and_runtime_requirements.md](docs/customer_data_and_runtime_requirements.md) as the data and platform backlog.

At minimum, production requires normalized keys and dates, certified metrics, row/column authorization, freshness and lineage enforcement, distributed workers, warehouse cost controls, evaluated analytical methods, business regression datasets, model monitoring, and approval before externally visible actions.
