# Customer Data and Runtime Requirements

## Purpose and conclusion

The customer question catalogue covers Normal B2C, Series, Groups, and Offer Management through both agency and sector lenses. The expanded [`database_context.md`](../data/schema/database_context.md) materially improves readiness: it verifies the live PostgreSQL schema, source grains, join paths, date semantics, category values, current-versus-history behavior, privacy constraints, and code-backed metric formulas for the most relevant relations.

The new context is enough to begin governed descriptive analysis for selected B2C, agency, sector, group-pipeline, inventory, competitor-fare, offer-definition, schedule, and operations questions. It is **not** enough for the complete catalogue. Series data is absent; campaign exposure and redemption are absent; several cross-domain identities and business definitions are unresolved; and reliable predictive, causal, economic, and optimization questions require additional data products and validated models.

Traccia Runtime should orchestrate, secure, trace, and verify the analysis. It should not become the airline data warehouse or contain airline business logic. Customer data must first be made available through curated, read-only analytical models; airline-specific calculations then belong in the example's semantic configuration and analytical plugins.

## Additional data required from the customer

This assessment uses the live-schema evidence in `database_context.md`. It does not count a table as sufficient merely because similarly named columns exist. A production-ready source also needs a governed grain, stable keys, historical behavior, business meaning, freshness, ownership, and access policy.

Status meanings:

- **Available:** the context verifies a usable source for the stated scope; customer certification is still required.
- **Partial:** relevant data exists, but missing history, keys, semantics, or governance limits the questions it can answer.
- **Missing:** no verified source supports the requirement.
- **Conditional:** needed only if the customer expects that predictive, causal, market-wide, or optimization capability.

| Status | Data product | What the new database context verifies | What the customer still needs to provide or approve | Why the remaining requirement matters |
|---|---|---|---|---|
| **Partial — required** | Agency master and hierarchy | A normalized current hierarchy exists: `zones → territories → pos → agencies`. Revenue-tracker and ancillary daily facts also carry hierarchy keys. | A governed `pnr_flight.point_of_sale` → agency/POS mapping with collision and unmapped-value rules; approved channel taxonomy; effective-dated hierarchy/ownership history; rules for agency-level disclosure and suppression. | The PNR relationship is currently an inferred text match and current hierarchy cannot reconstruct historical ownership. Cross-domain agency trends may otherwise be misattributed. |
| **Partial — required** | Route, schedule, and flight-instance master | Schedule definitions, schedule times, sector reference data, inventory flight dates, PNR flight fields, and uploaded operations extracts exist. | A stable route ID and normalized flight-instance ID; canonical flight-number/sector normalization; schedule version and overlap precedence; timezone; aircraft/tail assignment; physical capacity; cancellation and operated-flight status. | A schedule definition is not an operated flight. Stable flight instances are necessary for safe joins, final outcomes, scenarios, and auditability. |
| **Missing — required** | Business calendar and reference dimensions | Ordinary dates and limited schedule/sector references exist. No shared calendar, holiday/event calendar, airport master, timezone table, currency-rate table, or governed status lookup was found. | A governed date dimension with fiscal periods, weekdays, holidays/events, comparable-period and same-DTD mappings; airport/market/timezone reference; currency and FX policy; status code dictionaries. | Executive periods, seasonality, event effects, local departure time, comparable windows, currency aggregation, and readable status filters cannot be governed without these references. |
| **Partial — required** | B2C booking, revenue, and passenger lifecycle | `pnr_flight` supports the code-backed displayed sales measures; `rbd_daily`, `sales_metrics_*`, `rt_agency_*`, and `anc_agency_sector_daily` provide supplementary operational and rollup views. | Booking/segment event history; payment, reissue, cancellation, refund, no-show, and passenger-status events; a governed booked-versus-flown-versus-recognized reconciliation; currency and revenue-component definitions; valid-date/data-quality guarantees. | Current facts support selected descriptive totals and booking curves, but not a complete commercial lifecycle, settlement view, or reliable final realized outcome. |
| **Partial — required** | Flight inventory and fare-class history | `pp_fare_class_history` is a true point-in-time capture source. `pp_inventory` and `pp_rbd_inventory_daily` provide current RBD state; `pp_rbd_fares` provides the fare ladder. | Authoritative physical capacity and its relation to authorized/RBD capacity; stable flight-instance linkage; explicit open/close/reopen event history or certified derivation rules; snapshot cadence, completeness, retention, and late-arrival SLA; interpretation of repeated flight totals and null values. | Historical inventory enables same-DTD analysis, but missing captures are not zero and current upserted sources cannot alone prove when a class changed state. |
| **Missing — required for Series scope** | Series agreement master | No verified Series entity exists. | Agreement ID/version, agency, route/flight applicability, validity, cadence, committed seats, contracted fare/currency, release rules, lifecycle status, and created/approved timestamps. | Series revenue, secured base load, expiring commitment, and contracted economics cannot be inferred from fare type, group requests, or chart terminology. |
| **Missing — required for Series scope** | Series commitment and consumption history | No verified commitment, release, utilization, or Series-to-booking relation exists. | Agreement/commitment ID, flight instance, allotted/consumed/released/cancelled seats, event timestamps, linked booking/segment, realized fare, no-show and departure outcome. | Agreement headers alone cannot calculate utilization, unconsumed exposure, release timing, or realized Series contribution. |
| **Partial — required** | Group request, quote, and outcome history | `group_requests` contains current request status, pax, quoted/approved values and hierarchy snapshots; `group_request_routes` contains legs and fares; group rollups and advance-paid PNR receipts exist. | Status-transition, quote-version, negotiation, approval and expiry event history; definitive accepted-request → booking/PNR linkage; outcome/rejection reasons; status dictionary; currency/fare-unit rules; timestamp/date quality and hierarchy reconciliation. | Current-state upserts can answer current pipeline questions, but cannot reconstruct time-to-stage, historical funnel leakage, quote changes, conversion attribution, or final realized economics. |
| **Partial — required for Offer Management scope** | Campaign and offer definition | `rm_offers` stores versioned flight/sector targets, agency/territory targeting, validity, publish time, and face-value amount. | Decide whether an offer belongs to a broader campaign; campaign ID/version, objective, eligibility, owner, approval, budget, channel and policy version where required; approve amount units and empty-target semantics. | An offer definition describes an intended intervention, not its delivery, cost, response, or business outcome. |
| **Missing — required for Offer Management outcomes** | Offer exposure, response, redemption, and cost | Offer definitions and send-processing evidence exist, but no governed exposure, view/acknowledgement, participation, redemption, payout, attributed-booking, or experiment assignment exists. | Immutable recipient/exposure events, channel and timestamp, eligibility, response/redemption, linked booking/segment, incentive payout/cost, treatment/control assignment, attribution window and consent/retention rules. | Uptake, ROI, incremental sales, and causal uplift require knowing who was eligible, exposed, responded, redeemed, and generated the outcome. |
| **Partial — required** | Authoritative capacity and operated-flight outcome | Inventory/FSI data and versioned operations uploads provide partial capacity, sales, delay, and OTP signals. | A final flight-instance ledger with aircraft/tail, physical capacity, cancellation/diversion status, boarded passengers, final flown revenue, final LF/yield, source precedence, and correction history. | Authorized capacity can differ from physical capacity, and uploaded/current extracts do not form a complete final operated-flight record. |
| **Partial — required** | Governed semantic metrics and thresholds | The context documents code-backed formulas for displayed tickets, revenue, yield, RT contribution, groups, inventory, FSI, and operations, with grains and warnings. | Data-owner sign-off on formulas, units, date lens, inclusions/exclusions, currency, authoritative source, certification state, X/Y/Z materiality thresholds, minimum samples, confidence rules, freshness SLAs, and reconciliation tolerances. | Documentation of observed code behavior is evidence, not business certification. Executive answers must not invent meanings for “yield,” “risk,” “market share,” “successful,” or “material.” |
| **Missing — required for production governance** | Data ownership, quality, privacy, and service-level metadata | The context identifies sensitive columns and current technical limitations; the live schema has no relation or column comments and no curated analytical views. | Named data/metric owners; source-of-truth matrix; freshness and completeness SLAs; quality tests and quarantine rules; sensitivity classification; row/column access scopes; retention/deletion policy; small-group suppression limits; incident and correction process. | The runtime can enforce declared policy, but it cannot infer accountability, acceptable staleness, data quality, or lawful access from DDL. |
| **Missing — conditional** | External market denominator | Internal contribution and target metadata exist; no verified market-wide fact exists. | Total market passengers, revenue and/or capacity by route and period, provider/source, coverage, methodology, revisions, and currency. | Internal Fly91 contribution must not be labelled external airline market share. |
| **Partial — conditional** | Forecast baseline and demand signals | Booking events/DTD fields, point-in-time inventory, competitor history, schedules, and an FSI revenue-gap heuristic exist. A prediction table exists but is empty and external forecast responses are not persisted. | Sufficient retained history; cancellations; calendar/events; disruptions; capacity/schedule revisions; certified feature definitions; persisted forecast output with model/version/training window/as-of time/confidence; accuracy and drift thresholds. | The current FSI output is a deterministic heuristic, not a trained demand forecast. Production anomaly and opportunity claims need reproducible, backtested forecasts. |
| **Missing — conditional** | Flight economics | No verified full flight-cost model exists. | Variable operating, fuel, airport/handling, crew and incremental-frequency costs; contribution rules; currency; aircraft/fleet assumptions; source and validity dates. | Revenue or demand alone cannot determine whether added capacity or another flight is economically attractive. |
| **Missing — conditional** | Intervention and experiment history | No treatment/control assignment or outcome ledger exists. | Eligibility, treatment/control assignment, intervention timestamp, policy/model version, counterfactual method, observed outcome, exclusions and sample-size rules. | Before/after movement does not establish that an intervention caused an improvement. |
| **Missing — conditional** | Demand, choice, and network-model inputs | No governed elasticity, spill/recapture, choice-set, connection, competitor-response, fleet/slot, or model-version dataset exists. | Calibrated elasticity and choice models; spill/recapture; connection flows; competitor response; aircraft rotations, slots and operational constraints; versioned model artifacts and validation results. | Fare, RBD, schedule, and capacity “what-if” questions require calibrated simulation or optimization, not an LLM estimate. |

### Is the new database context enough?

It is enough for an initial, bounded production pilot **only after business-owner certification** of the relevant sources and definitions. The pilot should be limited to:

- Descriptive B2C sales, tickets, yield, ancillary revenue, and internal contribution by supported time, sector, and authorized hierarchy dimensions.
- Current group pipeline, requested/approved values, group legs, and advance receipts, without historical stage-duration or definitive request-to-booking attribution.
- Current and historical inventory position, fare classes, and competitor fares, with explicit snapshot and missing-data caveats.
- Current offer definitions and targeting, without claims about exposure, redemption, ROI, or uplift.
- Schedule and available operations/OTP analysis where the selected upload and flight matching are explicit.

The context is still insufficient for Series questions, external market share, complete commercial lifecycle analysis, campaign outcomes, causal attribution, authoritative final flight economics, trained forecasts, or prescriptive fare/RBD/schedule/capacity optimization.

### Minimum customer response package

The customer does not need to rewrite `database_context.md`. They should supply or approve the following governed artifacts:

1. A signed metric contract and status/category dictionary that resolves the open questions listed in `database_context.md`.
2. Conformed-key specifications for POS-to-agency, route/sector, flight instance, schedule version, and group-to-booking relationships, including collision and unmatched-key handling.
3. Data contracts or DDL for the missing calendar/reference, Series, lifecycle-event, offer-outcome, and operated-flight products applicable to the agreed scope.
4. A source certification matrix naming each owner, authoritative source, grain, history behavior, refresh SLA, completeness threshold, correction policy, sensitivity class, retention, and permitted audience.
5. Representative anonymized fixtures and expected answers for the highest-priority executive questions, including no-data and ambiguous cases.
6. For predictive or prescriptive scope, versioned model cards, training/backtest evidence, feature definitions, uncertainty policy, monitoring thresholds, and approval boundaries.

## Business definitions the customer must approve

Before implementation, the customer should approve a metric contract for:

- Market share: internal Fly91 sales contribution or external airline market share.
- Yield: numerator, denominator, included revenue components, unit and treatment of taxes, fees, reissues, cancellations, and refunds.
- Revenue: whether `pnr_flight.total_amount` and `rbd_daily.total_revenue` represent booked, flown, recognized, net, ancillary-inclusive, or another basis; how the two pipelines reconcile; and which currency applies.
- Tickets and passengers: segment count, PNR count, booked seats, or boarded passengers.
- Series utilization and the treatment of cancellations, releases, substitutions, and no-shows.
- Group conversion denominator; whether `status_id=6` permanently means approved; mapping of Won, Lost, In Progress, and Expired statuses; approved-versus-quoted fallback; and whether route fares are per passenger, per leg, or total.
- POS-to-agency matching, hierarchy history, flight/sector normalization, schedule overlap precedence, operations-upload precedence, and handling of ambiguous or unmatched keys.
- Comparable-period and same-days-to-departure rules, including timezone, invalid string dates, missing captures, and late-arriving records.
- Inventory capacity: physical, authorized, allocated, sold, remaining, or available; interpretation of flight totals repeated at RBD grain; and the approved capacity fallback.
- Competitor fare comparability: taxes, fees, direct versus connecting itineraries, websites/operators, cabin/fare conditions, and freshness.
- Campaign baseline, attribution window, exposure rule, incremental revenue, incentive cost, meaning of `rm_offers.amount_inr`, and interpretation of an empty target array.
- Revenue-at-risk and FSI benchmark methods, including comparison windows, capacity fallback, expected-volume baseline, and whether heuristic output may be used for decisions.
- Materiality thresholds represented by X, Y, and Z in the customer questionnaire.
- Minimum sample size, small-group suppression, confidence threshold, and human-review requirement for recommendations or externally visible actions.

These definitions are versioned business policy. The language model may resolve synonyms, but it must not invent the definitions.

## How Traccia Runtime will use the data

Traccia Runtime does not ingest operational airline systems or perform warehouse transformations. The customer data platform should publish curated, tested marts or governed views through a least-privilege read-only PostgreSQL identity.

The runtime uses those products as follows:

1. **Schema and catalog ingestion.** Schema descriptions, ownership, freshness, sensitivity, joins, and source identifiers are indexed for retrieval. Retrieved documentation remains untrusted context.
2. **Semantic registration.** Metrics, dimensions, grains, time rules, allowed joins, certifications, and policy tags are loaded from versioned YAML or code. The semantic digest is attached to each result.
3. **Intent resolution.** The router maps conversational language such as “Series risk” or “weak sectors” to governed semantic references and approved analytical methods.
4. **Data sufficiency checks.** The planner checks that every required metric, dimension, model input, threshold, and time basis exists. Missing material information produces one concise clarification or a bounded blocked result.
5. **Typed analytical planning.** A workflow graph creates semantic-query, comparison, transformation, model, verification, and composition nodes with explicit dependencies and budgets.
6. **Safe query execution.** The runtime compiles or validates parameterized read-only SQL, enforces tenant and data permissions, limits query cost, and returns bounded typed result sets.
7. **Analytical execution.** Application-owned airline plugins calculate trends, contribution, concentration, funnel metrics, Series risk, campaign uplift, forecasts, or optimization outcomes. Predictive and causal work is delegated to versioned model services.
8. **Verification.** Deterministic checks and an independent reviewer compare claims with result provenance, model limits, confidence, freshness, and policy.
9. **Conversation response.** The dashboard receives typed narrative, tables, charts, SQL where permitted, warnings, assumptions, citations, suggested drill-downs, and approval requests.
10. **Telemetry and audit.** Traccia captures the conversation turn, agent/model/tool hierarchy, semantic and prompt versions, data sources, policy decisions, latency, usage, confidence, and outcome without private reasoning.

## Modifications implemented in Traccia Runtime

These reusable, cross-industry capabilities now live in Traccia Runtime rather than in the airline
application. The implementation is exposed through the dependency-injected container and documented
in [`docs/analytical-runtime.md`](../../../docs/analytical-runtime.md). Production hardening items that
still require deployment work are kept explicit in the Runtime production-readiness roadmap.

| Capability | Implemented Runtime surface | Talk-to-Data use |
|---|---|---|
| Typed graph | `analytics` DAG contracts, schema validation, parallel scheduling, retry/cache/budgets, independent result handles, memory/PostgreSQL checkpoints | The dashboard publishes each SQL result as a separate `AnalyticalResult`; complex workflows can move to the graph without changing method contracts. |
| Semantic planning/compilation | Extended metric/time/snapshot/SCD contracts, `ProductionSemanticQueryCompiler`, and `SemanticQueryGraphPlanner` with governed joins, fan-out rejection, ratio/non-additive/null and semi-additive behavior, freshness/SCD predicates, lineage and explanations | Existing generated-SQL checks remain; new flows compile independent governed requests into separate query nodes and use explicit transformations for multi-fact, rolling, cohort, funnel, or same-DTD work. Unsupported compound shapes fail closed rather than being silently ignored. |
| Analytical methods | Versioned manifests, typed schemas, requirements, lifecycle, validation/backtest/approval metadata, deterministic and model-service modes | Airline plugins install into `container.analytical_methods`; formulas remain in `application/analytics_tools.py`. |
| Model services | Separate forecast/optimization/simulation/causal registry with version pinning, batch validation, health, drift, timeout, cancellation and fallback | Future airline model endpoints register through this interface; LLM prompts cannot impersonate analytical models. |
| Query governance | Source trust, purpose/authorization/metric/column controls, quotas, fingerprints, cache/audit/export policy | The read-only executor sends certified-source evidence, identity, purpose and workload estimates before every query. |
| Verification/evaluation | Claim-to-cell reconciliation, metric/model/sample/window checks, golden plans/SQL/numerics/claim scoring and promotion gates | Customer fixtures can become deterministic regression cases independent of model credentials. |
| Distributed durability | PostgreSQL graph states and `SKIP LOCKED` work leases, deduplication, renewal/recovery, distributed worker, and cross-process durable event polling | Local dashboard remains in-process; deployed analytical graphs can be handled by separately supervised workers. |

### 1. Typed analytical workflow graph — implemented

Add an execution graph whose nodes can represent `semantic_query`, `join_results`, `compare_periods`, `calculate`, `model_call`, `rank`, `verify`, and `compose`. Each node needs typed inputs and outputs, dependencies, concurrency rules, provenance, retry policy, cache policy, and budgets. Result sets must remain separate and addressable; independently generated query rows must not be flattened into one undifferentiated collection.

### 2. Production semantic query planner and compiler — implemented

Extend the semantic contracts and compiler with:

- Safe multi-model joins and fan-out detection.
- Derived, ratio, semi-additive, and non-additive metrics.
- Time grains, calendars, relative periods, rolling windows, and period comparisons.
- Snapshot selection and same-days-to-departure semantics.
- Cohorts, funnels, contribution, concentration, ranking, and window calculations.
- Default filters, null behavior, late-arriving data, and slowly changing dimensions.
- Metric-level authorization, sensitivity, freshness, lineage, and certification policies.
- Dialect-aware SQL compilation and explainable compilation metadata.

### 3. Analytical method registry — implemented

Generalize analytical tools into versioned method contracts declaring:

- Stable method name and version.
- Supported analysis kinds.
- Typed input result schemas and parameter schemas.
- Required metrics, dimensions, history, sample size, and model artifacts.
- Output schema, uncertainty, assumptions, warnings, and evidence linkage.
- Deterministic implementation or remote model-service adapter.
- Backtest, validation, approval, and deprecation metadata.

Airline implementations remain plugins registered by the dashboard application.

### 4. Model-service abstraction — implemented

Add a provider-neutral interface for forecast, optimization, simulation, and causal-model services. It should support model discovery, version pinning, input validation, batch/async execution, timeouts, cancellation, confidence intervals, feature/model lineage, drift state, fallback policy, and health checks. These services are different from LLM providers and must not be represented as prompts.

### 5. Data trust and query governance — implemented

Add reusable enforcement for:

- Data freshness and completeness requirements before a plan runs.
- Source and column lineage attached to every result and claim.
- Row-, column-, metric-, and purpose-level authorization.
- Query estimation, timeout, scan/compute quotas, and concurrency pools.
- Query fingerprinting, idempotency, result caching, and audit retention.
- Policy-controlled SQL visibility, downloads, and artifact export.

Database RLS and grants remain the final security boundary.

### 6. Verification and evaluation — implemented

Add deterministic claim-to-cell provenance, numerical reconciliation, metric certification checks, minimum-sample validation, comparison-window validation, and model-version enforcement. Evaluation hooks should support golden questions, expected plans, SQL properties, fixed-data numerical results, allowed claims, forbidden claims, regression scoring, and promotion gates.

### 7. Durable distributed execution — implemented primitives

The Runtime now provides worker leases, distributed task claiming, cross-process durable event
polling, resumable graph checkpoints, deduplication, and safe recovery of completed graph nodes. A
deployment must still supervise workers, renew long-running leases, configure Redis/PostgreSQL,
provide atomic event sequencing at high publisher concurrency, and operate dead-letter/reconciliation
flows. The dashboard deliberately retains in-process execution for local development.

## Additions required only in the airline application

The following must not be added to Traccia Runtime core:

- Airline warehouse marts and source-system connectors.
- B2C, Series, Group, campaign, route, flight, RBD, LF, yield, and revenue-at-risk definitions.
- Airline-specific planners, thresholds, and clarification rules.
- Booking-curve, Series-risk, Group-funnel, campaign-attribution, capacity, and revenue-management plugins.
- Airline forecasting, elasticity, fleet, schedule, and network-optimization model integrations.
- Executive question catalogue, suggested questions, charts, and business acceptance tests.

## Suggested delivery sequence

1. Agree the pilot question set and explicitly mark unsupported Series, causal, external-market, and prescriptive questions.
2. Resolve the open data-owner questions in `database_context.md`; approve metric definitions, privacy rules, source ownership, and service levels.
3. Certify governed views over the existing PNR, RT, ancillary, group, offer, inventory, competitor, schedule, and operations sources. Normalize dates and publish conformed agency, route, and flight-instance keys.
4. Add the missing business calendar/reference data and authoritative operated-flight ledger needed by the pilot.
5. Configure and exercise the implemented Traccia semantic contracts, analytical graph, source certification checks, and deterministic evaluations against the customer's infrastructure.
6. Define and certify the airline semantic layer and descriptive/comparison/contribution/concentration methods against fixed expected results; register them through the Runtime method registry.
7. Add Group event history, Series products, and offer exposure/redemption products only for the next agreed functional scope.
8. Add validated forecasting, causal, economic, and optimization services only after their required data, backtests, model governance, and approval boundaries exist.
9. Convert the customer questionnaire and anonymized fixtures into automated plan, SQL, numerical, authorization, and response regression tests.
10. Add production authorization, freshness, lineage, distributed execution, and human approval, then release in stages: descriptive, diagnostic, predictive, and prescriptive.

## Readiness rule

A question is production-ready only when its required sources are available, its metrics and joins are certified, its analytical method is versioned and validated, authorization and freshness checks pass, and a deterministic evaluation case confirms the expected result. Adding another agent or a larger language model does not replace any of these requirements.
