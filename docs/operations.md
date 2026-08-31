# Operations guide

Run `agent-core` with `AGENT_CORE_CONFIG` set to one or more OS-path-separated YAML files. Put secrets in the referenced environment variables. Use PostgreSQL for durable checkpoints and immutable events, Redis for expiring memory/rate coordination, and object storage behind `ArtifactStore` for large artifacts.

Health endpoints are `/health/live` and `/health/ready`. Export OpenTelemetry through a deployment-specific SDK exporter; content capture defaults off. Logs are JSON and redacted. Alert on run failure ratio, provider circuit openings, approval age, p95 model/tool latency, budget exhaustion, and event-store lag.

Use at least two workers behind a durable queue in production. A worker must claim one checkpoint version, heartbeat long calls, and rely on tool idempotency keys. Run database migration `src/agent_core/persistence/migrations/001_initial.sql` before switching to PostgreSQL.

Cancellation is cooperative for provider/tool adapters. Keep adapter timeouts lower than run deadlines. Graceful shutdown should stop accepting runs, cancel active tasks, flush telemetry, and leave resumable checkpoints.

Back up run, event, audit, approval, and artifact metadata according to retention policy. User deletion must remove tenant/session memory and authorized artifacts while preserving legally required, redacted audit records.

