# Production readiness backlog

This file is the short operational checklist for the example. The complete customer-data and platform analysis is in [customer_data_and_runtime_requirements.md](customer_data_and_runtime_requirements.md).

## Data and semantics

- Publish curated airline marts with normalized dates and authoritative agency, route, flight, and channel keys.
- Define and certify B2C, Series, Groups, campaign, inventory, capacity, yield, market-share, and revenue metrics.
- Configure the implemented governed join compiler against customer-approved relationships, then prove grain and fan-out safety with production fixtures.
- Populate and enforce the implemented freshness, completeness, ownership, lineage, sensitivity, SCD, and certification contracts with customer-owned metadata.

## Analytical methods

- Replace reference statistics with validated airline forecasting, attribution, simulation, and optimization services.
- Version and backtest deterministic and model-based methods.
- Define confidence, minimum sample, drift, and business acceptance thresholds.
- Build golden executive-question evaluation and regression datasets.

## Runtime and deployment

- Wire the implemented typed, resumable graph and semantic graph planner into the deployed worker lifecycle for workflows selected for distributed execution.
- Operate the implemented PostgreSQL worker leases, deduplication, checkpoints, and cross-process durable event polling with supervision, fencing, dead-letter handling, and reconciliation.
- Supply a warehouse-specific estimator to the implemented governed executor; tune resource pools, Redis caching, scan/compute quotas, and statement limits using load tests.
- Map authenticated roles and database RLS to the implemented row-, column-, metric-, tenant-, and purpose-level authorization contracts.
- Configure production schema administration, backups, retention, disaster recovery, and secrets.

## Governance and experience

- Convert customer fixtures into suites for the implemented numerical, claim-to-cell, SQL-property, and promotion-gate verification services.
- Connect the implemented export decisions to every dashboard SQL, table, chart, and download endpoint.
- Add human approval before an advisory recommendation becomes an operational action.
- Capture executive feedback and connect evaluation results to deployment promotion gates.
